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
