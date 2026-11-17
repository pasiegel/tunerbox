"""Setup › Tuners: running tuners and On Demand streams, named for people and counted per playlist."""
import threading
import time

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
    assert tuner.running() == [{"id": id(t), "url": "u", "mode": "copy", "audio": "aac", "viewers": ["tv1", "dvr:4"], "source_id": 3,
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


def test_rows_have_stop_ids(setup):
    feed, chan, movie = connections.in_use()["tuners"]
    assert chan["id"].startswith("t") and feed["id"].startswith("t") and movie["id"].startswith("s")


def test_stop_tuner_cancels_its_recording(setup, monkeypatch):
    rid = db.one("SELECT id FROM recordings")["id"]
    monkeypatch.setattr(tuner, "running", lambda: [
        {"id": 7, "url": f"{XT}/live/bob/pw/1.ts", "mode": "copy", "audio": "aac", "since": 1, "mb": 0,
         "viewers": ["tv1", f"dvr:{rid}"], "source_id": setup[0]}])
    stopped = []
    # the recording is cancelled before the tuner stops, or the DVR could reopen the stream
    monkeypatch.setattr(tuner, "stop_id", lambda tid: stopped.append(tid) or
                        db.one("SELECT status FROM recordings WHERE id=?", (rid,))["status"] == "cancelled")
    r = TestClient(api.app).post("/api/tuners/stop", json={"id": "t7"})
    assert r.status_code == 200 and r.json()["recordings"] == [rid] and stopped == [7]
    assert db.one("SELECT status FROM recordings WHERE id=?", (rid,))["status"] == "cancelled"


def test_stop_on_demand(setup, monkeypatch):
    stopped = []
    monkeypatch.setattr(streaming, "playing", lambda: [
        {"id": 42, "url": "http://x/m.mp4", "label": "Heat", "client": "tv9", "since": 1}])
    monkeypatch.setattr(streaming, "stop_id", lambda pid: stopped.append(pid) or True)
    assert TestClient(api.app).post("/api/tuners/stop", json={"id": "s42"}).json()["recordings"] == []
    assert stopped == [42]


def test_stop_unknown_is_404(setup):
    c = TestClient(api.app)
    for bad in ("t999", "s999", "hnope", "", "x1", "tabc"):
        assert c.post("/api/tuners/stop", json={"id": bad}).status_code == 404


def test_stop_real_streaming_session(monkeypatch):
    """streaming.stop_id forgets the session and kills its ffmpeg."""
    class P:
        killed = False
        def poll(self): return None
        def kill(self): P.killed = True
    p = P()
    streaming._sessions["tvX"] = p
    streaming._playing[id(p)] = {"url": "u", "label": "", "client": "tvX", "since": 0, "source_id": None, "proc": p}
    assert [x["id"] for x in streaming.playing()] == [id(p)] and "proc" not in streaming.playing()[0]
    monkeypatch.setattr(streaming, "kill", lambda proc: proc.kill())
    assert streaming.stop_id(id(p))
    threading.Event().wait(0.2)
    assert P.killed and "tvX" not in streaming._sessions and id(p) not in streaming._playing
    assert not streaming.stop_id(id(p))


@pytest.mark.parametrize("ua,name", [
    ("Roku/DVP-14.5 (14.5.4.5934-46)", "app:Roku@10.0.0.5"),
    ("VLC/3.0.20 LibVLC/3.0.20", "app:VLC@10.0.0.5"),
    ("Lavf/60.3.100", ""),                       # plain ffmpeg: could be anything
    ("", ""),
])
def test_app_reader_from_user_agent(ua, name):
    assert connections.app_reader(ua, "10.0.0.5") == name


def test_roku_shown_as_roku(monkeypatch):
    monkeypatch.setattr(tuner, "running", lambda: [
        {"id": 1, "url": "http://x/1.ts", "mode": "copy", "audio": "aac", "since": 1, "mb": 0,
         "viewers": ["app:Roku@192.168.1.50", "app:VLC@192.168.1.9"]}])
    monkeypatch.setattr(streaming, "playing", lambda: [
        {"id": 2, "url": "http://x/m.mp4", "label": "Heat", "client": "roku-abc123", "since": 2}])
    monkeypatch.setattr(hls, "playing", lambda: [])
    chan, movie = connections.in_use()["tuners"]
    assert chan["viewers"] == [{"kind": "roku", "name": "Roku · 192.168.1.50"}, {"kind": "app", "name": "VLC · 192.168.1.9"}]
    assert movie["viewers"] == [{"kind": "roku", "name": "Roku"}]


def test_live_stream_names_roku_reader(monkeypatch):
    """/api/stream from the Roku (no client=) joins the tuner as the Roku, not as "Another app"."""
    db.run("INSERT INTO sources(name, type, url) VALUES ('Free', 'm3u', 'http://free.example/l.m3u')")
    seen = {}

    def fake_open(url, client_id=None, mode=None, audio="aac", buffer=None, source_id=None, name=""):
        seen.update(client=client_id, name=name)
        raise connections.Full({"id": 1, "name": "Free"}, 1, 1)      # stop before any ffmpeg starts
    monkeypatch.setattr(tuner, "open_for", fake_open)
    monkeypatch.setattr(guide, "channel", lambda n: {"streams": ["k"]})
    monkeypatch.setattr(guide, "stream_rows", lambda keys: [{"url": "http://cdn/1.ts", "source_id": 1}])
    r = TestClient(api.app).get("/api/stream/5", headers={"User-Agent": "Roku/DVP-14.5 (14.5.4)"})
    assert r.status_code == 503 and seen == {"client": None, "name": "app:Roku@testclient"}


def test_roku_named_from_its_stream(monkeypatch):
    """The Roku app sends client=roku-<id>&name=<its name>: Tuners shows that name."""
    seen = {}

    def fake_open(url, client_id=None, mode=None, audio="aac", buffer=None, source_id=None, name=""):
        seen.update(client=client_id, name=name)
        raise connections.Full({"id": 1, "name": "Free"}, 1, 1)
    monkeypatch.setattr(tuner, "open_for", fake_open)
    monkeypatch.setattr(guide, "channel", lambda n: {"streams": ["k"]})
    monkeypatch.setattr(guide, "stream_rows", lambda keys: [{"url": "http://cdn/1.ts", "source_id": 1}])
    monkeypatch.setattr(connections, "_client_names", {})
    TestClient(api.app).get("/api/stream/5?client=roku-abc&name=Bedroom%20Roku",
                            headers={"User-Agent": "Roku/DVP-14.5 (14.5.4)"})
    assert seen == {"client": "roku-abc", "name": ""}            # a client id: no User-Agent guess
    monkeypatch.setattr(tuner, "running", lambda: [
        {"id": 1, "url": "http://cdn/1.ts", "mode": "copy", "audio": "aac", "since": 1, "mb": 0, "viewers": ["roku-abc", "roku-zzz"]}])
    monkeypatch.setattr(streaming, "playing", lambda: [])
    monkeypatch.setattr(hls, "playing", lambda: [])
    assert connections.in_use()["tuners"][0]["viewers"] == [{"kind": "roku", "name": "Bedroom Roku"}, {"kind": "roku", "name": "Roku"}]


@pytest.fixture
def holds(monkeypatch):
    monkeypatch.setattr(connections, "_stopped", {})
    monkeypatch.setattr(tuner, "stop_id", lambda tid: True)
    monkeypatch.setattr(tuner, "running", lambda: [
        {"id": 7, "url": "http://cdn/1.ts", "mode": "copy", "audio": "aac", "since": 1, "mb": 0,
         "viewers": ["tv1", "hls:pad1", "roku-abc", "app:VLC@10.0.0.9", "", "setup-x"]}])
    assert connections.stop("t7") == []


def test_stop_refuses_apps_reconnecting(holds):
    for client in ("roku-abc", None):
        with pytest.raises(connections.Stopped):
            connections.after_stop(client, "http://cdn/1.ts")
    connections.after_stop(None, "http://cdn/2.ts")          # another channel: fine
    connections.after_stop("roku-zzz", "http://cdn/1.ts")    # another Roku: fine
    assert isinstance(connections.Stopped(), connections.Full)  # so every endpoint answers 503


def test_stopped_screens_are_told_until_they_tune(holds):
    c = TestClient(api.app)
    assert c.get("/api/stopped?client=tv1").json() == {"stopped": True}
    assert c.get("/api/stopped?client=tv1").json() == {"stopped": True}   # an error and the end can both ask
    assert connections.was_stopped("pad1") and not connections.was_stopped("setup-x")
    assert not connections.was_stopped("tv2") and not connections.was_stopped("")
    connections.after_stop("tv1", "http://cdn/1.ts")         # a screen tuning is never refused…
    assert c.get("/api/stopped?client=tv1").json() == {"stopped": False}  # …and ends its hold


def test_stop_hold_runs_out(holds, monkeypatch):
    later = connections.time.monotonic() + connections.SCREEN_HOLD + 1
    monkeypatch.setattr(connections.time, "monotonic", lambda: later)
    connections.after_stop("roku-abc", "http://cdn/1.ts")
    assert not connections.was_stopped("tv1")


def test_stop_holds_on_demand_client(setup, monkeypatch):
    monkeypatch.setattr(connections, "_stopped", {})
    monkeypatch.setattr(hls, "playing", lambda: [
        {"id": "ab12", "url": "http://x/m.mp4", "label": "Heat", "client": "roku-abc", "since": 1}])
    monkeypatch.setattr(hls, "stop_id", lambda sid: sid == "ab12")
    assert connections.stop("hab12") == []
    with pytest.raises(connections.Stopped):
        connections.after_stop("roku-abc", "http://x/other.mp4")


def test_shows_whats_on_now(setup):
    now = int(time.time())
    db.run("INSERT INTO programs(channel, start, stop, title, subtitle, episode) VALUES ('gn:8', ?, ?, 'SportsCenter', 'Late', 'S45E10')",
           (now - 600, now + 1200))
    db.run("INSERT INTO programs(channel, start, stop, title) VALUES ('gn:8', ?, ?, 'NFL Live')", (now + 1200, now + 3000))
    guide.invalidate()
    feed, chan, movie = connections.in_use()["tuners"]
    assert chan["show"] == {"title": "SportsCenter", "subtitle": "Late", "episode": "S45E10", "start": now - 600, "stop": now + 1200}
    assert feed["show"] is None and movie["show"] is None    # no guide for feeds; On Demand's title is the show
