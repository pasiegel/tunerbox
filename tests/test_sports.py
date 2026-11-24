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


@pytest.mark.parametrize("title, category, want", [
    ("Live: WWE NXT", "", True),                       # wrestling the guide files as nothing / "Series"
    ("WWE Monday Night Raw", "Series", True),
    ("All Elite Wrestling: Dynamite", "Series", True),
    ("TNA Wrestling", "Special", True),
    ("Lucha Libre IWRG", "", True),
    ("UFC Fight Night", "", True),
    ("PBR Team Series", "Series", True),
    ("Rodeo", "", True),
    ("Fight Sports: World Championship Kickboxing", "Martial arts", True),
    ("NBA Basketball", "Sports", True),                # the category still counts
    ("Raw Questions Relevant Answers", "", False),     # a bare "Raw" or "Impact" isn't wrestling
    ("World Impact With Dr. Billy Wilson", "", False),
    ("WWE Legends", "Series", False),                  # shows about the sport
    ("UFC Countdown", "", False),
    ("The Wrestler", "Movie", False),
    ("Seinfeld", "Sitcom", False),
])
def test_is_sport_program_by_title(title, category, want):
    assert sports.is_sport_program({"title": title, "category": category}) is want


@pytest.mark.parametrize("title, genres, want, sport", [
    ("Some Promotion Weekly", "Series|Pro wrestling", True, "Wrestling"),    # the guide's second category says
    ("Fight Sports: World Championship Kickboxing", "Series|Martial arts|Boxing", True, "MMA"),
    ("Canadian Finals Rodeo", "Series|Rodeo", True, "Rodeo"),
    ("NFL Films Presents", "Series|Football|Documentary", False, ""),        # about the sport, not the sport
    ("The NFL Today", "Sports talk|Football", False, ""),
    ("IMPACT in 60", "Series|Pro wrestling", False, ""),
    ("A Fistful of Dynamite", "Movie|Western", False, ""),
])
def test_is_sport_program_by_any_category(title, genres, want, sport):
    p = {"title": title, "category": genres.split("|")[0], "genres": genres, "subtitle": ""}
    assert sports.is_sport_program(p) is want
    if want:
        assert sports.sport_type(p) == sport


def test_on_now_has_wrestling_with_no_category():
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    now = int(time.time())
    for n, title, cat in [(2, "Live: WWE NXT", ""), (3, "Raw Questions Relevant Answers", ""),
                          (4, "All Elite Wrestling: Dynamite", "Series")]:
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, 'http://s')", (f"k{n}", sid, f"C{n}"))
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (n, f"C{n}"))
        db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, ?, 'manual')", (n, f"k{n}"))
        db.run("INSERT INTO programs(channel, start, stop, title, category) VALUES (?, ?, ?, ?, ?)",
               (f"gn:{n}", now - 600, now + 3600, title, cat))
    guide.invalidate()
    on_now = sports.overview()["on_now"]
    assert sorted(p["title"] for p in on_now) == ["All Elite Wrestling: Dynamite", "Live: WWE NXT"]
    assert {p["sport"] for p in on_now} == {"Wrestling"}


def test_on_now_uses_the_guides_other_categories():
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    now = int(time.time())
    for n, title, genres in [(2, "Saturday Night Brawl", "Series|Pro wrestling"), (3, "Cooking Live", "Series|Cooking")]:
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, 'http://s')", (f"k{n}", sid, f"C{n}"))
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (n, f"C{n}"))
        db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, ?, 'manual')", (n, f"k{n}"))
        db.run("INSERT INTO programs(channel, start, stop, title, category) VALUES (?, ?, ?, ?, 'Series')",
               (f"gn:{n}", now - 600, now + 3600, title))
        db.run("INSERT INTO program_genres VALUES (?, ?, ?, ?)", (f"gn:{n}", now - 600, now + 3600, genres))
    guide.invalidate()
    on_now = sports.overview()["on_now"]
    assert [(p["title"], p["sport"], p["category"]) for p in on_now] == [("Saturday Night Brawl", "Wrestling", "Series")]
    assert "genres" not in on_now[0]                        # the rows the TV gets keep their keys


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
    db.run("INSERT INTO streams(key, source_id, name, url, grp) VALUES ('f2', ?, ?, 'http://f', 'US| NFL PPV')",
           (sid, "NFL | 01 - 1pm Cowboys @ Eagles"))
    db.run("INSERT INTO streams(key, source_id, name, url, grp) VALUES ('f3', ?, ?, 'http://f', 'US| ESPN+ PPV')",
           (sid, "US (ESPN+ 204) | Football: NFL Football: Dallas Cowboys vs. Philadelphia Eagles"))
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
                    ("nfl:1", 5, True),         # found by team names though the listing has no category; one row, not two
                    ("nba:4", None, True)]      # no listing: plays from the event feed
    nfl = d["on_now"][1]
    assert (nfl["is_live"], nfl["sport"], nfl["event"]["score"]) == (1, "Football", "DAL 14, PHI 21")
    assert d["on_now"][0]["stop"] > now
    assert d["on_now"][2]["feed"]["key"] == "f1"
    assert (nfl["heading"], nfl["matchup"]) == ("NFL", "Dallas Cowboys at Philadelphia Eagles")
    assert (nfl["feed"]["key"], nfl["feed"]["kind"]) == ("f2", "ppv")    # on channel 5, and on a PPV feed as a second choice
    # every channel and feed that has the game is offered; the league's own feed group comes first
    assert [c["number"] for c in nfl["also"]] == [6] and [f["key"] for f in nfl["feeds"]] == ["f2", "f3"]

    # one tab at a time gives the same lists
    same = lambda rows: [{k: v for k, v in p.items() if k != "stop"} for p in rows]     # a game running long ends "now + 30 min"
    assert same(sports.overview("now")["on_now"]) == same(d["on_now"]) and "upcoming" not in sports.overview("now")
    assert same(sports.overview("up")["upcoming"]) == same(d["upcoming"]) and "on_now" not in sports.overview("up")
    assert sports.overview("ch") == {"channels": d["channels"]} and sports.overview("ev") == {"groups": d["groups"]}

    up = [(p["event"]["id"], p["ch"] and p["ch"]["number"]) for p in d["upcoming"]]
    assert up == [("nhl:5", None), ("ncaaf:3", 8)]               # the Bruins game: listed, no channel
    assert d["upcoming"][0]["event"]["on"] == ["ESPN+"]

    config.update({"sports_show_unmatched": False})
    assert [p["event"]["id"] for p in sports.overview()["upcoming"]] == ["ncaaf:3"]
    config.update({"sports_leagues": []})                            # off: the guide alone, as before
    d = sports.overview()
    assert [(p["ch"]["number"], "event" in p) for p in d["on_now"]] == [(6, False)]


_EV = {"name": "Iona Gaels at Hofstra Pride", "league_name": "Men's College Basketball", "heading": "College Basketball"}


@pytest.mark.parametrize("title, subtitle, event, want", [
    ("Baloncesto Universitario", "Iona vs. Hofstra", _EV, ("College Basketball", "Iona Gaels at Hofstra Pride")),
    ("Iona Gaels at Hofstra Pride", "Men's College Basketball", _EV, ("College Basketball", "Iona Gaels at Hofstra Pride")),
    ("College Basketball", "NCAA Basketball", _EV, ("College Basketball", "Iona Gaels at Hofstra Pride")),
    ("College Basketball", "ManningCast", _EV, ("College Basketball", "Iona Gaels at Hofstra Pride (ManningCast)")),
    ("NHL Hockey", "Bruins at Red Wings", None, ("NHL Hockey", "Bruins at Red Wings")),     # no scoreboard game: as listed
])
def test_heading(title, subtitle, event, want):
    assert sports.heading({"title": title, "subtitle": subtitle, "event": event}) == want


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


# ---------------------------------------------------------------- favorite teams
def _fav_team(name, nick, loc, abbr, tid, score=""):
    return {**_team(name, nick, loc, abbr, score), "id": tid}


def test_favorite_teams_first_and_alerted():
    now = int(time.time())
    h = 3600
    lions = _fav_team("Detroit Lions", "Lions", "Detroit", "DET", "8", "7")
    bears = _fav_team("Chicago Bears", "Bears", "Chicago", "CHI", "3", "3")
    eagles, cowboys = {**EAGLES, "id": "21"}, {**COWBOYS, "id": "6"}
    _channel(5, "FOX", [(now - h, now + 2 * h, "NFL Football", "Dallas Cowboys at Philadelphia Eagles", "Sports")])
    _channel(6, "CBS", [(now - h // 2, now + 2 * h, "NFL Football", "Chicago Bears at Detroit Lions", "Sports")])
    _channel(7, "NBC", [(now + 2 * h, now + 5 * h, "NFL Football", "Detroit Lions at Chicago Bears", "Sports"),
                        (now + 30 * h, now + 33 * h, "NFL Football", "Chicago Bears at Detroit Lions", "Sports")])
    _store(_game(1, "nfl", now - h, eagles, cowboys, "in", "3rd 5:21"),
           _game(2, "nfl", now - h // 2, lions, bears, "in", "1st 2:00"),
           _game(3, "nfl", now + 2 * h, bears, lions),
           _game(4, "nfl", now + 30 * h, lions, bears))               # too far off to pop up yet
    config.update({"sports_leagues": [], "sports_teams": ["nfl:8"]})   # NFL not picked: the Lions bring it in
    sports._alerts.update(sig=None)
    guide.invalidate()
    assert scoreboard.selected() == ["nfl"]

    on_now = sports.overview()["on_now"]
    # the Lions game started later and isn't LIVE-first by start time, but a favorite comes first
    assert [p["event"]["id"] for p in on_now] == ["nfl:2", "nfl:1"]
    assert on_now[0]["event"]["fav"] == ["Detroit Lions"] and on_now[1]["event"]["fav"] == []

    alerts = sports.favorite_alerts(now)
    assert [(p["event"]["id"], p["ch"]["number"]) for p in alerts] == [("nfl:2", 6), ("nfl:3", 7)]

    config.update({"sports_teams": []})
    assert sports.favorite_alerts(now) == []


def test_parse_event_keeps_team_ids():
    raw = {"id": "9", "date": "2026-11-13T01:15Z", "status": {"type": {"state": "pre"}}, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "8", "displayName": "Detroit Lions"}},
        {"homeAway": "away", "team": {"id": "3", "displayName": "Chicago Bears"}}]}]}
    ev = scoreboard.parse_event("nfl", raw)
    assert (ev["home"]["id"], ev["away"]["id"]) == ("8", "3")
    config.update({"sports_teams": ["nfl:3"]})
    assert scoreboard.favorite_teams(ev) == ["Chicago Bears"]
    assert scoreboard.favorite_teams({**ev, "league": "ncaaf"}) == []        # same id, other league: not it


def test_team_setting_is_cleaned():
    got = config.clean_changes({"sports_teams": ["NFL:8", " nfl:8 ", "ncaaf:333", "bad", ""]}, from_pc=False)
    assert got == {"sports_teams": ["ncaaf:333", "nfl:8"]}


def test_team_list_is_kept(monkeypatch):
    calls = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"sports": [{"leagues": [{"teams": [
                {"team": {"id": "8", "displayName": "Detroit Lions", "abbreviation": "DET", "logos": [{"href": "http://l/det.png"}]}},
                {"team": {"id": "3", "displayName": "Chicago Bears", "abbreviation": "CHI"}}]}]}]}

    monkeypatch.setattr(scoreboard.httpx, "get", lambda *a, **k: calls.append(k["params"]) or R())
    scoreboard._teams_mem.clear()
    want = [{"id": "3", "name": "Chicago Bears", "abbr": "CHI", "logo": ""},
            {"id": "8", "name": "Detroit Lions", "abbr": "DET", "logo": "http://l/det.png"}]
    assert scoreboard.teams("nfl") == want
    scoreboard._teams_mem.clear()                     # read back from the database, not downloaded again
    assert scoreboard.teams("nfl") == want and len(calls) == 1


def test_public_game_has_both_teams_logos_and_colours():
    raw = {"id": "7", "date": "2026-11-13T01:15Z", "status": {"type": {"state": "pre"}}, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "14", "displayName": "Miami Heat", "abbreviation": "MIA",
                                      "logo": "http://l/mia.png", "color": "98002E", "alternateColor": "f9a01b"}},
        {"homeAway": "away", "team": {"id": "8", "displayName": "Detroit Pistons", "abbreviation": "DET",
                                      "logo": "http://l/det.png", "color": "#1d428a;x", "alternateColor": "c8102e"}}]}]}
    teams = scoreboard.public(scoreboard.parse_event("nba", raw))["teams"]
    # away first, as in "Detroit Pistons at Miami Heat"; a colour that isn't plain hex is dropped
    assert teams == [{"name": "Detroit Pistons", "abbr": "DET", "logo": "http://l/det.png", "color": "", "score": ""},
                     {"name": "Miami Heat", "abbr": "MIA", "logo": "http://l/mia.png", "color": "98002e", "score": ""}]
    raw["competitions"][0]["competitors"][1]["team"]["color"] = "98002e"     # same colour: the home team uses its other one
    teams = scoreboard.public(scoreboard.parse_event("nba", raw))["teams"]
    assert [t["color"] for t in teams] == ["98002e", "f9a01b"]
    raw["status"]["type"].update(state="in", shortDetail="Q3 5:21")
    for c, sc in zip(raw["competitions"][0]["competitors"], ("88", "92")):
        c["score"] = sc
    assert [t["score"] for t in scoreboard.public(scoreboard.parse_event("nba", raw))["teams"]] == ["92", "88"]   # away, home


@pytest.mark.parametrize("title, category, genres, is_live, want", [
    ("Live: WWE NXT", "", "", 0, "live"),
    ("WWE NXT", "", "", 1, "live"),
    ("NBA Basketball", "Sports", "", 0, "on"),                 # the guide doesn't say: neither live nor a replay
    ("Cubs Classics", "Sports", "", 0, "replay"),
    ("TNA No Surrender 2012", "Special", "Special|Pro wrestling", 0, "replay"),   # an old event
    ("Fight Sports: Boxing", "Sports", "Sports|Classic Sport Event|Boxing", 0, "replay"),
    ("Mile High Postgame Live", "Sports", "", 1, "show"),      # live, but a show about the game
    ("The Dan Le Batard Show", "Sports talk", "", 1, "show"),
    ("NFL Films Presents", "Series", "Series|Football|Documentary", 0, "show"),
    (f"{time.localtime().tm_year - 1} College Football Playoff", "Sports", "", 0, "on"),   # last year's: can be live in January
])
def test_program_kind(title, category, genres, is_live, want):
    assert sports.program_kind({"title": title, "category": category, "is_live": is_live}, genres) == want



def _live(league, home, away, period, clock):
    return {"league": league, "state": "in", "home": {"score": str(home)}, "away": {"score": str(away)},
            "period": period, "clock": clock}


@pytest.mark.parametrize("ev, close, ot", [
    (_live("ncaab", 54, 56, 2, 130), True, False),        # men's college basketball: two halves, 2:10 left
    (_live("ncaab", 54, 56, 1, 130), False, False),       # end of the first half: not late
    (_live("nba", 90, 104, 4, 60), False, False),         # 14 points: not close
    (_live("nba", 100, 100, 5, 200), True, True),         # overtime
    (_live("nfl", 17, 21, 4, 400), True, False),
    (_live("nhl", 2, 1, 3, 500), True, False),
    (_live("mlb", 3, 2, 8, 0), True, False),              # late innings
    (_live("epl", 1, 1, 2, 80 * 60), True, False),        # soccer's clock counts up
    (_live("epl", 1, 1, 2, 50 * 60), False, False),
    ({**_live("nba", 100, 100, 4, 60), "state": "post"}, False, False),
])
def test_close_games(ev, close, ot):
    assert scoreboard.is_close(ev) is close and scoreboard.overtime(ev) is ot


def test_sports_layout_setting():
    assert config.clean_changes({"sports_layout": "enhanced"}, from_pc=False) == {"sports_layout": "enhanced"}
    with pytest.raises(ValueError):
        config.clean_changes({"sports_layout": "fancy"}, from_pc=False)


# ---------------------------------------------------------------- team names in the guide grid
TEXAS = _team("Texas Longhorns", "Longhorns", "Texas", "TEX", short="Texas")
TAMU = _team("Texas A&M Aggies", "Aggies", "Texas A&M", "TA&M", short="Texas A&M")
DUKE = _team("Duke Blue Devils", "Blue Devils", "Duke", "DUKE", short="Duke")
UNC = _team("North Carolina Tar Heels", "Tar Heels", "North Carolina", "UNC", short="North Carolina")


def _guide(numbers, start, stop):
    from fastapi.testclient import TestClient
    from tunerbox import api
    nums = ",".join(map(str, numbers))
    g = TestClient(api.app).get(f"/api/guide?start={start}&stop={stop}&numbers={nums}").json()
    return {int(n): [(p["title"], p["matchup"]) for p in lst] for n, lst in g.items()}


def test_guide_names_the_teams():
    now = int(time.time()) // 60 * 60
    h = 3600
    _channel(401, "FS2", [(now - h, now + 2 * h, "College Football", "", "Sports"),           # nothing but the network
                          (now + 2 * h, now + 3 * h, "Fox College Football Postgame", "", "Sports")])
    _channel(40, "ESPN2", [(now - h, now + h, "College Basketball", "", "Sports")])           # two games on ESPN2: can't tell
    _channel(41, "ESPN", [(now - h, now + 2 * h, "NFL Football", "Dallas Cowboys at Philadelphia Eagles", "Sports"),
                          (now + 2 * h, now + 4 * h, "College Football", "Michigan vs. Ohio State", "Sports")])  # not on the scoreboard
    _channel(42, "HGTV", [(now - h, now + h, "House Hunters", "Smith vs. Jones", "Reality")])
    _channel(43, "SEC", [(now - h, now + h, "Women's College Basketball", "", "Sports"),       # "SEC" is ESPN's "SECN"
                         (now + h, now + 3 * h, "Texas at Texas A&M", "", "Sports")])         # the title says it already
    _store(_game(1, "ncaaf", now - h + 120, TAMU, TEXAS, "in", on=["FS2"]),
           _game(2, "nfl", now - h, EAGLES, COWBOYS, "in"),
           _game(3, "ncaab", now - h, UNC, DUKE, "in", on=["ESPN2"]),
           _game(4, "ncaab", now - h, OSU, MICHIGAN, "in", on=["ESPN2"]),
           _game(5, "ncaaw", now - h, DUKE, UNC, "in", on=["SECN"]),
           _game(6, "ncaab", now - h, TAMU, TEXAS, "in", on=["SECN"]),          # men's: not the women's listing
           _game(7, "ncaaf", now + h, TAMU, TEXAS, on=["SECN"]))
    config.update({"sports_leagues": ["ncaaf", "nfl", "ncaab", "ncaaw"]})
    guide.invalidate()
    g = _guide([401, 40, 41, 42, 43], now, now + 4 * h)
    assert g[401] == [("College Football", "Texas at Texas A&M"), ("Fox College Football Postgame", "")]
    assert g[40] == [("College Basketball", "")]
    assert g[41] == [("NFL Football", "Cowboys at Eagles"), ("College Football", "Michigan vs. Ohio State")]
    assert g[42] == [("House Hunters", "")]                     # not sport: its subtitle stays out of the grid
    assert g[43] == [("Women's College Basketball", "North Carolina at Duke"), ("Texas at Texas A&M", "")]


def test_guide_team_names_without_the_scoreboard():
    now = int(time.time()) // 60 * 60
    _channel(401, "FS2", [(now - 3600, now + 3600, "College Football", "Texas at Texas A&M", "Sports"),
                          (now + 3600, now + 7200, "College Football", "", "Sports")])
    config.update({"sports_leagues": []})
    guide.invalidate()
    assert _guide([401], now, now + 7200)[401] == [("College Football", "Texas at Texas A&M"), ("College Football", "")]


def test_short_title():
    assert scoreboard.short_title(_game(1, "nfl", 0, EAGLES, COWBOYS)) == "Cowboys at Eagles"
    assert scoreboard.short_title(_game(1, "mls", 0, EAGLES, COWBOYS)) == "Eagles vs Cowboys"
