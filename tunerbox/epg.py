"""Guide data: Gracenote for the local lineup, XMLTV for everything else."""
import gzip
import hashlib

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

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
    progs, channels_done = {}, False
    with _client(90) as c:
        for i, t in enumerate(steps):
            params = _grid_params(g, t, 6)
            try:
                r = c.get(GN_GRID, params=params,
                          headers={"Referer": "https://tvlistings.gracenote.com/grid-affiliates.html?aid=orbebb"})
                r.raise_for_status()
                data = json.loads(r.content.decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("Gracenote block %d failed: %s", i, e)
                continue
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
        _store(progs.values(), t0)
    log.info("Gracenote: %d programs", len(progs))
    return len(progs)


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
    with db.conn() as c:
        c.execute("DELETE FROM epg_names")
    for n, url in enumerate(urls):
        try:
            got = _parse_xmltv(url, ids, names, claimed, lo, hi)
        except Exception as e:  # noqa: BLE001
            log.warning("XMLTV %s failed: %s", url, e)
            got = {}
        if got:
            claimed.update(got.keys())
            rows = [p for lst in got.values() for p in lst]
            _store(rows, lo, channels=list(got.keys()))
            total += len(rows)
        if progress:
            progress(n + 1, len(urls))
        log.info("XMLTV %s: %d channels", url, len(got))
    return total


def _open_stream(url):
    """Download to data/cache (streamed, so 100MB+ guides don't sit in memory)."""
    if re.match(r"^https?://", url, re.I):
        cache = config.DATA / "cache"
        cache.mkdir(exist_ok=True)
        path = cache / ("epg_" + hashlib.sha1(url.encode()).hexdigest()[:10])
        with _client(600) as c, c.stream("GET", url) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
    else:
        path = url
    with open(path, "rb") as f:
        magic = f.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def _parse_xmltv(url, ids, names, claimed, lo, hi):
    f = _open_stream(url)
    try:
        return _parse_open(f, ids, names, claimed, lo, hi)
    finally:
        f.close()


def _parse_open(f, ids, names, claimed, lo, hi):
    keep = {i for i in ids if i not in claimed}
    name_hits = []
    out = {}
    for _ev, el in ET.iterparse(f, events=("end",)):
        tag = el.tag
        if tag == "channel":
            cid = el.get("id") or ""
            if cid and cid not in claimed:
                for dn in el.findall("display-name"):
                    nn = norm(dn.text or "")
                    if nn in names:
                        keep.add(cid)
                        name_hits.append((nn, f"x:{cid}"))
            el.clear()
        elif tag == "programme":
            cid = el.get("channel") or ""
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
    return out


def _txt(el, tag):
    e = el.find(tag)
    return (e.text or "").strip() if e is not None else ""


# ------------------------------------------------------------------ storage / queries
def _store(rows, from_ts, channels=None):
    rows = list(rows)
    chans = channels or sorted({r[0] for r in rows})
    with db.conn() as c:
        c.execute("DELETE FROM programs WHERE stop < ?", (int(time.time()) - 6 * 3600,))
        for ch in chans:
            c.execute("DELETE FROM programs WHERE channel=? AND start>=?", (ch, from_ts))
        c.executemany("INSERT OR REPLACE INTO programs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def refresh_all(progress=None):
    n1 = fetch_gracenote(lambda a, b: progress and progress("Local guide (Gracenote)", a, b))
    n2 = fetch_xmltv(lambda a, b: progress and progress("Online guides (XMLTV)", a, b))
    db.set_meta("epg_updated", int(time.time()))
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
