import sqlite3

from tunerbox import config, db


def _cols(table):
    with db.conn() as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def test_new_database_is_at_latest_schema():
    with db.conn() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert {"hidden_groups", "video_mode"} <= _cols("sources")


def test_old_database_is_upgraded(fresh_data):
    path = config.DATA / "tunerbox.db"
    path.unlink()
    for ext in ("-wal", "-shm"):
        (config.DATA / f"tunerbox.db{ext}").unlink(missing_ok=True)
    con = sqlite3.connect(path)       # a 1.0 database: no hidden_groups / video_mode, user_version 0
    con.execute("""CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, type TEXT NOT NULL,
                   url TEXT NOT NULL, username TEXT DEFAULT '', password TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
                   exclude_groups TEXT DEFAULT '', xtream_format TEXT DEFAULT 'ts', epg_url TEXT DEFAULT '',
                   status TEXT DEFAULT '', updated_at INTEGER DEFAULT 0)""")
    con.execute("INSERT INTO sources(name, type, url) VALUES ('old', 'm3u', 'http://x')")
    con.commit()
    con.close()
    db.init()
    assert {"hidden_groups", "video_mode"} <= _cols("sources")
    assert db.one("SELECT name, hidden_groups FROM sources") == {"name": "old", "hidden_groups": "[]"}
    db.init()                         # running again changes nothing
    with db.conn() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_old_recordings_get_the_resume_columns(fresh_data):
    path = config.DATA / "tunerbox.db"
    path.unlink()
    for ext in ("-wal", "-shm"):
        (config.DATA / f"tunerbox.db{ext}").unlink(missing_ok=True)
    con = sqlite3.connect(path)       # a 1.5 database: recordings without picture / resume / watched
    con.execute("""CREATE TABLE recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, number INTEGER NOT NULL,
                   channel_name TEXT, title TEXT, subtitle TEXT, descr TEXT, episode TEXT, start INTEGER NOT NULL,
                   stop INTEGER NOT NULL, status TEXT DEFAULT 'scheduled', file TEXT DEFAULT '', rule_id INTEGER,
                   error TEXT DEFAULT '', UNIQUE (number, start))""")
    con.execute("INSERT INTO recordings(number, title, start, stop, status) VALUES (4, 'Old show', 100, 200, 'done')")
    con.execute("PRAGMA user_version=6")
    con.commit()
    con.close()
    db.init()
    new = {"image", "position", "duration", "watched", "watched_at"}
    assert new <= _cols("recordings")
    assert db.one("SELECT title, image, position, duration, watched, watched_at FROM recordings") == \
        {"title": "Old show", "image": "", "position": 0, "duration": 0, "watched": 0, "watched_at": 0}
    db.init()                         # running again changes nothing
    assert new <= _cols("recordings")


def test_database_from_a_newer_version_is_left_alone():
    with db.conn() as c:
        c.execute(f"PRAGMA user_version={len(db.MIGRATIONS) + 5}")
    db.init()
    with db.conn() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) + 5


def test_migrations_repair_a_database_numbered_by_another_build():
    """A development build used schema versions 3-4 for other columns: ours must still be added."""
    import sqlite3
    from tunerbox import config
    path = config.DATA / "tunerbox.db"
    path.unlink()
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT, type TEXT, url TEXT, user_agent TEXT)")
    c.execute(f"PRAGMA user_version={len(db.MIGRATIONS)}")
    c.commit()
    c.close()
    db.init()
    cols = {r["name"] for r in db.rows("PRAGMA table_info(sources)")}
    assert {"hidden_groups", "video_mode", "vod_movies", "vod_series", "vod_hidden", "user_agent"} <= cols
    db.init()                       # and again: nothing breaks on a second start


def test_old_on_demand_titles_get_the_version_columns(fresh_data):
    path = config.DATA / "tunerbox.db"
    path.unlink()
    for ext in ("-wal", "-shm"):
        (config.DATA / f"tunerbox.db{ext}").unlink(missing_ok=True)
    from tunerbox import vod
    # On Demand's table as it was before bitrate / tmdb / o_search: today's, cut off before them
    table = vod.SCHEMA.split("CREATE TABLE IF NOT EXISTS vod_titles")[1].split("\n    bitrate ")[0]
    table = table.replace("country TEXT DEFAULT '',", "country TEXT DEFAULT ''") + "\n)"
    eps = vod.SCHEMA.split("CREATE TABLE IF NOT EXISTS vod_episodes")[1].split(",\n    av ")[0] + "\n)"
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE vod_titles {table}")
    con.execute(f"CREATE TABLE vod_episodes {eps}")             # and its episodes before they kept picture and sound
    assert "bitrate" not in {r[1] for r in con.execute("PRAGMA table_info(vod_titles)")}
    assert "av" not in {r[1] for r in con.execute("PRAGMA table_info(vod_episodes)")}
    con.execute("INSERT INTO vod_titles(id, source_id, kind, name, search) VALUES ('1:m1', 1, 'movie', 'Old', 'old')")
    con.commit()
    con.close()
    db.init()
    assert {"bitrate", "tmdb", "o_search", "av"} <= _cols("vod_titles")
    assert "av" in _cols("vod_episodes")
    assert db.one("SELECT bitrate, tmdb, o_search, av FROM vod_titles") == {"bitrate": 0, "tmdb": "", "o_search": "", "av": ""}
    db.init()                         # running again changes nothing


def test_versions_are_found_through_indexes_not_a_scan():
    from tunerbox import vod
    t = {"kind": "movie", "search": "film", "year": "2020", "tmdb": "585083"}
    with db.conn() as c:
        captured = []
        orig = db.rows
        db.rows = lambda q, a=(): captured.append((q, a)) or []
        try:
            vod._version_rows(t)
        finally:
            db.rows = orig
        q, a = captured[0]
        plan = " ".join(r[3] for r in c.execute(f"EXPLAIN QUERY PLAN {q}", a))
    assert "ix_vod_same" in plan and "ix_vod_tmdb" in plan and "ix_vod_browse" not in plan


# ---------------------------------------------------------------- the database lock
def test_long_write_is_logged_with_its_caller(monkeypatch, caplog):
    monkeypatch.setattr(db, "SLOW_WRITE", -1)          # every write counts as slow
    with caplog.at_level("WARNING", logger="tunerbox.db"):
        db.set_meta("lock_test", 1)
        db.rows("SELECT 1")                            # a read doesn't hold the lock: not logged
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "Database write took" in msgs[0] and "db.set_meta" not in msgs[0]


def test_is_locked():
    import sqlite3
    assert db.is_locked(sqlite3.OperationalError("database is locked"))
    assert not db.is_locked(sqlite3.OperationalError("no such table: x"))
    assert not db.is_locked(ValueError("database is locked"))


def test_scoreboard_skips_a_locked_database(monkeypatch, caplog):
    """Another job holding the database mustn't fail the scoreboard refresh (it tries again next time)."""
    import sqlite3
    import time
    from tunerbox import scoreboard
    monkeypatch.setattr(scoreboard, "selected", lambda: ["nfl"])
    monkeypatch.setattr(scoreboard, "fetch", lambda client, key, day: [])
    real = db._connect

    class Busy:
        def __init__(self):
            self.con = real()
        def execute(self, sql, *a):
            if sql.lstrip().upper().startswith("DELETE FROM SPORTS_EVENTS"):
                raise sqlite3.OperationalError("database is locked")
            return self.con.execute(sql, *a)
        def __getattr__(self, k):
            return getattr(self.con, k)

    monkeypatch.setattr(db, "_connect", Busy)
    with caplog.at_level("WARNING", logger="tunerbox.scoreboard"):
        assert scoreboard.refresh(True, int(time.time())) == 0
    assert any("database busy" in r.getMessage() for r in caplog.records)


def test_guide_and_on_demand_write_in_batches(monkeypatch):
    """The big writers commit a few thousand rows at a time; the result is the same as one transaction."""
    from tunerbox import epg, vod
    monkeypatch.setattr(db, "BATCH", 3)
    t0 = 1_800_000_000
    epg._store([("x:a", t0, t0 + 60, "Old", "", "", "", "", "", "", 0, 0)], [(t0, None)])
    rows = [(f"x:{c}", t0 + i * 60, t0 + i * 60 + 60, f"{c}{i}", "", "", "", "", "", "", 0, 0)
            for c in "abcd" for i in range(2)]
    epg._store(rows, [(t0, None)])
    got = db.rows("SELECT channel, title FROM programs WHERE start>=? ORDER BY channel, start", (t0,))
    assert [(r["channel"], r["title"]) for r in got] == [(r[0], r[3]) for r in rows]

    with db.conn() as c:
        c.execute("INSERT INTO sources(id, name, type, url) VALUES (77, 'p', 'xtream', 'http://x')")
    src = {"id": 77, "name": "p", "vod_hidden": "[]"}
    vod._store(src, ("movie",), [{"id": f"77:m{i}", "kind": "movie", "name": f"M{i}", "category": "c"} for i in range(8)])
    later = vod.time.time() + 5                                   # the second read, a few seconds on
    monkeypatch.setattr(vod.time, "time", lambda: later)
    vod._store(src, ("movie",), [{"id": f"77:m{i}", "kind": "movie", "name": f"M{i}", "category": "c"} for i in range(2, 7)])
    ids = [r["id"] for r in db.rows("SELECT id FROM vod_titles WHERE source_id=77 ORDER BY id")]
    assert ids == [f"77:m{i}" for i in range(2, 7)]
