"""Optional features left out when not in use: Recordings (Setup › Settings › DVR) and On Demand."""
import time

from fastapi.testclient import TestClient

from tunerbox import api, config, db, dvr, home, sports


def _recordings(now):
    db.run("INSERT INTO recordings(number, title, start, stop, status, file) VALUES (4, 'Done', ?, ?, 'done', 'x.mp4')",
           (now - 9000, now - 7200))
    db.run("INSERT INTO recordings(number, title, start, stop, status, file) VALUES (4, 'Later', ?, ?, 'scheduled', '')",
           (now + 3600, now + 5400))
    db.run("INSERT INTO reminders(number, channel_name, title, start, stop) VALUES (7, 'Ch 7', 'Big Game', ?, ?)",
           (now + 600, now + 4000))
    dvr.progress(1, 300, 1800)


def test_recordings_on_by_default():
    assert config.get("dvr_enabled") is True and dvr.enabled()


def test_recordings_off_records_nothing(monkeypatch):
    config.update({"dvr_enabled": False})
    now = int(time.time())
    started = []
    monkeypatch.setattr(dvr, "_start", started.append)
    db.run("INSERT INTO recordings(number, title, start, stop, status) VALUES (4, 'Now', ?, ?, 'scheduled')", (now - 60, now + 600))
    db.run("INSERT INTO recordings(number, title, start, stop, status) VALUES (4, 'Over', ?, ?, 'scheduled')", (now - 900, now - 60))
    dvr.tick()
    assert started == []
    rows = {r["title"]: r for r in db.rows("SELECT title, status, error FROM recordings")}
    assert rows["Now"]["status"] == "scheduled"                       # starts if they're turned back on in time
    assert rows["Over"]["status"] == "failed" and "turned off" in rows["Over"]["error"]
    assert dvr.apply_rules() == 0
    config.update({"dvr_enabled": True})
    dvr.tick()
    assert [r["title"] for r in started] == ["Now"]


def test_recording_requests_are_refused_when_off():
    config.update({"dvr_enabled": False})
    c = TestClient(api.app)
    r = c.post("/api/recordings/manual", json={"number": 4, "start": int(time.time()), "minutes": 5})
    assert r.status_code == 409 and "turned off" in r.json()["detail"]
    assert c.post("/api/recordings", json={"number": 4, "start": int(time.time())}).status_code == 409
    assert c.get("/api/recordings").status_code == 200                # what's recorded can still be listed


def test_home_leaves_recordings_out_but_keeps_reminders(monkeypatch):
    monkeypatch.setattr(sports, "overview", lambda tab: {"on_now": []})
    now = int(time.time())
    _recordings(now)
    on = home.overview()
    assert on["recorded"] and any(x["type"] == "rec" for x in on["continue"]) and any(x["type"] == "rec" for x in on["coming"])
    config.update({"dvr_enabled": False})
    off = home.overview()
    assert off["recorded"] == [] and not any(x["type"] == "rec" for x in off["continue"])
    assert [x["type"] for x in off["coming"]] == ["remind"]


def test_settings_say_whether_on_demand_is_set_up():
    c = TestClient(api.app)
    assert c.get("/api/settings").json()["vod_available"] is False
    db.run("INSERT INTO sources(name, type, url, vod_movies) VALUES ('p', 'xtream', 'http://x', 1)")
    assert c.get("/api/settings").json()["vod_available"] is True
    db.run("UPDATE sources SET enabled=0")
    assert c.get("/api/settings").json()["vod_available"] is False       # a turned-off playlist doesn't count


def test_vod_available_is_read_only():
    out = config.clean_changes({"vod_available": True, "dvr_enabled": False}, from_pc=False)
    assert out == {"dvr_enabled": False}                                   # ignored, never saved
