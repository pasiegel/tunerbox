from fastapi.testclient import TestClient

from tunerbox import api, gamestats, scoreboard, sports


def _team(tid, abbr, name, ha, score, rec="5-7", color="0150b5"):
    return {"homeAway": ha, "score": score, "record": [{"summary": rec}],
            "team": {"id": tid, "abbreviation": abbr, "displayName": name, "color": color, "logos": [{"href": f"http://l/{abbr}.png"}]}}


def _ath(name, num, stats, dnp=False):
    return {"athlete": {"shortName": name, "jersey": num, "position": {"abbreviation": "G"}}, "stats": stats, "didNotPlay": dnp}


NBA_LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST"]


def _nba(state="in"):
    return {
        "header": {"competitions": [{"status": {"type": {"state": state, "shortDetail": "0:38 - 3rd"}},
                                     "competitors": [_team("2", "BOS", "Boston Celtics", "home", "74", "11-2", "008348"),
                                                     _team("19", "ORL", "Orlando Magic", "away", "73")]}]},
        "boxscore": {
            "teams": [{"team": {"id": "19"}, "statistics": [{"label": "FG", "displayValue": "25-61"}, {"label": "Rebounds", "displayValue": "30"},
                                                            {"label": "Odd one", "displayValue": "1"}]},
                      {"team": {"id": "2"}, "statistics": [{"label": "FG", "displayValue": "28-66"}, {"label": "Rebounds", "displayValue": "36"}]}],
            "players": [
                {"team": {"id": "2"}, "statistics": [{"labels": NBA_LABELS, "athletes": [
                    _ath("P. George", "13", ["25", "19", "6-11", "4-7", "3-6", "7", "2"]),
                    _ath("J. Tatum", "0", ["30", "20", "8-20", "2-7", "2-3", "11", "4"]),
                    _ath("Bench Guy", "99", [], dnp=True)]}]},
                {"team": {"id": "19"}, "statistics": [{"labels": NBA_LABELS, "athletes": [
                    _ath("F. Wagner", "22", ["25", "16", "6-17", "0-2", "4-4", "5", "4"])]}]},
            ]},
        "plays": [{"text": "Tatum makes layup", "scoringPlay": True, "clock": {"displayValue": "1:02"}, "period": {"displayValue": "3rd Quarter"}},
                  {"text": "White defensive rebound", "clock": {"displayValue": "0:40"}, "period": {"number": 3}}],
    }


def test_parse_basketball_away_team_first_sorted_by_points():
    d = gamestats.parse("nba", _nba())
    assert [t["abbr"] for t in d["teams"]] == ["ORL", "BOS"]
    assert d["teams"][1]["score"] == "74" and d["teams"][1]["color"] == "008348"
    orl, bos = d["players"]
    assert orl["abbr"] == "ORL" and bos["abbr"] == "BOS"
    t = bos["tables"][0]
    assert t["cols"] == ["PTS", "REB", "AST", "FG"]
    assert [r["name"] for r in t["rows"]] == ["J. Tatum", "P. George"]      # most points first, DNP left out
    assert t["rows"][0]["stats"] == ["20", "11", "4", "8-20"]
    # only stats both teams have, in the order the sport lists them
    assert d["team_stats"] == [{"label": "FG", "values": ["25-61", "28-66"]}, {"label": "Rebounds", "values": ["30", "36"]}]
    # newest play first; a period with only a number gets the sport's tag
    assert d["plays"][0] == {"text": "White defensive rebound", "when": "Q3 · 0:40", "score": False}
    assert d["plays"][1]["score"] is True


def test_parse_before_the_game_has_no_stats():
    d = gamestats.parse("nba", _nba(state="pre"))
    assert d["players"] == [] and d["team_stats"] == [] and d["plays"] == []
    assert all(t["score"] == "" for t in d["teams"])


def test_parse_soccer_home_team_first():
    d = gamestats.parse("epl", _nba())
    assert [t["abbr"] for t in d["teams"]] == ["BOS", "ORL"]
    assert d["players"] == []          # no player tables for soccer


def test_parse_football_groups():
    raw = _nba()
    raw["boxscore"]["players"] = [{"team": {"id": "2"}, "statistics": [
        {"name": "passing", "labels": ["C/ATT", "YDS", "AVG", "TD", "INT"], "athletes": [_ath("QB", "10", ["3/6", "58", "9.7", "0", "0"])]},
        {"name": "rushing", "labels": ["CAR", "YDS", "AVG", "TD"], "athletes": [_ath("RB1", "8", ["4", "26", "6.5", "0"]),
                                                                                 _ath("RB2", "22", ["3", "29", "9.7", "1"])]},
        {"name": "defensive", "labels": ["TOT"], "athletes": [_ath("LB", "50", ["4"])]}]}]
    d = gamestats.parse("nfl", raw)
    tables = d["players"][0]["tables"]
    assert [t["title"] for t in tables] == ["Passing", "Rushing"]
    assert tables[0]["cols"] == ["C/ATT", "YDS", "TD", "INT"]
    assert [r["name"] for r in tables[1]["rows"]] == ["RB2", "RB1"]       # most yards first


def test_game_for_channel_and_feed(monkeypatch):
    gamestats._lookup["t"] = 0
    monkeypatch.setattr(sports, "overview", lambda tab="": {"on_now": [
        {"event": {"id": "nba:1"}, "ch": {"number": 206}, "also": [{"number": 207}], "feeds": [{"key": "f1"}]},
        {"event": None, "ch": {"number": 300}},
    ]})
    assert gamestats.game_for(ch="206") == "nba:1"
    assert gamestats.game_for(ch=207) == "nba:1"
    assert gamestats.game_for(feed="f1") == "nba:1"
    assert gamestats.game_for(ch="300") is None
    gamestats._lookup["t"] = 0


def test_summary_rejects_unknown_ids():
    assert gamestats.summary("nope:1") is None
    assert gamestats.summary("nba:abc") is None


def test_api_stats(monkeypatch):
    monkeypatch.setattr(gamestats, "game_for", lambda ch="", feed="": "nba:1" if ch == "206" else None)
    monkeypatch.setattr(gamestats, "summary", lambda gid: {"id": gid, "state": "in"})
    c = TestClient(api.app)
    monkeypatch.setattr(gamestats, "others", lambda gid=None: [{"id": "nba:2", "for": gid}])
    monkeypatch.setattr(scoreboard, "selected", lambda: ["nba"])
    assert c.get("/api/sports/stats?ch=206").json() == {"game": {"id": "nba:1", "state": "in"}, "others": [{"id": "nba:2", "for": "nba:1"}], "enabled": True}
    # no game on this channel: still every live game, for the panel's Live scores tab
    assert c.get("/api/sports/stats?ch=5").json() == {"game": None, "others": [{"id": "nba:2", "for": None}], "enabled": True}
    # no leagues or favorite teams picked: the panel says where to turn sports on
    monkeypatch.setattr(scoreboard, "selected", lambda: [])
    assert c.get("/api/sports/stats?ch=206").json() == {"game": None, "others": [], "enabled": False}


def _ev(gid, sport="Basketball", league="NBA", state="in", fav=None):
    return {"id": gid, "name": f"Game {gid}", "sport": sport, "league": league, "state": state, "detail": "Q2", "fav": fav or [],
            "teams": [{"abbr": "AAA", "color": "111111", "score": "10"}, {"abbr": "BBB", "color": "", "score": "12"}]}


def test_others_every_live_game_this_league_first(monkeypatch):
    gamestats._lookup["t"] = 0
    feed = {"key": "f9", "name": "NBA 03", "group": "NBA League Pass", "kind": "events"}
    monkeypatch.setattr(sports, "overview", lambda tab="": {"on_now": [
        {"event": _ev("nba:1"), "ch": {"number": 206}},                       # the game being watched
        {"event": _ev("nba:2"), "ch": None, "feed": None},                    # live, nothing has it
        {"event": _ev("ncaab:3", league="NCAAM"), "ch": {"number": 210}},     # same sport, other league (Division I)
        {"event": _ev("ncaaw:8", league="NCAAW"), "ch": {"number": 214}},     # women's college: left out
        {"event": _ev("nba:4"), "ch": None, "feed": feed, "feeds": [feed]},
        {"event": _ev("nba:5", state="post"), "ch": {"number": 211}},         # over
        {"event": _ev("nhl:6", sport="Hockey", league="NHL"), "ch": {"number": 212}},
        {"event": _ev("nba:7", state="pre"), "ch": {"number": 213}},          # started since the stored scoreboard
    ]})
    started = {"id": "nba:7", "state": "in", "detail": "Q1 11:40",
               "home": {"name": "H", "abbr": "HHH", "color": "222222", "score": "2"},
               "away": {"name": "A", "abbr": "AWY", "color": "", "score": "0"}, "league": "nba"}
    monkeypatch.setattr(gamestats, "_live_scores", lambda lg, now: {"nba:7": started} if lg == "nba" else {})
    out = gamestats.others("nba:1")
    # this league first (watchable before not), then the same sport (Division I men's college), then the
    # rest; women's college basketball is left out
    assert [o["id"] for o in out] == ["nba:4", "nba:7", "nba:2", "ncaab:3", "nhl:6"]
    assert out[0]["feeds"] == [feed] and out[0]["ch"] is None
    assert out[1]["detail"] == "Q1 11:40" and [t["abbr"] for t in out[1]["teams"]] == ["AWY", "HHH"]
    assert out[2]["ch"] is None and out[2]["feeds"] == [] and out[2]["also"] == []
    assert out[3]["league"] == "NCAAM" and out[4]["league"] == "NHL" and out[4]["ch"] == {"number": 212}
    gamestats._lookup["t"] = 0


def test_others_without_a_game_lists_every_live_game(monkeypatch):
    gamestats._lookup["t"] = 0
    monkeypatch.setattr(sports, "overview", lambda tab="": {"on_now": [
        {"event": _ev("nhl:6", sport="Hockey", league="NHL"), "ch": {"number": 212}},
        {"event": _ev("nba:1", fav=["BOS"]), "ch": None},
        {"event": _ev("nba:5", state="post"), "ch": {"number": 211}},
    ]})
    monkeypatch.setattr(gamestats, "_live_scores", lambda lg, now: {})
    assert [o["id"] for o in gamestats.others()] == ["nba:1", "nhl:6"]      # your team's game first, finished ones left out
    gamestats._lookup["t"] = 0


def test_lookup_is_rebuilt_once_and_read_without_the_lock(monkeypatch):
    gamestats._lookup["t"] = 0
    calls = []
    monkeypatch.setattr(sports, "overview", lambda tab="": calls.append(1) or {"on_now": [
        {"event": _ev("nba:1"), "ch": {"number": 206}}]})
    assert gamestats.game_for(ch="206") == "nba:1"
    assert gamestats.game_for(ch="206") == "nba:1"
    assert len(calls) == 1                       # kept for LOOKUP_TTL
    with gamestats._lookup_lock:                 # a rebuild under way elsewhere doesn't stop a fresh lookup being read
        assert gamestats.game_for(ch="206") == "nba:1"
    gamestats._lookup["t"] = 0


def test_others_lists_at_most_15(monkeypatch):
    gamestats._lookup["t"] = 0
    games = [{"event": _ev(f"nba:{i}"), "ch": None} for i in range(10, 30)]
    games.append({"event": _ev("nhl:1", sport="Hockey", league="NHL", fav=["BOS"]), "ch": {"number": 212}})
    monkeypatch.setattr(sports, "overview", lambda tab="": {"on_now": games})
    monkeypatch.setattr(gamestats, "_live_scores", lambda lg, now: {})
    out = gamestats.others("nba:10")
    assert len(out) == gamestats.MAX_OTHERS == 15
    assert out[0]["id"] == "nhl:1"                # your team's game is never the one cut
    gamestats._lookup["t"] = 0


def test_favorites_always_first_even_from_a_skipped_league(monkeypatch):
    gamestats._lookup["t"] = 0
    monkeypatch.setattr(sports, "overview", lambda tab="": {"on_now": [
        {"event": _ev("nba:2"), "ch": {"number": 206}},                                   # this league, watchable
        {"event": _ev("ncaaw:8", league="NCAAW", fav=["UConn Huskies"]), "ch": None},    # skipped league, but your team
        {"event": _ev("nhl:6", sport="Hockey", league="NHL", fav=["Boston Bruins"]), "ch": None},
        {"event": _ev("ncaaw:9", league="NCAAW"), "ch": {"number": 214}},                # skipped league: left out
    ]})
    monkeypatch.setattr(gamestats, "_live_scores", lambda lg, now: {})
    for gid in ("nba:1", None):                      # watching an NBA game, or no game at all
        out = gamestats.others(gid)
        assert [o["id"] for o in out][:2] in (["ncaaw:8", "nhl:6"], ["nhl:6", "ncaaw:8"]) and all(o["fav"] for o in out[:2])
        assert "ncaaw:9" not in [o["id"] for o in out]
    gamestats._lookup["t"] = 0
