"""DVR: one-off and series recordings, captured with ffmpeg."""
import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config, db, guide
from .streaming import NO_WINDOW, ffmpeg_bin, kill, play_args, track

log = logging.getLogger("tunerbox.dvr")
_active = {}  # recording id -> Popen
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
def schedule(number, start, stop, title="", subtitle="", descr="", episode="", rule_id=None):
    ch = guide.channel(number)
    name = ch["name"] if ch else str(number)
    db.run("""INSERT OR IGNORE INTO recordings(number, channel_name, title, subtitle, descr, episode,
              start, stop, rule_id) VALUES (?,?,?,?,?,?,?,?,?)""",
           (number, name, title or name, subtitle, descr, episode, int(start), int(stop), rule_id))
    return db.one("SELECT * FROM recordings WHERE number=? AND start=?", (number, int(start)))


def schedule_program(number, start):
    ch = guide.channel(number)
    if not ch or not ch["epg"]:
        return None
    p = db.one("SELECT * FROM programs WHERE channel=? AND start<=? AND stop>?", (ch["epg"], start, start))
    if not p:
        return None
    return schedule(number, p["start"], p["stop"], p["title"], p["subtitle"], p["descr"], p["episode"])


def add_rule(title, number=None, new_only=False):
    rid = db.run("INSERT INTO rules(title, number, new_only, created) VALUES (?,?,?,?)",
                 (title, number, int(new_only), int(time.time())))
    apply_rules()
    return rid


def apply_rules():
    """Create scheduled recordings for upcoming airings that match series rules."""
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
                schedule(num, p["start"], p["stop"], p["title"], p["subtitle"], p["descr"], p["episode"], r["id"])
                made += 1
    return made


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
    stop_at = rec["stop"] + int(config.get("dvr_pad_after", 180))
    ts = _ext(base, ".ts")
    err = ""
    for attempt, s in enumerate(streams * 3):  # cycle through alternates on failure
        remaining = stop_at - int(time.time())
        if remaining <= 5:
            break
        inp, vid, aud = play_args(s["url"], s.get("video_mode") or "copy")
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
               *inp, "-sn", "-dn", *vid, *aud, "-t", str(remaining), "-f", "mpegts", "pipe:1"]
        with open(ts, "ab") as out:
            p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                 creationflags=NO_WINDOW)
            _active[rec["id"]] = track(p)
            _, e = p.communicate()
            err = e.decode("utf-8", "ignore")[-300:]
        if _active.pop(rec["id"], None) is None:
            break  # cancelled
        if int(time.time()) >= stop_at - 5:
            break
        log.warning("Recording %s interrupted (attempt %d): %s", rec["title"], attempt + 1, err)
        time.sleep(3)
    row = db.one("SELECT status, error FROM recordings WHERE id=?", (rec["id"],))
    if not row:                     # deleted while recording
        ts.unlink(missing_ok=True)
        return
    _finish_file(rec["id"], base, cancelled=row["status"] == "cancelled", err=err, note=row["error"] or "")


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


def cancel(rec_id, delete_file=False):
    rec = db.one("SELECT * FROM recordings WHERE id=?", (rec_id,))
    if not rec:
        return
    if rec["status"] == "recording":
        db.run("UPDATE recordings SET status='cancelled' WHERE id=?", (rec_id,))
        p = _active.pop(rec_id, None)
        kill(p)
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
                kill(_active.pop(rid, None))
    now = int(time.time())
    pad = int(config.get("dvr_pad_before", 60))
    for rec in db.rows("SELECT * FROM recordings WHERE status='scheduled' AND start-?<=? ORDER BY start", (pad, now)):
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
