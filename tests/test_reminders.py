import time

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, db, guide


@pytest.fixture
def client():
    return TestClient(api.app)


def _channel_with_show(start, stop, title="Big Game", number=7):
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES ('k7', ?, 'ESPN', 'http://s')", (sid,))
    db.run("INSERT INTO lineup(number, name) VALUES (?, 'ESPN')", (number,))
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, 'k7', 'manual')", (number,))
    db.run("INSERT INTO programs(channel, start, stop, title, subtitle, category) VALUES (?, ?, ?, ?, 'Lions at Bears', 'Sports')",
           (f"gn:{number}", start, stop, title))
    guide.invalidate()


def test_set_list_and_cancel_a_reminder(client):
    now = int(time.time())
    _channel_with_show(now + 3600, now + 7200)
    r = client.post("/api/reminders", json={"number": 7, "start": now + 3600 + 60})    # any time inside the show
    assert r.status_code == 200
    rem = r.json()
    assert (rem["title"], rem["subtitle"], rem["start"], rem["channel_name"]) == ("Big Game", "Lions at Bears", now + 3600, "ESPN")
    assert client.post("/api/reminders", json={"number": 7, "start": now + 3600}).json()["id"] == rem["id"]   # no duplicate
    assert [x["id"] for x in client.get("/api/reminders").json()] == [rem["id"]]
    g = client.get(f"/api/guide?start={now}&stop={now + 4 * 3600}&numbers=7").json()
    assert [p["remind"] for p in g["7"]] == [True]
    client.delete(f"/api/reminders/{rem['id']}")
    assert client.get("/api/reminders").json() == []


def test_no_guide_listing_no_reminder(client):
    now = int(time.time())
    _channel_with_show(now + 3600, now + 7200)
    assert client.post("/api/reminders", json={"number": 7, "start": now + 9 * 3600}).status_code == 400
    assert client.post("/api/reminders", json={"number": 99, "start": now + 3600}).status_code == 400


def test_old_reminders_are_cleared(client):
    now = int(time.time())
    db.run("INSERT INTO reminders(number, title, start, stop) VALUES (7, 'old', ?, ?)", (now - 4 * 3600, now - 2 * 3600))
    db.run("INSERT INTO reminders(number, title, start, stop) VALUES (7, 'on now', ?, ?)", (now - 600, now + 600))
    assert [r["title"] for r in client.get("/api/reminders").json()] == ["on now"]
    assert db.one("SELECT count(*) n FROM reminders")["n"] == 1
