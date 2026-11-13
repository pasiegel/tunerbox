"""Playlist sources: M3U (url or file) and Xtream Codes."""
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from . import config, db
from .text import norm

log = logging.getLogger("tunerbox.sources")
ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def client(timeout=60):
    return httpx.Client(timeout=timeout, follow_redirects=True,
                        headers={"User-Agent": config.get("user_agent")})


# ---------------------------------------------------------------- M3U
def parse_m3u(text: str):
    """Returns (header_attrs, [entry dicts])."""
    header, entries, cur, opts = {}, [], None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            header = dict(ATTR.findall(line))
        elif line.startswith("#EXTINF"):
            attrs = dict(ATTR.findall(line))
            # display name = text after the last comma that is outside quotes
            name, inq = "", False
            for i, ch in enumerate(line):
                if ch == '"':
                    inq = not inq
                elif ch == "," and not inq:
                    name = line[i + 1:]
            cur = {"name": name.strip() or attrs.get("tvg-name", ""), "attrs": attrs}
            opts = {}
        elif line.startswith("#EXTVLCOPT:"):
            k, _, v = line[11:].partition("=")
            opts[k.strip().lower()] = v.strip()
        elif line.startswith("#EXTGRP:") and cur is not None:
            cur["attrs"].setdefault("group-title", line[8:].strip())
        elif not line.startswith("#"):
            if cur is None:
                cur = {"name": line.rsplit("/", 1)[-1], "attrs": {}}
            url = line
            ua = opts.get("http-user-agent")
            ref = opts.get("http-referrer") or opts.get("http-referer")
            extra = []
            if ua:
                extra.append(f"User-Agent={ua}")
            if ref:
                extra.append(f"Referer={ref}")
            if extra and "|" not in url:
                url += "|" + "&".join(extra)
            cur["url"] = url
            entries.append(cur)
            cur, opts = None, {}
    return header, entries


def xtream_from_get_url(url: str):
    """http://host:port/get.php?username=u&password=p&type=m3u_plus -> (server, u, p)"""
    u = urlparse(url)
    if not u.path.endswith("get.php"):
        return None
    q = parse_qs(u.query)
    if "username" in q and "password" in q:
        return f"{u.scheme}://{u.netloc}", q["username"][0], q["password"][0]
    return None


def fetch_m3u(src):
    loc = src["url"].strip()
    if re.match(r"^https?://", loc, re.I):
        with client(120) as c:
            r = c.get(loc)
            r.raise_for_status()
            text = r.content.decode("utf-8", "replace")
    else:
        text = Path(loc).read_text(encoding="utf-8", errors="replace")
    header, entries = parse_m3u(text)
    out = []
    for e in entries:
        a = e["attrs"]
        out.append({
            "name": e["name"], "url": e["url"], "tvg_id": a.get("tvg-id", ""),
            "logo": a.get("tvg-logo", ""), "grp": a.get("group-title", ""),
        })
    epg = (header.get("x-tvg-url") or header.get("url-tvg") or "").split(",")[0].strip()
    if epg and not re.match(r"^https?://", epg, re.I) and not Path(epg).is_absolute():
        # relative guide link: resolve next to the playlist
        if re.match(r"^https?://", loc, re.I):
            from urllib.parse import urljoin
            epg = urljoin(loc, epg)
        else:
            epg = str(Path(loc).parent / epg)
    return out, epg


# ---------------------------------------------------------------- Xtream
def xtream_base(src):
    return src["url"].rstrip("/")


def xtream_api(src, action=None, **params):
    p = {"username": src["username"], "password": src["password"]}
    if action:
        p["action"] = action
    p.update(params)
    with client(120) as c:
        r = c.get(f"{xtream_base(src)}/player_api.php", params=p)
        r.raise_for_status()
        return r.json()


def fetch_xtream(src):
    info = xtream_api(src)
    if isinstance(info, dict) and info.get("user_info", {}).get("auth") == 0:
        raise RuntimeError("Xtream login rejected")
    try:        # how many streams the account may play at once (advertised to Plex as tuners)
        db.set_meta(f"xtream_max_{src['id']}", int((info.get("user_info") or {}).get("max_connections") or 0))
    except (TypeError, ValueError, AttributeError):
        pass
    cats = {str(c["category_id"]): c.get("category_name", "") for c in xtream_api(src, "get_live_categories") or []}
    streams = xtream_api(src, "get_live_streams") or []
    ext = src.get("xtream_format") or "ts"
    base = xtream_base(src)
    out = []
    for s in streams:
        sid = s.get("stream_id")
        if sid is None:
            continue
        out.append({
            "xid": sid,
            "name": s.get("name") or str(sid),
            "url": f"{base}/live/{src['username']}/{src['password']}/{sid}.{ext}",
            "tvg_id": s.get("epg_channel_id") or "",
            "logo": s.get("stream_icon") or "",
            "grp": cats.get(str(s.get("category_id")), ""),
        })
    epg = f"{base}/xmltv.php?username={src['username']}&password={src['password']}"
    return out, epg


# ---------------------------------------------------------------- HDHomeRun
def tuner_count():
    """Tuners TunerBox's HDHomeRun emulation offers Plex: the setting, or - when every enabled
    playlist is an Xtream account that states its connection limit - the sum of those limits."""
    n = int(config.get("tuner_count") or 0)
    if n > 0:
        return n
    srcs = db.rows("SELECT id, type FROM sources WHERE enabled=1")
    limits = [int(db.get_meta(f"xtream_max_{s['id']}", 0) or 0) for s in srcs if s["type"] == "xtream"]
    if srcs and len(limits) == len(srcs) and all(limits):
        return max(1, sum(limits))
    return 4


def hdhr_base(addr):
    a = (addr or "").strip().rstrip("/")
    if not a:
        raise ValueError("Enter the tuner's address")
    if not re.match(r"^https?://", a, re.I):
        a = "http://" + a
    return a


def hdhr_info(addr, timeout=6):
    with client(timeout) as c:
        r = c.get(hdhr_base(addr) + "/discover.json")
        r.raise_for_status()
        d = r.json()
    if d.get("DeviceAuth") == "tunerbox":
        raise RuntimeError("That address is TunerBox itself, not an HDHomeRun tuner")
    return d


def fetch_hdhomerun(src):
    d = hdhr_info(src["url"], 15)
    lineup_url = d.get("LineupURL") or hdhr_base(src["url"]) + "/lineup.json"
    with client(30) as c:
        r = c.get(lineup_url)
        r.raise_for_status()
        rows = r.json()
    # the station's callsign, from whichever subchannel names it (8.1 'WOOD-HD' -> 8.2 is WOOD-DT2)
    from .text import find_callsigns
    calls = {}
    for it in rows:
        major = str(it.get("GuideNumber", "")).split(".")[0]
        found = sorted(find_callsigns(it.get("GuideName", "")))
        if found and major not in calls:
            calls[major] = found[0][0]
    from .epg import ota_callsigns
    ota = ota_callsigns()          # exact callsign per channel number, from the local antenna lineup
    out = []
    for it in rows:
        if it.get("DRM") or not it.get("URL"):
            continue
        num = str(it.get("GuideNumber", ""))
        major, _, minor = num.partition(".")
        call = calls.get(major)
        tvg = ota.get(num) or ((call if not minor or minor == "1" else f"{call}DT{minor}") if call else "")
        tvg = f"ota:{tvg}" if tvg else ""
        out.append({"name": it.get("GuideName") or num, "url": it["URL"], "tvg_id": tvg, "logo": "",
                    "grp": f"Antenna ({d.get('FriendlyName') or 'HDHomeRun'})", "xid": f"hd{num}"})
    return out, ""


def discover_hdhomerun(timeout=2.0):
    """HDHomeRun tuners on this network: the tuners' own UDP discovery, then SiliconDust's lookup."""
    import socket
    import struct
    import zlib
    found = {}
    payload = bytes([1, 4]) + struct.pack(">I", 0xFFFFFFFF) + bytes([2, 4]) + struct.pack(">I", 0xFFFFFFFF)
    pkt = struct.pack(">HH", 2, len(payload)) + payload
    pkt += struct.pack("<I", zlib.crc32(pkt) & 0xFFFFFFFF)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        s.bind(("", 0))
        s.sendto(pkt, ("255.255.255.255", 65001))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, (ip, _) = s.recvfrom(2048)
            except socket.timeout:
                continue
            if len(data) > 4 and struct.unpack(">H", data[:2])[0] == 3:
                found.setdefault(ip, {"ip": ip})
        s.close()
    except OSError as e:
        log.info("HDHomeRun broadcast discovery failed: %s", e)
    try:
        with client(4) as c:
            for d in c.get("https://api.hdhomerun.com/discover").json() or []:
                ip = d.get("LocalIP") or ""
                if ip and d.get("DeviceID"):
                    found.setdefault(ip.split(":")[0], {"ip": ip.split(":")[0]})
    except Exception as e:  # noqa: BLE001
        log.info("HDHomeRun cloud discovery failed: %s", e)
    out = []
    for ip in found:
        try:
            d = hdhr_info(ip, 3)
        except Exception:  # noqa: BLE001
            continue
        out.append({"ip": ip, "name": d.get("FriendlyName", "HDHomeRun"), "model": d.get("ModelNumber", ""),
                    "id": d.get("DeviceID", ""), "tuners": d.get("TunerCount", 0)})
    return out


# ---------------------------------------------------------------- refresh
def refresh_source(src):
    try:
        if src["type"] == "xtream":
            items, epg = fetch_xtream(src)
        elif src["type"] == "hdhomerun":
            items, epg = fetch_hdhomerun(src)
        else:
            items, epg = fetch_m3u(src)
            from . import vod
            items = vod.take_m3u(src, items)        # video files are On Demand titles, not channels
    except Exception as e:  # noqa: BLE001
        log.exception("Source %s failed", src["name"])
        db.run("UPDATE sources SET status=? WHERE id=?", (f"error: {e}", src["id"]))
        return 0

    rx = exclude_regex(src)
    hidden = hidden_groups(src)
    now = int(time.time())
    all_groups = {}
    for it in items:
        all_groups[it["grp"] or ""] = all_groups.get(it["grp"] or "", 0) + 1
    kept = [it for it in items if not ((it["grp"] or "") in hidden or (rx and rx.search(it["grp"] or "")))]
    keys = stream_keys(src, kept)
    seen = set(keys)
    with db.conn() as c:
        c.execute("DELETE FROM source_groups WHERE source_id=?", (src["id"],))
        c.executemany("INSERT INTO source_groups(source_id, grp, count) VALUES (?,?,?)",
                      [(src["id"], g, n) for g, n in all_groups.items()])
        c.executemany("""INSERT INTO streams(key, source_id, name, url, tvg_id, logo, grp, norm, seen_at)
                         VALUES (?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(key) DO UPDATE SET name=excluded.name, url=excluded.url,
                           tvg_id=excluded.tvg_id, logo=excluded.logo, grp=excluded.grp,
                           norm=excluded.norm, seen_at=excluded.seen_at""",
                      [(key, src["id"], it["name"], it["url"], it["tvg_id"], it["logo"], it["grp"],
                        norm(it["name"]), now) for key, it in zip(keys, kept)])
        c.execute("DELETE FROM streams WHERE source_id=? AND seen_at<?", (src["id"], now))
        c.execute("UPDATE sources SET status=?, epg_url=?, updated_at=? WHERE id=?",
                  (f"ok: {len(seen)} channels", epg or "", now, src["id"]))
    log.info("Source %s: %d channels", src["name"], len(seen))
    return len(seen)


def stream_keys(src, items):
    """A stable key per playlist entry, so its extra channel number (and any recording on it) survives
    refreshes. Xtream/HDHomeRun entries carry their own id. Plain M3U entries are keyed by name, tvg-id and
    group; when that key is new but the entry's URL belonged to a stream that is now gone, the old key is
    kept. That way a PPV slot renamed for tonight's event ("PPV 03" -> "PPV 03: UFC 310") keeps its number."""
    keys, counts = [], {}
    for it in items:
        if "xid" in it:
            keys.append(f"{src['id']}:x{it['xid']}")
            continue
        base = hashlib.sha1(f"{it['name']}|{it['tvg_id']}|{it['grp']}".encode()).hexdigest()[:12]
        counts[base] = counts.get(base, 0) + 1
        keys.append(f"{src['id']}:{base}" + (f"-{counts[base]}" if counts[base] > 1 else ""))
    if all("xid" in it for it in items):
        return keys
    old_by_url = {}
    for r in db.rows("SELECT key, url FROM streams WHERE source_id=? ORDER BY rowid", (src["id"],)):
        old_by_url.setdefault(r["url"], []).append(r["key"])
    old = {k for ks in old_by_url.values() for k in ks}
    taken = {k for k in keys if k in old}          # entries whose key is unchanged keep it
    for i, it in enumerate(items):
        if "xid" in it or keys[i] in old:
            continue
        free = [k for k in old_by_url.get(it["url"], []) if k not in taken]
        if free:
            keys[i] = free[0]
            taken.add(free[0])
    return keys


def exclude_regex(src):
    excl = (src.get("exclude_groups") or "").strip()
    try:
        return re.compile(excl, re.I) if excl else None
    except re.error:
        log.warning("Bad skip pattern for %s: %s", src.get("name"), excl)
        return None


def hidden_groups(src):
    try:
        return set(json.loads(src.get("hidden_groups") or "[]"))
    except ValueError:
        return set()


def groups(source_id):
    """Every group of a playlist with its channel count and whether it's left out."""
    src = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        return None
    rx, hidden = exclude_regex(src), hidden_groups(src)
    rows = db.rows("SELECT grp, count FROM source_groups WHERE source_id=?", (source_id,))
    partial = False
    if not rows:                # scanned before 1.2: only the groups that were kept are known
        partial = bool(rx or hidden)
        rows = db.rows("SELECT grp, count(*) AS count FROM streams WHERE source_id=? GROUP BY grp", (source_id,))
        known = {r["grp"] for r in rows}
        rows += [{"grp": g, "count": 0} for g in hidden if g not in known]
    out = [{"name": r["grp"], "count": r["count"], "hidden": r["grp"] in hidden,
            "pattern": bool(rx and rx.search(r["grp"] or ""))} for r in rows]
    out.sort(key=lambda g: g["name"].lower())
    return {"source": {"id": src["id"], "name": src["name"]}, "groups": out, "partial": partial}


def set_hidden_groups(source_id, names):
    """Save the hidden list. Newly hidden groups are dropped right away; groups shown again
    need the playlist re-read, so the caller rescans."""
    src = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        return None
    old, new = hidden_groups(src), set(names)
    db.run("UPDATE sources SET hidden_groups=? WHERE id=?", (json.dumps(sorted(new)), source_id))
    dropped = new - old
    if dropped:
        with db.conn() as c:
            q = ",".join("?" * len(dropped))
            c.execute(f"DELETE FROM streams WHERE source_id=? AND grp IN ({q})", (source_id, *dropped))
    return {"hidden": len(new), "newly_hidden": len(dropped), "shown_again": len(old - new)}


def add_source(name, type_, url, username="", password="", exclude_groups="", xtream_format="ts", video_mode=None):
    if type_ == "m3u":
        xt = xtream_from_get_url(url)
        if xt:  # an Xtream get.php link: use the API instead (better groups + EPG ids)
            url, username, password = xt
            type_ = "xtream"
    if type_ == "hdhomerun":
        d = hdhr_info(url)          # fail now, with a clear message, if it isn't a tuner
        url = hdhr_base(url)
        name = name or d.get("FriendlyName") or "HDHomeRun"
        # antenna TV is MPEG-2, which browsers can't play: re-encode to H.264 unless told otherwise
        video_mode = video_mode or "h264"
    return db.run("""INSERT INTO sources(name, type, url, username, password, exclude_groups, xtream_format, video_mode)
                     VALUES (?,?,?,?,?,?,?,?)""",
                  (name or url, type_, url, username, password, exclude_groups, xtream_format, video_mode or ""))


# ---------------------------------------------------------------- backup / move to another PC
EXPORT_KIND = "tunerbox-playlists"
_TYPES = ("m3u", "xtream", "hdhomerun")


def export_sources():
    """Every playlist with its settings (logins included), for Setup > Playlists > Export."""
    from . import __version__
    out = []
    for s in db.rows("SELECT * FROM sources ORDER BY id"):
        out.append({"name": s["name"], "type": s["type"], "url": s["url"], "username": s["username"] or "",
                    "password": s["password"] or "", "enabled": bool(s["enabled"]),
                    "exclude_groups": s["exclude_groups"] or "", "hidden_groups": sorted(hidden_groups(s)),
                    "xtream_format": s["xtream_format"] or "ts", "video_mode": s.get("video_mode") or "",
                    "on_demand": {"movies": bool(s.get("vod_movies")), "series": bool(s.get("vod_series")),
                                  "hidden": json.loads(s.get("vod_hidden") or "[]")}})
    return {"kind": EXPORT_KIND, "version": 1, "app_version": __version__,
            "exported": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "playlists": out}


def _same(a, b):
    """The same playlist: same kind, address and login (a renamed one still counts)."""
    def key(s):
        return (s["type"], (s["url"] or "").strip().rstrip("/").lower(), (s.get("username") or "").strip())
    return key(a) == key(b)


def import_sources(data):
    """Add the playlists in an export file. One that's already here (same address and login) has
    its settings updated instead of being added twice. Returns counts plus any entries skipped."""
    if not isinstance(data, dict) or data.get("kind") != EXPORT_KIND or not isinstance(data.get("playlists"), list):
        raise ValueError("That isn't a TunerBox playlists file")
    have = db.rows("SELECT * FROM sources")
    added = updated = 0
    skipped = []
    for i, p in enumerate(data["playlists"], 1):
        if not isinstance(p, dict):
            skipped.append(f"entry {i}: not a playlist")
            continue
        typ, url = str(p.get("type") or "m3u"), str(p.get("url") or "").strip()
        name = str(p.get("name") or "").strip() or url
        if typ not in _TYPES or not url:
            skipped.append(f"{name or f'entry {i}'}: missing address or unknown type")
            continue
        hidden = p.get("hidden_groups") or []
        if not isinstance(hidden, list):
            hidden = []
        row = {"name": name, "type": typ, "url": url, "username": str(p.get("username") or "").strip(),
               "password": str(p.get("password") or ""), "enabled": 1 if p.get("enabled", True) else 0,
               "exclude_groups": str(p.get("exclude_groups") or ""),
               "hidden_groups": json.dumps(sorted({str(g) for g in hidden})),
               "xtream_format": "m3u8" if p.get("xtream_format") == "m3u8" else "ts",
               "video_mode": p.get("video_mode") if p.get("video_mode") in ("copy", "h264", "roku720") else ""}
        od = p.get("on_demand")
        if isinstance(od, dict):        # files from before On Demand don't have it: leave those settings alone
            hidden_od = od.get("hidden") if isinstance(od.get("hidden"), list) else []
            row.update(vod_movies=1 if od.get("movies") else 0, vod_series=1 if od.get("series") else 0,
                       vod_hidden=json.dumps(sorted({(str(h[0]), str(h[1])) for h in hidden_od
                                                     if isinstance(h, list) and len(h) == 2 and h[0] in ("movie", "series")})))
        cur = next((s for s in have if _same(s, row)), None)
        if cur:
            cols = [k for k in row if k not in ("type", "url", "username")]
            db.run(f"UPDATE sources SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?",
                   (*[row[k] for k in cols], cur["id"]))
            updated += 1
        else:
            cols = list(row)
            db.run(f"INSERT INTO sources({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                   tuple(row[k] for k in cols))
            have.append(row)
            added += 1
    return {"added": added, "updated": updated, "skipped": skipped}


def enabled_sources():
    return db.rows("SELECT * FROM sources WHERE enabled=1 ORDER BY id")
