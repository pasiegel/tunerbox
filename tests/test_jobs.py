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
