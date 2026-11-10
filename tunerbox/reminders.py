"""Reminders: "tell me when this starts". Kept on the server so every TV screen pops it up; each screen
times the pop-up itself from GET /api/reminders."""
import time

from . import db, guide

KEEP_AFTER = 3600      # a reminder is dropped this long after its program ends


def upcoming(now=None):
    """Reminders whose program hasn't ended yet, soonest first. Old ones are cleared out on the way."""
    now = int(now or time.time())
    db.run("DELETE FROM reminders WHERE stop < ?", (now - KEEP_AFTER,))
    return db.rows("SELECT * FROM reminders WHERE stop > ? ORDER BY start, number", (now,))


def add(number, start):
    """Remind about the program on channel `number` that's on at `start` (from the guide). None if the
    guide has no such program."""
    ch = guide.channel(number)
    if not ch or not ch["epg"]:
        return None
    p = db.one("SELECT * FROM programs WHERE channel=? AND start<=? AND stop>?", (ch["epg"], start, start))
    if not p:
        return None
    db.run("""INSERT INTO reminders(number, channel_name, title, subtitle, start, stop, created) VALUES (?,?,?,?,?,?,?)
              ON CONFLICT(number, start) DO UPDATE SET title=excluded.title, subtitle=excluded.subtitle,
              stop=excluded.stop""",
           (number, ch["label"] or ch["name"], p["title"], p["subtitle"] or "", p["start"], p["stop"], int(time.time())))
    return db.one("SELECT * FROM reminders WHERE number=? AND start=?", (number, p["start"]))


def remove(rid):
    db.run("DELETE FROM reminders WHERE id=?", (rid,))


def keys(start, stop):
    """{(number, start)} of reminders for programs overlapping start..stop (to mark them in the guide)."""
    return {(r["number"], r["start"]) for r in db.rows(
        "SELECT number, start FROM reminders WHERE stop > ? AND start < ?", (start, stop))}
