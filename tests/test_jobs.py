from datetime import datetime

from tunerbox import config, db, jobs, matcher, sources


def _ts(*a):
    return int(datetime(*a).timestamp())


def test_scan_every_n_hours_by_default():
    now = _ts(2026, 11, 10, 12)
    assert jobs.scan_due(now, now - 25 * 3600)
    assert not jobs.scan_due(now, now - 23 * 3600)


def test_scan_at_a_fixed_hour():
    config.update({"scan_hour": 5})
    assert not jobs.scan_due(_ts(2026, 11, 10, 4, 59), _ts(2026, 11, 9, 5, 1))    # today's 5am not here yet
    assert jobs.scan_due(_ts(2026, 11, 10, 5, 0, 30), _ts(2026, 11, 9, 5, 1))    # it is now
    assert not jobs.scan_due(_ts(2026, 11, 10, 9), _ts(2026, 11, 10, 5, 1))      # already ran
    assert jobs.scan_due(_ts(2026, 11, 10, 9), 0)                                # never scanned


def test_quick_refresh_interval_counts_from_the_last_scan_or_refresh():
    now = 10_000_000
    db.set_meta("scan_updated", now - 3 * 3600)
    assert jobs.quick_due(now)
    db.set_meta("quick_updated", now - 3600)
    assert not jobs.quick_due(now)
    config.update({"event_refresh_hours": 0})
    db.set_meta("quick_updated", 0)
    assert not jobs.quick_due(now)


def test_matching_is_skipped_when_nothing_changed(monkeypatch):
    db.run("INSERT INTO lineup(number, name) VALUES (?,?)", (202, "Cable News Network"))
    sid = sources.add_source("p", "m3u", "http://x/list.m3u")
    entries = [{"name": "CNN HD", "url": "http://s/cnn", "tvg_id": "", "logo": "", "grp": "News"},
               {"name": "PPV 01", "url": "http://s/p1", "tvg_id": "", "logo": "", "grp": "PPV"}]
    monkeypatch.setattr(sources, "fetch_m3u", lambda src: (entries, ""))
    sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    assert matcher.match_all(force=False) == (1, 1)

    calls = []
    real = matcher.process.cdist
    monkeypatch.setattr(matcher.process, "cdist", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert matcher.match_all(force=False) == (1, 1)
    assert calls == []                                   # skipped
    entries[1]["name"] = "PPV 01: Big Fight"
    sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    assert matcher.match_all(force=False) == (1, 1)
    assert calls                                         # a name changed: matched again
    assert matcher.match_all() == (1, 1)                 # force (the Re-match button) always runs


class _Stop(Exception):
    pass


def _run_housekeeping(monkeypatch, loops=3):
    """Run the housekeeping loop a few times; returns how often it refreshed the guide."""
    calls, n = [], {"i": 0}
    monkeypatch.setattr(jobs, "refresh_epg", lambda: calls.append(1))
    monkeypatch.setattr(jobs.dvr, "tick", lambda: None)
    monkeypatch.setattr(jobs.media, "write_epg", lambda: None)
    monkeypatch.setattr(jobs.sports, "warm", lambda _now: None)      # a background thread that would outlive the test
    monkeypatch.setattr(jobs.scoreboard, "tick", lambda *_a, **_k: False)   # would download from ESPN in a thread

    def sleep(_s):
        n["i"] += 1
        if n["i"] >= loops:
            raise _Stop
    monkeypatch.setattr(jobs.time, "sleep", sleep)
    try:
        jobs.housekeeping()
    except _Stop:
        pass
    return len(calls)


def test_guide_refreshes_once_at_startup_when_older_than_half_an_hour(monkeypatch):
    db.set_meta("epg_updated", int(jobs.time.time()) - 2 * 3600)      # well inside the 12 hour interval
    assert _run_housekeeping(monkeypatch) == 1


def test_fresh_guide_is_not_downloaded_again_on_a_quick_restart(monkeypatch):
    db.set_meta("epg_updated", int(jobs.time.time()) - 5 * 60)
    assert _run_housekeeping(monkeypatch) == 0


def test_a_queued_job_runs_after_the_running_one():
    import threading
    from tunerbox import jobs
    gate, ran = threading.Event(), []
    assert jobs._run("first", lambda: (gate.wait(5), ran.append("first"))[1])
    assert not jobs._run("other", lambda: ran.append("other"))           # not queued: refused, as before
    assert jobs._run("second", lambda: ran.append("second"), queue=True)
    assert jobs._run("second", lambda: ran.append("again"), queue=True)  # already waiting: not twice
    gate.set()
    for _ in range(100):
        if not jobs.state["running"]:
            break
        threading.Event().wait(0.05)
    assert ran == ["first", "second"] and not jobs._queue


def _wait_idle():
    import threading
    for _ in range(100):
        if not jobs.state["running"] and not jobs.lib_state["running"] and not jobs.vid_state["running"]:
            return
        threading.Event().wait(0.05)


def test_my_library_runs_beside_the_playlists():
    import threading
    gate, ran = threading.Event(), []
    assert jobs._run("library:7", lambda: (gate.wait(5), ran.append("rescan"))[1])       # a long Full rescan
    assert jobs._run("scan", lambda: ran.append("scan")) is True                         # not held up by it
    for _ in range(100):
        if "scan" in ran:
            break
        threading.Event().wait(0.05)
    assert ran == ["scan"] and jobs.lib_state["running"] and not jobs.state["running"]
    assert jobs._run("library:task:remove", lambda: ran.append("remove"), queue=True) == "queued"   # its own lane: waits
    assert jobs.running_what(library=True) == "another My Library job" and jobs.running_what() == ""
    gate.set()
    _wait_idle()
    assert ran == ["scan", "rescan", "remove"] and not jobs._lib_queue
    assert jobs.lib_state["message"] == "Done" and jobs.state["message"] == "Done"


def test_a_playlist_scan_waits_its_turn_instead_of_being_dropped(monkeypatch):
    import threading
    gate, ran = threading.Event(), []
    assert jobs._run("epg", lambda: (gate.wait(5), ran.append("epg"))[1])
    real = jobs._run
    monkeypatch.setattr(jobs, "_run", lambda name, fn, queue=False: real(name, lambda: ran.append(name), queue))
    assert jobs.scan() is False                          # Scan playlists while the guide updates: refused, as before…
    assert jobs.scan(queue=True) == "queued"             # …unless asked to wait (a playlist added or changed)
    gate.set()
    _wait_idle()
    assert ran == ["epg", "scan"]


def test_scan_now_in_setup_queues_and_says_after_what(monkeypatch):
    from fastapi.testclient import TestClient
    from tunerbox import api
    monkeypatch.setattr(jobs, "scan", lambda force=False, queue=False, source_id=None: "queued" if queue else False)
    monkeypatch.setattr(jobs, "running_what", lambda library=False: "the guide update")
    assert TestClient(api.app).post("/api/jobs/scan").json() == {"started": "queued", "after": "the guide update"}
    j = TestClient(api.app).get("/api/jobs").json()
    assert j["library"]["running"] is False and "running" in j


def _run_scan_now(monkeypatch, **kw):
    """Run jobs.scan's job here, with the network stubbed: -> (playlists read, On Demand asked for, streams checked)."""
    read, od, checked = [], [], []
    monkeypatch.setattr(jobs, "_run", lambda name, fn, queue=False: fn())
    monkeypatch.setattr(jobs.sources, "refresh_source", lambda s: read.append(s["id"]))
    monkeypatch.setattr(jobs.vod, "enabled", lambda: True)
    monkeypatch.setattr(jobs.vod, "refresh_all", lambda p=None, source_id=None, kinds=None: od.append(source_id) or "")
    monkeypatch.setattr(jobs.probe, "probe_streams", lambda todo, cb: checked.extend(r["key"] for r in todo) or (0, len(todo)))
    monkeypatch.setattr(jobs.matcher, "match_all", lambda *a, **k: (0, 0))
    monkeypatch.setattr(jobs.epg, "fetch_xmltv", lambda *a, **k: None)
    monkeypatch.setattr(jobs.dvr, "apply_rules", lambda: 0)
    db.set_meta("epg_updated", 1)
    jobs.scan(**kw)
    return read, od, checked


def test_a_new_playlist_is_read_on_its_own(monkeypatch):
    old = sources.add_source("old", "m3u", "http://x/old.m3u")
    new = sources.add_source("new", "m3u", "http://x/new.m3u")
    db.run("INSERT INTO streams(key, source_id, name, url, checked_at) VALUES ('o1', ?, 'Old 1', 'http://s/o1', 0)", (old,))
    db.run("INSERT INTO streams(key, source_id, name, url, checked_at) VALUES ('n1', ?, 'New 1', 'http://s/n1', 0)", (new,))
    db.set_meta("scan_updated", 5)
    assert _run_scan_now(monkeypatch, source_id=new) == ([new], [new], ["n1"])     # the old one left alone
    assert db.get_meta("scan_updated") == "5"                                      # not counted as the full scan
    read, od, checked = _run_scan_now(monkeypatch)                                 # Scan playlists: all of them
    assert read == [old, new] and od == [None] and sorted(checked) == ["n1", "o1"]
    assert db.get_meta("scan_updated") != "5"


def test_the_job_bar_learns_when_grabbing_video_details_starts_again(monkeypatch):
    from fastapi.testclient import TestClient
    from tunerbox import api, library
    monkeypatch.setattr(library, "resume_at", 0.0)
    assert TestClient(api.app).get("/api/jobs").json()["videos"]["resume_at"] == 0
    monkeypatch.setattr(library, "resume_at", 2_000_000_000.4)
    assert TestClient(api.app).get("/api/jobs").json()["videos"]["resume_at"] == 2_000_000_000


def test_grabbing_video_details_doesnt_hold_up_finding_new_videos():
    import threading
    gate, ran = threading.Event(), []
    assert jobs._run("library:videos", lambda: (gate.wait(5), ran.append("details"))[1])   # hours of reading files
    assert jobs._run("library:task:scan", lambda: ran.append("scan")) is True            # the half-hourly scan runs
    for _ in range(100):
        if "scan" in ran:
            break
        threading.Event().wait(0.05)
    assert ran == ["scan"] and jobs.vid_state["running"] and not jobs.lib_state["running"]
    assert jobs.running_what(videos=True) == "grabbing video details" and jobs.running_what(library=True) == ""
    # the scan found new videos: their details are grabbed after the run that's going, once
    assert jobs._run("library:videos", lambda: ran.append("new"), queue=True) == "queued"
    assert jobs._run("library:videos", lambda: ran.append("twice"), queue=True) == "queued"
    gate.set()
    _wait_idle()
    assert ran == ["scan", "details", "new"] and not jobs._vid_queue


def test_each_scope_of_video_details_gets_its_own_place_in_the_queue(monkeypatch):
    names = []
    monkeypatch.setattr(jobs, "_run", lambda name, fn, queue=False: names.append(name) or "queued")
    jobs.library_measure()
    jobs.library_measure(retry_days=0)
    jobs.library_measure(share_id=3, before=5)
    assert names == ["library:videos", "library:videos:new", "library:videos:3"]
    assert jobs.library.measure_later is None           # the tests' own (conftest); jobs.py sets library_measure
