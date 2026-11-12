"""Search plus the Sports & PPV area.

* Sports on now / coming up come from the guide (program categories), plus the games of the leagues
  picked in Settings (scoreboard.py): those are matched to a guide listing or an event feed.
* Sports channels are lineup/extra channels that look like sports networks.
* Events & PPV are playlist groups of event feeds (ESPN+, Peacock events, PPV, NFL/NBA team
  feeds...). Those only carry video while something is on, so the stream check usually marks
  them dead; they are listed anyway, with an on-demand "which are live now" check.
"""
import asyncio
import functools
import re
import threading
import time

from rapidfuzz import fuzz, process

from . import config, db, epg, guide, probe, scoreboard
from .text import is_foreign, norm

SPORT_CATEGORY = re.compile(
    r"^(sports?|sports? events?|sporting events?|football|american football|basketball|baseball|softball|"
    r"hockey|ice hockey|soccer|golf|tennis|boxing|wrestling|pro wrestling|mixed martial arts|mma|"
    r"multi-sport event|action sports|extreme sports|motorsports?|auto racing|racing|volleyball|lacrosse|"
    r"rugby|cricket|curling|cycling|athletics|track and field|swimming|skiing|figure skating|gymnastics|"
    r"horse racing|olympics|darts|snooker|billiards|bowling|bull riding|rodeo)$", re.I)

SPORTS_CHANNEL = re.compile(
    r"\b(espn\w*|fs ?[12]|fox sports|fox deportes|nbc ?sports|cbs sports|golf|tennis|nfl|nba|wnba|mlb|nhl|"
    r"big ten|btn|sec network|acc network|acc digital|pac[ -]?12|bein|bally|fanduel sports|marquee|tudn|deportes|"
    r"sportsnet|tsn|olympic|willow|stadium|red ?zone|nesn|yes network|sny|altitude|root sports|masn|"
    r"sportsman|tvg|fight|ufc|wwe|boxing|nascar|racing|f1|dazn|fubo sports|sports?|golazo|fifa|"
    r"bellator|draftkings|fanduel|billiard|lacrosse|outdoor channel)\b", re.I)

PPV_GROUP = re.compile(r"\bppv\b|pay.?per.?view|fight ?night", re.I)
EVENT_GROUP = re.compile(
    r"\b(events?|live sports|nfl|nba|wnba|mlb|nhl|mls|nwsl|ncaa[a-z]*|cfb|cbb|epl|premier league|la ?liga|serie a|bundesliga|"
    r"ligue 1|uefa|champions league|f1|formula 1|nascar|indycar|motogp|ufc|wwe|aew|boxing|mma|pga|"
    r"cricket|rugby|dazn|flo ?sports|fite|sunday ticket|game ?pass|league ?pass|center ice|mlb\.?tv|"
    r"nhl\.?tv|b1g)\b|espn\s*(\+|plus)|btn\s*(\+|plus)", re.I)
# 24/7 movie loops and entertainment networks some providers file under "PPV"
NOT_EVENTS = re.compile(r"24/7|cinema|movie|film|series|original|network|kids|music", re.I)
# "ESPN+ 12", "PPV Event 03", ":Golf  02": an empty slot until an event is scheduled
GENERIC_SLOT = re.compile(r"^[A-Za-z+: ]{0,28}?[\s#|:-]*\d{1,3}\s*$")
EMPTY_SLOT = re.compile(r"no event|2098-|[:|-]\s*$|^\W*$", re.I)
ENDED = re.compile(r"^\W*(ended|end)\s*\|", re.I)
HEADER = re.compile(r"^\W*#{2,}|#{3,}\W*$")              # "####### ESPN+ PPV #######"
# "US| ESPN+ PPV", "DE: SKY SPORT": a country code in front of a group or channel name
COUNTRY_PREFIX = re.compile(r"^\W*([A-Za-z]{2,3})\s*[|:]")


# Which sport a guide listing is. The guide category is often just "Sports", so the title decides.
# First match wins: soccer comes before football so "Premier League Football" counts as soccer.
SPORT_TYPES = [(name, re.compile(rx, re.I)) for name, rx in [
    ("Soccer", r"\b(soccer|f[uú]tbol|mls|nwsl|premier league|epl|la ?liga|liga mx|serie a|bundesliga|ligue 1|uefa|"
               r"champions league|europa league|fifa|world cup|copa|concacaf|fa cup|carabao)\b"),
    ("Football", r"\b(nfl|ncaaf|cfb|xfl|ufl|cfl|football|super bowl|bowl game)\b"),
    ("Basketball", r"\b(nba|wnba|ncaab|cbb|basketball|march madness|final four)\b"),
    ("Baseball", r"\b(mlb|baseball|world series)\b"),
    ("Softball", r"\bsoftball\b"),
    ("Hockey", r"\b(nhl|hockey|stanley cup)\b"),
    ("Golf", r"\b(golf|pga|lpga|liv golf|masters tournament|ryder cup)\b"),
    ("Tennis", r"\b(tennis|atp|wta|wimbledon|us open tennis)\b"),
    ("Motorsports", r"\b(nascar|f1|formula 1|formula one|indycar|motogp|imsa|motocross|supercross|"
                    r"auto racing|motorsports?|grand prix|drag racing)\b"),
    ("MMA", r"\b(ufc|mma|mixed martial arts|bellator|pfl)\b"),
    ("Boxing", r"\bboxing\b"),
    ("Wrestling", r"\b(wrestling|wwe|aew|raw|smackdown|nxt)\b"),
    ("Volleyball", r"\bvolleyball\b"),
    ("Lacrosse", r"\blacrosse\b"),
    ("Rugby", r"\brugby\b"),
    ("Cricket", r"\bcricket\b"),
    ("Horse Racing", r"\b(horse racing|thoroughbred|kentucky derby|preakness|belmont stakes|breeders' cup)\b"),
    ("Cycling", r"\b(cycling|tour de france)\b"),
    ("Track & Field", r"\b(track and field|athletics|marathon)\b"),
    ("Skiing", r"\b(skiing|snowboarding)\b"),
    ("Figure Skating", r"\bfigure skating\b"),
    ("Gymnastics", r"\bgymnastics\b"),
    ("Swimming", r"\b(swimming|diving)\b"),
    ("Bowling", r"\bbowling\b"),
    ("Darts", r"\bdarts\b"),
    ("Billiards", r"\b(billiards|snooker|pool)\b"),
    ("Rodeo", r"\b(rodeo|bull riding|pbr)\b"),
    ("Curling", r"\bcurling\b"),
    ("Poker", r"\bpoker\b"),
]]
_GENERIC_SPORT = re.compile(r"^(sports?|sports? events?|sporting events?|multi-sport event|action sports|"
                            r"extreme sports|sports? non-event|sports talk)$", re.I)


def sport_type(p):
    """'Basketball', 'Football'... for a guide listing, or '' when it can't tell."""
    cat = (p.get("category") or "").strip()
    # title first: UK-style guides file soccer under "Football"
    for text in (p.get("title") or "", (cat if not _GENERIC_SPORT.match(cat) else ""), p.get("subtitle") or ""):
        for name, rx in SPORT_TYPES:
            if text and rx.search(text):
                return name
    return ""


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


def is_sport_program(p):
    return bool(p["category"] and SPORT_CATEGORY.match(p["category"].strip()))


def _regions():
    r = config.get("sports_regions", "US")
    return {x.strip().upper() for x in (r if isinstance(r, list) else str(r).split(",")) if x.strip()}


def in_region(text):
    """Groups/channels without a country prefix always count; 'DE| ...' only if DE is wanted.
    Setting sports_regions: "US" (default), e.g. "US,CA,UK", or "*" for everything."""
    regions = _regions()
    if "*" in regions:
        return True
    if "US" in regions:
        regions = regions | {"USA"}
    m = COUNTRY_PREFIX.match(text or "")
    return not m or m.group(1).upper() in regions


def is_sports_channel(c):
    grp = c.get("group") or ""
    if not ("sport" in grp.lower() or SPORTS_CHANNEL.search(c["name"] or "")):
        return False
    return c["kind"] == "lineup" or not (group_kind(grp) or not in_region(grp) or not in_region(c["name"])
                                         or is_foreign(c["name"]))


@functools.lru_cache(maxsize=4096)
def group_kind(grp, name=""):
    """'ppv', 'events' or None for a playlist group (falls back to the stream name)."""
    if grp and NOT_EVENTS.search(grp):
        return None
    for part in [p.strip() for p in (grp or "").split(";") if p.strip()] or [""]:
        if PPV_GROUP.search(part):
            return "ppv"
        if EVENT_GROUP.search(part) and not re.fullmatch(r"(?i)sports?", part):
            return "events"
    if re.search(r"(?i)^\W*ppv\b|\bppv\s*\d", name or ""):
        return "ppv"
    return None


# ------------------------------------------------------------------ guide-based lists
def _channels_by_epg(chans):
    by = {}
    for c in chans:
        if c["epg"]:
            by.setdefault(c["epg"], c)
    return by


def _chan_out(c):
    return {"number": c["number"], "name": c["name"], "label": c["label"], "logo": c["logo"]}


def _sport_programs(start, stop, evs=()):
    """Guide listings that are sports (by category) or carry one of the scoreboard games `evs`.
    With games, listings that ended in the last few hours come back too: a game can run long."""
    chans = guide.channels()
    by_epg = _channels_by_epg(chans)
    lo = start - OVERRUN if evs else start
    matched = scoreboard.match_programs(evs, by_epg, lo, stop, sport_of=sport_type,
                                        cache_key=(id(_base()), db.get_meta("epg_updated", "")))
    by_id = {e["id"]: e for e in evs}
    rows = db.rows("SELECT * FROM programs WHERE stop > ? AND start < ? AND category != '' ORDER BY start", (start, stop))
    have = {(p["channel"], p["start"]) for p in rows}
    with db.conn() as con:          # the matched listings that query left out (no category, or already over)
        for k in matched.keys() - have:
            r = con.execute("SELECT * FROM programs WHERE channel=? AND start=?", k).fetchone()
            if r:
                rows.append(dict(r))
    rows.sort(key=lambda p: p["start"])
    out = []
    for p in rows:
        c = by_epg.get(p["channel"])
        ev = by_id.get(matched.get((p["channel"], p["start"])))
        if not c or not (ev or is_sport_program(p)):
            continue
        q = dict(p)
        q.pop("channel", None)
        q["ch"] = _chan_out(c)
        q["game"] = ev
        out.append(q)
    return out


OVERRUN = 4 * 3600


def _one_per_game(progs, now):
    """One listing per scoreboard game (the first: on now before later, lowest channel). A listing
    that already ended only stays if the game is still being played and nothing else carries it:
    the game ran long, and the channel is still on it whatever the guide says."""
    seen, out = set(), []
    for p in sorted(progs, key=lambda p: p["stop"] <= now):      # stable: listings still on air first
        ev = p.pop("game", None)
        if ev:
            if ev["id"] in seen or (p["stop"] <= now and ev["state"] != "in"):
                continue
            seen.add(ev["id"])
            if p["stop"] <= now:
                p["stop"] = now + 1800
            p["event"] = scoreboard.public(ev)
            p["is_live"] = int(ev["state"] == "in" or (ev["state"] == "pre" and bool(p["is_live"])))
        elif p["stop"] <= now:
            continue
        out.append(p)
    return out, seen


def _feed_finder():
    """Finds the event feed whose name has both teams of a game; a feed that's live right now first."""
    feeds = [({**f, "kind": g["kind"]}, scoreboard.candidates(f["name"]))
             for g in event_groups() for f in g["feeds"] if not f["slot"]]
    feeds.sort(key=lambda ft: ft[0]["status"] != "ok")
    return scoreboard.Finder(feeds)


def _game_items(evs, taken, now, finder):
    """Scoreboard games with no guide listing: on an event feed if a feed's name has both teams,
    otherwise listed without a channel (unless that's switched off in Settings)."""
    # "pre" long after the start time is a postponed game or a scoreboard that stopped updating
    evs = [e for e in evs if e["id"] not in taken
           and (e["state"] == "in" or (e["state"] == "pre" and e["start"] > now - 3600))]
    if not evs:
        return []
    show_all = config.get("sports_show_unmatched", True)
    out = []
    for ev in evs:
        feed = finder.find(scoreboard.event_keys(ev))
        if not feed and not show_all:
            continue
        pub = scoreboard.public(ev)
        out.append({"title": pub["name"], "subtitle": pub["league_name"], "descr": "", "category": "Sports",
                    "start": ev["start"], "stop": max(ev["start"] + 3 * 3600, now + 1800 if ev["state"] == "in" else 0),
                    "is_live": int(ev["state"] == "in"), "is_new": 0, "ch": None, "feed": feed, "event": pub})
    return out


def _dedupe(progs, now):
    """The same game listed by two guides / channel copies: keep the first (lowest channel)."""
    seen, out = set(), []
    for p in progs:
        t = re.sub(r"^\s*live:\s*", "", (p["title"] or "").lower()).strip()
        k = (t, (p["subtitle"] or "").lower().strip())
        if p["start"] > now:
            k += (p["start"] // 1800,)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


_sc_cache = {"src": None, "list": []}


def _sports_channels(allc):
    if _sc_cache["src"] is not _base():
        names = _chan_norm(allc)
        out, seen = [], set()
        for c in allc:                      # one entry per network (lineups repeat SD/HD copies)
            k = names[id(c)]
            if k not in seen and is_sports_channel(c):
                seen.add(k)
                out.append(c)
        _sc_cache.update(src=_base(), list=out)
    return _sc_cache["list"]


def _ch_num(p):
    return p["ch"]["number"] if p["ch"] else 10 ** 9


def overview():
    now = int(time.time())
    evs = scoreboard.events(now - 8 * 3600, now + 2 * 86400)
    progs = _sport_programs(now, now + 2 * 86400, evs)
    progs.sort(key=lambda p: (p["start"] > now, p["start"] if p["start"] > now else 0, p["ch"]["number"]))
    progs, taken = _one_per_game(progs, now)
    finder = _feed_finder() if evs else None
    by_id = {e["id"]: e for e in evs}
    progs = _dedupe(progs, now)
    # A game on a channel may be on an event feed too. The guide only says what the channel is
    # scheduled to show (a blackout or a regional feed looks the same here), so offer both.
    for p in progs:
        if p.get("event"):
            p["feed"] = finder.find(scoreboard.event_keys(by_id[p["event"]["id"]]))
    progs += _game_items(evs, taken, now, finder)
    on_now = [p for p in progs if p["start"] <= now]
    upcoming = sorted((p for p in progs if p["start"] > now), key=lambda p: (p["start"], _ch_num(p)))[:300]
    for p in progs:
        if p.get("event"):
            p["sport"] = p["event"]["sport"]
    for p in on_now:
        p.setdefault("sport", sport_type(p))
    # games nothing here carries go last. Then LIVE broadcasts first, then the rest; within each:
    # earliest start, sport (unknown last), name
    on_now.sort(key=lambda p: (not (p["ch"] or p.get("feed")), not p["is_live"], p["start"], not p["sport"], p["sport"],
                               _natural(p["title"]), _ch_num(p)))

    chans = _sports_channels(guide.channels())
    cur = epg.programs({c["epg"] for c in chans if c["epg"]}, now, now + 1)
    sports_ch = []
    for c in chans:
        p = next((x for x in cur.get(c["epg"], []) if x["start"] <= now < x["stop"]), None) if c["epg"] else None
        sports_ch.append({**_chan_out(c), "kind": c["kind"],
                          "now": {"title": p["title"], "subtitle": p["subtitle"], "stop": p["stop"],
                                  "is_live": p["is_live"]} if p else None})
    groups = event_groups()
    return {"on_now": on_now, "upcoming": upcoming, "channels": sports_ch,
            "groups": [{k: v for k, v in g.items() if k != "feeds"} for g in groups]}


# ------------------------------------------------------------------ event / PPV feeds
def _event_rows():
    return db.rows("""SELECT s.key, s.name, s.grp, s.logo, s.status, s.checked_at, src.type AS src_type
                      FROM streams s JOIN sources src ON src.id=s.source_id WHERE src.enabled=1""")


def _feed_out(r):
    name = (r["name"] or "").strip()
    ended = bool(ENDED.match(name))
    return {"key": r["key"], "name": name, "group": r["grp"], "logo": r["logo"],
            "status": r["status"], "checked": r["checked_at"], "ended": ended,
            "slot": ended or bool(GENERIC_SLOT.match(name) or EMPTY_SLOT.search(name))}


def _group_name(r, kind):
    return r["grp"] or ("PPV" if kind == "ppv" else "Events")


_groups_cache = {"t": 0, "data": None, "src": None}


def event_groups():
    if _groups_cache["data"] is not None and _groups_cache["src"] is _base() and time.time() - _groups_cache["t"] < 60:
        return _groups_cache["data"]
    groups = {}
    for r in _event_rows():
        if HEADER.search(r["name"] or "") or not in_region(r["grp"]):
            continue
        kind = group_kind(r["grp"], r["name"])
        if not kind:
            continue
        name = _group_name(r, kind)
        g = groups.setdefault(name, {"name": name, "kind": kind, "feeds": [], "checkable": 0})
        g["feeds"].append(_feed_out(r))
        if r["src_type"] != "xtream" or config.get("probe_xtream"):
            g["checkable"] += 1
    for g in groups.values():
        g["feeds"].sort(key=lambda f: (f["status"] != "ok", f["slot"], _natural(f["name"])))
        g["count"] = len(g["feeds"])
        g["live"] = sum(f["status"] == "ok" for f in g["feeds"])
        g["named"] = sum(not f["slot"] for f in g["feeds"])
        g["checked"] = max((f["checked"] for f in g["feeds"]), default=0)
    out = sorted(groups.values(), key=lambda g: (g["named"] == 0, _natural(g["name"])))
    _groups_cache.update(t=time.time(), data=out, src=_base())
    return out


def group_feeds(name):
    return next((g for g in event_groups() if g["name"] == name), None)


# on-demand "which event feeds are live right now"
check_state = {"running": False, "group": "", "done": 0, "total": 0, "live": 0, "finished": 0}
_check_lock = threading.Lock()


def start_check(group=None):
    """Probe the event feeds (of one group, or all). Xtream feeds only if probe_xtream is on,
    because providers limit connections."""
    with _check_lock:
        if check_state["running"]:
            return False
        rows = []
        for r in _event_rows():
            kind = group_kind(r["grp"], r["name"])
            if kind and in_region(r["grp"]) and not HEADER.search(r["name"] or "") \
                    and (not group or _group_name(r, kind) == group) \
                    and (r["src_type"] != "xtream" or config.get("probe_xtream")):
                rows.append(r)
        if not rows:
            return False
        check_state.update(running=True, group=group or "", done=0, total=len(rows), live=0)

    def run():
        try:
            def prog(d, t):
                check_state["done"] = d
            todo = [{"key": r["key"], "url": r["url"]} for r in guide.stream_rows([r["key"] for r in rows])]
            ok, n = asyncio.run(probe.probe_many(todo, prog, conc=40, tmo=6))
            check_state.update(done=n, live=ok)
        finally:
            _groups_cache["data"] = None
            check_state.update(running=False, finished=int(time.time()))
    threading.Thread(target=run, daemon=True).start()
    return True


# ------------------------------------------------------------------ search
_norm_cache = {"src": None, "names": {}}


def _base():
    """The guide's cached channel list; it is replaced whenever channels change, so caches key on it."""
    guide.channels(True)
    return guide._cache["all"]


def _chan_norm(chans):
    """norm() of every channel name, worked out once per channel list (47k channels is slow)."""
    if _norm_cache["src"] is not _base():
        _norm_cache.update(src=_base(), names={id(c): norm(c["name"]) or (c["name"] or "").lower() for c in _base()})
    return _norm_cache["names"]


def _chan_score(qn, ql, c, n):
    lab = (c["label"] or "").lower()
    if ql.isdigit():
        s = str(c["number"])
        return 100 if s == ql else (90 if s.startswith(ql) else 0)
    if qn in (n, lab):
        return 100
    if n.startswith(qn):
        return 95
    if f" {qn}" in f" {n}" or lab.startswith(ql):
        return 90
    if qn in n:
        return 80
    return 0


_ee_cache = {"src": None, "ids": set()}


def _event_extras(allc):
    """Extra channels that are really event/PPV feeds (Xtream numbers those too)."""
    if _ee_cache["src"] is not _base():
        _ee_cache.update(src=_base(), ids={id(c) for c in allc if c["kind"] == "extra" and group_kind(c.get("group") or "")})
    return _ee_cache["ids"]


def search(q, limit=40):
    ql = q.strip().lower()
    if not ql:
        return {"channels": [], "programs": [], "feeds": []}
    qn = norm(q) or ql
    now = int(time.time())
    chans = guide.channels()

    names = _chan_norm(chans)
    feedish = _event_extras(chans)
    pool = [c for c in chans if id(c) not in feedish]
    scored = [(s, c) for c in pool if (s := _chan_score(qn, ql, c, names[id(c)]))]
    if len(scored) < 15 and len(qn) >= 4 and not ql.isdigit():
        got = {id(c) for _, c in scored}
        for _, sc, i in process.extract(qn, [names[id(c)] for c in pool], scorer=fuzz.partial_ratio,
                                        score_cutoff=88, limit=30):
            if id(pool[i]) not in got:
                scored.append((70, pool[i]))
    scored.sort(key=lambda sc: (-sc[0], sc[1]["kind"] != "lineup", sc[1]["number"]))
    top = [c for _, c in scored[:15]]
    cur = epg.programs({c["epg"] for c in top if c["epg"]}, now, now + 1)
    channels = []
    for c in top:
        p = next((x for x in cur.get(c["epg"], []) if x["start"] <= now < x["stop"]), None) if c["epg"] else None
        channels.append({**_chan_out(c), "kind": c["kind"], "now": p["title"] if p else ""})

    programs, feeds = [], []
    if len(ql) < 2 or ql.isdigit():          # "42" is a channel number, not a show
        return {"channels": channels, "programs": programs, "feeds": feeds}
    word = re.compile(r"(?<![a-z0-9])" + re.escape(ql), re.I)     # "lions" but not "millions"
    by_epg = _channels_by_epg(chans)
    like = f"%{ql}%"
    seen = set()
    for p in db.rows("""SELECT * FROM programs WHERE stop > ? AND start < ?
                        AND (lower(title) LIKE ? OR lower(subtitle) LIKE ?) ORDER BY start LIMIT 800""",
                     (now, now + 7 * 86400, like, like)):
        c = by_epg.get(p["channel"])
        k = (p["title"], p["subtitle"], p["start"])
        if not c or k in seen or not (word.search(p["title"] or "") or word.search(p["subtitle"] or "")):
            continue
        seen.add(k)
        d = dict(p)
        d.pop("channel", None)
        d["ch"] = _chan_out(c)
        programs.append(d)
    programs.sort(key=lambda p: (p["start"] > now, p["start"] if p["start"] > now else 0, p["ch"]["number"]))
    programs = _dedupe(programs, now)[:limit]

    for g in event_groups():
        for f in g["feeds"]:
            if not f["slot"] and (word.search(f["name"]) or word.search(g["name"])):
                feeds.append({**f, "kind": g["kind"]})
    feeds.sort(key=lambda f: (f["status"] != "ok", not word.search(f["name"]), _natural(f["name"])))
    return {"channels": channels, "programs": programs, "feeds": feeds[:25]}
