"""The Home screen: what's on your favorite and recent channels, what you're part-way through, games
on now, new recordings and what's coming up. One request, so the screen draws in one go."""
import logging
import time

from . import config, db, epg, guide, reminders, sports, vod

log = logging.getLogger("tunerbox.home")
ROW = 20                    # most tiles in a row
RESUME_MIN = 30             # seconds into a recording before it counts as started (as On Demand)
PROG_KEYS = ("title", "subtitle", "episode", "descr", "category", "start", "stop", "image", "is_live", "is_new", "rating")


def _on_now(chans, now):
    """Channel tiles with the show that's on now."""
    progs = epg.programs({c["epg"] for c in chans if c["epg"]}, now, now + 1)
    out = []
    for c in chans:
        p = next((x for x in progs.get(c["epg"], []) if x["start"] <= now < x["stop"]), None) if c["epg"] else None
        out.append({"number": c["number"], "name": c["name"], "label": c.get("label") or c["name"],
                    "logo": c.get("logo") or "", "now": {k: p[k] for k in PROG_KEYS} if p else None})
    return out


def _channels(numbers):
    seen, out = set(), []
    for n in numbers:
        c = guide.channel(n)
        if c and c["mapped"] and n not in seen:
            seen.add(n)
            out.append(c)
    return out


def _recording(r):
    return {k: r[k] for k in ("id", "number", "channel_name", "title", "subtitle", "episode", "descr", "start", "stop",
                              "status", "position", "duration", "watched", "file", "error")} | {"art": bool(r.get("image"))}


def _continue(now):
    """On Demand titles and recordings you're part-way through, the latest first."""
    items = [{"type": "vod", **t} for t in (vod.continue_watching() if vod.enabled() else [])]
    for r in db.rows("""SELECT * FROM recordings WHERE status IN ('done','cancelled') AND file != '' AND watched=0
                        AND position > ? ORDER BY watched_at DESC LIMIT ?""", (RESUME_MIN, ROW)):
        items.append({"type": "rec", "at": r["watched_at"], **_recording(r)})
    items.sort(key=lambda x: -(x.get("at") or 0))
    return items[:ROW]


def _games(now):
    """Games on now that one of your channels or event feeds carries."""
    try:
        games = sports.overview("now").get("on_now", [])
    except Exception:  # noqa: BLE001 - the rest of Home still works without sports
        log.exception("Home: sports")
        return []
    return [g for g in games if g.get("ch") or (g.get("event") and g.get("feeds"))][:ROW]


def _coming_up(now):
    """Scheduled recordings and reminders, soonest first."""
    items = [{"type": "rec", **_recording(r)} for r in db.rows(
        "SELECT * FROM recordings WHERE status IN ('scheduled','recording') AND stop > ? ORDER BY start LIMIT ?", (now, ROW))]
    recs = {(r["number"], r["start"]) for r in items}
    for r in reminders.upcoming(now):
        if (r["number"], r["start"]) not in recs:
            items.append({"type": "remind", **r})
    items.sort(key=lambda x: x["start"])
    return items[:ROW]


def overview(recent=()):
    now = int(time.time())
    favs = [int(n) for n in config.get("favorites") or []]
    recent = [n for n in recent if n not in favs]
    rec_done = db.rows("""SELECT * FROM recordings WHERE status='done' AND file != '' ORDER BY start DESC LIMIT ?""", (ROW,))
    return {
        "favorites": _on_now(_channels(favs)[:ROW], now),
        "recent": _on_now(_channels(recent)[:ROW], now),
        "continue": _continue(now),
        "sports": _games(now),
        "recorded": [_recording(r) for r in rec_done],
        "coming": _coming_up(now),
    }
