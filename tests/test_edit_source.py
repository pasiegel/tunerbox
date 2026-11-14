"""Editing a playlist from its card in Setup."""
import pytest
from fastapi.testclient import TestClient

from tunerbox import api, db, jobs, sources


@pytest.fixture
def client(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "scan", lambda *a, **k: calls.append("scan") or True)
    c = TestClient(api.app)
    c.scans = calls
    return c


def _src(type_="xtream"):
    return db.run("INSERT INTO sources(name, type, url, username, password) VALUES ('old', ?, 'http://a', 'bob', 'pw')", (type_,))


def test_rename_keeps_login_and_doesnt_rescan(client):
    sid = _src()
    r = client.patch(f"/api/sources/{sid}", json={"name": "  Strong  ", "password": ""}).json()
    s = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    assert (s["name"], s["password"]) == ("Strong", "pw")          # blank password: kept
    assert r["job"] == "" and client.scans == []


def test_new_login_rescans(client):
    sid = _src()
    r = client.patch(f"/api/sources/{sid}", json={"url": "http://b ", "username": "amy", "password": "new"}).json()
    s = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    assert (s["url"], s["username"], s["password"]) == ("http://b", "amy", "new")
    assert r["job"] == "scan" and client.scans == ["scan"]


def test_empty_name_or_url_refused(client):
    sid = _src()
    assert client.patch(f"/api/sources/{sid}", json={"name": " "}).status_code == 400
    assert client.patch(f"/api/sources/{sid}", json={"url": ""}).status_code == 400
    assert db.one("SELECT name FROM sources WHERE id=?", (sid,))["name"] == "old"


def test_hdhomerun_address_is_checked(client, monkeypatch):
    sid = _src("hdhomerun")
    def info(addr, timeout=6):
        if addr != "192.168.1.50":
            raise OSError("timed out")
        return {}
    monkeypatch.setattr(sources, "hdhr_info", info)
    assert client.patch(f"/api/sources/{sid}", json={"url": "10.0.0.9"}).status_code == 400
    assert client.patch(f"/api/sources/{sid}", json={"url": "192.168.1.50"}).status_code == 200
    assert db.one("SELECT url FROM sources WHERE id=?", (sid,))["url"] == sources.hdhr_base("192.168.1.50")


def test_unknown_playlist(client):
    assert client.patch("/api/sources/999", json={"name": "x"}).status_code == 404


def test_suggest_one_playlist(client):
    a = db.run("INSERT INTO sources(name, type, url) VALUES ('strong', 'm3u', 'http://a')")
    b = db.run("INSERT INTO sources(name, type, url) VALUES ('antenna', 'hdhomerun', 'http://b')")
    db.run("INSERT INTO streams(key, source_id, name, url, norm) VALUES ('s1', ?, 'US: PBS HD', 'u1', 'pbs')", (a,))
    db.run("INSERT INTO streams(key, source_id, name, url, norm) VALUES ('s2', ?, 'WGVU-HD', 'u2', 'wgvu')", (b,))
    db.run("INSERT INTO streams(key, source_id, name, url, norm) VALUES ('s3', ?, 'PBS Kids', 'u3', 'pbs kids')", (b,))
    every = {s["key"] for s in client.get("/api/suggest", params={"q": "pbs"}).json()}
    assert {"s1", "s3"} <= every
    one = client.get("/api/suggest", params={"q": "pbs", "source_id": b}).json()
    assert one and {s["source_id"] for s in one} == {b}


def test_search_drops_lookalikes(client):
    a = db.run("INSERT INTO sources(name, type, url) VALUES ('antenna', 'm3u', 'http://a')")
    for k, n, t in [("w1", "WOTV", "ota:WOTVDT"), ("w2", "THE CW", "ota:WOTVDT2"), ("o1", "WOOD TV", "ota:WOODDT"),
                    ("o2", "CourtTV", "ota:WZPXDT2"), ("d1", "Discovery Channel", "")]:
        db.run("INSERT INTO streams(key, source_id, name, url, tvg_id, norm) VALUES (?, ?, ?, ?, ?, ?)",
               (k, a, n, "u" + k, t, sources.norm(n)))
    keys = lambda q: [s["key"] for s in client.get("/api/suggest", params={"q": q}).json()]
    assert sorted(keys("wotv")) == ["w1", "w2"]          # its subchannels, not WOOD TV or CourtTV
    assert keys("discvery") == ["d1"]                    # a typo still finds it
    assert keys("19") == []
