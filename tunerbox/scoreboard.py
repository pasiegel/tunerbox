"""Live games from ESPN's public scoreboard (no account or key needed), matched onto the guide.

The guide alone misses games: many listings have no "Sports" category, and event feeds have no
listings at all. The scoreboard says which games the leagues picked in Settings are playing; each
game is then matched to a guide listing (both team names, around the start time) or to a playlist
event feed, so it shows up under Sports > On Now / Coming Up with its score.
"""
import bisect
import json
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db

log = logging.getLogger("tunerbox.scoreboard")

URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
# key: (name in Settings, short tag, sport, ESPN path, extra query). "groups" asks for every
# Division I game; without it the college scoreboards only list ranked teams.
LEAGUES = {
    "nfl": ("NFL", "NFL", "Football", "football/nfl", {}),
    "ncaaf": ("College Football", "NCAAF", "Football", "football/college-football", {"groups": 80}),
    "nba": ("NBA", "NBA", "Basketball", "basketball/nba", {}),
    "wnba": ("WNBA", "WNBA", "Basketball", "basketball/wnba", {}),
    "ncaab": ("Men's College Basketball", "NCAAM", "Basketball", "basketball/mens-college-basketball", {"groups": 50}),
    "ncaaw": ("Women's College Basketball", "NCAAW", "Basketball", "basketball/womens-college-basketball", {"groups": 50}),
    "mlb": ("MLB", "MLB", "Baseball", "baseball/mlb", {}),
    "nhl": ("NHL", "NHL", "Hockey", "hockey/nhl", {}),
    "mls": ("MLS", "MLS", "Soccer", "soccer/usa.1", {}),
    "epl": ("Premier League", "EPL", "Soccer", "soccer/eng.1", {}),
    "ucl": ("Champions League", "UCL", "Soccer", "soccer/uefa.champions", {}),
    "laliga": ("La Liga", "La Liga", "Soccer", "soccer/esp.1", {}),
    "ligamx": ("Liga MX", "Liga MX", "Soccer", "soccer/mex.1", {}),
}

# What the TV and phone head a game with, where the Settings name is longer than needed
HEADING = {"ncaab": "College Basketball"}

FULL_EVERY = 6 * 3600     # all three days
BUSY_EVERY = 5 * 60       # today only, while a game is on or about to start
IDLE_EVERY = 30 * 60
RETRY = 2 * 60            # after a refresh that got nothing
EARLY, LATE = 45 * 60, 5 * 60   # a listing counts if it's on air between 5 and 45 minutes after the start


def league_list():
    return [{"key": k, "name": v[0], "sport": v[2]} for k, v in LEAGUES.items()]


def selected():
    want = config.get("sports_leagues") or []
    return [k for k in LEAGUES if k in want]


# ------------------------------------------------------------------ download
def _days(now, full):
    """ESPN files games under the US Eastern date. Today (+ the next two for a full refresh), and
    yesterday too in the small hours, when last night's late games are still going."""
    et = datetime.fromtimestamp(now, timezone.utc) - timedelta(hours=5)
    days = [et + timedelta(days=i) for i in range(3 if full else 1)]
    if et.hour < 6:
        days.insert(0, et - timedelta(days=1))
    return [d.strftime("%Y%m%d") for d in days]


def _start(text):
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except (TypeError, ValueError):
            pass
    return 0


def parse_event(key, e):
    """One scoreboard event as the dict TunerBox keeps, or None (no teams, no start time yet)."""
    comp = (e.get("competitions") or [{}])[0]
    teams = {}
    for t in comp.get("competitors") or []:
        tm = t.get("team") or {}
        teams[t.get("homeAway")] = {
            "name": tm.get("displayName") or "", "short": tm.get("shortDisplayName") or "",
            "nick": tm.get("name") or "", "loc": tm.get("location") or "",
            "abbr": tm.get("abbreviation") or "", "score": str(t.get("score") or "")}
    start = _start(e.get("date"))
    st = (e.get("status") or {}).get("type") or {}
    state = st.get("state") or "pre"
    if set(teams) != {"home", "away"} or not all(t["name"] for t in teams.values()) or not start:
        return None
    if state == "pre" and comp.get("timeValid") is False:      # "TBD": the date is a placeholder
        return None
    on = []
    for b in comp.get("broadcasts") or []:
        on += [n for n in b.get("names") or [] if n and n not in on]
    return {"id": f"{key}:{e.get('id')}", "league": key, "start": start, "state": state,
            "detail": (st.get("shortDetail") or "") if state != "pre" else "",
            "home": teams["home"], "away": teams["away"], "on": on[:3]}


def fetch(client, key, day):
    _, _, _, path, extra = LEAGUES[key]
    r = client.get(URL.format(path=path), params={"dates": day, "limit": 500, **extra})
    r.raise_for_status()
    return [ev for ev in (parse_event(key, e) for e in r.json().get("events") or []) if ev]


def refresh(full=True, now=None):
    """Download the picked leagues' games. Returns how many league-days were read."""
    now = int(now or time.time())
    keys = selected()
    good = 0
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": config.get("user_agent")}) as client:
        for key in keys:
            for day in _days(now, full):
                try:
                    evs = fetch(client, key, day)
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("Scoreboard %s %s: %s", key, day, e)
                    continue
                with db.conn() as c:
                    c.execute("DELETE FROM sports_events WHERE league=? AND day=?", (key, day))
                    c.executemany("INSERT OR REPLACE INTO sports_events(id, league, day, start, data) VALUES (?, ?, ?, ?, ?)",
                                  [(ev["id"], key, day, ev["start"], json.dumps(ev)) for ev in evs])
                good += 1
    db.run("DELETE FROM sports_events WHERE start < ?", (now - 86400,))
    if good or not keys:
        db.set_meta("sports_quick", now)
        if full:
            db.set_meta("sports_full", now)
            db.set_meta("sports_sig", ",".join(keys))
    _match_cache["key"] = None
    return good


def events(start, stop):
    """Games of the picked leagues starting in [start, stop), earliest first."""
    keys = selected()
    if not keys:
        return []
    q = ",".join("?" * len(keys))
    return [json.loads(r["data"]) for r in db.rows(
        f"SELECT data FROM sports_events WHERE league IN ({q}) AND start >= ? AND start < ? ORDER BY start, id",
        (*keys, start, stop))]


def _busy(now):
    return any(e["state"] == "in" or (e["state"] == "pre" and e["start"] - 900 < now)
               for e in events(now - 5 * 3600, now + 900))


def due(now):
    """'full', 'quick' or None."""
    keys = selected()
    if not keys:
        return None
    if db.get_meta("sports_sig", "") != ",".join(keys) or now - int(db.get_meta("sports_full", 0) or 0) > FULL_EVERY:
        return "full"
    if now - int(db.get_meta("sports_quick", 0) or 0) > (BUSY_EVERY if _busy(now) else IDLE_EVERY):
        return "quick"
    return None


_run = {"running": False, "tried": 0}
_lock = threading.Lock()


def tick(now=None, force=False):
    """Called from the housekeeping loop: refresh in the background when it's time."""
    now = int(now or time.time())
    with _lock:
        if _run["running"] or (not force and now - _run["tried"] < RETRY):
            return False
        kind = "full" if force else due(now)
        if not kind:
            return False
        _run.update(running=True, tried=now)

    def work():
        try:
            refresh(kind == "full")
        except Exception:  # noqa: BLE001
            log.exception("scoreboard refresh")
        finally:
            _run["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return True


# ------------------------------------------------------------------ matching
_CLOCK = re.compile(r"@?\s*\b\d{1,2}[:.]\d{2}\s*(?:[ap]\.?m\.?)?(?:\s*[ecmp][sd]?t)?\b", re.I)
_SEP = re.compile(r" (?:at|vs|v|versus) ")
_RANK = re.compile(r"^(?:no )?\d{1,2} ")
_TAIL = re.compile(r"(?: (?:hd|fhd|uhd|4k|sd|live|\d+))+$")
_WORDS = re.compile(r"[a-z0-9]+")
_STATE = re.compile(r"(?<=[a-z]) st(?= (?:at|vs|v|versus) |$)")


def _n(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and ").replace("@", " at ")).strip()
    return _STATE.sub(" state", s)          # "Coppin St" -> "coppin state", but not "St. Louis"


def team_keys(t):
    """Names to look for. `strong` ones may appear anywhere in a listing ("Philadelphia Eagles",
    "Eagles"); the rest ("Michigan") must be a whole side of "A at B", so Michigan never matches
    "Michigan State at ..."."""
    full, nick, loc, short = _n(t["name"]), _n(t["nick"]), _n(t["loc"]), _n(t["short"])
    strong = {full} | ({nick} if len(nick) >= 4 else set())
    return {"strong": strong - {""}, "exact": {full, nick, loc, short, f"{loc} {nick}".strip()} - {""}}


def event_keys(ev):
    return team_keys(ev["home"]), team_keys(ev["away"])


def candidates(*texts):
    """Normalized pieces of a listing or feed name to test: the whole thing and each part around a
    colon or bar ("NFL Football: Cowboys at Eagles", "NBA 03 | Lakers vs Celtics @ 7:30 PM")."""
    out = []
    for text in texts:
        text = _CLOCK.sub(" ", text or "")
        for part in [text] + (re.split(r"[:|]", text) if re.search(r"[:|]", text) else []):
            n = _n(part)
            if n and n not in out:
                out.append(n)
    return out


def _hit(team, side):
    return side in team["exact"] or any(f" {a} " in f" {side} " for a in team["strong"])


def text_matches(keys, texts):
    home, away = keys
    for text in texts:
        parts = _SEP.split(text)
        if len(parts) == 2:
            a, b = (_TAIL.sub("", _RANK.sub("", p)).strip() for p in parts)
            if (_hit(home, a) and _hit(away, b)) or (_hit(home, b) and _hit(away, a)):
                return True
        padded = f" {text} "
        if all(any(f" {a} " in padded for a in t["strong"]) for t in (home, away)):
            return True
    return False


class Finder:
    """Finds which of many named things (event feeds) carries a game, without testing every name
    against every game: a word index narrows it to the few names that have a word of both teams."""

    def __init__(self, items):
        self.items = items                       # [(thing, candidates(name))], best first
        self.words = {}
        for i, (_, texts) in enumerate(items):
            for w in {w for t in texts for w in t.split()}:
                self.words.setdefault(w, set()).add(i)

    def _with(self, team):
        """Names holding every word of one of the team's names."""
        out = set()
        for alias in team["exact"] | team["strong"]:
            sets = [self.words.get(w) for w in alias.split()]
            if all(sets):
                out |= set.intersection(*sets)
        return out

    def find_all(self, keys, limit=30):
        """Every thing whose name has both teams, in the order they were given."""
        home, away = keys
        out = []
        for i in sorted(self._with(home) & self._with(away)):
            if text_matches(keys, self.items[i][1]):
                out.append(self.items[i][0])
                if len(out) >= limit:
                    break
        return out

    def find(self, keys):
        return next(iter(self.find_all(keys, 1)), None)


_match_cache = {"key": None, "t": 0, "map": {}}


def match_programs(evs, epg_ids, lo, hi, cache_key=None, sport_of=None):
    """{(guide channel, program start): event id} for listings between lo and hi on the given guide
    channels. `sport_of(program)` (optional) names the sport a listing is clearly about, so an NFL
    game never lands on a baseball listing that happens to share two nicknames."""
    if not evs:
        return {}
    key = (cache_key, tuple(e["id"] for e in evs), lo // 600)
    if cache_key is not None and _match_cache["key"] == key and time.time() - _match_cache["t"] < 600:
        return _match_cache["map"]
    starts = [e["start"] for e in evs]             # already sorted
    keys = [event_keys(e) for e in evs]
    # Most listings name no team at all. A listing can only match if it has the last word of some
    # team name, which is cheap to check before the real (slow) comparison.
    last = {a.split()[-1] for home, away in keys for t in (home, away) for a in t["exact"] | t["strong"]}
    if "state" in last:
        last.add("st")
    out = {}
    for p in db.rows("SELECT channel, start, stop, title, subtitle, category FROM programs WHERE stop > ? AND start < ?",
                     (lo, hi)):
        if p["channel"] not in epg_ids:
            continue
        i, j = bisect.bisect_right(starts, p["start"] - EARLY), bisect.bisect_left(starts, p["stop"] - LATE)
        if i >= j:
            continue
        raw = f"{p['title'] or ''} {p['subtitle'] or ''}".lower()
        if raw.isascii() and last.isdisjoint(_WORDS.findall(raw)):
            continue
        texts = candidates(p["subtitle"], p["title"], f"{p['title'] or ''} {p['subtitle'] or ''}")
        sport = None
        for n in range(i, j):
            if not text_matches(keys[n], texts):
                continue
            if sport is None:
                sport = (sport_of(p) if sport_of else "") or ""
            if sport and sport != LEAGUES[evs[n]["league"]][2]:
                continue
            out[(p["channel"], p["start"])] = evs[n]["id"]
            break
    if cache_key is not None:
        _match_cache.update(key=key, t=time.time(), map=out)
    return out


# ------------------------------------------------------------------ what the Sports screen shows
def title(ev):
    if LEAGUES[ev["league"]][2] == "Soccer":
        return f"{ev['home']['name']} vs {ev['away']['name']}"
    return f"{ev['away']['name']} at {ev['home']['name']}"


def public(ev):
    """The part of a game the TV and phone screens show next to a listing."""
    name, tag, sport, _, _ = LEAGUES[ev["league"]]
    score = ""
    if ev["state"] != "pre" and ev["home"]["score"] != "" and ev["away"]["score"] != "":
        score = f"{ev['away']['abbr']} {ev['away']['score']}, {ev['home']['abbr']} {ev['home']['score']}"
    return {"id": ev["id"], "league": tag, "league_name": name, "heading": HEADING.get(ev["league"], name),
            "sport": sport, "name": title(ev),
            "state": ev["state"], "detail": ev["detail"], "score": score, "on": ev["on"]}
