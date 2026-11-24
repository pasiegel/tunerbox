"""UFC and PFL: an ESPN event is a card of fights between two fighters, not a game between two teams."""
import json
import time
from datetime import datetime, timezone

from tunerbox import config, db, gamestats, guide, scoreboard, sports


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _fight(start, a, b, state="pre"):
    return {"date": _iso(start), "status": {"type": {"state": state}}, "broadcasts": [{"names": ["Paramount+"]}],
            "competitors": [{"type": "athlete", "order": 2, "athlete": {"displayName": b, "shortName": b}},
                            {"type": "athlete", "order": 1, "athlete": {"displayName": a, "shortName": a}}]}


def _raw(start, state="pre", fights_done=0):
    """A card like ESPN's: prelims at `start`, the main card three hours later, the main event last."""
    main = start + 3 * 3600
    fights = [_fight(start, "Shoaib Yousaf", "John Mitchell"), _fight(start, "Amir Albazi", "Alessandro Costa"),
              _fight(main, "Brian Ortega", "Dan Hooker"), _fight(main, "Jiří Procházka", "Navajo Stirling")]
    for f in fights[:fights_done]:
        f["status"]["type"]["state"] = "post"
    return {"id": "600062", "name": "UFC Fight Night: Procházka vs. Stirling", "date": _iso(start),
            "status": {"type": {"state": state, "shortDetail": "Final" if state == "post" else ""}}, "competitions": fights}


def test_a_card_is_a_game_between_its_main_event_fighters():
    start = 1795000000 // 60 * 60
    ev = scoreboard.parse_event("ufc", _raw(start))
    assert (ev["id"], ev["start"], ev["main"], ev["state"], ev["on"]) == ("ufc:600062", start, start + 3 * 3600, "pre", ["Paramount+"])
    assert (ev["away"]["name"], ev["home"]["name"]) == ("Jiří Procházka", "Navajo Stirling")       # by their order on the card
    pub = scoreboard.public(ev)
    assert (pub["name"], pub["score"], pub["league"], pub["sport"]) == ("UFC Fight Night: Procházka vs. Stirling", "", "UFC", "MMA")
    assert [t["name"] for t in pub["teams"]] == ["Jiří Procházka", "Navajo Stirling"]


def test_which_part_of_a_card_is_on():
    start = 1795000000 // 60 * 60
    assert scoreboard.parse_event("ufc", _raw(start, "in", 1))["detail"] == "Prelims"
    assert scoreboard.parse_event("ufc", _raw(start, "in", 2))["detail"] == "Main card"
    assert scoreboard.parse_event("ufc", _raw(start, "post", 4))["detail"] == "Final"
    raw = _raw(start)
    raw["competitions"] = []
    assert scoreboard.parse_event("ufc", raw) is None
    raw = _raw(start)
    raw["competitions"][-1]["timeValid"] = False                       # "TBD": not listed until it has a date
    assert scoreboard.parse_event("ufc", raw) is None


def test_feed_and_listing_names_match_a_card():
    keys = scoreboard.event_keys(scoreboard.parse_event("ufc", _raw(1795000000)))
    for name in ("UFC event: 1 name: UFC FIGHT NIGHT: PROCHAZKA VS STIRLING  start:2026-11-21 18:55:00",
                 "UFC Fight Night: Procházka vs. Stirling", "UFC FIGHT NIGHT: PROCHAZKA VS STIRLING PRELIMS"):
        assert scoreboard.text_matches(keys, scoreboard.candidates(name)), name
    assert not scoreboard.text_matches(keys, scoreboard.candidates("UFC QATAR: POST-FIGHT PRESS CONFERENCE"))
    assert not scoreboard.text_matches(keys, scoreboard.candidates("UFC Fight Night: Ortega vs. Volkanovski"))


def test_fight_leagues_have_no_teams_to_pick():
    by = {lg["key"]: lg for lg in scoreboard.league_list()}
    assert (by["ufc"]["sport"], by["ufc"]["teams"], by["pfl"]["teams"], by["nfl"]["teams"]) == ("MMA", False, False, True)
    assert scoreboard.teams("ufc") == []                     # not asked of ESPN, which has none
    assert gamestats.summary("ufc:600062") is None            # and no box score


def _store(ev):
    db.run("INSERT INTO sports_events(id, league, day, start, data) VALUES (?, ?, '20260101', ?, ?)",
           (ev["id"], ev["league"], ev["start"], json.dumps(ev)))


def _feed(key, name, grp="US| UFC PPV", status="ok"):
    sid = db.one("SELECT id FROM sources")
    sid = sid["id"] if sid else db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    db.run("INSERT INTO streams(key, source_id, name, url, grp, status) VALUES (?, ?, ?, 'http://f', ?, ?)",
           (key, sid, name, grp, status))


def test_a_card_on_now_plays_from_its_main_feed_prelims_second():
    now = int(time.time())
    _store(scoreboard.parse_event("ufc", _raw(now - 3600, "in", 1)))         # prelims on, main card in 2 hours
    _feed("f1", "UFC event: 3 name: UFC FIGHT NIGHT: PROCHAZKA VS STIRLING PRELIMS")
    _feed("f2", "UFC event: 1 name: UFC FIGHT NIGHT: PROCHAZKA VS STIRLING")
    _feed("f3", "UFC event: 4 name: UFC FIGHT NIGHT: PROCHAZKA VS STIRLING POST-FIGHT PRESS CONFERENCE")
    config.update({"sports_leagues": ["ufc"]})
    guide.invalidate()
    (row,) = [p for p in sports.overview()["on_now"] if p.get("event")]
    assert (row["title"], row["sport"], row["is_live"], row["event"]["detail"]) == (
        "UFC Fight Night: Procházka vs. Stirling", "MMA", 1, "Prelims")
    assert [f["key"] for f in row["feeds"]] == ["f2", "f1"]                 # not the press conference
    assert row["stop"] >= now + 5 * 3600 - 60                                # on until the main card is over (ESPN times: whole minutes)


def test_a_card_matches_its_main_card_listing_hours_after_the_prelims():
    now = int(time.time())
    h = 3600
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES ('k5', ?, 'FOX', 'http://s')", (sid,))
    db.run("INSERT INTO lineup(number, name) VALUES (5, 'FOX')")
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (5, 'k5', 'manual')")
    start = now - 3 * h - 600                                                # the main card began 10 minutes ago
    db.run("INSERT INTO programs(channel, start, stop, title, subtitle, category) VALUES "
           "('gn:5', ?, ?, 'UFC Fight Night', 'Procházka vs. Stirling', 'Series')", (start + 3 * h, start + 6 * h))
    _store(scoreboard.parse_event("ufc", _raw(start, "in", 2)))
    config.update({"sports_leagues": ["ufc"]})
    guide.invalidate()
    (row,) = sports.overview()["on_now"]
    assert (row["ch"]["number"], row["event"]["id"], row["event"]["detail"]) == (5, "ufc:600062", "Main card")
