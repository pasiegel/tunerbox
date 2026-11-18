"""Setup › Channel lineup: see every feed matched to a channel and remove the wrong ones for good."""
from fastapi.testclient import TestClient

from tunerbox import api, db, matcher, sources


def _setup(monkeypatch):
    db.run("INSERT INTO lineup(number, name) VALUES (?,?)", (202, "Cable News Network"))
    sid = sources.add_source("p", "m3u", "http://x/list.m3u")
    entries = [{"name": n, "url": f"http://s/{i}", "tvg_id": "", "logo": "", "grp": "News"}
               for i, n in enumerate(["CNN HD", "CNN", "CNN FHD"])]
    monkeypatch.setattr(sources, "fetch_m3u", lambda src: (entries, ""))
    sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    matcher.match_all()
    return TestClient(api.app)


def _ch(c):
    return next(x for x in c.get("/api/lineup").json() if x["number"] == 202)


def test_lineup_lists_every_feed_main_first(monkeypatch):
    c = _setup(monkeypatch)
    ch = _ch(c)
    assert len(ch["feeds"]) == 3 and ch["alts"] == 2 and ch["removed"] == 0
    assert ch["feeds"][0]["key"] == ch["stream"]["key"]


def test_removed_backup_stays_gone_after_rematch(monkeypatch):
    c = _setup(monkeypatch)
    ch = _ch(c)
    bad = ch["feeds"][2]["key"]
    assert c.post("/api/mapping/remove-feed", json={"number": 202, "stream_key": bad}).json()["ok"]
    ch = _ch(c)
    assert bad not in [f["key"] for f in ch["feeds"]] and len(ch["feeds"]) == 2 and ch["removed"] == 1
    matcher.match_all(force=False)                      # the removal counts as a change: matched again
    assert bad not in [f["key"] for f in _ch(c)["feeds"]]


def test_removing_the_main_feed_moves_the_first_backup_up(monkeypatch):
    c = _setup(monkeypatch)
    main, first = [f["key"] for f in _ch(c)["feeds"][:2]]
    c.post("/api/mapping/remove-feed", json={"number": 202, "stream_key": main})
    ch = _ch(c)
    assert ch["stream"]["key"] == first and ch["mode"] == "auto" and len(ch["feeds"]) == 2


def test_removing_the_last_feed_unmaps_and_restore_brings_them_back(monkeypatch):
    c = _setup(monkeypatch)
    for f in _ch(c)["feeds"]:
        c.post("/api/mapping/remove-feed", json={"number": 202, "stream_key": f["key"]})
    ch = _ch(c)
    assert ch["stream"] is None and ch["feeds"] == [] and ch["removed"] == 3
    matcher.match_all()
    assert _ch(c)["stream"] is None
    monkeypatch.setattr(api.jobs, "rematch", lambda: matcher.match_all())
    c.post("/api/mapping/restore-feeds", json={"number": 202})
    ch = _ch(c)
    assert len(ch["feeds"]) == 3 and ch["removed"] == 0


def test_only_auto_matched_feeds_can_be_removed(monkeypatch):
    c = _setup(monkeypatch)
    key = _ch(c)["stream"]["key"]
    c.post("/api/mapping", json={"number": 202, "stream_key": key, "mode": "manual"})
    assert c.post("/api/mapping/remove-feed", json={"number": 202, "stream_key": key}).status_code == 400
    assert c.post("/api/mapping/remove-feed", json={"number": 999, "stream_key": key}).status_code == 400


def test_feed_order_sets_main_and_survives_rematch(monkeypatch):
    c = _setup(monkeypatch)
    keys = [f["key"] for f in _ch(c)["feeds"]]
    want = [keys[2], keys[0], keys[1]]
    assert c.post("/api/mapping/feed-order", json={"number": 202, "keys": want}).json()["ok"]
    ch = _ch(c)
    assert [f["key"] for f in ch["feeds"]] == want and ch["stream"]["key"] == keys[2] and ch["ordered"]
    matcher.match_all()                                  # a full re-match keeps the chosen order
    assert [f["key"] for f in _ch(c)["feeds"]] == want
    matcher.match_all(force=False)
    assert [f["key"] for f in _ch(c)["feeds"]] == want


def test_feed_order_reset_goes_back_to_best_match(monkeypatch):
    c = _setup(monkeypatch)
    keys = [f["key"] for f in _ch(c)["feeds"]]
    c.post("/api/mapping/feed-order", json={"number": 202, "keys": keys[::-1]})
    monkeypatch.setattr(api.jobs, "rematch", lambda: matcher.match_all())
    c.post("/api/mapping/feed-order", json={"number": 202, "keys": []})
    ch = _ch(c)
    assert [f["key"] for f in ch["feeds"]] == keys and not ch["ordered"]


def test_feed_order_rejects_a_list_that_isnt_the_channels_feeds(monkeypatch):
    c = _setup(monkeypatch)
    keys = [f["key"] for f in _ch(c)["feeds"]]
    for bad in (keys[:2], keys + ["nope"], [keys[0], keys[0], keys[1]]):
        assert c.post("/api/mapping/feed-order", json={"number": 202, "keys": bad}).status_code == 400
    c.post("/api/mapping", json={"number": 202, "stream_key": keys[0], "mode": "manual"})
    assert c.post("/api/mapping/feed-order", json={"number": 202, "keys": [keys[0]]}).status_code == 400


def test_removing_an_ordered_feed_keeps_the_rest_in_order(monkeypatch):
    c = _setup(monkeypatch)
    keys = [f["key"] for f in _ch(c)["feeds"]]
    want = keys[::-1]
    c.post("/api/mapping/feed-order", json={"number": 202, "keys": want})
    c.post("/api/mapping/remove-feed", json={"number": 202, "stream_key": want[0]})
    assert [f["key"] for f in _ch(c)["feeds"]] == want[1:]
    matcher.match_all()
    assert [f["key"] for f in _ch(c)["feeds"]] == want[1:]


def test_channel_feeds_lists_them_in_play_order_without_addresses(monkeypatch):
    c = _setup(monkeypatch)
    keys = [f["key"] for f in _ch(c)["feeds"]]
    c.post("/api/mapping/feed-order", json={"number": 202, "keys": keys[::-1]})
    feeds = c.get("/api/channels/202/feeds").json()
    names = {f["key"]: f["name"] for f in _ch(c)["feeds"]}
    assert [f["alt"] for f in feeds] == [0, 1, 2]
    assert [f["name"] for f in feeds] == [names[k] for k in keys[::-1]]
    assert all(f["source"] == "p" and "url" not in f for f in feeds)
    assert c.get("/api/channels/99999/feeds").status_code == 404


def test_feeds_on_a_switched_off_playlist_are_left_out(monkeypatch):
    c = _setup(monkeypatch)
    sid = sources.add_source("q", "m3u", "http://y/list.m3u")
    monkeypatch.setattr(sources, "fetch_m3u", lambda src: ([{"name": "CNN East", "url": "http://t/9", "tvg_id": "",
                                                              "logo": "", "grp": "News"}], ""))
    sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    matcher.match_all()
    keys = [f["key"] for f in _ch(c)["feeds"]]
    off = next(k for k in keys if k.startswith(f"{sid}:"))
    db.run("UPDATE sources SET enabled=0 WHERE id=?", (sid,))
    api.guide.invalidate()
    shown = [f["key"] for f in _ch(c)["feeds"]]
    assert off not in shown and len(shown) == len(keys) - 1
    assert off not in [f["name"] for f in c.get("/api/channels/202/feeds").json()]
    assert c.post("/api/mapping/feed-order", json={"number": 202, "keys": shown[::-1]}).json()["ok"]
    assert [f["key"] for f in _ch(c)["feeds"]] == shown[::-1]
    db.run("UPDATE sources SET enabled=1 WHERE id=?", (sid,))     # switched back on: it's still there, last
    assert [f["key"] for f in _ch(c)["feeds"]] == shown[::-1] + [off]
