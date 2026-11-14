"""Setup › Tuners: running tuners and On Demand streams, named for people and counted per playlist."""
import threading

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, connections, db, guide, hls, remote, streaming, tuner

XT = "http://prov.example:8080"


@pytest.fixture
def setup(monkeypatch):
    a = db.run("INSERT INTO sources(name, type, url, username) VALUES ('Prov', 'xtream', ?, 'bob')", (XT,))
    b = db.run("INSERT INTO sources(name, type, url) VALUES ('Free', 'm3u', 'http://free.example/list.m3u')")
    db.set_meta(f"xtream_max_{a}", 2)
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES ('k8', ?, 'US: ESPN HD', ?)", (a, f"{XT}/live/bob/pw/1.ts"))
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES ('kf', ?, 'PPV 1: Fight Night', 'http://cdn.free/9.ts')", (b,))
    db.run("INSERT INTO lineup(number, name) VALUES (8, 'ESPN')")
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (8, 'k8', 'manual')")
    rid = db.run("INSERT INTO recordings(number, title, start, stop, status) VALUES (8, 'SportsCenter', 1, 2, 'recording')")
    guide.invalidate()
    remote.screen("tv1", "Living room")
    monkeypatch.setattr(tuner, "running", lambda: [
        {"url": f"{XT}/live/bob/pw/1.ts", "mode": "copy", "audio": "aac", "since": 100, "mb": 12.5,
         "viewers": ["tv1", f"dvr:{rid}", ""]},
        {"url": "http://cdn.free/9.ts", "mode": "copy", "audio": "aac", "since": 50, "mb": 1.0, "viewers": ["setup-x"]},
    ])
    monkeypatch.setattr(streaming, "playing", lambda: [
        {"url": f"{XT}/movie/bob/pw/77.mp4", "label": "Heat · 1995", "client": "tv1", "since": 200}])
    monkeypatch.setattr(hls, "playing", lambda: [])
    yield a, b
    remote._screens.pop("tv1", None)


def test_in_use(setup):
    a, b = setup
    d = connections.in_use()
    feed, chan, movie = d["tuners"]                       # oldest first
    assert chan["kind"] == "channel" and chan["title"] == "8 ESPN" and chan["source_id"] == a
    assert [v["name"] for v in chan["viewers"]] == ["Living room", "Recording: SportsCenter", "Another app (Plex, VLC…)"]
    assert feed["kind"] == "feed" and feed["title"] == "PPV 1: Fight Night" and feed["source_id"] == b
    assert feed["viewers"] == [{"kind": "setup", "name": "Setup preview"}]
    # On Demand isn't in the streams table: matched to the Xtream account by server and username
    assert movie["kind"] == "ondemand" and movie["title"] == "Heat · 1995" and movie["source_id"] == a
    srcs = {s["id"]: s for s in d["sources"]}
    assert srcs[a]["used"] == 2 and srcs[a]["limit"] == 2
    assert srcs[b]["used"] == 1 and srcs[b]["limit"] == 0      # m3u: limit unknown


def test_never_sends_provider_addresses(setup):
    body = TestClient(api.app).get("/api/tuners").text
    assert "prov.example" not in body and "/bob/" not in body and "cdn.free" not in body


def test_nothing_running(monkeypatch):
    monkeypatch.setattr(tuner, "running", lambda: [])
    monkeypatch.setattr(streaming, "playing", lambda: [])
    monkeypatch.setattr(hls, "playing", lambda: [])
    assert connections.in_use() == {"sources": [], "tuners": []}


def test_running_lists_tuner_viewers(monkeypatch):
    t = tuner.Tuner.__new__(tuner.Tuner)                  # no ffmpeg: just the fields running() reads
    t.url, t.mode, t.audio, t.ended, t.sent, t.started, t.source_id = "u", "copy", "aac", False, 2_500_000, 123.9, 3
    t.lock = threading.Lock()
    t.subs = [type("S", (), {"name": "tv1"})(), type("S", (), {"name": "dvr:4"})()]
    monkeypatch.setattr(tuner, "_tuners", [t])
    assert tuner.running() == [{"url": "u", "mode": "copy", "audio": "aac", "viewers": ["tv1", "dvr:4"], "source_id": 3,
                                "since": 123, "mb": 2.5}]


def test_playlist_limit_is_saved_exported_and_offered_to_plex():
    from tunerbox import sources
    c = TestClient(api.app)
    a = db.run("INSERT INTO sources(name, type, url) VALUES ('Plain', 'm3u', 'http://plain/list.m3u')")
    b = db.run("INSERT INTO sources(name, type, url) VALUES ('Antenna', 'hdhomerun', 'http://10.0.0.5')")
    db.set_meta(f"hdhr_tuners_{b}", 4)
    assert sources.tuner_count() == 4                      # the M3U has no limit: Plex gets the default
    assert c.patch(f"/api/sources/{a}", json={"max_conn": 2}).status_code == 200
    assert sources.tuner_count() == 6                      # 2 set by hand + the antenna's 4 tuners
    listed = {s["id"]: s for s in c.get("/api/sources").json()}
    assert listed[a]["max_conn"] == 2 and listed[a]["conn_auto"] == 0 and listed[b]["conn_auto"] == 4
    assert c.patch(f"/api/sources/{a}", json={"max_conn": "lots"}).status_code == 400
    exported = sources.export_sources()
    assert {p["name"]: p["max_connections"] for p in exported["playlists"]} == {"Plain": 2, "Antenna": 0}
    db.run("UPDATE sources SET max_conn=0 WHERE id=?", (a,))
    sources.import_sources(exported)
    assert db.one("SELECT max_conn FROM sources WHERE id=?", (a,))["max_conn"] == 2
