import json
import time

import pytest

from tunerbox import config, db, guide, scoreboard, sports


@pytest.mark.parametrize("title, category, want", [
    ("NFL Football", "Sports", "Football"),
    ("College Basketball", "Sports event", "Basketball"),
    ("Premier League", "Football", "Soccer"),          # UK-style category: the title wins
    ("Premier League Football", "Sports", "Soccer"),
    ("Lakers at Celtics", "Basketball", "Basketball"),  # nothing in the title: the category decides
    ("NASCAR Cup Series", "Sports", "Motorsports"),
    ("UFC Fight Night", "Sports", "MMA"),
    ("SportsCenter", "Sports", ""),
])
def test_sport_type(title, category, want):
    assert sports.sport_type({"title": title, "category": category, "subtitle": ""}) == want


def test_on_now_live_first_then_start_sport_name():
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    now = int(time.time())
    shows = [  # (channel, minutes ago it started, title, live)
        (2, 30, "NHL Hockey", 0),
        (3, 30, "College Basketball", 0),
        (4, 30, "NBA Basketball", 1),
        (5, 90, "Golf Central Live", 0),
        (6, 30, "SportsCenter", 0),
        (7, 90, "MLB Baseball", 0),
        (8, 60, "NFL Football", 1),
        (9, 30, "NHL Hockey Night", 1),
    ]
    for n, ago, title, live in shows:
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, 'http://s')", (f"k{n}", sid, f"C{n}"))
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (n, f"C{n}"))
        db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, ?, 'manual')", (n, f"k{n}"))
        db.run("INSERT INTO programs(channel, start, stop, title, category, is_live) VALUES (?, ?, ?, ?, 'Sports', ?)",
               (f"gn:{n}", now - ago * 60, now + 3600, title, live))
    guide.invalidate()
    on_now = sports.overview()["on_now"]
    assert [p["title"] for p in on_now] == [
        # LIVE
        "NFL Football",                                            # started 60 min ago
        "NBA Basketball", "NHL Hockey Night",                      # 30 min ago: Basketball < Hockey
        # ON
        "MLB Baseball", "Golf Central Live",                       # 90 min ago: Baseball < Golf
        "College Basketball", "NHL Hockey",                        # 30 min ago: Basketball < Hockey
        "SportsCenter",                                            # unknown sport last
    ]
    assert on_now[0]["sport"] == "Football"


# ---------------------------------------------------------------- scoreboard games
def _team(name, nick, loc, abbr, score="", short=None):
    return {"name": name, "short": short or nick, "nick": nick, "loc": loc, "abbr": abbr, "score": score}


def _game(eid, league, start, home, away, state="pre", detail="", on=()):
    return {"id": f"{league}:{eid}", "league": league, "start": start, "state": state, "detail": detail,
            "home": home, "away": away, "on": list(on)}


EAGLES = _team("Philadelphia Eagles", "Eagles", "Philadelphia", "PHI", "21")
COWBOYS = _team("Dallas Cowboys", "Cowboys", "Dallas", "DAL", "14")
MICHIGAN = _team("Michigan Wolverines", "Wolverines", "Michigan", "MICH", short="Michigan")
MSU = _team("Michigan State Spartans", "Spartans", "Michigan State", "MSU", short="Michigan St")
OSU = _team("Ohio State Buckeyes", "Buckeyes", "Ohio State", "OSU", short="Ohio State")


@pytest.mark.parametrize("home, away, title, subtitle, want", [
    (EAGLES, COWBOYS, "NFL Football", "Dallas Cowboys at Philadelphia Eagles", True),
    (EAGLES, COWBOYS, "NFL Football: Cowboys at Eagles", "", True),
    (EAGLES, COWBOYS, "Cowboys @ Eagles", "", True),
    (EAGLES, COWBOYS, "NFL Football", "Dallas Cowboys at New York Giants", False),
    (EAGLES, COWBOYS, "Eagles Postgame Live", "", False),
    (OSU, MICHIGAN, "College Football", "Michigan at Ohio State", True),
    (OSU, MICHIGAN, "College Football", "(3) Michigan vs. (1) Ohio St.", True),
    (OSU, MICHIGAN, "College Football", "Michigan State at Ohio State", False),     # not Michigan
    (OSU, MSU, "College Football", "Michigan St. at Ohio State", True),
    (OSU, MSU, "College Football", "Michigan at Ohio State", False),
])
def test_game_matches_listing(home, away, title, subtitle, want):
    keys = scoreboard.event_keys(_game(1, "nfl", 0, home, away))
    assert scoreboard.text_matches(keys, scoreboard.candidates(subtitle, title, f"{title} {subtitle}")) is want


def test_feed_names_match():
    keys = scoreboard.event_keys(_game(1, "nfl", 0, EAGLES, COWBOYS))
    assert scoreboard.text_matches(keys, scoreboard.candidates("NFL 03 | Dallas Cowboys vs Philadelphia Eagles @ 8:20 PM ET"))
    assert scoreboard.text_matches(keys, scoreboard.candidates("NFL: Cowboys vs Eagles (8:20PM) HD"))
    assert not scoreboard.text_matches(keys, scoreboard.candidates("NFL 04 | Cowboys Cheerleaders: Making the Team"))


def test_parse_event():
    raw = {"id": "401", "date": "2026-11-13T01:15Z",
           "status": {"type": {"state": "in", "shortDetail": "3rd 5:21"}},
           "competitions": [{"broadcasts": [{"market": "national", "names": ["ESPN", "ABC"]}], "competitors": [
               {"homeAway": "home", "score": "21", "team": {"displayName": "Philadelphia Eagles", "shortDisplayName": "Eagles",
                                                            "name": "Eagles", "location": "Philadelphia", "abbreviation": "PHI"}},
               {"homeAway": "away", "score": "14", "team": {"displayName": "Dallas Cowboys", "shortDisplayName": "Cowboys",
                                                            "name": "Cowboys", "location": "Dallas", "abbreviation": "DAL"}}]}]}
    ev = scoreboard.parse_event("nfl", raw)
    assert (ev["id"], ev["start"], ev["state"], ev["on"]) == ("nfl:401", 1794532500, "in", ["ESPN", "ABC"])
    pub = scoreboard.public(ev)
    assert (pub["name"], pub["score"], pub["detail"], pub["league"], pub["sport"]) == \
        ("Dallas Cowboys at Philadelphia Eagles", "DAL 14, PHI 21", "3rd 5:21", "NFL", "Football")
    raw["competitions"][0]["competitors"].pop()
    assert scoreboard.parse_event("nfl", raw) is None            # one team only: not a game


def _store(*evs):
    for ev in evs:
        db.run("INSERT INTO sports_events(id, league, day, start, data) VALUES (?, ?, '20260101', ?, ?)",
               (ev["id"], ev["league"], ev["start"], json.dumps(ev)))


def _channel(n, name, programs):
    sid = db.one("SELECT id FROM sources")
    sid = sid["id"] if sid else db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, 'http://s')", (f"k{n}", sid, name))
    db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (n, name))
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, ?, 'manual')", (n, f"k{n}"))
    for start, stop, title, subtitle, category in programs:
        db.run("INSERT INTO programs(channel, start, stop, title, subtitle, category) VALUES (?, ?, ?, ?, ?, ?)",
               (f"gn:{n}", start, stop, title, subtitle, category))


def test_scoreboard_games_in_overview():
    now = int(time.time())
    h = 3600
    _channel(5, "FOX", [(now - h, now + 2 * h, "NFL Football", "Dallas Cowboys at Philadelphia Eagles", "")])   # no category
    _channel(6, "FOX SD", [(now - h, now + 2 * h, "NFL Football", "Dallas Cowboys at Philadelphia Eagles", "Sports")])
    _channel(7, "ABC", [(now - 4 * h, now - h // 2, "College Football", "Michigan at Ohio State", "Sports"),      # ran long
                        (now - h // 2, now + h, "Local News", "", "News")])
    _channel(8, "ESPN", [(now + 3 * h, now + 6 * h, "College Football", "Michigan State at Ohio State", "Sports")])
    sid = db.one("SELECT id FROM sources")["id"]
    db.run("INSERT INTO streams(key, source_id, name, url, grp, status) VALUES ('f1', ?, ?, 'http://f', 'US| ESPN+ EVENTS', 'ok')",
           (sid, "ESPN+ 07 | Toronto Raptors vs Philadelphia 76ers"))
    sixers = _team("Philadelphia 76ers", "76ers", "Philadelphia", "PHI", "50")
    raptors = _team("Toronto Raptors", "Raptors", "Toronto", "TOR", "48")
    _store(_game(1, "nfl", now - h, EAGLES, COWBOYS, "in", "3rd 5:21"),
           _game(2, "ncaaf", now - 4 * h, OSU, MICHIGAN, "in", "4th 2:00"),
           _game(3, "ncaaf", now + 3 * h, OSU, MSU),
           _game(4, "nba", now - h // 2, sixers, raptors, "in", "2nd 1:10"),
           _game(5, "nhl", now + h, _team("Boston Bruins", "Bruins", "Boston", "BOS"),
                 _team("Detroit Red Wings", "Red Wings", "Detroit", "DET"), on=["ESPN+"]),
           _game(6, "mls", now + h, EAGLES, COWBOYS))                # league not picked in Settings
    guide.invalidate()
    d = sports.overview()

    rows = [(p["event"]["id"], p["ch"] and p["ch"]["number"], bool(p.get("feed"))) for p in d["on_now"]]
    assert rows == [("ncaaf:2", 7, False),      # its listing ended, the game didn't: still on channel 7
                    ("nfl:1", 5, False),        # found by team names though the listing has no category; one row, not two
                    ("nba:4", None, True)]      # no listing: plays from the event feed
    nfl = d["on_now"][1]
    assert (nfl["is_live"], nfl["sport"], nfl["event"]["score"]) == (1, "Football", "DAL 14, PHI 21")
    assert d["on_now"][0]["stop"] > now
    assert d["on_now"][2]["feed"]["key"] == "f1"

    up = [(p["event"]["id"], p["ch"] and p["ch"]["number"]) for p in d["upcoming"]]
    assert up == [("nhl:5", None), ("ncaaf:3", 8)]               # the Bruins game: listed, no channel
    assert d["upcoming"][0]["event"]["on"] == ["ESPN+"]

    config.update({"sports_show_unmatched": False})
    assert [p["event"]["id"] for p in sports.overview()["upcoming"]] == ["ncaaf:3"]
    config.update({"sports_leagues": []})                            # off: the guide alone, as before
    d = sports.overview()
    assert [(p["ch"]["number"], "event" in p) for p in d["on_now"]] == [(6, False)]


def test_league_setting_is_cleaned():
    assert config.clean_changes({"sports_leagues": ["NFL", " nba ", "nfl", ""]}, from_pc=False) == {"sports_leagues": ["nba", "nfl"]}
    with pytest.raises(ValueError):
        config.clean_changes({"sports_leagues": "nfl"}, from_pc=False)


def test_refresh_due(monkeypatch):
    now = int(time.time())
    assert scoreboard.due(now) == "full"                             # never fetched
    monkeypatch.setattr(scoreboard, "fetch", lambda client, key, day: [])
    assert scoreboard.refresh(True, now) > 0
    assert scoreboard.due(now + 60) is None
    assert scoreboard.due(now + scoreboard.IDLE_EVERY + 1) == "quick"
    _store(_game(1, "nfl", now - 600, EAGLES, COWBOYS, "in"))
    assert scoreboard.due(now + scoreboard.BUSY_EVERY + 1) == "quick"   # a game is on: scores every 5 minutes
    config.update({"sports_leagues": ["nfl"]})
    assert scoreboard.due(now + 60) == "full"                        # the picked leagues changed
    config.update({"sports_leagues": []})
    assert scoreboard.due(now + 10 ** 6) is None
