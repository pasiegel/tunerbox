"""HTTP checks. TestClient requests come from host "testclient", i.e. another device on the network
(not this PC), unless _as_pc() is used."""
import pytest
from fastapi.testclient import TestClient

from tunerbox import api, config, db, remote
from tunerbox.bundle import FOOTER, HEADER


@pytest.fixture
def client():
    return TestClient(api.app)        # not as a context manager: no startup jobs or network refreshes


@pytest.fixture
def as_pc(monkeypatch):
    monkeypatch.setattr(remote, "is_pc", lambda request: True)


def test_settings_validation(client):
    assert client.post("/api/settings", json={"nope": 1}).status_code == 400
    assert client.post("/api/settings", json={"hide_unmapped": "x"}).status_code == 400
    r = client.post("/api/settings", json={"hide_unmapped": False, "match_threshold": "90"})
    assert r.status_code == 200 and r.json()["match_threshold"] == 90


def test_other_devices_cannot_change_programs_or_folders(client):
    r = client.post("/api/settings", json={"ffmpeg": r"\\evil\share\x.exe"})
    assert r.status_code == 403
    assert config.get("ffmpeg") == "ffmpeg"


def test_pc_can_change_ffmpeg(client, as_pc):
    assert client.post("/api/settings", json={"ffmpeg": "/opt/ffmpeg"}).status_code == 200
    assert config.get("ffmpeg") == "/opt/ffmpeg"


def test_settings_never_expose_devices(client):
    config.update({"devices": [{"id": "a", "hash": "secret"}]})
    assert "devices" not in client.get("/api/settings").json()


def test_recording_files_need_pairing_with_the_lock_on(client):
    config.update({"require_pairing": True})
    rid = db.run("INSERT INTO recordings(number, title, start, stop, status) VALUES (8, 'S', 1, 2, 'done')")
    assert client.delete(f"/api/recordings/{rid}?delete=1").status_code == 401
    assert db.one("SELECT id FROM recordings WHERE id=?", (rid,))


def test_direct_url_needs_pairing_with_the_lock_on(client):
    config.update({"require_pairing": True})
    assert client.get("/api/direct/8").status_code == 401


def test_hdhomerun_discover(client):
    d = client.get("/discover.json").json()
    assert d["DeviceID"] == config.get("hdhr_device_id") and config.valid_device_id(d["DeviceID"])
    assert d["TunerCount"] == 4


def test_tuner_count_follows_xtream_limits(client):
    a = db.run("INSERT INTO sources(name, type, url) VALUES ('a', 'xtream', 'http://a')")
    b = db.run("INSERT INTO sources(name, type, url) VALUES ('b', 'xtream', 'http://b')")
    db.set_meta(f"xtream_max_{a}", 2)
    db.set_meta(f"xtream_max_{b}", 1)
    assert client.get("/discover.json").json()["TunerCount"] == 3
    db.run("INSERT INTO sources(name, type, url) VALUES ('c', 'm3u', 'http://c')")     # unknown limit
    assert client.get("/discover.json").json()["TunerCount"] == 4
    config.update({"tuner_count": 6})
    assert client.get("/discover.json").json()["TunerCount"] == 6


def test_tv_script_is_joined_from_parts(client):
    r = client.get("/static/tv.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.text.startswith(HEADER) and r.text.endswith(FOOTER)
    assert "function powerOn" in r.text and r.headers["cache-control"] == "no-cache"


def test_page_stamps_tv_script_with_parts_version(client):
    from tunerbox import bundle
    assert f'/static/tv.js?v={bundle.version()}"' in client.get("/").text


def test_network_name_is_for_tuner_apps_not_home_screen_apps():
    from fastapi.testclient import TestClient
    from tunerbox import api, config
    c = TestClient(api.app)
    assert c.get("/discover.json").json()["FriendlyName"] == "TunerBox"
    assert c.get("/static/manifest.webmanifest").json()["name"] == "TunerBox"
    config.update(config.clean_changes({"box_name": "  TunerBox  -  Private "}, from_pc=False))
    assert config.box_name() == "TunerBox - Private"
    assert c.get("/discover.json").json()["FriendlyName"] == "TunerBox - Private"
    assert c.get("/static/manifest.webmanifest").json()["name"] == "TunerBox"          # home-screen apps keep the name
    assert c.get("/static/remote.webmanifest").json()["name"] == "TunerBox Remote"
    assert "<title>TunerBox</title>" in c.get("/").text
    assert "<small>Setup · TunerBox - Private</small>" in c.get("/setup").text
    config.update(config.clean_changes({"box_name": "   "}, from_pc=False))          # blank goes back to the default
    assert config.box_name() == "TunerBox"
    assert config.clean_changes({"box_name": "x" * 99}, from_pc=False)["box_name"] == "x" * 40
