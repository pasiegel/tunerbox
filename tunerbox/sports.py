"""Search plus the Sports & PPV area.

* Sports on now / coming up come from the guide (program categories).
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

from . import config, db, epg, guide, probe
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


def _sport_programs(start, stop):
    chans = guide.channels()
    by_epg = _channels_by_epg(chans)
    out = []
    for p in db.rows("SELECT * FROM programs WHERE stop > ? AND start < ? AND category != '' ORDER BY start",
                     (start, stop)):
        c = by_epg.get(p["channel"])
        if not c or not is_sport_program(p):
            continue
        q = dict(p)
        q.pop("channel", None)
        q["ch"] = _chan_out(c)
        out.append(q)
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


def overview():
    now = int(time.time())
    progs = _sport_programs(now, now + 2 * 86400)
    progs.sort(key=lambda p: (p["start"] > now, p["start"] if p["start"] > now else 0, p["ch"]["number"]))
    progs = _dedupe(progs, now)
    on_now = [p for p in progs if p["start"] <= now]
    upcoming = [p for p in progs if p["start"] > now][:200]
    on_now.sort(key=lambda p: (-p["is_live"], p["ch"]["number"]))

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
