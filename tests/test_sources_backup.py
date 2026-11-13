"""Setup > Playlists > Export / Import: a JSON backup that restores here or on another PC."""
import json

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, db, jobs, remote, sources


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(jobs, "scan", lambda *a, **k: True)          # no real playlist download
    return TestClient(api.app)


def _two_playlists():
    db.run("""INSERT INTO sources(name, type, url, username, password, enabled, exclude_groups, hidden_groups,
              xtream_format, video_mode) VALUES ('Eagle', 'xtream', 'http://host:8080', 'me', 'secret', 1,
              'XXX', '["UK| NEWS", "AR| KIDS"]', 'm3u8', 'roku720')""")
    db.run("INSERT INTO sources(name, type, url, enabled) VALUES ('Free TV', 'm3u', 'https://x.example/list.m3u', 0)")


def test_export_has_every_setting_including_the_login(client, monkeypatch):
    _two_playlists()
    monkeypatch.setattr(remote, "trusted", lambda _r: True)
    r = client.get("/api/sources/export")
    assert r.status_code == 200
    assert 'attachment; filename="tunerbox-playlists-' in r.headers["content-disposition"]
    data = r.json()
    assert data["kind"] == "tunerbox-playlists" and len(data["playlists"]) == 2
    eagle = data["playlists"][0]
    assert eagle == {"name": "Eagle", "type": "xtream", "url": "http://host:8080", "username": "me",
                     "password": "secret", "enabled": True, "exclude_groups": "XXX",
                     "hidden_groups": ["AR| KIDS", "UK| NEWS"], "xtream_format": "m3u8", "video_mode": "roku720"}
    assert data["playlists"][1]["enabled"] is False


def test_export_refused_to_an_unpaired_device(client, monkeypatch):
    _two_playlists()
    monkeypatch.setattr(remote, "trusted", lambda _r: False)
    r = client.get("/api/sources/export")
    assert r.status_code == 403 and "secret" not in r.text


def test_import_into_an_empty_tunerbox_restores_everything(client):
    _two_playlists()
    backup = sources.export_sources()
    db.run("DELETE FROM sources")
    r = client.post("/api/sources/import", json=backup)
    assert r.status_code == 200
    assert r.json() == {"added": 2, "updated": 0, "skipped": [], "job": "scan"}
    assert sources.export_sources()["playlists"] == backup["playlists"]


def test_import_updates_a_playlist_it_already_has_instead_of_adding_it_twice(client):
    _two_playlists()
    backup = sources.export_sources()
    backup["playlists"][0].update(name="Eagle 4K", password="new", url="http://HOST:8080/")   # same server, other spelling
    r = client.post("/api/sources/import", json=backup).json()
    assert (r["added"], r["updated"]) == (0, 2)
    rows = db.rows("SELECT name, password FROM sources ORDER BY id")
    assert len(rows) == 2 and (rows[0]["name"], rows[0]["password"]) == ("Eagle 4K", "new")


def test_import_skips_bad_entries_and_rejects_other_files(client):
    r = client.post("/api/sources/import", json={"kind": "tunerbox-playlists", "playlists": [
        {"name": "ok", "type": "m3u", "url": "http://a/list.m3u", "video_mode": "rm -rf", "hidden_groups": "nope"},
        {"name": "no address", "type": "m3u"}, {"name": "odd", "type": "ftp", "url": "x"}, "junk"]}).json()
    assert r["added"] == 1 and len(r["skipped"]) == 3
    row = db.one("SELECT video_mode, hidden_groups FROM sources")
    assert (row["video_mode"], json.loads(row["hidden_groups"])) == ("", [])
    assert client.post("/api/sources/import", json={"settings": {}}).status_code == 400
