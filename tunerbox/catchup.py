"""Catch-up TV: watch a show that already aired (or start the one on now from the beginning) from the
provider's archive.

Xtream providers mark channels with an archive (get_live_streams: tv_archive=1, tv_archive_duration =
days) and play any stretch of it from /timeshift/<user>/<pass>/<minutes>/<YYYY-MM-DD:HH-MM>/<id>.ts, the
start written in the server's own clock. M3U playlists say so per channel with catchup / catchup-days /
catchup-source (the Kodi and TiviMate attributes).

A show from the archive plays in the On Demand player, as the item "cu-<channel>-<start>": it has a
length, pause and skipping, and skipping asks for the archive from the new time.
"""
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import db, guide, sources, streaming

MAX_DAYS = 14               # archives longer than this are cut to it (and the guide keeps no more history)
START_EARLY = 0             # seconds before the listed start to begin (0: providers already pad their archive)
_ITEM = re.compile(r"^cu-(\d+)-(\d+)$")


# ------------------------------------------------------------------ what a playlist says
def xtream_days(stream):
    """Days of archive an Xtream get_live_streams entry has (0 = none)."""
    if str(stream.get("tv_archive") or "0").strip() not in ("1", "true", "True"):
        return 0
    return _days(stream.get("tv_archive_duration")) or 1


def m3u_info(attrs, header=None):
    """(days, rule) from an M3U entry's attributes, with the #EXTM3U line's as defaults. rule is the JSON
    kept with the stream: {"type": ..., "source": ...}, or "" with no catch-up."""
    header = header or {}

    def get(*names):
        for d in (attrs, header):
            for n in names:
                v = (d.get(n) or "").strip()
                if v:
                    return v
        return ""

    kind = get("catchup", "catchup-type").lower()
    source = get("catchup-source")
    days = _days(get("catchup-days", "timeshift", "tvg-rec"))
    if not (kind or source or days) or kind in ("0", "no", "none", "off", "disabled"):
        return 0, ""
    if not days:
        days = 1 if (kind or source) else 0
    if not days:
        return 0, ""
    if not kind:
        kind = "default" if source else "shift"
    return days, json.dumps({"type": kind, "source": source})


def _days(v):
    try:
        return max(0, min(MAX_DAYS, int(float(str(v).strip() or 0))))
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------ the address for a stretch of archive
def server_offset(source_id):
    """Seconds the Xtream server's clock is ahead of UTC (it writes catch-up starts in its own time)."""
    try:
        return int(db.get_meta(f"xtream_tz_{source_id}", 0) or 0)
    except (TypeError, ValueError):
        return 0


def xtream_url(src, stream_id, start, minutes, offset=None):
    off = server_offset(src["id"]) if offset is None else offset
    when = datetime.fromtimestamp(start + off, timezone.utc).strftime("%Y-%m-%d:%H-%M")
    return (f"{sources.xtream_base(src)}/timeshift/{src['username']}/{src['password']}/"
            f"{max(1, int(minutes))}/{when}/{stream_id}.ts")


_XC_LIVE = re.compile(r"^(https?://[^/]+)/(?:live/)?([^/]+)/([^/]+)/(\d+)(?:\.\w+)?$", re.I)


def _fill(template, start, stop, now):
    """Kodi / TiviMate placeholders: {utc} {start} {timestamp} ${start}, {utcend} {end}, {lutc} {now},
    {duration} {duration:60} (seconds, or divided), {offset} (seconds ago), {Y} {m} {d} {H} {M} {S}
    (UTC), {utc:Y-m-d} style formats."""
    vals = {"utc": start, "start": start, "timestamp": start, "utcend": stop, "end": stop,
            "lutc": now, "now": now, "duration": stop - start, "offset": max(0, now - start)}

    def date(t, fmt):
        d = datetime.fromtimestamp(t, timezone.utc)
        return re.sub(r"[YmdHMS]", lambda m: d.strftime("%" + m.group(0)), fmt)

    def one(m):
        name, arg = m.group(1).lower(), m.group(2)
        if name in ("y", "m", "d", "h", "s") or m.group(1) == "M":
            return date(start, m.group(1))
        if name not in vals:
            return m.group(0)
        v = vals[name]
        if arg is None:
            return str(v)
        if arg.isdigit():
            return str(v // max(1, int(arg)))
        return date(v, arg)
    return re.sub(r"\$?\{(\w+)(?::([^}]+))?\}", one, template)


def m3u_url(url, rule, start, stop, now=None):
    """The archive address for an M3U channel, or "" when the rule can't make one."""
    now = int(now or time.time())
    try:
        r = json.loads(rule) if isinstance(rule, str) else dict(rule or {})
    except ValueError:
        return ""
    kind, source = (r.get("type") or "").lower(), r.get("source") or ""
    base, bar, headers = url.partition("|")         # "url|User-Agent=..." keeps its headers
    tail = bar + headers
    if kind in ("append",) and source:
        return _fill(base + source, start, stop, now) + tail
    if kind in ("default", "") and source:
        if not re.match(r"^https?://", source, re.I):
            return _fill(base + source, start, stop, now) + tail
        return _fill(source, start, stop, now) + tail
    if kind in ("flussonic", "flussonic-hls", "flussonic-ts", "fs"):
        p = urlsplit(base)
        path = p.path
        dur = stop - start
        if path.endswith(("/index.m3u8", "/video.m3u8", "/mono.m3u8")):
            folder, name = path.rsplit("/", 1)
            name = "archive" if name == "index.m3u8" else name.split(".")[0]
            new = f"{folder}/{name}-{start}-{dur}.m3u8"
        elif path.endswith("/mpegts"):
            new = path[:-len("mpegts")] + f"timeshift_abs-{start}.ts"
        else:
            return ""
        return p._replace(path=new).geturl() + tail
    if kind in ("xc", "xtream"):
        m = _XC_LIVE.match(base)
        if not m:
            return ""
        host, user, pw, sid = m.groups()
        when = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d:%H-%M")
        return f"{host}/timeshift/{user}/{pw}/{max(1, (stop - start + 59) // 60)}/{when}/{sid}.ts" + tail
    # shift (and "default" without a source): the live address with utc= / lutc=
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}utc={start}&lutc={now}" + tail


# ------------------------------------------------------------------ channels and shows
def stream_for(ch):
    """The first of a channel's streams with an archive, as a stream row (with archive_days), or None."""
    if not ch:
        return None
    for r in guide.stream_rows(ch.get("streams") or []):
        if (r.get("archive_days") or 0) > 0:
            return r
    return None


def window(days, now=None):
    """Earliest start that is still in an archive of `days` days."""
    return int(now or time.time()) - int(days) * 86400


def item_id(number, start):
    return f"cu-{int(number)}-{int(start)}"


def is_item(item_id):
    return bool(_ITEM.match(item_id or ""))


def item(item_id, now=None):
    """The On Demand player's view of a show from the archive, with the stream row behind it, or None
    when it isn't (or is no longer) in the archive."""
    m = _ITEM.match(item_id or "")
    if not m:
        return None
    number, start = int(m.group(1)), int(m.group(2))
    now = int(now or time.time())
    ch = guide.channel(number)
    row = stream_for(ch)
    if not row or not ch.get("epg") or start < window(row["archive_days"], now) or start >= now:
        return None
    p = db.one("SELECT * FROM programs WHERE channel=? AND start=?", (ch["epg"], start))
    if not p:
        return None
    sub = " · ".join(x for x in (p["episode"], p["subtitle"]) if x)
    return {
        "id": item_id, "catchup": True, "number": number, "channel": ch["name"],
        "title": p["title"], "sub": sub or f"{ch['number']} {ch['name']}",
        "start": p["start"], "stop": p["stop"], "duration": max(60, p["stop"] - p["start"]),
        "airing": p["stop"] > now, "position": 0, "next": None, "image": p["image"] or "",
        "source_id": row["source_id"], "video_mode": row.get("video_mode") or "", "row": row,
    }


def url_at(it, ss=0.0, now=None):
    """(address, seconds still to skip inside it) to play a show from `ss` seconds in. The archive is
    asked for from the whole minute; ffmpeg skips the rest."""
    now = int(now or time.time())
    at = it["start"] + max(0, int(ss)) - START_EARLY
    at -= at % 60
    rest = max(0.0, it["start"] + float(ss) - START_EARLY - at)
    stop = max(it["stop"], at + 60)
    row = it["row"]
    if row.get("src_type") == "xtream":
        src = db.one("SELECT * FROM sources WHERE id=?", (row["source_id"],))
        sid = row["key"].split(":x", 1)[1]
        minutes = (stop - at + 59) // 60 + 5           # a little past the end, for a late finish
        return xtream_url(dict(src), sid, at, minutes), rest
    return m3u_url(row["url"], row.get("catchup") or "", at, stop, now), rest


def pace(rest=0.0):
    """ffmpeg reading options for a show from the archive: the first seconds (and any part skipped inside
    the minute) as fast as the provider sends them, then at playing speed. Older ffmpeg: -re."""
    return streaming.pace(int(rest) + 10)


def keep_history():
    """{guide channel key: days} for channels with catch-up, so the guide keeps their past shows."""
    days = {r["key"]: r["archive_days"] for r in db.rows("SELECT key, archive_days FROM streams WHERE archive_days > 0")}
    out = {}
    if not days:
        return out
    for c in guide.channels(True):
        d = next((days[k] for k in c.get("streams") or [] if k in days), 0)
        if d and c.get("epg"):
            out[c["epg"]] = max(out.get(c["epg"], 0), min(MAX_DAYS, d))
    return out


def prune(c, now=None):
    """Delete guide listings that ended long ago: after 6 hours, or after the archive for channels
    with catch-up. Runs inside epg's storing transaction (connection c)."""
    now = int(now or time.time())
    try:
        keep = keep_history()
    except Exception:  # noqa: BLE001 - a half-built database: keep the old rule
        keep = {}
    if not keep:
        c.execute("DELETE FROM programs WHERE stop < ?", (now - 6 * 3600,))
        return
    c.execute("CREATE TEMP TABLE IF NOT EXISTS _cu_keep(channel TEXT PRIMARY KEY, days INTEGER)")
    c.execute("DELETE FROM _cu_keep")
    c.executemany("INSERT INTO _cu_keep VALUES (?,?)", list(keep.items()))
    c.execute("""DELETE FROM programs WHERE stop < ? AND channel NOT IN (SELECT channel FROM _cu_keep)""",
              (now - 6 * 3600,))
    c.execute("""DELETE FROM programs WHERE channel IN (SELECT channel FROM _cu_keep)
                 AND stop < ? - 86400 * (SELECT days FROM _cu_keep k WHERE k.channel=programs.channel)""", (now,))
    c.execute("DELETE FROM _cu_keep")

