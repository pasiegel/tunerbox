"""Box score for the game on screen: ESPN's public game summary (no account or key needed),
trimmed to what fits a side panel over the picture.

The scoreboard (scoreboard.py) already knows which game each channel or event feed is showing;
this fetches that game's summary on demand and keeps it for a few seconds, so several screens
watching the same game share one download. Each sport's summary is shaped differently (one box
score table for basketball, passing / rushing / receiving for football, skaters and goalies for
hockey), so each is cut down to a few tables of a few columns.
"""
import logging
import threading
import time

import httpx

from . import config, scoreboard, sports

log = logging.getLogger("tunerbox.gamestats")

URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/summary"
LIVE_TTL, IDLE_TTL, LOOKUP_TTL, SCORES_TTL = 20, 300, 60, 30
MAX_PLAYS = 10

# Per sport: the player tables to show, in order: (ESPN group names, title, columns, sort column, rows).
# Columns are ESPN's labels; ones a summary doesn't have are left out. sort None keeps ESPN's order
# (a batting order, starters first).
PLAYERS = {
    "Basketball": [(("",), "", ("PTS", "REB", "AST", "FG"), "PTS", 8)],
    "Football": [(("passing",), "Passing", ("C/ATT", "YDS", "TD", "INT"), "YDS", 2),
                 (("rushing",), "Rushing", ("CAR", "YDS", "TD"), "YDS", 3),
                 (("receiving",), "Receiving", ("REC", "YDS", "TD"), "YDS", 3)],
    "Hockey": [(("forwards", "defenses", "skaters"), "Skaters", ("G", "A", "SOG", "+/-"), "G+A", 6),
               (("goalies",), "Goalies", ("SA", "SV", "SV%"), None, 2)],
    "Baseball": [(("batting",), "Batting", ("H-AB", "R", "HR", "RBI"), None, 9),
                 (("pitching",), "Pitching", ("IP", "H", "ER", "K"), None, 4)],
}
# Team stats worth a row, by ESPN's label (any it doesn't have are skipped; none at all: the first ten)
TEAM_STATS = {
    "Basketball": ("FG", "3PT", "FT", "Rebounds", "Assists", "Turnovers", "Steals", "Blocks", "Fast Break Points", "Points in Paint"),
    "Football": ("Total Yards", "Passing", "Rushing", "1st Downs", "3rd down efficiency", "4th down efficiency",
                 "Turnovers", "Penalties", "Possession"),
    "Hockey": ("Shots", "Power Play Goals", "Power Play Opportunities", "Faceoffs Won", "Hits", "Blocked Shots",
               "Takeaways", "Giveaways", "Penalty Minutes"),
    "Soccer": ("Possession", "SHOTS", "ON GOAL", "Corner Kicks", "Fouls", "Saves", "Offsides", "Yellow Cards", "Red Cards"),
    "Baseball": ("Hits", "Runs", "Errors", "Home Runs", "Strikeouts", "Walks", "Left On Base"),
}

_lock = threading.Lock()          # the summary cache
_lookup_lock = threading.Lock()   # one rebuild of the lookup at a time; never held while reading it
_cache = {}                       # game id -> (fetched at, summary)
# a snapshot, replaced whole (never changed in place), so readers need no lock
_lookup = {"t": 0, "ch": {}, "feed": {}, "games": []}
# leagues left out of Live scores (except your favorite teams' games, which are always listed, first). Men's college basketball stays: its scoreboard is Division I only
# (scoreboard.py asks ESPN for groups=50), the same as the Sports screen.
SCORES_SKIP = {"ncaaw"}
MAX_OTHERS = 15                   # Live scores lists at most this many: the first ones in its order are kept
_scores = {}                      # league -> (fetched at, {game id: scoreboard game})


# ---------------------------------------------------------------- which game is on
def _refresh_lookup(now):
    """{channel number: game id} and {event feed key: game id} for the games on right now. Building it
    is the whole Sports list (a second or so on a big lineup), so it runs under its own lock: the
    stats of a game already looked up never wait for it."""
    global _lookup
    ch, feed = {}, {}
    try:
        games = sports.overview("now").get("on_now", [])
    except Exception:  # noqa: BLE001 - no lookup is "no game here", not an error for the screen
        log.exception("Game stats: games on now")
        games = []
    for g in games:
        e = g.get("event")
        if not e:
            continue
        for c in ([g["ch"]] if g.get("ch") else []) + (g.get("also") or []):
            ch.setdefault(str(c["number"]), e["id"])
        for f in g.get("feeds") or []:
            feed.setdefault(f["key"], e["id"])
    _lookup = {"t": now, "ch": ch, "feed": feed, "games": [g for g in games if g.get("event")]}


def _current(now):
    """The lookup, rebuilt first when it's older than LOOKUP_TTL (by one caller; the others wait for it)."""
    if now - _lookup["t"] > LOOKUP_TTL:
        with _lookup_lock:
            if now - _lookup["t"] > LOOKUP_TTL:      # not already rebuilt while this one waited
                _refresh_lookup(now)
    return _lookup


def game_for(ch="", feed="", now=None):
    """The scoreboard game id ("nba:401..." ) on channel `ch` or event feed `feed`, or None."""
    lk = _current(now or time.time())
    return lk["feed"].get(feed) if feed else lk["ch"].get(str(ch))


# ---------------------------------------------------------------- ESPN summary -> panel
def _num(v):
    try:
        return float(str(v).replace("+", "").split("-")[0].split("/")[0] or 0)
    except ValueError:
        return 0.0


def _players(sport, box, order):
    """[{team abbr, tables: [{title, cols, rows: [{name, num, pos, stats}]}]}] in team order."""
    spec = PLAYERS.get(sport)
    by_team = {str((p.get("team") or {}).get("id")): p for p in box.get("players") or []}
    out = []
    for tid, abbr in order:
        p = by_team.get(tid)
        if not p or not spec:
            continue
        groups = p.get("statistics") or []
        tables = []
        for names, title, want, sort, limit in spec:
            picked = [g for g in groups if (g.get("name") or "") in names] or (groups[:1] if names == ("",) else [])
            if not picked:
                continue
            labels = picked[0].get("labels") or []
            cols = [c for c in want if c in labels] or labels[:4]
            idx = [labels.index(c) for c in cols]
            rows = []
            for g in picked:
                for a in g.get("athletes") or []:
                    st = a.get("stats") or []
                    if not st or a.get("didNotPlay"):
                        continue
                    ath = a.get("athlete") or {}
                    rows.append({"name": ath.get("shortName") or ath.get("displayName") or "",
                                 "num": ath.get("jersey") or "", "pos": (ath.get("position") or {}).get("abbreviation") or "",
                                 "stats": [st[i] if i < len(st) else "" for i in idx],
                                 "_all": dict(zip(labels, st))})
            if sort == "G+A":
                rows.sort(key=lambda r: (-(_num(r["_all"].get("G")) + _num(r["_all"].get("A"))), -_num(r["_all"].get("SOG"))))
            elif sort:
                rows.sort(key=lambda r: -_num(r["_all"].get(sort)))
            for r in rows:
                del r["_all"]
            if rows:
                tables.append({"title": title, "cols": cols, "rows": rows[:limit]})
        if tables:
            out.append({"abbr": abbr, "tables": tables})
    return out


def _team_stats(sport, box, order):
    """[{label, values: [first team's, second team's]}] for the stats both teams have."""
    by_team = {str((t.get("team") or {}).get("id")): {(s.get("label") or s.get("name") or ""): s.get("displayValue", "")
                                                       for s in t.get("statistics") or []}
               for t in box.get("teams") or []}
    sides = [by_team.get(tid, {}) for tid, _ in order]
    if len(sides) != 2 or not sides[0] or not sides[1]:
        return []
    want = [k for k in TEAM_STATS.get(sport, ()) if k in sides[0] and k in sides[1]]
    if not want:
        want = [k for k in sides[0] if k in sides[1]][:10]
    return [{"label": k, "values": [sides[0][k], sides[1][k]]} for k in want]


def _plays(d, sport):
    """The latest plays, newest first: [{text, when, score}]."""
    tag = {"Football": "Q", "Basketball": "Q", "Hockey": "P"}.get(sport, "")
    plays = d.get("plays")
    if not plays:
        cur = (d.get("drives") or {}).get("current") or {}
        plays = cur.get("plays") or d.get("scoringPlays") or d.get("keyEvents") or []
    out = []
    for p in reversed(plays[-MAX_PLAYS:]):
        text = (p.get("text") or (p.get("type") or {}).get("text") or "").strip()
        if not text:
            continue
        clock = (p.get("clock") or {}).get("displayValue") or ""
        per = (p.get("period") or {}).get("displayValue") or ""
        if not per and (p.get("period") or {}).get("number"):
            per = f"{tag}{p['period']['number']}"
        out.append({"text": text, "when": " · ".join(x for x in (per, clock) if x), "score": bool(p.get("scoringPlay"))})
    return out


def parse(league, d):
    """An ESPN game summary as the panel's data."""
    sport = scoreboard.LEAGUES[league][2]
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    st = comp.get("status") or {}
    state = (st.get("type") or {}).get("state") or "pre"
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    keys = ("home", "away") if sport == "Soccer" else ("away", "home")
    teams, order = [], []
    for k in keys:
        c = sides.get(k) or {}
        t = c.get("team") or {}
        rec = (c.get("record") or [{}])[0].get("summary", "") if c.get("record") else ""
        teams.append({"name": t.get("displayName") or "", "short": t.get("shortDisplayName") or t.get("name") or "",
                      "abbr": t.get("abbreviation") or "",
                      "color": scoreboard._color(t.get("color")), "logo": ((t.get("logos") or [{}])[0].get("href") or t.get("logo") or ""),
                      "score": str(c.get("score") or "") if state != "pre" else "", "record": rec,
                      "winner": bool(c.get("winner"))})
        order.append((str(t.get("id") or ""), t.get("abbreviation") or ""))
    box = d.get("boxscore") or {}
    # before the start a summary's team stats are season averages: the panel shows game stats only
    live = state != "pre"
    return {"league": scoreboard.LEAGUES[league][1], "sport": sport, "state": state,
            "detail": (st.get("type") or {}).get("shortDetail") or "",
            "teams": teams,
            "players": _players(sport, box, order) if live else [],
            "team_stats": _team_stats(sport, box, order) if live else [],
            "plays": _plays(d, sport) if live else []}


def summary(game_id, now=None):
    """The panel's data for scoreboard game `game_id` ("nba:401..."), or None if that isn't a game."""
    league, _, espn_id = game_id.partition(":")
    if league not in scoreboard.LEAGUES or not espn_id.isdigit():
        return None
    now = now or time.time()
    with _lock:
        hit = _cache.get(game_id)
    if hit and now - hit[0] < (LIVE_TTL if hit[1]["state"] == "in" else IDLE_TTL):
        return hit[1]
    path = scoreboard.LEAGUES[league][3]
    try:
        with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": config.get("user_agent")}) as client:
            r = client.get(URL.format(path=path), params={"event": espn_id})
            r.raise_for_status()
            data = parse(league, r.json())
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Game stats %s: %s", game_id, e)
        return hit[1] if hit else None      # an older copy beats nothing
    data["id"] = game_id
    data["updated"] = int(now)
    with _lock:
        _cache[game_id] = (now, data)
        for k in [k for k, (t, _) in _cache.items() if now - t > 6 * 3600]:
            del _cache[k]
    return data


# ---------------------------------------------------------------- every other live game
def _live_scores(league, now):
    """{game id: game} from the league's scoreboard, at most SCORES_TTL old. The stored scoreboard
    is refreshed every few minutes, too slow for scores shown beside a live game."""
    hit = _scores.get(league)
    if hit and now - hit[0] < SCORES_TTL:
        return hit[1]
    out = {}
    try:
        with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": config.get("user_agent")}) as client:
            for day in scoreboard._days(now, False):
                out.update({ev["id"]: ev for ev in scoreboard.fetch(client, league, day)})
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Game stats scores %s: %s", league, e)
        return hit[1] if hit else {}
    _scores[league] = (now, out)
    return out


def others(game_id=None, now=None):
    """Every other game being played right now in the leagues picked in Settings, for the panel's
    Live scores tab: [{id, league, teams: [{abbr, name, logo, color, score}], detail, fav, ch, also, feeds, ...}]. Your
    teams' games first, then this game's league, then this sport, then ones you can watch. With no
    game_id (nothing the scoreboard knows is on), every live game, your teams' first.
    `ch` is the channel showing it (None if none does) and `also` the others; `feeds` the event feeds
    named after it. The TV asks which one to watch when there's more than one, like the Sports screen."""
    league = (game_id or "").partition(":")[0]
    if game_id and league not in scoreboard.LEAGUES:
        return []
    now = now or time.time()
    sport, tag = (scoreboard.LEAGUES[league][2], scoreboard.LEAGUES[league][1]) if game_id else (None, None)
    games = list(_current(now)["games"])
    games = [g for g in games if g["event"]["id"] != game_id
             and (g["event"].get("fav") or g["event"]["id"].partition(":")[0] not in SCORES_SKIP)]
    fresh = {}
    for lg in {g["event"]["id"].partition(":")[0] for g in games}:
        fresh.update(_live_scores(lg, now))
    out = []
    for g in games:
        e = g["event"]
        ev = fresh.get(e["id"])
        if ev:
            e = {**e, "state": ev["state"], "detail": ev["detail"], "teams": scoreboard._sides(ev)}
        if e["state"] != "in":
            continue
        out.append({"id": e["id"], "league": e["league"], "detail": e["detail"], "fav": bool(e.get("fav")),
                    "teams": [{"abbr": t["abbr"], "name": t.get("name") or "", "logo": t.get("logo") or "",
                               "color": t["color"], "score": t["score"]} for t in e["teams"]],
                    "sport": e["sport"], "name": e["name"], "title": g.get("title") or e["name"],
                    "start": g.get("start"), "stop": g.get("stop"),
                    "ch": g.get("ch"), "also": g.get("also") or [], "feeds": g.get("feeds") or []})
    # your teams' games always first (so the MAX_OTHERS cut never drops one), whatever is on this channel
    out.sort(key=lambda o: (not o["fav"], o["league"] != tag, o["sport"] != sport, not o["ch"] and not o["feeds"], o["league"]))
    return out[:MAX_OTHERS]
