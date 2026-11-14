"""Setup › Tuners: what is using provider connections right now. Each running tuner (one per channel or
feed, shared by every screen, Plex and the DVR on it) and each On Demand title being played holds one
connection. Shown next to each playlist's connection limit, so "No Signal" on a full Xtream account can
be explained. Provider addresses hold logins, so nothing here returns a URL."""
from urllib.parse import urlsplit

from . import db, guide, hls, remote, streaming, tuner


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


def _who(name, screens, recs):
    """A reader's name (tuner.py / streaming.py) as something a person recognises."""
    if not name:
        return {"kind": "app", "name": "Another app (Plex, VLC…)"}
    if name.startswith("dvr:"):
        r = recs.get(name[4:])
        return {"kind": "dvr", "name": f"Recording: {r['title']}" if r and r["title"] else "Recording"}
    hls_ = name.startswith("hls:")
    if hls_:
        name = name[4:]
    if name.startswith("setup-"):
        return {"kind": "setup", "name": "Setup preview"}
    s = screens.get(name)
    if s:
        return {"kind": "tv", "name": s["name"] or "TV"}
    return {"kind": "tv", "name": "iPhone / iPad" if hls_ else "A TV screen"}


def in_use():
    tuners, ondemand = tuner.running(), streaming.playing() + hls.playing()
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

    out = []
    for t in tuners:
        st = streams.get(t["url"])
        c = chan_of.get(st["key"]) if st else None
        if c:
            kind, title = "channel", f"{c['number']} {c['name']}"
        else:
            kind, title = "feed", st["name"] if st else "A channel"
        out.append({"kind": kind, "title": title, "stream": st["name"] if st else "",
                    "source_id": _source_for(t["url"], by_url, sources), "since": t["since"], "mb": t["mb"],
                    "viewers": [_who(n, screens, recs) for n in t["viewers"]]})
    for p in ondemand:
        out.append({"kind": "ondemand", "title": p["label"] or "On Demand", "stream": "",
                    "source_id": _source_for(p["url"], by_url, sources), "since": p["since"], "mb": None,
                    "viewers": [_who(p["client"], screens, recs)] if p["client"] else []})
    out.sort(key=lambda x: x["since"])

    used = {}
    for x in out:
        used[x["source_id"]] = used.get(x["source_id"], 0) + 1
    srcs = [{"id": s["id"], "name": s["name"], "type": s["type"], "used": used.get(s["id"], 0),
             "limit": int(db.get_meta(f"xtream_max_{s['id']}", 0) or 0) if s["type"] == "xtream" else 0}
            for s in sources if s["enabled"] or used.get(s["id"])]
    return {"sources": srcs, "tuners": out}
