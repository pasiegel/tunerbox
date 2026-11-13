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
                     "hidden_groups": ["AR| KIDS", "UK| NEWS"], "xtream_format": "m3u8", "video_mode": "roku720",
                     "on_demand": {"movies": False, "series": False, "hidden": [], "tags": None}}
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


def test_on_demand_settings_go_with_the_playlist(client, monkeypatch):
    _two_playlists()
    db.run("""UPDATE sources SET vod_movies=1, vod_series=0, vod_hidden='[["movie", "FOR ADULTS"]]' WHERE name='Eagle'""")
    monkeypatch.setattr(remote, "trusted", lambda _r: True)
    data = client.get("/api/sources/export").json()
    assert data["playlists"][0]["on_demand"] == {"movies": True, "series": False, "hidden": [["movie", "FOR ADULTS"]], "tags": None}
    db.run("DELETE FROM sources")
    assert client.post("/api/sources/import", json=data).json()["added"] == 2
    eagle = db.one("SELECT * FROM sources WHERE name='Eagle'")
    assert (eagle["vod_movies"], eagle["vod_series"], json.loads(eagle["vod_hidden"])) == (1, 0, [["movie", "FOR ADULTS"]])
    # a file from before On Demand leaves those settings as they are
    for pl in data["playlists"]:
        pl.pop("on_demand")
    db.run("UPDATE sources SET vod_series=1 WHERE name='Eagle'")
    assert client.post("/api/sources/import", json=data).json()["updated"] == 2
    assert db.one("SELECT vod_movies, vod_series FROM sources WHERE name='Eagle'") == {"vod_movies": 1, "vod_series": 1}
