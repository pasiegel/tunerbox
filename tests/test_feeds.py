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
