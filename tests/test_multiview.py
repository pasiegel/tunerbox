"""Multiview: which feed each tile plays from, moving a tile to another playlist to make room."""
import json

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, config, connections, db, guide, hls, multiview, streaming, tuner

STRONG = "http://strong.example"
OTHER = "http://other.example"


@pytest.fixture
def lineup(monkeypatch):
    """Two playlists that each allow one connection. ESPN (702) is on both (Strong first); FS1 (705)
    and NBC (710) are only on Strong; CNN (720) is only on Other."""
    s = db.run("INSERT INTO sources(name, type, url, username) VALUES ('Strong', 'xtream', ?, 'me')", (STRONG,))
    o = db.run("INSERT INTO sources(name, type, url, username) VALUES ('Other', 'xtream', ?, 'me')", (OTHER,))
    db.set_meta(f"xtream_max_{s}", 1)
    db.set_meta(f"xtream_max_{o}", 1)
    feeds = {"espn-s": (s, f"{STRONG}/1.ts"), "espn-o": (o, f"{OTHER}/1.ts"), "fs1-s": (s, f"{STRONG}/2.ts"),
             "nbc-s": (s, f"{STRONG}/3.ts"), "cnn-o": (o, f"{OTHER}/4.ts")}
    for k, (sid, url) in feeds.items():
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, ?)", (k, sid, k, url))
    for num, name, main, alts in ((702, "ESPN", "espn-s", ["espn-o"]), (705, "FS1", "fs1-s", []),
                                  (710, "NBC", "nbc-s", []), (720, "CNN", "cnn-o", [])):
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (num, name))
        db.run("INSERT INTO mappings(number, stream_key, alt_keys, mode) VALUES (?, ?, ?, 'manual')",
               (num, main, json.dumps(alts)))
    guide.invalidate()
    config.update({"multiview_enabled": True})      # off by default: these tests are about using it
    running = []
    monkeypatch.setattr(tuner, "running", lambda: list(running))
    monkeypatch.setattr(streaming, "playing", lambda: [])
    monkeypatch.setattr(hls, "playing", lambda: [])
    return {"strong": s, "other": o, "feeds": feeds, "running": running}


def on(lineup, key, *viewers):
    sid, url = lineup["feeds"][key]
    lineup["running"].append({"id": len(lineup["running"]) + 1, "url": url, "mode": "copy", "audio": "aac",
                              "viewers": list(viewers), "source_id": sid, "since": 1, "mb": 0})


def test_tile_client_ids():
    assert multiview.tile_client("abc", 1) == "abc"
    assert multiview.tile_client("abc", 3) == "abc~3"
    assert multiview.split("hls:abc~3") == ("abc", 3)
    assert multiview.split("abc") == ("abc", 1)


def test_room_left_no_move(lineup):
    on(lineup, "espn-s", "tv")
    r = multiview.plan("tv", [{"slot": 1, "number": 702, "alt": 0}], {"number": 720}, 2)
    assert r == {"ok": True, "alt": 0, "moves": [], "note": ""}


def test_moves_a_tile_to_free_the_only_playlist_that_has_it(lineup):
    # ESPN on Strong in the main picture; FS1 is only on Strong: ESPN moves to Other
    on(lineup, "espn-s", "tv")
    r = multiview.plan("tv", [{"slot": 1, "number": 702, "alt": 0}], {"number": 705}, 2)
    assert r["ok"] and r["alt"] == 0
    assert r["moves"] == [{"slot": 1, "alt": 1, "source": "Other"}]
    assert "Other" in r["note"] and "Strong" in r["note"]


def test_nothing_to_move_says_whats_using_it(lineup):
    # FS1 on Strong in the main picture has no other copy: NBC (only on Strong) can't be added
    on(lineup, "fs1-s", "tv")
    r = multiview.plan("tv", [{"slot": 1, "number": 705, "alt": 0}], {"number": 710}, 2)
    assert not r["ok"] and "Strong" in r["reason"]
    (full,) = r["full"]
    assert full["source"] == "Strong" and full["limit"] == 1
    assert full["using"][0]["slot"] == 1 and full["using"][0]["number"] == 705


def test_same_channel_twice_shares_the_tuner(lineup):
    on(lineup, "fs1-s", "tv")
    r = multiview.plan("tv", [{"slot": 1, "number": 705, "alt": 0}], {"number": 705}, 2)
    assert r["ok"] and r["alt"] == 0 and r["moves"] == []


def test_another_screens_stream_counts_and_is_never_moved(lineup):
    on(lineup, "espn-s", "kitchen")              # another TV holds Strong
    r = multiview.plan("tv", [], {"number": 705}, 2)
    assert not r["ok"]
    assert "slot" not in r["full"][0]["using"][0]
    # ...but joining what it's watching is free
    assert multiview.plan("tv", [], {"number": 702}, 2) == {"ok": True, "alt": 0, "moves": [], "note": ""}


def test_replacing_a_tile_frees_its_connection(lineup):
    on(lineup, "espn-o", "tv~2")
    on(lineup, "fs1-s", "tv")
    # tile 2 (ESPN on Other) becomes CNN, only on Other: fine, since tile 2's own stream goes
    r = multiview.plan("tv", [{"slot": 1, "number": 705, "alt": 0}, {"slot": 2, "number": 702, "alt": 1}],
                       {"number": 720}, 2)
    assert r == {"ok": True, "alt": 0, "moves": [], "note": ""}


def test_api_checks_the_request(lineup):
    c = TestClient(api.app)
    assert c.post("/api/mv/plan", json={"client": "tv~2", "want": {"number": 705}, "slot": 2}).status_code == 400
    assert c.post("/api/mv/plan", json={"client": "tv", "want": {"number": 705}, "slot": 9}).status_code == 400
    r = c.post("/api/mv/plan", json={"client": "tv", "tiles": [], "want": {"number": 705}, "slot": 2})
    assert r.status_code == 200 and r.json()["ok"]
    rooms = c.get("/api/mv/room").json()
    assert {x["source"] for x in rooms} == {"Strong", "Other"}
    assert "strong.example" not in r.text + str(rooms)


def test_setup_names_tiles(lineup):
    from tunerbox import remote
    remote.screen("tv", "Living room")
    on(lineup, "fs1-s", "tv~2")
    try:
        (t,) = connections.in_use()["tuners"]
        assert t["viewers"] == [{"kind": "tv", "name": "Living room · multiview 2"}]
    finally:
        remote._screens.pop("tv", None)


def test_capacity_adds_up_the_playlists_connections(lineup):
    # two playlists that allow one connection each: two pictures
    assert multiview.capacity() == 2
    c = TestClient(api.app)
    s = c.get("/api/settings").json()
    assert s["multiview_max"] == 2 and s["multiview_enabled"] is True
    # a third picture is refused (the screen hides the option; this is the server's own check)
    r = c.post("/api/mv/plan", json={"client": "tv", "slot": 3, "want": {"number": 720},
                                     "tiles": [{"slot": 1, "number": 702, "alt": 0}, {"slot": 2, "number": 705, "alt": 0}]})
    assert r.status_code == 409 and "room for 2" in r.json()["detail"]
    # a disabled playlist doesn't count: one connection left, so no multiview
    db.run("UPDATE sources SET enabled=0 WHERE id=?", (lineup["other"],))
    assert multiview.capacity() == 1
    r = c.post("/api/mv/plan", json={"client": "tv", "tiles": [], "want": {"number": 705}, "slot": 2})
    assert r.status_code == 409 and "one channel at a time" in r.json()["detail"]
    # one with no limit (none set, none from the provider) has room for all four
    db.set_meta(f"xtream_max_{lineup['strong']}", 0)
    assert multiview.capacity() == 4
    # the playlist's own number on its card counts over the provider's
    db.run("UPDATE sources SET max_conn=3 WHERE id=?", (lineup["strong"],))
    assert multiview.capacity() == 3


def test_my_channels_allow_four(lineup):
    # channels made from your own videos use no provider connection
    db.run("UPDATE sources SET enabled=0 WHERE id=?", (lineup["other"],))
    assert multiview.capacity() == 1
    db.run("INSERT INTO media_channels(name, number, item_count, enabled) VALUES ('Sitcoms', 900, 12, 1)")
    assert multiview.capacity() == 4


def test_multiview_is_off_by_default():
    assert config.DEFAULTS["multiview_enabled"] is False
    assert multiview.capacity() == 1


def test_setup_can_turn_multiview_off(lineup):
    c = TestClient(api.app)
    assert c.post("/api/settings", json={"multiview_enabled": False}).status_code == 200
    assert multiview.capacity() == 1
    s = c.get("/api/settings").json()
    assert s["multiview_enabled"] is False and s["multiview_max"] == 1
    r = c.post("/api/mv/plan", json={"client": "tv", "tiles": [], "want": {"number": 705}, "slot": 2})
    assert r.status_code == 409 and "turned off" in r.json()["detail"]
    # multiview_max is worked out, never saved
    assert c.post("/api/settings", json={"multiview_max": 4}).status_code == 200
    assert "multiview_max" not in config.load()


def test_a_movie_in_a_small_picture_holds_its_connection(lineup, monkeypatch):
    # tile 2 plays a movie from Strong (On Demand: it can't move to another playlist)
    movie = [{"id": 1, "url": f"{STRONG}/movie/9.mkv", "label": "A film", "client": "tv~2", "since": 0,
              "source_id": lineup["strong"]}]
    monkeypatch.setattr(streaming, "playing", lambda: list(movie))
    on(lineup, "espn-s", "tv")
    # FS1 is only on Strong: the movie has its connection, and ESPN moves to Other to make room... but
    # Strong allows one, so with the movie on it there is none
    r = multiview.plan("tv", [{"slot": 1, "number": 702, "alt": 0}], {"number": 705}, 3)
    assert r["ok"] is False
    # replacing the movie itself frees its connection
    r = multiview.plan("tv", [{"slot": 1, "number": 702, "alt": 0}], {"number": 705}, 2)
    assert r["ok"] is True and r["moves"] == [{"slot": 1, "alt": 1, "source": "Other"}]


def test_my_library_allows_four(lineup):
    # your own movies and shows play in a small picture without a provider connection
    db.run("UPDATE sources SET enabled=0 WHERE id=?", (lineup["other"],))
    assert multiview.capacity() == 1
    sh = db.run("INSERT INTO lib_shares(name, kind, type, path, enabled, count) VALUES ('Movies', 'movie', 'folder', '/m', 1, 0)")
    assert multiview.capacity() == 1                    # nothing found in it yet
    db.run("UPDATE lib_shares SET count=4453 WHERE id=?", (sh,))
    assert multiview.capacity() == 4
    db.run("UPDATE lib_shares SET enabled=0 WHERE id=?", (sh,))
    assert multiview.capacity() == 1


def test_on_demand_is_offered_with_on_demand_or_my_library(lineup):
    # the screens' "From On Demand…" (and On Demand itself) follows vod_available
    c = TestClient(api.app)
    assert c.get("/api/settings").json()["vod_available"] is False
    sh = db.run("INSERT INTO lib_shares(name, kind, type, path, enabled) VALUES ('Movies', 'movie', 'folder', '/m', 1)")
    assert c.get("/api/settings").json()["vod_available"] is True          # My Library
    db.run("UPDATE lib_shares SET enabled=0 WHERE id=?", (sh,))
    assert c.get("/api/settings").json()["vod_available"] is False
    db.run("UPDATE sources SET vod_movies=1 WHERE id=?", (lineup["strong"],))
    assert c.get("/api/settings").json()["vod_available"] is True          # a playlist's On Demand
