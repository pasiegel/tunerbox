"""The Home screen's one request."""
import time

from fastapi.testclient import TestClient

from tunerbox import api, config, db, dvr, guide, home, sports


def _chan(n, epg=None):
    return {"number": n, "name": f"Ch {n}", "label": f"C{n}", "logo": "", "epg": epg, "mapped": True, "streams": []}


def _setup(monkeypatch):
    chans = {4: _chan(4, "gn:4"), 7: _chan(7, "gn:7"), 9: _chan(9)}
    monkeypatch.setattr(guide, "channel", chans.get)
    monkeypatch.setattr(sports, "overview", lambda tab: {"on_now": [
        {"title": "Lions at Bears", "ch": {"number": 7}, "start": 1, "stop": 2},
        {"title": "Nobody has it", "ch": None, "event": {"on": []}, "feeds": []}]})
    now = int(time.time())
    db.run("INSERT INTO programs(channel, start, stop, title, image) VALUES ('gn:4', ?, ?, 'News at 6', 'https://i/x.jpg')",
           (now - 600, now + 600))
    return now


def test_favorites_recent_and_games(monkeypatch):
    _setup(monkeypatch)
    config.update({"favorites": [4]})
    d = home.overview([4, 9, 99])
    assert [c["number"] for c in d["favorites"]] == [4]
    assert d["favorites"][0]["now"]["title"] == "News at 6" and d["favorites"][0]["now"]["image"] == "https://i/x.jpg"
    assert [c["number"] for c in d["recent"]] == [9]          # favorites aren't repeated; unknown channels left out
    assert d["recent"][0]["now"] is None
    assert [g["title"] for g in d["sports"]] == ["Lions at Bears"]   # only games you can watch


def test_recordings_in_progress_recorded_and_coming_up(monkeypatch):
    now = _setup(monkeypatch)
    for i, (status, start) in enumerate([("done", now - 9000), ("done", now - 5000), ("scheduled", now + 3600)]):
        db.run("INSERT INTO recordings(number, title, start, stop, status, file, image) VALUES (4, ?, ?, ?, ?, ?, ?)",
               (f"Show {i}", start, start + 1800, status, "x.mp4" if status == "done" else "", "https://i/r.jpg" if i == 0 else ""))
    db.run("INSERT INTO reminders(number, channel_name, title, start, stop) VALUES (7, 'Ch 7', 'Big Game', ?, ?)",
           (now + 600, now + 4000))
    dvr.progress(1, 300, 1800)                      # part-way through Show 0
    dvr.progress(2, 1790, 1800)                     # Show 1: as good as finished
    d = home.overview()
    assert [(x["type"], x["title"]) for x in d["continue"]] == [("rec", "Show 0")]
    assert d["continue"][0]["position"] == 300 and d["continue"][0]["art"]
    assert d["continue"][0]["file"] == "x.mp4"                      # the player needs it to play from Home
    assert db.one("SELECT watched, position FROM recordings WHERE id=2") == {"watched": 1, "position": 0}
    assert [r["title"] for r in d["recorded"]] == ["Show 1", "Show 0"]                 # newest first
    assert [(x["type"], x["title"]) for x in d["coming"]] == [("remind", "Big Game"), ("rec", "Show 2")]


def test_home_api(monkeypatch):
    _setup(monkeypatch)
    c = TestClient(api.app)
    r = c.get("/api/home?recent=9,abc,4")
    assert r.status_code == 200 and [x["number"] for x in r.json()["recent"]] == [9, 4]
    db.run("INSERT INTO recordings(number, title, start, stop, status, file) VALUES (4, 'S', 1, 2, 'done', 'x.mp4')")
    assert c.post("/api/recordings/1/progress", json={"position": 120, "duration": 1800}).status_code == 200
    assert db.one("SELECT position, duration FROM recordings WHERE id=1") == {"position": 120, "duration": 1800}
    assert c.get("/api/recordings/1/art").status_code == 404          # no picture


def test_recordings_keep_the_shows_picture(monkeypatch):
    monkeypatch.setattr(guide, "channel", lambda n: _chan(n, "gn:4"))
    db.run("INSERT INTO programs(channel, start, stop, title, image) VALUES ('gn:4', 1000, 2800, 'News', 'https://i/n.jpg')")
    assert dvr.schedule_program(4, 1000)["image"] == "https://i/n.jpg"
