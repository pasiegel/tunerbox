"""The channel list the set-top box shows (lineup + extras) and stream lookup."""
import json
import threading
import time

from . import config, db

_lock = threading.Lock()
_cache = {"t": 0, "all": None}


def invalidate():
    with _lock:
        _cache["t"] = 0


def _build():
    streams = {s["key"]: s for s in db.rows(
        """SELECT s.key, s.name, s.tvg_id, s.logo, s.norm, s.status, s.grp FROM streams s
           JOIN sources src ON src.id=s.source_id WHERE src.enabled=1""")}
    epg_chans = {r["channel"] for r in db.rows("SELECT DISTINCT channel FROM programs")}
    by_name = {}
    for r in db.rows("SELECT norm, channel FROM epg_names"):
        if r["channel"] in epg_chans:
            by_name.setdefault(r["norm"], r["channel"])

    def epg_for_stream(s):
        if not s:
            return None
        if s["tvg_id"] and f"x:{s['tvg_id']}" in epg_chans:
            return f"x:{s['tvg_id']}"
        return by_name.get(s["norm"])

    out = []
    maps = {m["number"]: m for m in db.rows("SELECT * FROM mappings")}
    for ch in db.rows("SELECT * FROM lineup ORDER BY number"):
        m = maps.get(ch["number"])
        keys = []
        if m and m["stream_key"]:
            keys = [k for k in [m["stream_key"]] + json.loads(m["alt_keys"] or "[]") if k in streams]
        s = streams.get(keys[0]) if keys else None
        gn = f"gn:{ch['number']}"
        out.append({
            "number": ch["number"], "name": ch["name"],
            "label": ch["callsign"] or _label(ch["name"]),
            "logo": ch["logo"] or (s["logo"] if s else ""),
            "kind": "lineup", "streams": keys, "mapped": bool(keys),
            "mode": m["mode"] if m else "",
            "epg": gn if gn in epg_chans else epg_for_stream(s),
        })
    extras = db.rows("SELECT * FROM extras WHERE active=1 AND enabled=1 ORDER BY number") if config.get("extras_enabled", True) else []
    for e in extras:
        s = streams.get(e["stream_key"])
        if not s:
            continue
        out.append({
            "number": e["number"], "name": s["name"], "label": _label(s["name"]),
            "logo": s["logo"], "kind": "extra", "group": s["grp"], "streams": [s["key"]],
            "mapped": True, "mode": "extra", "epg": epg_for_stream(s),
        })
    taken = {c["number"] for c in out}
    for m in db.rows("SELECT * FROM media_channels WHERE enabled=1 ORDER BY number"):
        if m["number"] in taken:
            continue
        out.append({
            "number": m["number"], "name": m["name"], "label": _label(m["name"]), "logo": m["logo"] or "",
            "kind": "media", "group": "Local channels", "streams": [f"media:{m['id']}"],
            "mapped": m["item_count"] > 0, "mode": "media", "epg": f"m:{m['id']}",
        })
    out.sort(key=lambda c: c["number"])
    return out


def _label(name):
    words = (name or "").replace("-", " ").split()
    lab = " ".join(words)[:10]
    return lab.upper() if len(lab) <= 5 else lab


def channels(include_unmapped=None):
    with _lock:
        if _cache["all"] is None or time.time() - _cache["t"] > 600:   # changes call invalidate()
            _cache["all"] = _build()
            _cache["t"] = time.time()
        allc = _cache["all"]
    if include_unmapped is None:
        include_unmapped = not config.get("hide_unmapped", True)
    return allc if include_unmapped else [c for c in allc if c["mapped"]]


def channel(number):
    allc = channels(True)
    with _lock:
        if _cache.get("by_num_src") is not allc:
            _cache["by_num"] = {c["number"]: c for c in allc}
            _cache["by_num_src"] = allc
        return _cache["by_num"].get(number)


def stream_rows(keys):
    if not keys:
        return []
    rows = {k: {"key": k, "url": k, "source_id": 0, "name": k, "video_mode": "", "status": "ok"}
            for k in keys if k.startswith("media:")}
    real = [k for k in keys if k not in rows]
    if real:
        q = ",".join("?" * len(real))
        rows.update({r["key"]: r for r in db.rows(
            f"""SELECT s.*, COALESCE(src.video_mode, '') AS video_mode, src.type AS src_type FROM streams s
                LEFT JOIN sources src ON src.id=s.source_id WHERE s.key IN ({q})""", tuple(real))})
    return [rows[k] for k in keys if k in rows]
