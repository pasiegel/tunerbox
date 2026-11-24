"""Setup › Tuners: what is using provider connections right now. Each running tuner (one per channel or
feed, shared by every screen, Plex and the DVR on it) and each On Demand title being played holds one
connection. Shown next to each playlist's connection limit, so "No Signal" on a full Xtream account can
be explained. Provider addresses hold logins, so nothing here returns a URL.

It also enforces each playlist's limit: starting one more connection than the account allows is
refused up front (`Full`), so the screen can say what is using the connections instead of showing No
Signal after the provider turns it away. A recording that's starting may take a connection from live
TV (Setup › Settings › DVR)."""
import logging
import threading
import time
from urllib.parse import urlsplit

from . import config, db, epg, guide, hls, remote, streaming, tuner

log = logging.getLogger("tunerbox.connections")

# Held from checking a playlist's room until the new stream is registered (tuner.join, hls.start,
# streaming.start_cmd), so two screens tuning at once can't both take the last connection, and a
# connection freed for a recording can't be taken back by the screen that lost it. Taken before
# tuner._lock, hls._lock and streaming._lock, never after.
admit = threading.RLock()


class Full(Exception):
    """Every connection a playlist allows is in use."""

    def __init__(self, src, used, limit):
        self.source_id, self.source, self.used, self.limit = src["id"], src["name"], used, limit
        super().__init__(f"{src['name']} allows {limit} connection{'' if limit == 1 else 's'} at a time, "
                         f"and {'it is' if limit == 1 else 'all are'} in use")


class Stopped(Full):
    """The stream was just stopped from Setup › Tuners, and this app is asking for it again (Roku, Plex
    and VLC reconnect by themselves). A Full, so every endpoint already answers it with a 503."""

    def __init__(self):
        Exception.__init__(self, "This stream was stopped from Setup › Tuners")
        self.source_id, self.source, self.used, self.limit = None, "", 0, 0


def auto_limit(src):
    """What the provider (Xtream max_connections) or the device (HDHomeRun TunerCount) says; 0 = unknown."""
    if src["type"] == "xtream":
        return int(db.get_meta(f"xtream_max_{src['id']}", 0) or 0)
    if src["type"] == "hdhomerun":
        return int(db.get_meta(f"hdhr_tuners_{src['id']}", 0) or 0)
    return 0


def limit_of(src):
    """Connections a playlist may use at once: the user's number on its card, else auto_limit. 0 = no limit."""
    own = int(src.get("max_conn") or 0)
    return own if own > 0 else auto_limit(src)


def _source(source_id):
    return db.one("SELECT * FROM sources WHERE id=?", (source_id,)) if source_id else None


def _mine(client):
    """Reader names a screen's own stream goes by (tuner.py / hls.py): what it gives up when it tunes."""
    return {client, f"hls:{client}"} if client else set()


def used(source_id, client=""):
    """Connections a playlist has open. `client`'s own streams don't count: tuning stops them first."""
    mine = _mine(client)
    n = 0
    for t in tuner.running():
        if t.get("source_id") == source_id and not (mine and t["viewers"] and set(t["viewers"]) <= mine):
            n += 1
    for p in streaming.playing() + hls.playing():
        if p.get("source_id") == source_id and not (client and p["client"] == client):
            n += 1
    return n


def check(source_id, url=None, mode=None, audio="aac", client="", any_mode=False):
    """Raise Full if opening `url` needs a connection its playlist doesn't have. Joining a tuner that's
    already running on it is free. `url=None`: always a new connection (On Demand)."""
    src = _source(source_id)
    lim = limit_of(src) if src else 0
    if not lim:
        return
    if url and tuner.sharing(url, mode, audio, any_mode):
        return
    n = used(source_id, client)
    if n >= lim:
        raise Full(src, n, lim)


def live_used(source_id):
    """Connections a playlist has open for live TV and On Demand (not for recordings)."""
    n = sum(1 for t in tuner.running() if t.get("source_id") == source_id
            and not any(v.startswith("dvr:") for v in t["viewers"]))
    return n + sum(1 for p in streaming.playing() + hls.playing() if p.get("source_id") == source_id)


def make_room(source_id, why=""):
    """Free one connection on a playlist for a recording: live TV first (the channel with the fewest
    viewers, newest first), then On Demand. Never another recording. True when one was freed."""
    live = [t for t in tuner.running() if t.get("source_id") == source_id
            and not any(n.startswith("dvr:") for n in t["viewers"])]
    if live:
        t = min(live, key=lambda t: (len(t["viewers"]), -t["since"]))
        if tuner.evict(t["url"], t["mode"], t["audio"]):
            log.warning("Connection limit: live TV on playlist %s stopped for %s", source_id, why or "a recording")
            return True
    for p in streaming.playing() + hls.playing():
        if p.get("source_id") == source_id and p["client"]:
            streaming.stop_client(p["client"])
            hls.stop_client(p["client"])
            log.warning("Connection limit: On Demand on playlist %s stopped for %s", source_id, why or "a recording")
            return True
    return False


def dvr_takes_over():
    return bool(config.get("dvr_takes_connection", True))


def _host(url):
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _source_for(url, by_url, sources):
    """The playlist a provider address belongs to: from the streams table, else by server (On Demand)."""
    sid = by_url.get(url)
    if sid is not None:
        return sid
    host = _host(url)
    hits = [s for s in sources if host and _host(s["url"]) == host]
    named = [s for s in hits if s["username"] and f"/{s['username']}/" in url]
    best = named or hits
    return best[0]["id"] if best else None


# Players that say who they are in their User-Agent, for streams opened without a client id (the
# Roku app's live channels, Plex, VLC…). Only prefixes these players really send; anything else stays
# "Another app". Roku's media player sends "Roku/DVP-14.5 (…)".
_AGENTS = (("roku/", "Roku"), ("plexmediaserver", "Plex"), ("plex ", "Plex"), ("vlc/", "VLC"), ("kodi/", "Kodi"),
           ("jellyfin", "Jellyfin"), ("emby", "Emby"), ("channels", "Channels DVR"))


_client_names = {}      # client id -> the name a device gave itself (?name= on a stream: the Roku app)


def name_client(client, name):
    """Remember what a device that isn't a TV screen calls itself, for Setup › Tuners."""
    name = (name or "").strip()[:40]
    if client and name:
        if len(_client_names) > 200 and client not in _client_names:
            _client_names.clear()           # only ever a handful of devices; never let it grow
        _client_names[client] = name


def app_reader(user_agent, ip=""):
    """The reader name for a stream with no client id: "app:Roku@192.168.1.50", or "" when unknown."""
    ua = (user_agent or "").lower()
    app = next((n for p, n in _AGENTS if ua.startswith(p)), "")
    return f"app:{app}@{ip}" if app else ""


# After Setup › Tuners › Stop, so whatever was watching doesn't just reconnect. A TV screen asks
# /api/stopped before it reconnects or tries a backup feed by itself (so a channel it picks on purpose
# always plays); apps can't be asked, so they're refused for a while instead: by their client id (the
# Roku app) or, with none, by the stream (Plex, VLC…).
APP_HOLD, SCREEN_HOLD = 30, 120          # seconds
_stopped = {}                            # hold key -> time.monotonic() it ends


def _hold_key(name, url):
    if not name or name.startswith("app:"):
        return "url:" + url
    return name.removeprefix("hls:")


def _app(key):
    return key.startswith(("url:", "roku-"))


def _hold(names, url):
    now = time.monotonic()
    for k in [k for k, until in _stopped.items() if until <= now]:
        del _stopped[k]
    for n in names:
        if not n.startswith(("dvr:", "setup-")):
            k = _hold_key(n, url)
            _stopped[k] = now + (APP_HOLD if _app(k) else SCREEN_HOLD)


def after_stop(client, url):
    """Before a stream opens: raises Stopped when an app asks again for what Setup just stopped. A TV
    screen tuning means it (it checks was_stopped before retrying by itself), so its hold just ends."""
    k = _hold_key(client or "", url)
    if not _app(k):
        _stopped.pop(k, None)
    elif _stopped.get(k, 0) > time.monotonic():
        raise Stopped()


def was_stopped(client):
    """For a TV screen whose stream ended: was it stopped from Setup? Until it tunes something (after_stop)."""
    return bool(client) and _stopped.get(_hold_key(client, ""), 0) > time.monotonic()


def client_name(client):
    """For the log: the name a device gave itself, else its id (both come from the request: one line, kept short)."""
    name = str(_client_names.get(client) or client or "")
    return "".join(ch if ch.isprintable() else " " for ch in name)[:60]


def _who(name, screens, recs):
    """A reader's name (tuner.py / streaming.py) as something a person recognises."""
    if not name:
        return {"kind": "app", "name": "Another app (Plex, VLC…)"}
    if name.startswith("app:"):
        app, _, ip = name[4:].partition("@")
        return {"kind": "roku" if app == "Roku" else "app", "name": f"{app} · {ip}" if ip else app}
    if name.startswith("roku-"):        # the Roku app's own client id (On Demand, and its multiview pictures)
        base, _, tile = name.partition("~")
        return {"kind": "roku", "name": _client_names.get(base, "Roku") + (f" · multiview {tile}" if tile else "")}
    if name.startswith("dvr:"):
        r = recs.get(name[4:])
        return {"kind": "dvr", "name": f"Recording: {r['title']}" if r and r["title"] else "Recording"}
    hls_ = name.startswith("hls:")
    if hls_:
        name = name[4:]
    if name.startswith("setup-"):
        return {"kind": "setup", "name": "Setup preview"}
    name, _, tile = name.partition("~")        # a multiview tile: "<screen>~<slot>" (multiview.py)
    more = f" · multiview {tile}" if tile else ""
    s = screens.get(name)
    if s:
        return {"kind": "tv", "name": (s["name"] or "TV") + more}
    return {"kind": "tv", "name": ("iPhone / iPad" if hls_ else "A TV screen") + more}


def in_use():
    out = _entries()
    for x in out:
        x.pop("_names", None)
    sources = db.rows("SELECT * FROM sources ORDER BY id")
    used_ = {}
    for x in out:
        used_[x["source_id"]] = used_.get(x["source_id"], 0) + 1
    srcs = [{"id": s["id"], "name": s["name"], "type": s["type"], "used": used_.get(s["id"], 0), "limit": limit_of(s)}
            for s in sources if s["enabled"] or used_.get(s["id"])]
    return {"sources": srcs, "tuners": out}


def _show(p):
    """What's on a channel now (a guide programme row), for Setup › Tuners; None without a guide."""
    if not p or not p["title"]:
        return None
    return {"title": p["title"], "subtitle": p["subtitle"] or "", "episode": p["episode"] or "",
            "start": p["start"], "stop": p["stop"]}


def _entries():
    """Everything holding a connection, oldest first, with `_names`: the raw reader names."""
    tuners = tuner.running()
    ondemand = ([dict(p, _key=f"s{p.get('id')}") for p in streaming.playing()]
                + [dict(p, _key=f"h{p.get('id')}") for p in hls.playing()])
    sources = db.rows("SELECT id, name, type, url, username, enabled FROM sources ORDER BY id")
    urls = [t["url"] for t in tuners] + [p["url"] for p in ondemand]
    streams = {}
    if urls:
        q = ",".join("?" * len(urls))
        streams = {r["url"]: r for r in db.rows(f"SELECT key, name, url, source_id FROM streams WHERE url IN ({q})", tuple(urls))}
    by_url = {u: r["source_id"] for u, r in streams.items()}
    chan_of = {}
    for c in guide.channels(True):
        for k in c.get("streams") or ():
            chan_of.setdefault(k, c)
    screens = {s["id"]: s for s in remote.screens()}
    dvr_ids = {n[4:] for t in tuners for n in t["viewers"] if n.startswith("dvr:") and n[4:].isdigit()}
    recs = {}
    if dvr_ids:
        q = ",".join("?" * len(dvr_ids))
        recs = {str(r["id"]): r for r in db.rows(f"SELECT id, title FROM recordings WHERE id IN ({q})", tuple(map(int, dvr_ids)))}

    # the show on each channel in use right now, from the guide (one query for all of them)
    chans = {t["url"]: chan_of.get(streams[t["url"]]["key"]) for t in tuners if t["url"] in streams}
    now = int(time.time())
    on_now = epg.programs({c["epg"] for c in chans.values() if c and c.get("epg")}, now, now + 1)

    out = []
    for t in tuners:
        st = streams.get(t["url"])
        c = chans.get(t["url"])
        p = (on_now.get(c["epg"]) or [None])[0] if c and c.get("epg") else None
        if c:
            kind, title = "channel", f"{c['number']} {c['name']}"
        else:
            kind, title = "feed", st["name"] if st else "A channel"
        out.append({"id": f"t{t.get('id')}", "kind": kind, "title": title, "stream": st["name"] if st else "",
                    "number": c["number"] if c else None, "feed": st["key"] if st and not c else None,
                    "source_id": t.get("source_id") or _source_for(t["url"], by_url, sources),
                    "since": t["since"], "mb": t["mb"], "show": _show(p),
                    "viewers": [_who(n, screens, recs) for n in t["viewers"]], "_names": list(t["viewers"])})
    for p in ondemand:
        out.append({"id": p["_key"], "kind": "ondemand", "title": p["label"] or "On Demand", "stream": "", "number": None, "feed": None,
                    "source_id": p.get("source_id") or _source_for(p["url"], by_url, sources), "since": p["since"],
                    "mb": None, "show": None, "viewers": [_who(p["client"], screens, recs)] if p["client"] else [],
                    "_names": [p["client"]] if p["client"] else []})
    out.sort(key=lambda x: x["since"])
    return out


def stop(entry_id, cancel=lambda rec_id: None):
    """Force-stop one thing holding a connection (Setup › Tuners › Stop), by the `id` in_use() gave it.
    `cancel(rec_id)` is called for each recording on it *before* the stream is stopped: otherwise the DVR
    can see the drop first, take it for an interruption and reopen the stream. Returns those recording
    ids, or None when nothing by that id is running any more."""
    kind, key = (entry_id or "")[:1], (entry_id or "")[1:]
    if kind == "t" and key.isdigit():
        t = next((t for t in tuner.running() if t.get("id") == int(key)), None)
        if not t:
            return None
        recs = [int(n[4:]) for n in t["viewers"] if n.startswith("dvr:") and n[4:].isdigit()]
        for rid in recs:
            cancel(rid)
        _hold(t["viewers"], t["url"])
        return recs if tuner.stop_id(int(key)) else None
    if kind in "sh" and key:
        mod = streaming if kind == "s" else hls
        p = next((p for p in mod.playing() if str(p.get("id")) == key), None)
        if not p:
            return None
        _hold([p["client"]], p["url"])
        return [] if mod.stop_id(p["id"]) else None
    return None


def busy(stream_rows, client="", mode=None, audio="aac"):
    """Why a channel or feed can't start, when the reason is connection limits: for each playlist it
    could play from that is full, what is using its connections. [] when any of them has room (or
    no limit), so the failure was something else. `mode` / `audio`: what the screen asked for."""
    ids = []
    for r in stream_rows:
        if r.get("url") and tuner.sharing(r["url"], mode or r.get("video_mode") or None, audio):
            return []                   # already on: watching it takes no new connection
        if r.get("source_id") not in ids:
            ids.append(r.get("source_id"))
    full = []
    for sid in ids:
        src = _source(sid)
        lim = limit_of(src) if src else 0
        if not lim or used(sid, client) < lim:
            return []
        full.append((src, lim))
    if not full:
        return []
    mine = _mine(client)
    entries = _entries()
    out = []
    for src, lim in full:
        using = [{k: x[k] for k in ("kind", "title", "number", "feed", "viewers")}
                 for x in entries if x["source_id"] == src["id"] and not (mine and x["_names"] and set(x["_names"]) <= mine)]
        out.append({"source": src["name"], "limit": lim, "using": using})
    return out
