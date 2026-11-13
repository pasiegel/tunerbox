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
