"""DVR: one-off and series recordings. A recording is one more reader of the channel's shared tuner
(tuner.py), so recording what a TV is watching doesn't use a second provider connection."""
import logging
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, db, guide, tuner
from .streaming import NO_WINDOW, ffmpeg_bin

log = logging.getLogger("tunerbox.dvr")
_active = {}  # recording id -> tuner.Subscriber
MIN_FREE_START = 2_000_000_000      # don't start a recording with less free space than this
MIN_FREE_RUNNING = 500_000_000      # stop running recordings (keeping what they have) below this
MIN_KEEP = 200_000                  # smaller captures are treated as "no data"


def rec_dir() -> Path:
    p = config.data_path(config.get("recordings_dir", "recordings"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ext(base: Path, ext):
    """base + extension. (Path.with_suffix would cut "Mr. Robot (...)" down to "Mr.ts".)"""
    return base.with_name(base.name + ext)


def _safe(s):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", s or "").strip()[:80] or "Recording"


# ------------------------------------------------------------------ scheduling
def schedule(number, start, stop, title="", subtitle="", descr="", episode="", rule_id=None, image=""):
    ch = guide.channel(number)
    name = ch["name"] if ch else str(number)
    db.run("""INSERT OR IGNORE INTO recordings(number, channel_name, title, subtitle, descr, episode,
              start, stop, rule_id, image) VALUES (?,?,?,?,?,?,?,?,?,?)""",
           (number, name, title or name, subtitle, descr, episode, int(start), int(stop), rule_id, image or ""))
    return db.one("SELECT * FROM recordings WHERE number=? AND start=?", (number, int(start)))


def schedule_program(number, start):
    ch = guide.channel(number)
    if not ch or not ch["epg"]:
        return None
    p = db.one("SELECT * FROM programs WHERE channel=? AND start<=? AND stop>?", (ch["epg"], start, start))
    if not p:
        return None
    return schedule(number, p["start"], p["stop"], p["title"], p["subtitle"], p["descr"], p["episode"], image=p["image"])


def add_rule(title, number=None, new_only=False):
    rid = db.run("INSERT INTO rules(title, number, new_only, created) VALUES (?,?,?,?)",
                 (title, number, int(new_only), int(time.time())))
    apply_rules()
    return rid


def enabled():
    """Setup › Settings › DVR › Recordings."""
    return config.get("dvr_enabled", True) is not False


def apply_rules():
    """Create scheduled recordings for upcoming airings that match series rules."""
    if not enabled():
        return 0
    now = int(time.time())
    chans = guide.channels(True)
    by_epg = {}
    for c in chans:
        if c["epg"] and c["mapped"]:
            by_epg.setdefault(c["epg"], c)  # lowest channel number wins
    made = 0
    for r in db.rows("SELECT * FROM rules"):
        if r["number"] is not None:
            c = guide.channel(r["number"])
            eps = [c["epg"]] if c and c["epg"] else []
        else:
            eps = list(by_epg)
        if not eps:
            continue
        q = ",".join("?" * len(eps))
        progs = db.rows(f"""SELECT * FROM programs WHERE channel IN ({q}) AND start > ?
                            AND lower(title)=lower(?) ORDER BY start""", (*eps, now, r["title"]))
        seen = set()
        for p in progs:
            if r["new_only"] and not p["is_new"]:
                continue
            ident = (p["subtitle"] or p["episode"] or str(p["start"])).lower()
            if ident in seen:
                continue  # same episode on another channel/time
            seen.add(ident)
            dup = db.one("""SELECT id FROM recordings WHERE rule_id=? AND status IN ('done','scheduled','recording')
                            AND lower(coalesce(subtitle,''))=? AND subtitle != ''""", (r["id"], (p["subtitle"] or "").lower()))
            if dup:
                continue
            num = r["number"] if r["number"] is not None else by_epg[p["channel"]]["number"]
            before = db.one("SELECT id FROM recordings WHERE number=? AND start=?", (num, p["start"]))
            if not before:
                schedule(num, p["start"], p["stop"], p["title"], p["subtitle"], p["descr"], p["episode"], r["id"], p["image"])
                made += 1
    return made


LIVE_AHEAD = 3600                       # conflicts(): how far ahead live TV is assumed to stay on


def conflicts():
    """Upcoming recordings that won't get a connection: {recording id: why}. Recordings of the same
    stream share one tuner, so one connection (even on different channel numbers); a channel whose
    backup feed is on a playlist with room is fine. Earlier recordings get first pick, as they do
    when they start. Live TV only counts when recordings don't take connections from it (Setup ›
    Settings › DVR): then what is on now is assumed to stay on for the next LIVE_AHEAD seconds."""
    from .connections import dvr_takes_over, limit_of, live_used
    now = int(time.time())
    before, after = int(config.get("dvr_pad_before", 60)), int(config.get("dvr_pad_after", 180))
    recs = db.rows("SELECT * FROM recordings WHERE status IN ('scheduled','recording') AND stop > ? ORDER BY start, id", (now,))
    takes = dvr_takes_over()
    if not recs or (takes and len(recs) < 2):
        return {}
    srcs = {s["id"]: s for s in db.rows("SELECT * FROM sources")}
    limits = {sid: limit_of(s) for sid, s in srcs.items()}
    if not any(limits.values()):
        return {}
    live = {} if takes else {sid: live_used(sid) for sid, lim in limits.items() if lim}
    feeds = {}                          # channel number -> its feeds (playlist, stream url), in the order they're tried
    for n in {r["number"] for r in recs}:
        c = guide.channel(n)
        feeds[n] = [(x["source_id"], x["url"]) for x in guide.stream_rows(c["streams"])] if c else []
    placed, out = [], {}                # placed: (start, stop, stream url, playlist, title)

    def busiest(sid, a, b, overlap):
        """Most streams on playlist `sid` recording at one moment in [a, b)."""
        mine = [p for p in overlap if p[3] == sid]
        return max((len({p[2] for p in mine if p[0] <= t < p[1]}) for t in {a, *(p[0] for p in mine if a <= p[0] < b)}),
                   default=0)

    def fits(sid, url, a, b, overlap):
        if any(p[2] == url for p in overlap) or not limits.get(sid):
            return True                 # joins a recording of the same stream, or no limit
        return busiest(sid, a, b, overlap) + (live.get(sid, 0) if a < now + LIVE_AHEAD else 0) < limits[sid]

    for r in recs:
        a, b = r["start"] - before, r["stop"] + after
        overlap = [p for p in placed if p[0] < b and a < p[1]]
        mine = feeds.get(r["number"], [])
        pick = next((f for f in mine if fits(*f, a, b, overlap)), None)
        if pick is None and mine:
            first = mine[0][0]
            sids = {f[0] for f in mine}
            others = list(dict.fromkeys(p[4] for p in overlap if p[3] in sids))
            lim = limits.get(first, 0)
            name = srcs[first]["name"] if first in srcs else "the playlist"
            rule = f"{name} allows {lim} connection{'' if lim == 1 else 's'} at a time"
            if others:
                out[r["id"]] = f"Overlaps {' and '.join(others[:2])}{' and others' if len(others) > 2 else ''}: {rule}"
            else:
                out[r["id"]] = f"Live TV is using {name}'s connections ({rule}): the recording waits until one is free"
            continue
        if pick:
            placed.append((a, b, pick[1], pick[0], r["title"]))
    return out


# ------------------------------------------------------------------ recorder
def _start(rec):
    ch = guide.channel(rec["number"])
    streams = guide.stream_rows(ch["streams"]) if ch else []
    if not streams:
        db.run("UPDATE recordings SET status='failed', error='No stream mapped' WHERE id=?", (rec["id"],))
        return
    free = _free_bytes()
    if free is not None and free < MIN_FREE_START:
        db.run("UPDATE recordings SET status='failed', error=? WHERE id=?",
               (f"Not enough disk space ({free / 1e9:.1f} GB free)", rec["id"]))
        log.warning("Recording %s not started: only %.1f GB free", rec["title"], free / 1e9)
        return
    folder = rec_dir() / _safe(rec["title"])
    folder.mkdir(parents=True, exist_ok=True)
    when = datetime.fromtimestamp(rec["start"]).strftime("%Y-%m-%d %H%M")
    parts = [_safe(rec["title"])]
    if rec["episode"]:
        parts.append(_safe(rec["episode"]))
    if rec["subtitle"]:
        parts.append(_safe(rec["subtitle"]))
    base = folder / (" - ".join(parts) + f" ({when})")
    db.run("UPDATE recordings SET status='recording', file=? WHERE id=?", (str(base) + ".mp4", rec["id"]))
    threading.Thread(target=_record, args=(rec, streams, base), daemon=True).start()


def _record(rec, streams, base: Path):
    from .connections import Full, dvr_takes_over
    stop_at = rec["stop"] + int(config.get("dvr_pad_after", 180))
    ts = _ext(base, ".ts")
    err = ""
    attempt = tries = waiting = 0
    take = logged = False
    while attempt < len(streams) * 3:  # cycle through alternates on failure
        s = streams[tries % len(streams)]
        tries += 1
        if stop_at - int(time.time()) <= 5:
            break
        try:
            sub = tuner.join(s["url"], s.get("video_mode") or "copy", "aac", 0, any_mode=True, name=f"dvr:{rec['id']}",
                             source_id=s.get("source_id"), take=take)
        except OSError as e:            # ffmpeg missing
            err = str(e)
            break
        except Full as e:               # other recordings (or live TV) hold the connections
            if not take:
                waiting += 1
                if waiting % len(streams):
                    continue            # try the next feed: it may be on a playlist with room
                if dvr_takes_over():    # every feed is full: now stop live TV on the next one tried
                    take = True
                    continue
            take = False                # nothing could be stopped (only recordings on it): wait
            if not logged:
                logged = True
                log.warning("Recording %s waiting for a connection: %s", rec["title"], e)
                db.run("UPDATE recordings SET error=? WHERE id=?", (f"No connection free: {e}", rec["id"]))
            if not _wait_while_recording(rec["id"], 15):
                break                   # cancelled or deleted while waiting
            continue
        if logged:
            log.info("Recording %s got a connection", rec["title"])
            db.run("UPDATE recordings SET error='' WHERE id=?", (rec["id"],))
        waiting, take, logged = 0, False, False
        attempt += 1
        _active[rec["id"]] = sub
        try:
            _capture(sub, ts, stop_at)
        finally:
            sub.close()
        err = sub.tuner.error()[-300:]
        if _active.pop(rec["id"], None) is None:
            break  # cancelled
        if int(time.time()) >= stop_at - 5:
            break
        log.warning("Recording %s interrupted (attempt %d): %s", rec["title"], attempt, err)
        time.sleep(3)
    row = db.one("SELECT status, error FROM recordings WHERE id=?", (rec["id"],))
    if not row:                     # deleted while recording
        ts.unlink(missing_ok=True)
        return
    _finish_file(rec["id"], base, cancelled=row["status"] == "cancelled", err=err, note=row["error"] or "")


def _wait_while_recording(rec_id, secs):
    """Sleep up to `secs`; False as soon as the recording is cancelled or deleted."""
    end = time.time() + secs
    while time.time() < end:
        if not db.one("SELECT 1 FROM recordings WHERE id=? AND status='recording'", (rec_id,)):
            return False
        time.sleep(1)
    return True


def _capture(sub, ts: Path, stop_at):
    """Append the tuner's stream to `ts` until stop_at, the tuner ends or the recording is stopped."""
    with open(ts, "ab") as out:
        while time.time() < stop_at:
            try:
                chunk = sub.get(timeout=1)
            except queue.Empty:
                continue
            if not chunk:
                return
            out.write(chunk)


def _finish_file(rec_id, base: Path, cancelled=False, err="", note=""):
    """Turn a finished (or cut-short) capture into an MP4 and set the recording's final status.
    `note` is kept as the error text of a recording that still has a file (e.g. "stopped early")."""
    ts = _ext(base, ".ts")
    if ts.exists() and ts.stat().st_size > MIN_KEEP:
        mp4 = _ext(base, ".mp4")
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                            "-fflags", "+genpts", "-i", str(ts), "-map", "0:v?", "-map", "0:a?", "-c", "copy",
                            "-movflags", "+faststart", str(mp4)],
                           capture_output=True, creationflags=NO_WINDOW)
        if r.returncode == 0 and mp4.exists():
            ts.unlink(missing_ok=True)
            final = mp4
        else:
            final = ts
        db.run("UPDATE recordings SET status=?, file=?, error=? WHERE id=?",
               ("cancelled" if cancelled else "done", str(final), note, rec_id))
    else:
        ts.unlink(missing_ok=True)
        if cancelled:   # stopped before anything worth keeping was recorded
            db.run("UPDATE recordings SET status='cancelled', file='', error='' WHERE id=?", (rec_id,))
        else:
            db.run("UPDATE recordings SET status='failed', file='', error=? WHERE id=?",
                   (note or err or "No data", rec_id))


def _free_bytes():
    try:
        return shutil.disk_usage(rec_dir()).free
    except OSError:
        return None


def progress(rec_id, position, duration):
    """Where playback of a recording got to. Near the end counts as watched (and starts over next time)."""
    position, duration = max(0, int(position)), max(0, int(duration))
    done = bool(duration) and position >= duration - 60
    db.run("UPDATE recordings SET position=?, duration=?, watched=?, watched_at=? WHERE id=?",
           (0 if done else position, duration, int(done), int(time.time()), rec_id))


def _halt(sub):
    if sub:
        sub.close()


def cancel(rec_id, delete_file=False):
    rec = db.one("SELECT * FROM recordings WHERE id=?", (rec_id,))
    if not rec:
        return
    if rec["status"] == "recording":
        db.run("UPDATE recordings SET status='cancelled' WHERE id=?", (rec_id,))
        _halt(_active.pop(rec_id, None))
    elif rec["status"] == "scheduled":
        db.run("DELETE FROM recordings WHERE id=?", (rec_id,))
    elif delete_file:
        if rec["file"]:
            Path(rec["file"]).unlink(missing_ok=True)
        db.run("DELETE FROM recordings WHERE id=?", (rec_id,))


def tick():
    if _active:
        free = _free_bytes()
        if free is not None and free < MIN_FREE_RUNNING:
            for rid in list(_active):
                log.warning("Disk almost full (%.1f GB free): stopping recording %s", free / 1e9, rid)
                db.run("UPDATE recordings SET error='Stopped early: disk almost full' WHERE id=?", (rid,))
                _halt(_active.pop(rid, None))
    now = int(time.time())
    pad = int(config.get("dvr_pad_before", 60))
    for rec in db.rows("SELECT * FROM recordings WHERE status='scheduled' AND start-?<=? ORDER BY start", (pad, now)):
        if not enabled():           # recordings turned off: nothing starts; one that's over is marked skipped
            if rec["stop"] <= now:
                db.run("UPDATE recordings SET status='failed', error='Skipped: recordings were turned off' WHERE id=?", (rec["id"],))
            continue
        if rec["stop"] <= now:
            db.run("UPDATE recordings SET status='failed', error='Missed (app was not running)' WHERE id=?", (rec["id"],))
        else:
            log.info("Recording: %s on %s", rec["title"], rec["number"])
            _start(rec)


def recover():
    """Recordings left 'recording' from a previous run can't be resumed, but what was captured
    before TunerBox stopped is kept (in the background: making the MP4 can take a while)."""
    left = db.rows("SELECT id, file FROM recordings WHERE status='recording'")
    if not left:
        return

    def salvage():
        for r in left:
            if r["file"]:
                _finish_file(r["id"], Path(r["file"].removesuffix(".mp4")), note="Partial: TunerBox stopped while recording")
            else:
                db.run("UPDATE recordings SET status='failed', error='Interrupted (app restarted)' WHERE id=?", (r["id"],))
    threading.Thread(target=salvage, daemon=True).start()
