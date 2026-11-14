"""SQLite storage. One short-lived connection per call keeps threads simple."""
import logging
import sqlite3
from contextlib import contextmanager

from . import config

log = logging.getLogger("tunerbox.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,            -- m3u | xtream | hdhomerun
    url TEXT NOT NULL,             -- m3u url/path, or xtream server
    username TEXT DEFAULT '',
    password TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    exclude_groups TEXT DEFAULT '', -- regex, matched against group-title
    hidden_groups TEXT DEFAULT '[]', -- JSON list of exact group names to leave out
    xtream_format TEXT DEFAULT 'ts',
    epg_url TEXT DEFAULT '',       -- discovered x-tvg-url / xmltv.php
    status TEXT DEFAULT '',
    updated_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS streams (
    key TEXT PRIMARY KEY,          -- stable id across refreshes
    source_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    tvg_id TEXT DEFAULT '',
    logo TEXT DEFAULT '',
    grp TEXT DEFAULT '',
    norm TEXT DEFAULT '',
    status TEXT DEFAULT 'unknown', -- unknown | ok | dead
    checked_at INTEGER DEFAULT 0,
    seen_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_streams_source ON streams(source_id);
CREATE TABLE IF NOT EXISTS lineup (
    number INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    callsign TEXT DEFAULT '',
    affiliate TEXT DEFAULT '',
    station_id TEXT DEFAULT '',
    logo TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mappings (
    number INTEGER PRIMARY KEY,
    stream_key TEXT,               -- NULL with mode 'none' = deliberately off
    alt_keys TEXT DEFAULT '[]',    -- JSON list of fallback stream keys
    mode TEXT DEFAULT 'auto',      -- auto | manual | none
    score INTEGER DEFAULT 0,
    reason TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS extras (
    stream_key TEXT PRIMARY KEY,
    number INTEGER UNIQUE,
    enabled INTEGER DEFAULT 1,
    active INTEGER DEFAULT 1       -- still present in the latest scan
);
CREATE TABLE IF NOT EXISTS programs (
    channel TEXT NOT NULL,         -- gn:<number> or x:<xmltv id>
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    title TEXT,
    subtitle TEXT,
    descr TEXT,
    episode TEXT,
    category TEXT,
    rating TEXT,
    image TEXT,
    is_new INTEGER DEFAULT 0,
    is_live INTEGER DEFAULT 0,
    PRIMARY KEY (channel, start)
);
CREATE INDEX IF NOT EXISTS ix_programs_time ON programs(channel, stop);
CREATE TABLE IF NOT EXISTS epg_names (
    norm TEXT NOT NULL,            -- normalized XMLTV display-name
    channel TEXT NOT NULL,
    PRIMARY KEY (norm, channel)
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER NOT NULL,
    channel_name TEXT,
    title TEXT,
    subtitle TEXT,
    descr TEXT,
    episode TEXT,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    status TEXT DEFAULT 'scheduled', -- scheduled|recording|done|failed|cancelled
    file TEXT DEFAULT '',
    rule_id INTEGER,
    error TEXT DEFAULT '',
    UNIQUE (number, start)
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    number INTEGER,                -- NULL = any channel
    new_only INTEGER DEFAULT 0,
    created INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER NOT NULL,
    channel_name TEXT,
    title TEXT,
    subtitle TEXT,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    created INTEGER DEFAULT 0,
    UNIQUE (number, start)
);
CREATE TABLE IF NOT EXISTS sports_events (  -- games from the scoreboard (see scoreboard.py)
    id TEXT PRIMARY KEY,           -- <league>:<ESPN event id>
    league TEXT NOT NULL,
    day TEXT NOT NULL,             -- the scoreboard day it was listed under (YYYYMMDD)
    start INTEGER NOT NULL,
    data TEXT NOT NULL             -- JSON
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS source_groups (   -- every group in the playlist, kept or not
    source_id INTEGER NOT NULL,
    grp TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (source_id, grp)
);
"""


def _connect():
    config.DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DATA / "tunerbox.db", timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def conn():
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _add_column(c, table, column, decl):
    if column not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# Schema changes after the first release, in order. The database's PRAGMA user_version is how many have
# run. Add new ones at the end and never edit or reorder old ones. Each must be safe to run on a database
# that already has the change: databases from before this list existed are at version 0 whatever they hold.
MIGRATIONS = [
    lambda c: _add_column(c, "sources", "hidden_groups", "TEXT DEFAULT '[]'"),   # 1.2
    lambda c: _add_column(c, "sources", "video_mode", "TEXT DEFAULT ''"),        # 1.4: per-playlist video
    lambda c: _add_column(c, "sources", "vod_movies", "INTEGER DEFAULT 0"),      # 1.5: On Demand movies on
    lambda c: _add_column(c, "sources", "vod_series", "INTEGER DEFAULT 0"),      # 1.5: On Demand TV shows on
    lambda c: _add_column(c, "sources", "vod_hidden", "TEXT DEFAULT '[]'"),      # 1.5: [kind, category] left out
    lambda c: _add_column(c, "sources", "vod_tags", "TEXT DEFAULT ''"),          # 1.5: On Demand labels shown ('' = all)
    lambda c: _add_column(c, "recordings", "image", "TEXT DEFAULT ''"),          # Home: the show's picture
    lambda c: _add_column(c, "recordings", "position", "INTEGER DEFAULT 0"),     # Home: resume a recording
    lambda c: _add_column(c, "recordings", "duration", "INTEGER DEFAULT 0"),
    lambda c: _add_column(c, "recordings", "watched", "INTEGER DEFAULT 0"),
    lambda c: _add_column(c, "recordings", "watched_at", "INTEGER DEFAULT 0"),
]


def migrate(c):
    ver = c.execute("PRAGMA user_version").fetchone()[0]
    if ver > len(MIGRATIONS):
        log.warning("Database is from a newer TunerBox (schema %s, this version knows %s)", ver, len(MIGRATIONS))
    for n, step in enumerate(MIGRATIONS[ver:], ver + 1):
        step(c)
        c.execute(f"PRAGMA user_version={n}")
        c.commit()
        log.info("Database upgraded to schema %s", n)
    # The version number alone can't be trusted: a test or development build may have used the same
    # numbers for different changes, leaving a database "up to date" without this version's columns.
    # Every step is safe to run again, so make sure all of them have been applied.
    for step in MIGRATIONS[:min(ver, len(MIGRATIONS))]:
        step(c)
    c.commit()


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")    # stored in the database file: once is enough
        c.executescript(SCHEMA)
        migrate(c)
    from . import media, vod
    media.init()
    vod.init()


def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    with conn() as c:
        r = c.execute(sql, args).fetchone()
        return dict(r) if r else None


def run(sql, args=()):
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def get_meta(k, default=None):
    r = one("SELECT v FROM meta WHERE k=?", (k,))
    return r["v"] if r else default


def set_meta(k, v):
    run("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (k, str(v)))
