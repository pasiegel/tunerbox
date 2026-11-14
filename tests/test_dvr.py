import time
from pathlib import Path

from tunerbox import db, dvr, guide


def test_file_names_keep_dots_in_titles():
    base = Path("rec/Mr. Robot - S01E02 (2026-11-09 2000)")
    assert dvr._ext(base, ".ts").name == "Mr. Robot - S01E02 (2026-11-09 2000).ts"


def _channel_with_guide(airings):
    """Channel 8 with a working feed and the given (offset hours, subtitle, is_new) airings."""
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES ('k8', ?, 'WOOD', 'http://s/8')", (sid,))
    db.run("INSERT INTO lineup(number, name) VALUES (8, 'WOOD')")
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (8, 'k8', 'manual')")
    now = int(time.time())
    for h, sub, new in airings:
        db.run("INSERT INTO programs(channel, start, stop, title, subtitle, is_new) VALUES ('gn:8', ?, ?, 'Show', ?, ?)",
               (now + h * 3600, now + h * 3600 + 1800, sub, new))
    guide.invalidate()


def test_series_rule_records_each_episode_once():
    _channel_with_guide([(1, "Pilot", 1), (3, "Pilot", 0), (5, "Second", 1), (-5, "Old", 1)])
    dvr.add_rule("show", 8)
    recs = db.rows("SELECT subtitle FROM recordings ORDER BY start")
    assert [r["subtitle"] for r in recs] == ["Pilot", "Second"]   # repeat and past airing skipped
    assert dvr.apply_rules() == 0                                  # running again adds nothing


def test_new_only_rule():
    _channel_with_guide([(1, "Rerun", 0), (2, "Fresh", 1)])
    dvr.add_rule("Show", None, new_only=True)
    assert [r["subtitle"] for r in db.rows("SELECT subtitle FROM recordings")] == ["Fresh"]


_starts = iter(range(1000, 10**6, 10))


def _rec(status="recording", file=""):
    start = next(_starts)
    return db.run("INSERT INTO recordings(number, title, start, stop, status, file) VALUES (8, 'Show', ?, ?, ?, ?)",
                  (start, start + 5, status, file))


def test_cancelled_before_any_data_is_cancelled_not_failed(tmp_path):
    rid = _rec("cancelled")
    dvr._finish_file(rid, tmp_path / "Show", cancelled=True)
    assert db.one("SELECT status, error FROM recordings WHERE id=?", (rid,)) == {"status": "cancelled", "error": ""}


def test_no_data_is_failed(tmp_path):
    rid = _rec()
    (tmp_path / "Show.ts").write_bytes(b"x" * 100)
    dvr._finish_file(rid, tmp_path / "Show", err="403 Forbidden")
    assert db.one("SELECT status, error FROM recordings WHERE id=?", (rid,)) == {"status": "failed", "error": "403 Forbidden"}
    assert not (tmp_path / "Show.ts").exists()


def test_capture_is_kept_when_mp4_conversion_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(dvr, "ffmpeg_bin", lambda: "definitely-not-ffmpeg-xyz")
    monkeypatch.setattr(dvr.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1})())
    rid = _rec()
    ts = tmp_path / "Mr. Robot.ts"
    ts.write_bytes(b"x" * (dvr.MIN_KEEP + 1))
    dvr._finish_file(rid, tmp_path / "Mr. Robot", note="Stopped early: disk almost full")
    r = db.one("SELECT status, file, error FROM recordings WHERE id=?", (rid,))
    assert r == {"status": "done", "file": str(ts), "error": "Stopped early: disk almost full"}


def test_recover_salvages_interrupted_recordings(tmp_path, monkeypatch):
    monkeypatch.setattr(dvr.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1})())
    started = []
    monkeypatch.setattr(dvr.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda s: started.append(target())})())
    base = tmp_path / "Show (2026-11-09 2000)"
    dvr._ext(base, ".ts").write_bytes(b"x" * (dvr.MIN_KEEP + 1))
    kept = _rec(file=str(dvr._ext(base, ".mp4")))
    lost = _rec(file="")
    dvr.recover()
    assert db.one("SELECT status, error FROM recordings WHERE id=?", (kept,)) == {
        "status": "done", "error": "Partial: TunerBox stopped while recording"}
    assert db.one("SELECT status FROM recordings WHERE id=?", (lost,))["status"] == "failed"


def test_not_started_when_disk_is_full(monkeypatch):
    _channel_with_guide([])
    monkeypatch.setattr(dvr, "_free_bytes", lambda: 100_000_000)
    rid = _rec("scheduled")
    dvr._start(db.one("SELECT * FROM recordings WHERE id=?", (rid,)))
    r = db.one("SELECT status, error FROM recordings WHERE id=?", (rid,))
    assert r["status"] == "failed" and "disk space" in r["error"]


# ---------------------------------------------------------------- connection limits
def _limited(limit=1):
    """Channels 8 and 9 on an Xtream account allowing `limit` connections; 10 on a playlist with no limit."""
    a = db.run("INSERT INTO sources(name, type, url) VALUES ('Prov', 'xtream', 'http://prov')")
    db.set_meta(f"xtream_max_{a}", limit)
    b = db.run("INSERT INTO sources(name, type, url) VALUES ('Free', 'm3u', 'http://free')")
    for n, sid in ((8, a), (9, a), (10, b)):
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, ?)", (f"k{n}", sid, f"C{n}", f"http://s/{n}"))
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (n, f"C{n}"))
        db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, ?, 'manual')", (n, f"k{n}"))
    guide.invalidate()
    return a, b


def _sched(number, title, start_h, hours=1):
    now = int(time.time()) + 3600
    return dvr.schedule(number, now + start_h * 3600, now + (start_h + hours) * 3600, title)["id"]


def test_overlapping_recordings_on_a_one_connection_account_conflict():
    _limited(1)
    first = _sched(8, "News", 0)
    second = _sched(9, "Movie", 0.5)
    later = _sched(9, "Late Show", 3)
    clash = dvr.conflicts()
    assert list(clash) == [second]                  # the earlier one gets the connection
    assert "News" in clash[second] and "Prov allows 1 connection" in clash[second]
    assert first not in clash and later not in clash


def test_same_channel_and_other_playlists_dont_conflict():
    _limited(1)
    _sched(8, "News", 0)
    _sched(8, "More News", 0.5)                       # same channel: one shared tuner
    _sched(10, "Free Show", 0)                        # a playlist with no limit
    assert dvr.conflicts() == {}


def test_backup_feed_on_another_playlist_avoids_the_conflict():
    _limited(1)
    db.run("UPDATE mappings SET alt_keys='[\"k10\"]' WHERE number=9")      # 9's backup feed is on Free
    guide.invalidate()
    _sched(8, "News", 0)
    _sched(9, "Movie", 0)
    assert dvr.conflicts() == {}


def test_back_to_back_recordings_fit_two_connections():
    _limited(2)
    _sched(8, "Long", 0, hours=3)
    _sched(9, "First", 0)
    _sched(9, "Second", 2)            # never more than two at once, though three overlap "Long"
    assert dvr.conflicts() == {}


def test_recordings_api_reports_conflicts():
    from fastapi.testclient import TestClient

    from tunerbox import api
    _limited(1)
    _sched(8, "News", 0)
    second = _sched(9, "Movie", 0)
    rows = {r["id"]: r for r in TestClient(api.app).get("/api/recordings").json()}
    assert rows[second]["conflict"] and not [r for i, r in rows.items() if i != second and r["conflict"]]


def test_recording_waits_for_a_connection_then_gives_up_when_cancelled(monkeypatch, tmp_path):
    from tunerbox import connections, tuner
    a, _ = _limited(1)
    rid = _sched(8, "News", -1.5, hours=1)
    rec = db.one("SELECT * FROM recordings WHERE id=?", (rid,))
    db.run("UPDATE recordings SET status='recording' WHERE id=?", (rid,))
    src = db.one("SELECT * FROM sources WHERE id=?", (a,))

    def full(*a_, **k):
        raise connections.Full(src, 1, 1)
    monkeypatch.setattr(tuner, "join", full)
    waits = []

    def wait(rec_id, secs):
        waits.append(db.one("SELECT error FROM recordings WHERE id=?", (rec_id,))["error"])
        db.run("UPDATE recordings SET status='cancelled' WHERE id=?", (rec_id,))
        return False
    monkeypatch.setattr(dvr, "_wait_while_recording", wait)
    dvr._record(rec, guide.stream_rows(["k8"]), tmp_path / "News")
    assert waits and waits[0].startswith("No connection free: Prov allows 1 connection")
    assert db.one("SELECT status FROM recordings WHERE id=?", (rid,))["status"] == "cancelled"


def test_recording_uses_a_free_backup_feed_before_stopping_live_tv(monkeypatch, tmp_path):
    from tunerbox import connections, tuner
    a, _ = _limited(1)
    rid = _sched(8, "News", -1.5, hours=1)
    rec = db.one("SELECT * FROM recordings WHERE id=?", (rid,))
    db.run("UPDATE recordings SET status='recording' WHERE id=?", (rid,))
    src = db.one("SELECT * FROM sources WHERE id=?", (a,))
    calls = []

    class Sub:
        tuner = type("T", (), {"error": lambda self: ""})()

        def close(self, wait=True):
            pass

    def join(url, *a_, take=False, **k):
        calls.append((url, take))
        if url == "http://s/8":
            raise connections.Full(src, 1, 1)    # live TV holds Prov's one connection
        return Sub()
    monkeypatch.setattr(tuner, "join", join)
    monkeypatch.setattr(dvr, "_capture", lambda *a_: dvr._active.pop(rid, None))    # then cancelled
    monkeypatch.setattr(dvr, "_finish_file", lambda *a_, **k: None)
    dvr._record(rec, guide.stream_rows(["k8", "k10"]), tmp_path / "News")
    assert calls == [("http://s/8", False), ("http://s/10", False)]


def test_recording_takes_from_live_tv_only_when_every_feed_is_full(monkeypatch, tmp_path):
    from tunerbox import connections, tuner
    a, _ = _limited(1)
    rid = _sched(8, "News", -1.5, hours=1)
    rec = db.one("SELECT * FROM recordings WHERE id=?", (rid,))
    db.run("UPDATE recordings SET status='recording' WHERE id=?", (rid,))
    src = db.one("SELECT * FROM sources WHERE id=?", (a,))
    calls = []

    def join(url, *a_, take=False, **k):
        calls.append((url, take))
        if not take:
            raise connections.Full(src, 1, 1)
        db.run("UPDATE recordings SET status='cancelled' WHERE id=?", (rid,))
        raise connections.Full(src, 1, 1)        # nothing could be stopped
    monkeypatch.setattr(tuner, "join", join)
    monkeypatch.setattr(dvr, "_wait_while_recording", lambda *a_: False)
    dvr._record(rec, guide.stream_rows(["k8", "k9"]), tmp_path / "News")
    assert calls == [("http://s/8", False), ("http://s/9", False), ("http://s/8", True)]


def test_conflict_with_live_tv_when_recordings_dont_take_over(monkeypatch):
    from tunerbox import config, connections
    a, _ = _limited(1)
    soon = _sched(8, "News", -0.5)
    later = _sched(8, "Late News", 4)
    monkeypatch.setattr(connections, "live_used", lambda sid: 1 if sid == a else 0)
    assert dvr.conflicts() == {}                    # recordings take the connection (the default)
    config.update({"dvr_takes_connection": False})
    clash = dvr.conflicts()
    assert list(clash) == [soon] and "Live TV" in clash[soon]   # live TV isn't assumed on hours ahead
    assert later not in clash


def test_two_channels_on_the_same_stream_share_a_connection():
    _limited(1)
    db.run("INSERT INTO lineup(number, name) VALUES (108, 'C8 again')")
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (108, 'k8', 'manual')")
    guide.invalidate()
    _sched(8, "News", 0)
    _sched(108, "News again", 0)
    assert dvr.conflicts() == {}


def test_home_coming_up_shows_conflicts():
    from tunerbox import home
    _limited(1)
    _sched(8, "News", 0)
    second = _sched(9, "Movie", 0)
    coming = {x["id"]: x for x in home.overview()["coming"]}
    assert coming[second]["conflict"] and not [x for i, x in coming.items() if i != second and x["conflict"]]
