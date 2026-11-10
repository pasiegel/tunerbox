"""Guide data: Gracenote for the local lineup, XMLTV for everything else."""
import gzip
import hashlib

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import httpx

from . import config, db, lineup
from .text import norm

log = logging.getLogger("tunerbox.epg")

GN_GRID = "https://tvlistings.gracenote.com/api/grid"
GN_PROVIDERS = "https://tvlistings.gracenote.com/gapzap_webapi/api/Providers/getPostalCodeProviders/{country}/{zip}/gapzap/en-us"
GN_IMG = "https://zap2it.tmsimg.com/assets/{}.jpg"
FILTER_CAT = {"filter-movie": "Movie", "filter-sports": "Sports", "filter-news": "News",
              "filter-kids": "Kids", "filter-family": "Family", "filter-talk": "Talk",
              "filter-reality": "Reality"}


def _client(timeout=90):
    return httpx.Client(timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": config.get("user_agent")})


# ------------------------------------------------------------------ Gracenote
def gracenote_providers(postal_code, country="USA"):
    with _client(30) as c:
        r = c.get(GN_PROVIDERS.format(country=country, zip=postal_code))
        r.raise_for_status()
        return r.json().get("Providers", [])


_ota = {"t": 0, "zip": "", "map": {}}


def ota_callsigns(postal_code=None):
    """Over-the-air channels for the ZIP code from Gracenote: {'17.3': 'WXMIDT3', ...} (cached a day).
    Antenna tuners only know the channel numbers; this tells TunerBox which station each one is."""
    postal_code = postal_code or (config.get("gracenote") or {}).get("postal_code", "")
    if not postal_code:
        return {}
    if _ota["zip"] == postal_code and time.time() - _ota["t"] < 86400 and _ota["map"]:
        return _ota["map"]
    params = {"lineupId": f"USA-OTA{postal_code}", "timespan": 1, "headendId": "lineupId", "country": "USA",
              "timezone": "", "device": "-", "postalCode": postal_code, "isOverride": "true",
              "time": int(time.time()) // 1800 * 1800, "pref": "16,128", "userId": "-", "aid": "orbebb",
              "languagecode": "en-us"}
    try:
        with _client(30) as c:
            r = c.get(GN_GRID, params=params,
                      headers={"Referer": "https://tvlistings.gracenote.com/grid-affiliates.html?aid=orbebb"})
            r.raise_for_status()
            chans = r.json().get("channels", [])
    except Exception as e:  # noqa: BLE001
        log.info("Gracenote antenna lineup unavailable: %s", e)
        return _ota["map"] if _ota["zip"] == postal_code else {}
    m = {str(ch.get("channelNo")): ch.get("callSign") for ch in chans if ch.get("channelNo") and ch.get("callSign")}
    _ota.update(t=time.time(), zip=postal_code, map=m)
    return m


def gracenote_ready():
    """The Gracenote guide is on and a provider lineup has been picked (Setup > Guide)."""
    g = config.get("gracenote") or {}
    return bool(g.get("enabled") and g.get("lineup_id") and g.get("headend_id"))


def _grid_params(g, t, timespan):
    return {
        "lineupId": g["lineup_id"], "timespan": timespan, "headendId": g["headend_id"],
        "country": g.get("country", "USA"), "timezone": "", "device": g.get("device", "X"),
        "postalCode": g.get("postal_code", ""), "isOverride": "true", "time": t,
        "pref": "16,128", "userId": "-", "aid": "orbebb", "languagecode": "en-us",
    }


def _applied(chans):
    lineup.apply_gracenote(chans)
    db.set_meta("gn_lineup", config.get("gracenote")["lineup_id"])


def fetch_gracenote_channels():
    """Load the picked provider's channel numbers into the lineup (no listings). The scan does this
    before matching, so the first scan after picking a provider already uses its numbers."""
    if not gracenote_ready():
        return 0
    g = config.get("gracenote")
    try:
        with _client(60) as c:
            r = c.get(GN_GRID, params=_grid_params(g, int(time.time()) // 1800 * 1800, 1),
                      headers={"Referer": "https://tvlistings.gracenote.com/grid-affiliates.html?aid=orbebb"})
            r.raise_for_status()
            chans = json.loads(r.content.decode("utf-8")).get("channels", [])
    except Exception as e:  # noqa: BLE001
        log.warning("Gracenote channel list failed: %s", e)
        return 0
    if chans:
        _applied(chans)
    log.info("Gracenote lineup: %d channels", len(chans))
    return len(chans)


def fetch_gracenote(progress=None):
    if not gracenote_ready():
        return 0
    g = config.get("gracenote")
    hours = int(g.get("hours", 48))
    t0 = int(time.time()) // 1800 * 1800
    steps = list(range(t0, t0 + hours * 3600, 6 * 3600))
    progs, channels_done, windows = {}, False, []
    with _client(90) as c:
        for i, t in enumerate(steps):
            data = _grid_block(c, g, t, i)
            if data is None:
                continue
            windows.append((t, t + 6 * 3600))
            chans = data.get("channels", [])
            if not channels_done and chans:
                _applied(chans)
                channels_done = True
            for ch in chans:
                try:
                    num = int(float(ch["channelNo"]))
                except (KeyError, ValueError):
                    continue
                for ev in ch.get("events", []):
                    p = ev.get("program") or {}
                    start = _iso(ev.get("startTime"))
                    stop = _iso(ev.get("endTime"))
                    if not start or not stop:
                        continue
                    ep = ""
                    if p.get("season") and p.get("episode"):
                        ep = f"S{int(p['season']):02d}E{int(p['episode']):02d}"
                    sub = p.get("episodeTitle") or ""
                    cat = next((FILTER_CAT[f] for f in ev.get("filter") or [] if f in FILTER_CAT), "")
                    if cat == "Movie" and p.get("releaseYear") and not sub:
                        sub = str(p["releaseYear"])
                    flags = ev.get("flag") or []
                    progs[(f"gn:{num}", start)] = (
                        f"gn:{num}", start, stop, p.get("title") or "", sub, p.get("shortDesc") or "",
                        ep, cat, ev.get("rating") or "",
                        GN_IMG.format(ev["thumbnail"]) if ev.get("thumbnail") else "",
                        int("New" in flags), int("Live" in flags))
            if progress:
                progress(i + 1, len(steps))
            time.sleep(1.0)
    if progs:
        # Only replace the 6-hour windows that downloaded. A block that failed keeps the listings
        # from the last refresh instead of leaving a hole (e.g. no "on now" after a timeout).
        _store(progs.values(), windows)
    log.info("Gracenote: %d programs", len(progs))
    return len(progs)


GN_TRIES = 3


def _grid_block(c, g, t, i):
    """One 6-hour Gracenote grid block, retried a few times (read timeouts are common). None if it failed."""
    for attempt in range(1, GN_TRIES + 1):
        try:
            r = c.get(GN_GRID, params=_grid_params(g, t, 6),
                      headers={"Referer": "https://tvlistings.gracenote.com/grid-affiliates.html?aid=orbebb"})
            r.raise_for_status()
            return json.loads(r.content.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("Gracenote block %d failed (try %d/%d): %s", i, attempt, GN_TRIES, e)
            if attempt < GN_TRIES:
                time.sleep(5 * attempt)
    return None


def _iso(s):
    if not s:
        return 0
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


# ------------------------------------------------------------------ XMLTV
def _xmltv_time(s):
    s = (s or "").strip()
    m = re.match(r"(\d{14})\s*([+-]\d{4})?", s)
    if not m:
        return 0
    tz = m.group(2) or "+0000"
    return int(datetime.strptime(m.group(1) + tz, "%Y%m%d%H%M%S%z").timestamp())


def wanted_ids():
    """tvg-ids + names of every stream that's on the guide (mapped or extra)."""
    rows = db.rows("""SELECT s.tvg_id, s.norm FROM streams s
                      WHERE s.key IN (SELECT stream_key FROM mappings WHERE stream_key IS NOT NULL)
                         OR s.key IN (SELECT stream_key FROM extras WHERE active=1)""")
    ids = {r["tvg_id"] for r in rows if r["tvg_id"]}
    names = {r["norm"] for r in rows if r["norm"]}
    return ids, names


def xmltv_urls():
    urls = list(config.get("xmltv_sources") or [])
    for s in db.rows("SELECT epg_url FROM sources WHERE enabled=1 AND epg_url != ''"):
        for u in s["epg_url"].split(","):
            u = u.strip()
            if u and u not in urls:
                urls.append(u)
    return urls


def fetch_xmltv(progress=None):
    ids, names = wanted_ids()
    lo = int(time.time()) - 3 * 3600
    hi = int(time.time()) + 4 * 86400
    claimed = set()
    total = 0
    urls = xmltv_urls()
    old_names = [(r["norm"], r["channel"]) for r in db.rows("SELECT norm, channel FROM epg_names")]
    failed = False
    with db.conn() as c:
        c.execute("DELETE FROM epg_names")
    for n, url in enumerate(urls):
        try:
            got = _parse_xmltv(url, ids, names, claimed, lo, hi)
        except Exception as e:  # noqa: BLE001
            log.warning("XMLTV %s failed: %s", url, e)
            got = {}
            failed = True
        if isinstance(got, Unchanged):
            # same file, same channels wanted from it: the listings stored last time still stand
            claimed.update(got.channels)
            total += got.count
            if progress:
                progress(n + 1, len(urls))
            log.info("XMLTV %s: unchanged, %d channels kept", url, len(got.channels))
            continue
        if got:
            claimed.update(got.keys())
            rows = [p for lst in got.values() for p in lst]
            _store(rows, [(lo, None)], channels=list(got.keys()))
            total += len(rows)
        if progress:
            progress(n + 1, len(urls))
        log.info("XMLTV %s: %d channels", url, len(got))
    if failed and old_names:
        # A guide that didn't download keeps its channel-name matches from last time, so those
        # channels still show the listings already stored (fresh matches above win).
        keep = [(nn, ch) for nn, ch in old_names if ch[2:] not in claimed]
        with db.conn() as c:
            c.executemany("INSERT OR IGNORE INTO epg_names(norm, channel) VALUES (?,?)", keep)
    return total


class Unchanged:
    """_parse_xmltv's answer when a guide doesn't need reading again."""
    def __init__(self, channels, count):
        self.channels, self.count = channels, count


REPARSE_AFTER = 20 * 3600   # read an unchanged guide again at least this often (the listing window moves on)


def _cache_dir():
    cache = config.DATA / "cache"
    cache.mkdir(exist_ok=True)
    return cache


def _url_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:10]


def _download(url):
    """(local path, version) of a guide. Web guides go to data/cache (streamed, so 100MB+ guides don't sit
    in memory) and are asked for only if changed (ETag / Last-Modified); the version is a hash of the file.
    Files on disk are used in place; their version is their size and modified time."""
    if not re.match(r"^https?://", url, re.I):
        st = Path(url).stat()
        return url, f"{st.st_mtime_ns}:{st.st_size}"
    path = _cache_dir() / ("epg_" + _url_id(url))
    mkey = f"xmltv_http_{_url_id(url)}"
    try:
        prev = json.loads(db.get_meta(mkey) or "{}")
    except ValueError:
        prev = {}
    headers = {}
    if path.exists() and prev.get("sha"):
        if prev.get("etag"):
            headers["If-None-Match"] = prev["etag"]
        if prev.get("modified"):
            headers["If-Modified-Since"] = prev["modified"]
    tmp = path.with_name(path.name + ".part")
    with _client(600) as c, c.stream("GET", url, headers=headers) as r:
        if r.status_code == 304 and headers:
            return path, prev["sha"]
        r.raise_for_status()
        sha = hashlib.sha1()
        with open(tmp, "wb") as f:   # a half-finished download never replaces the last good copy
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                sha.update(chunk)
        etag, modified = r.headers.get("etag", ""), r.headers.get("last-modified", "")
    os.replace(tmp, path)
    db.set_meta(mkey, json.dumps({"etag": etag, "modified": modified, "sha": sha.hexdigest()}))
    return path, sha.hexdigest()


def _open_file(path):
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def _selection(index, ids, names, claimed):
    """What _parse_open would take from a guide, worked out from its channel index:
    (channel ids to keep, display-name matches)."""
    keep, hits = set(), []
    for cid, dns in index.items():
        if cid in claimed:
            continue
        if cid in ids:
            keep.add(cid)
        for nn in dns:
            if nn in names:
                keep.add(cid)
                hits.append((nn, f"x:{cid}"))
    return sorted(keep), hits


def _parse_xmltv(url, ids, names, claimed, lo, hi):
    """{channel id: [program rows]}, or Unchanged when the file and the channels wanted from it are the
    same as at the last read (less than REPARSE_AFTER ago), so its stored listings still stand."""
    path, version = _download(url)
    uid = _url_id(url)
    idx_file = _cache_dir() / f"epg_{uid}.idx.json"
    try:
        last = json.loads(db.get_meta(f"xmltv_last_{uid}") or "{}")
        idx = json.loads(idx_file.read_text(encoding="utf-8")) if idx_file.exists() else {}
    except (OSError, ValueError):
        last, idx = {}, {}
    if (last.get("version") == version == idx.get("version")
            and time.time() - last.get("t", 0) < REPARSE_AFTER):
        keep, hits = _selection(idx["index"], ids, names, claimed)
        if keep == last.get("keep"):
            if hits:
                with db.conn() as c:
                    c.executemany("INSERT OR IGNORE INTO epg_names(norm, channel) VALUES (?,?)", hits)
            return Unchanged(last.get("channels", []), last.get("count", 0))
    info = {}
    f = _open_file(path)
    try:
        out = _parse_open(f, ids, names, claimed, lo, hi, info)
    finally:
        f.close()
    try:
        idx_file.write_text(json.dumps({"version": version, "index": info["index"]}), encoding="utf-8")
        keep, _ = _selection(info["index"], ids, names, claimed)
        db.set_meta(f"xmltv_last_{uid}", json.dumps({
            "version": version, "t": int(time.time()), "keep": keep, "channels": sorted(out),
            "count": sum(len(v) for v in out.values())}))
    except OSError as e:
        log.info("Couldn't save the guide index for %s: %s", url, e)
    return out


def _parse_open(f, ids, names, claimed, lo, hi, info=None):
    """Programs of the wanted channels. `info`, if given, gets "index": {channel id: [display names]}
    of every channel in the file, so an unchanged file needn't be read again to know what it holds."""
    keep = {i for i in ids if i not in claimed}
    name_hits = []
    out = {}
    index = {}
    for _ev, el in ET.iterparse(f, events=("end",)):
        tag = el.tag
        if tag == "channel":
            cid = el.get("id") or ""
            dns = [norm(dn.text or "") for dn in el.findall("display-name")]
            if cid:
                index.setdefault(cid, []).extend(n for n in dns if n)
            if cid and cid not in claimed:
                for nn in dns:
                    if nn in names:
                        keep.add(cid)
                        name_hits.append((nn, f"x:{cid}"))
            el.clear()
        elif tag == "programme":
            cid = el.get("channel") or ""
            if cid and cid not in index:
                index[cid] = []
            if cid in keep:
                start, stop = _xmltv_time(el.get("start")), _xmltv_time(el.get("stop"))
                if stop > lo and start < hi:
                    ep = ""
                    for en in el.findall("episode-num"):
                        if en.get("system") == "onscreen" and en.text:
                            ep = en.text.strip()
                        elif en.get("system") == "xmltv_ns" and en.text and not ep:
                            parts = en.text.split(".")
                            try:
                                if parts[0].strip() and parts[1].strip():
                                    ep = f"S{int(parts[0].split('/')[0]) + 1:02d}E{int(parts[1].split('/')[0]) + 1:02d}"
                            except (ValueError, IndexError):
                                pass
                    icon = el.find("icon")
                    rating = el.find("rating/value")
                    out.setdefault(cid, []).append((
                        f"x:{cid}", start, stop, _txt(el, "title"), _txt(el, "sub-title"), _txt(el, "desc"),
                        ep, _txt(el, "category"), rating.text if rating is not None else "",
                        icon.get("src") if icon is not None else "",
                        int(el.find("new") is not None), int(el.find("live") is not None)))
            el.clear()
    if name_hits:
        with db.conn() as c:
            c.executemany("INSERT OR IGNORE INTO epg_names(norm, channel) VALUES (?,?)", name_hits)
    if info is not None:
        info["index"] = index
    return out


def _txt(el, tag):
    e = el.find(tag)
    return (e.text or "").strip() if e is not None else ""


# ------------------------------------------------------------------ storage / queries
def _store(rows, windows, channels=None):
    """Replace these channels' programs starting inside each (lo, hi) window (hi None = open-ended)."""
    rows = list(rows)
    chans = channels or sorted({r[0] for r in rows})
    with db.conn() as c:
        c.execute("DELETE FROM programs WHERE stop < ?", (int(time.time()) - 6 * 3600,))
        for ch in chans:
            for lo, hi in windows:
                if hi is None:
                    c.execute("DELETE FROM programs WHERE channel=? AND start>=?", (ch, lo))
                else:
                    c.execute("DELETE FROM programs WHERE channel=? AND start>=? AND start<?", (ch, lo, hi))
        c.executemany("INSERT OR REPLACE INTO programs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def refresh_all(progress=None):
    n1 = fetch_gracenote(lambda a, b: progress and progress("Local guide (Gracenote)", a, b))
    n2 = fetch_xmltv(lambda a, b: progress and progress("Online guides (XMLTV)", a, b))
    now = int(time.time())
    db.set_meta("epg_tried", now)
    # "Updated" means listings actually came in (or there is nothing set up to download).
    if n1 + n2 or not (gracenote_ready() or xmltv_urls()):
        db.set_meta("epg_updated", now)
    else:
        log.warning("Guide refresh downloaded nothing; keeping the old listings")
    return n1, n2


def programs(channel_keys, start, stop):
    if not channel_keys:
        return {}
    out = {}
    keys = list(channel_keys)
    for i in range(0, len(keys), 400):
        part = keys[i:i + 400]
        q = ",".join("?" * len(part))
        for r in db.rows(f"""SELECT * FROM programs WHERE channel IN ({q}) AND stop > ? AND start < ?
                             ORDER BY start""", (*part, start, stop)):
            out.setdefault(r["channel"], []).append(r)
    return out
