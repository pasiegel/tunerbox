"""The Home screen: what's on your favorite and recent channels, your teams' games, what you're
part-way through, games on now, new recordings and what's coming up. One request, so the screen draws in one go."""
import logging
import time

from . import config, db, dvr, epg, guide, reminders, sports, vod

log = logging.getLogger("tunerbox.home")
ROW = 20                    # most tiles in a row
RESUME_MIN = 30             # seconds into a recording before it counts as started (as On Demand)
PROG_KEYS = ("title", "subtitle", "episode", "descr", "category", "start", "stop", "image", "is_live", "is_new", "rating")


def _on_now(chans, now, games=None):
    """Channel tiles with the show that's on now, and the scoreboard game it is (logos and score),
    if `games` ({channel number: game}) has one for it."""
    games = games or {}
    progs = epg.programs({c["epg"] for c in chans if c["epg"]}, now, now + 1)
    out = []
    for c in chans:
        p = next((x for x in progs.get(c["epg"], []) if x["start"] <= now < x["stop"]), None) if c["epg"] else None
        out.append({"number": c["number"], "name": c["name"], "label": c.get("label") or c["name"],
                    "logo": c.get("logo") or "", "now": {k: p[k] for k in PROG_KEYS} if p else None,
                    "game": games.get(c["number"])})
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
    """On Demand titles and recordings you're part-way through, the latest first (up to ROW of each, as
    they're shown in two rows)."""
    items = [{"type": "vod", **t} for t in (vod.continue_watching() if vod.enabled() else [])][:ROW]
    recs = db.rows("""SELECT * FROM recordings WHERE status IN ('done','cancelled') AND file != '' AND watched=0
                      AND position > ? ORDER BY watched_at DESC LIMIT ?""", (RESUME_MIN, ROW)) if dvr.enabled() else []
    for r in recs:
        items.append({"type": "rec", "at": r["watched_at"], **_recording(r)})
    items.sort(key=lambda x: -(x.get("at") or 0))
    return items


def _sports_now():
    try:
        return sports.overview("now").get("on_now", [])
    except Exception:  # noqa: BLE001 - the rest of Home still works without sports
        log.exception("Home: sports")
        return []


def _by_channel(games):
    """{channel number: scoreboard game} for the channels showing a game right now (its first
    channel and the others that have it too), so a channel tile can show the score."""
    out = {}
    for g in games:
        if g.get("event"):
            for c in ([g["ch"]] if g.get("ch") else []) + (g.get("also") or []):
                out.setdefault(c["number"], g["event"])
    return out


def _games(games):
    """(your teams' games, other games): on now, and one of your channels or event feeds carries them.
    Favorite teams are picked in Settings › Sports."""
    games = [g for g in games if g.get("ch") or (g.get("event") and g.get("feeds"))]
    fav = [g for g in games if g.get("event") and g["event"].get("fav")]
    return fav[:ROW], [g for g in games if not (g.get("event") and g["event"].get("fav"))][:ROW]


def _coming_up(now):
    """Scheduled recordings and reminders, soonest first. `conflict` says why a recording won't get a
    connection ("" when it will), as on the Recordings screen."""
    rows = db.rows("SELECT * FROM recordings WHERE status IN ('scheduled','recording') AND stop > ? ORDER BY start LIMIT ?",
                   (now, ROW)) if dvr.enabled() else []         # recordings off: reminders only
    clash = dvr.conflicts() if rows else {}
    items = [{"type": "rec", **_recording(r), "conflict": clash.get(r["id"], "") if r["status"] == "scheduled" else ""}
             for r in rows]
    recs = {(r["number"], r["start"]) for r in items}
    for r in reminders.upcoming(now):
        if (r["number"], r["start"]) not in recs:
            items.append({"type": "remind", **r})
    items.sort(key=lambda x: x["start"])
    return items[:ROW]


# Home's rows in their usual order: {id: name in Setup}. The TV screen (web/tv/48-home.js homeRows) uses the same ids.
ROWS = {
    "fav": "Favorites on now",
    "teams": "Your teams on now",
    "cont": "Continue Watching: On Demand",
    "contrec": "Continue Watching: Recordings",
    "libnew": "New in My Library",
    "sports": "Sports on now",
    "recent": "Recent channels",
    "recorded": "Recently recorded",
    "coming": "Coming up",
}


def rows():
    """The rows to show, top to bottom (Setup › Settings › Screens › Home rows). A row the saved order
    doesn't name yet (one added in a later version) keeps its usual place after the rows it follows."""
    saved = [r for r in config.get("home_order") or [] if r in ROWS]
    order = list(saved)
    for i, r in enumerate(ROWS):
        if r not in order:
            before = [x for x in list(ROWS)[:i] if x in order]
            order.insert(order.index(before[-1]) + 1 if before else 0, r)
    hidden = set(config.get("home_hidden") or [])
    return [r for r in order if r not in hidden]


def overview(recent=()):
    now = int(time.time())
    shown = rows()
    on = set(shown)
    favs = [int(n) for n in config.get("favorites") or []]
    recent = [n for n in recent if n not in favs]
    # games also put scores on favorite and recent channel tiles
    on_now = _sports_now() if on & {"fav", "recent", "teams", "sports"} else []
    by_ch = _by_channel(on_now)
    team_games, games = _games(on_now)
    rec_done = db.rows("""SELECT * FROM recordings WHERE status='done' AND file != '' ORDER BY start DESC LIMIT ?""",
                       (ROW,)) if dvr.enabled() and "recorded" in on else []
    cont = _continue(now) if on & {"cont", "contrec"} else []
    cont = [x for x in cont if ("cont" if x["type"] == "vod" else "contrec") in on]
    return {
        "rows": shown,
        "favorites": _on_now(_channels(favs)[:ROW], now, by_ch) if "fav" in on else [],
        "recent": _on_now(_channels(recent)[:ROW], now, by_ch) if "recent" in on else [],
        "continue": cont,
        "library_new": [{"type": "vod", **t} for t in vod.library_new(limit=ROW, unwatched=True)]
                       if vod.enabled() and "libnew" in on else [],
        "teams": team_games if "teams" in on else [],
        "sports": games if "sports" in on else [],
        "recorded": [_recording(r) for r in rec_done],
        "coming": _coming_up(now) if "coming" in on else [],
    }
