"""Automatic tasks for My Library, kept in the database (lib_tasks): which run, how often, and how each went.

The housekeeping loop (jobs.py) starts whichever task is due as an ordinary background job, one at a time and
in the order of TASKS, so a scan always comes before the tasks that work on what it found. Setup › My Library
lists them, with Run now and Run all now. Each can run at a set time (at_hour, this PC's time), so the NAS and
its drives can be left alone during the day; Run now and Scan now work any time.
"""
import json
import logging
import time

from . import db, library

log = logging.getLogger("tunerbox.libtasks")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lib_tasks (
    key TEXT PRIMARY KEY,            -- one of TASKS
    enabled INTEGER DEFAULT 1,
    every_hours REAL DEFAULT 24,     -- 0.5 (every 30 minutes) or a whole number of hours
    at_hour INTEGER DEFAULT -1,      -- run at this hour (0-23, this PC's time) instead of any time: see _slot_ok()
    on_day INTEGER DEFAULT -1,       -- every week: on this weekday (0 Monday … 6 Sunday)
    last_run INTEGER DEFAULT 0,      -- when it last finished (by itself or Run now)
    took REAL DEFAULT 0,             -- seconds
    status TEXT DEFAULT ''           -- 'ok: …' or 'error: …'
);
"""

# key, name, what it does, and for a new install: every so many hours, at what hour (-1: any time). The scan runs
# every half hour (a quick one: little more than a look at each library's top folder); the rest start at 4 am and
# run one after another, in this order.
DEFAULT_AT = 4
TASKS = [
    ("scan", "Find new and changed videos",
     "Reads every library: adds new videos, follows files that were renamed or moved (keeping where you stopped, "
     "My List and the match) and hides ones that are gone. New titles get their details and posters straight away. "
     "Folders that haven't changed are skipped; each is read in full at least once a day.", 0.5, -1),
    ("details", "Update details and posters",
     "Looks up again titles TMDB didn't know (after a week), and downloads matched titles' details again "
     "after a month.", 24, DEFAULT_AT),
    ("lengths", "Measure video lengths",
     "Reads how long each new video is, so On Demand shows it before it's first played. Up to 10 minutes a run.", 24, DEFAULT_AT),
    ("remove", "Remove missing videos",
     "Takes titles out of On Demand once their files have been missing this long (until then they're hidden, and "
     "come back as they were if the files do), then deletes what they leave behind: cached subtitles and old "
     "records. Their watch progress and My List entries are kept for 90 days, so if a file comes back you carry "
     "on where you left off. Videos still in your library are never touched, and neither are your files.", 24, DEFAULT_AT),
]
KEYS = [t[0] for t in TASKS]
WEEK = 24 * 7
MAX_HOURS = WEEK                    # every hour … every week
CATCH_UP = 4 * 3600     # a set time missed (TunerBox was off, or the tasks before it ran long) is made up this long
                        # after it, else the next one waits


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(lib_tasks)")}
        for col in ("at_hour", "on_day"):
            if col not in cols:
                c.execute(f"ALTER TABLE lib_tasks ADD COLUMN {col} INTEGER DEFAULT -1")
        c.execute("UPDATE lib_tasks SET every_hours=? WHERE every_hours>?", (WEEK, WEEK))   # "every 30 days" is gone
        # "Clean up" was a task of its own: it's part of Remove missing videos now (which keeps its own settings)
        c.execute("DELETE FROM lib_tasks WHERE key NOT IN (%s)" % ",".join("?" * len(TASKS)), [t[0] for t in TASKS])
        # set times came later: tasks still on "any time" then start at 4 am too (once; what's picked after is kept)
        if not c.execute("SELECT 1 FROM meta WHERE k='lib_tasks_at_default'").fetchone():
            c.execute("UPDATE lib_tasks SET at_hour=? WHERE COALESCE(at_hour, -1) < 0 AND every_hours > 1", (DEFAULT_AT,))
            c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('lib_tasks_at_default', '1')")
        have = {r[0] for r in c.execute("SELECT key FROM lib_tasks")}
        for key, _, _, hours, at in TASKS:
            if key in have:
                continue
            enabled = 1
            if key == "scan":
                # "Look for new files" was a setting before these tasks: keep what was picked there
                try:
                    old = json.loads(db.get_meta("library_settings", "") or "{}").get("auto_scan_hours")
                except (TypeError, ValueError):
                    old = None
                if old is not None:
                    hours, enabled = (int(old), 1) if int(old) > 0 else (hours, 0)
            c.execute("INSERT INTO lib_tasks(key, enabled, every_hours, at_hour) VALUES (?,?,?,?)", (key, enabled, hours, at))


def _row(key):
    return db.one("SELECT * FROM lib_tasks WHERE key=?", (key,))


def _top(t):
    lt = time.localtime(t)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1)))


def _at(r):
    """The hour picked, else -1. Every hour or more often: no time to pick."""
    return r["at_hour"] if r.get("at_hour") is not None and r["at_hour"] >= 0 and r["every_hours"] > 1 else -1


def _day(r):
    """The weekday picked for a weekly task, else -1."""
    d = r.get("on_day")
    return d if d is not None and 0 <= d <= 6 and r["every_hours"] == WEEK else -1


def scheduled(r):
    """Runs at set times (a time or a day was picked), not just every so many hours after the last run."""
    return _at(r) >= 0 or _day(r) >= 0


def _slot_hours(r):
    """The hours of the day a task with a set time runs: 4 am every day, or 4, 10, 16 and 22 for every 6 hours
    from 4 am. A day picked with no time: from midnight."""
    h, a = r["every_hours"], _at(r)
    if a < 0:
        return {0}
    return {a} if h >= 24 else {(a + k * h) % 24 for k in range(24) if k * h < 24}


def _day_ok(r, lt):
    d = _day(r)
    return d < 0 or lt.tm_wday == d


def _slot_ok(r, t):
    """`t` (the top of an hour) is one of the task's set times, and one it should run at after its last run.
    A weekly task with no day picked runs at the first set time that's about a week after the last run."""
    lt = time.localtime(t)
    if lt.tm_hour not in _slot_hours(r) or not _day_ok(r, lt) or t <= r["last_run"]:
        return False
    if _day(r) >= 0 or r["every_hours"] <= 24:
        return True
    # last_run is when the last run finished, a little after its set time: half a day of leeway
    return t - r["last_run"] >= (r["every_hours"] - 12) * 3600


def _scheduled_due(r, now):
    # a missed time is made up soon after; a day with no time, any time that day
    grace = CATCH_UP if _at(r) >= 0 else 24 * 3600 - 1
    t = _top(now)
    while now - t <= grace:
        if _slot_ok(r, t):
            return True
        t = _top(t - 1800)
    return False


def next_run(r, now):
    """When the task runs next (0: it's off)."""
    if not r["enabled"]:
        return 0
    if not scheduled(r):
        return int(max(now, r["last_run"] + r["every_hours"] * 3600))
    if _scheduled_due(r, now):
        return int(now)
    t = _top(now) + 3600
    for _ in range(24 * 15):
        if _slot_ok(r, t):
            return t
        t = _top(t + 5400)          # the next hour, over clock changes too
    return 0


def listing(now=None):
    """For Setup: [{key, name, about, enabled, every_hours, last_run, took, status, next_run}]"""
    now = now or int(time.time())
    rows = {r["key"]: r for r in db.rows("SELECT * FROM lib_tasks")}
    out = []
    for key, name, about, *_ in TASKS:
        r = rows.get(key)
        if not r:
            continue
        out.append({**r, "name": name, "about": about, "next_run": next_run(r, now)})
    return out


def update(key, data):
    if key not in KEYS:
        raise KeyError(key)
    r = _row(key)
    enabled = r["enabled"]
    if "enabled" in data:
        enabled = 1 if data["enabled"] in (1, True, "1", "true") else 0
    hours = r["every_hours"]
    if "every_hours" in data:
        try:
            hours = float(data["every_hours"])
        except (TypeError, ValueError):
            raise ValueError("Every: a number of hours")
        if hours != 0.5 and (hours != int(hours) or not 1 <= hours <= MAX_HOURS):
            raise ValueError(f"Every: 30 minutes, or between 1 and {MAX_HOURS} hours")
        hours = 0.5 if hours == 0.5 else int(hours)
    at = r["at_hour"] if r["at_hour"] is not None else -1
    if "at_hour" in data:
        try:
            at = int(float(data["at_hour"] if data["at_hour"] not in (None, "") else -1))
        except (TypeError, ValueError):
            raise ValueError("At: an hour from 0 to 23")
        if not -1 <= at <= 23:
            raise ValueError("At: an hour from 0 to 23")
    day = r["on_day"] if r.get("on_day") is not None else -1
    if "on_day" in data:
        try:
            day = int(float(data["on_day"] if data["on_day"] not in (None, "") else -1))
        except (TypeError, ValueError):
            raise ValueError("On: a day")
    if not -1 <= day <= 6:
        raise ValueError("On: a day of the week")
    if hours != WEEK:
        day = -1
    db.run("UPDATE lib_tasks SET enabled=?, every_hours=?, at_hour=?, on_day=? WHERE key=?", (enabled, hours, at, day, key))
    return listing()


def due(now=None):
    """The first task that's due, or None. Nothing runs without a library turned on."""
    now = now or time.time()
    if not db.one("SELECT 1 FROM lib_shares WHERE enabled=1 LIMIT 1"):
        return None
    rows = {r["key"]: r for r in db.rows("SELECT * FROM lib_tasks WHERE enabled=1")}
    for key in KEYS:
        r = rows.get(key)
        if not r:
            continue
        if scheduled(r):
            if _scheduled_due(r, now):
                return key
        elif now - r["last_run"] >= r["every_hours"] * 3600:
            return key
    return None


def _details(progress):
    if not library.settings()["tmdb_key"]:
        return "Skipped: no TMDB key"
    return library.fetch_details(progress) or "Nothing to update"


def _remove(progress):
    """Take out titles missing long enough, then delete what's left of them (library.tidy)."""
    removed = library.remove_missing()
    tidied = library.tidy()             # "Removed 3 saved positions, …" or "Nothing to clean up"
    return removed if tidied == "Nothing to clean up" else f"{removed}; cleaned up {tidied.removeprefix('Removed ')}"


RUN = {
    "scan": lambda progress: library.scan_all(progress, only_new=True, quick=True),
    "details": _details,
    "lengths": lambda progress: library.measure_lengths(progress),
    "remove": lambda progress: _remove(progress),
}


def record(key, status, started):
    db.run("UPDATE lib_tasks SET last_run=?, took=?, status=? WHERE key=?",
           (int(time.time()), round(time.time() - started, 1), status[:500], key))


def run(key, progress=None, fn=None):
    """Run one task now (in the calling thread: jobs.library_task runs it in the background) and note how it
    went. A task that fails is noted too, so it waits its turn instead of trying again every 15 seconds.
    `fn`: run this instead of the task's own step (a full scan from the Scan button counts as a scan)."""
    if key not in KEYS:
        raise KeyError(key)
    progress = progress or (lambda *a, **k: None)
    started = time.time()
    try:
        msg = (fn or RUN[key])(progress) or "Done"
    except Exception as e:  # noqa: BLE001
        record(key, f"error: {e}", started)
        raise
    record(key, f"ok: {msg}", started)
    return msg


def run_all(progress=None):
    """Every task that's on, now, one after another in the usual order (Run all now). One that fails is noted
    and the rest still run. -> message."""
    progress = progress or (lambda *a, **k: None)
    on = [k for k in KEYS if (_row(k) or {}).get("enabled")]
    names = {k: n for k, n, *_ in TASKS}
    done, failed = 0, []
    for i, key in enumerate(on):
        progress(names[key], i, len(on))
        try:
            run(key, progress)
            done += 1
        except Exception as e:  # noqa: BLE001
            failed.append(f"{names[key]}: {e}")
    msg = f"Ran {done} of {len(on)} tasks"
    return msg + (f". Failed: {'; '.join(failed)}" if failed else "")


def reset():
    """Every task back to how a new install has it (on, its usual schedule, 4 am), and Remove missing videos'
    wait back to its default. When they last ran, and how, is kept. -> listing()"""
    with db.conn() as c:
        for key, _, _, hours, at in TASKS:
            c.execute("UPDATE lib_tasks SET enabled=1, every_hours=?, at_hour=?, on_day=-1 WHERE key=?", (hours, at, key))
    library.save_settings({"remove_after_days": library.DEFAULT_SETTINGS["remove_after_days"]})
    return listing()
