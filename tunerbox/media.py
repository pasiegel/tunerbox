"""Local channels: folders of video files (local or \\\\server\\share) played as linear TV channels,
in the spirit of ErsatzTV and dizqueTV.

How it plays: each channel has a fixed schedule worked out from the file durations, so tuning in
lands mid-show like real TV and the guide is exact. Every file is turned into the same format
(H.264 + AAC, same size and frame rate) by its own ffmpeg (`piece_cmd`, served at /api/media/piece);
an outer ffmpeg chains those pieces with the concat demuxer, reading them in real time, so the
viewer gets one unbroken stream however mixed the files are.
"""
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from . import config, db

log = logging.getLogger("tunerbox.media")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".mts", ".wmv", ".mpg", ".mpeg",
             ".webm", ".flv", ".vob", ".divx", ".ogv", ".3gp"}
SKIP = re.compile(r"(^|[\W_])(sample|trailer|featurette|behind[\W_]?the[\W_]?scenes|deleted[\W_]?scenes)([\W_]|$)", re.I)
ORDERS = ("order", "shuffle", "rotate")
EPG_HOURS = 48
LIST_DIR = Path(tempfile.gettempdir()) / "tunerbox-media"

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    folders TEXT DEFAULT '[]',        -- JSON list of folders (local paths or \\\\server\\share)
    play_order TEXT DEFAULT 'order',  -- order | shuffle | rotate
    recurse INTEGER DEFAULT 1,
    logo TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    epoch INTEGER DEFAULT 0,          -- when the schedule started
    status TEXT DEFAULT '',
    item_count INTEGER DEFAULT 0,
    total_secs REAL DEFAULT 0,
    updated_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media_items (
    channel_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    path TEXT NOT NULL,
    duration REAL NOT NULL,
    audio INTEGER DEFAULT 1,
    title TEXT, subtitle TEXT, episode TEXT, show TEXT,
    PRIMARY KEY (channel_id, idx)
);
CREATE TABLE IF NOT EXISTS media_files (      -- probe cache, shared by every channel
    path TEXT PRIMARY KEY,
    size INTEGER, mtime INTEGER,
    duration REAL, audio INTEGER, video INTEGER
);
"""


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ channels
def channels():
    out = db.rows("SELECT * FROM media_channels ORDER BY number")
    for ch in out:
        ch["folders"] = json.loads(ch["folders"] or "[]")
    return out


def channel(cid):
    ch = db.one("SELECT * FROM media_channels WHERE id=?", (cid,))
    if ch:
        ch["folders"] = json.loads(ch["folders"] or "[]")
    return ch


def used_numbers(exclude_id=None):
    nums = {r["number"] for r in db.rows("SELECT number FROM lineup")}
    nums |= {r["number"] for r in db.rows("SELECT number FROM extras WHERE number IS NOT NULL")}
    nums |= {r["number"] for r in db.rows("SELECT id, number FROM media_channels") if r["id"] != exclude_id}
    return nums


def next_number():
    used = used_numbers()
    n = int(config.get("media_channel_start", 2000))
    while n in used:
        n += 1
    return n


def _clean(data, cid=None):
    name = (data.get("name") or "").strip()[:60]
    folders = data.get("folders") or []
    if isinstance(folders, str):
        folders = folders.splitlines()
    folders = [f.strip().strip('"') for f in folders if f.strip()]
    if not name:
        raise ValueError("Give the channel a name")
    if not folders:
        raise ValueError("Add at least one folder")
    try:
        number = int(data.get("number") or 0)
    except (TypeError, ValueError):
        raise ValueError("Channel number must be a number")
    if number <= 0:
        number = next_number()
    if number in used_numbers(cid):
        raise ValueError(f"Channel {number} is already used")
    order = data.get("play_order") if data.get("play_order") in ORDERS else "order"
    return {"name": name, "number": number, "folders": json.dumps(folders), "play_order": order,
            "recurse": 1 if data.get("recurse", True) else 0, "logo": (data.get("logo") or "").strip(),
            "enabled": 1 if data.get("enabled", True) else 0}


def create(data):
    d = _clean(data)
    return db.run("""INSERT INTO media_channels(number, name, folders, play_order, recurse, logo, enabled, epoch, status)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (d["number"], d["name"], d["folders"], d["play_order"], d["recurse"], d["logo"], d["enabled"],
                   int(time.time()), "Waiting to scan"))


def update(cid, data):
    old = channel(cid)
    if not old:
        raise ValueError("No such channel")
    d = _clean({**old, **data}, cid)
    db.run("""UPDATE media_channels SET number=?, name=?, folders=?, play_order=?, recurse=?, logo=?, enabled=?
              WHERE id=?""", (d["number"], d["name"], d["folders"], d["play_order"], d["recurse"], d["logo"],
                              d["enabled"], cid))
    _order.cache_clear()
    return json.loads(d["folders"]) != old["folders"] or d["recurse"] != old["recurse"]   # needs a rescan


def delete(cid):
    with db.conn() as c:
        c.execute("DELETE FROM media_channels WHERE id=?", (cid,))
        c.execute("DELETE FROM media_items WHERE channel_id=?", (cid,))
        c.execute("DELETE FROM programs WHERE channel=?", (f"m:{cid}",))
    _order.cache_clear()


# ------------------------------------------------------------------ scanning
def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


EP = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})|(\d{1,2})x(\d{2,3})")
JUNK = re.compile(r"\b(2160p|1080p|720p|480p|x264|x265|h\.?264|h\.?265|hevc|web[ .-]?dl|webrip|bluray|brrip|hdtv|"
                  r"dvdrip|aac|ac3|dts|10bit|proper|repack)\b.*$", re.I)


def parse_name(path, root):
    """'Show - S01E02 - Title.mkv' / 'Show.S01E02.Title.1080p.mkv' / 'Movie (1999).mkv'
    -> (show/title, episode title, 'S01E02')."""
    p = Path(path)
    stem = p.stem
    m = EP.search(stem)
    if m:
        s, e = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        ep = f"S{int(s):02d}E{int(e):02d}"
        before, after = stem[:m.start()], stem[m.end():]
        show = _tidy(before)
        if not show:            # 'S01E02.mkv' inside 'Show/Season 1/'
            parent = p.parent
            if re.match(r"(?i)^(season|series|s)\s*\d+$", parent.name) and parent.parent != parent:
                parent = parent.parent
            show = parent.name
        return show, _tidy(JUNK.sub("", after)), ep
    return _tidy(JUNK.sub("", stem)), "", ""


def _tidy(s):
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s*[-–]\s*$|^\s*[-–]\s*", "", s.strip())
    s = re.sub(r"\[[^\]]*\]", "", s)              # [1080p], [group]
    s = re.sub(r"\s{2,}", " ", s).strip(" -[]")
    return s


DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe(path):
    """-> (duration seconds, has audio, has video) using ffmpeg itself (no ffprobe needed)."""
    from .streaming import ffmpeg_bin
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", path], capture_output=True,
                           timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return 0.0, False, False
    err = r.stderr.decode("utf-8", "ignore")
    m = DUR.search(err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    video = bool(re.search(r"Stream #\S+.*Video: (?!mjpeg|png|bmp)", err))
    return dur, " Audio: " in err, video


def reachable(folder, timeout=10):
    """os.path.isdir, but an offline \\server gives up after `timeout` seconds instead of ~30."""
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(os.path.isdir, folder).result(timeout=timeout)
    except Exception:  # noqa: BLE001  (timeout)
        return False
    finally:
        ex.shutdown(wait=False)


def _walk(folder, recurse):
    if recurse:
        for dp, dns, fns in os.walk(folder):
            dns[:] = sorted((d for d in dns if not d.startswith(".") and not SKIP.search(d)), key=_natural)
            for f in fns:
                yield os.path.join(dp, f)
    else:
        for f in os.listdir(folder):
            yield os.path.join(folder, f)


def scan(cid, progress=None):
    """Find the channel's videos, read their durations (cached) and save the item list."""
    ch = channel(cid)
    if not ch:
        return "No such channel"
    files, missing = [], []
    for folder in ch["folders"]:
        if not reachable(folder):
            missing.append(folder)
            continue
        for f in _walk(folder, ch["recurse"]):
            if Path(f).suffix.lower() in VIDEO_EXT and not SKIP.search(Path(f).stem):
                files.append((f, folder))
    files.sort(key=lambda x: _natural(x[0]))
    cache = {r["path"]: r for r in db.rows("SELECT * FROM media_files")}
    todo, info = [], {}
    for f, _ in files:
        try:
            st = os.stat(f)
        except OSError:
            continue
        c = cache.get(f)
        if c and c["size"] == st.st_size and c["mtime"] == int(st.st_mtime):
            info[f] = c
        else:
            todo.append((f, st))
    done = [0]
    lock = threading.Lock()

    def one(item):
        f, st = item
        dur, audio, video = probe(f)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], len(todo))
        return f, {"path": f, "size": st.st_size, "mtime": int(st.st_mtime), "duration": dur,
                   "audio": int(audio), "video": int(video)}
    if todo:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for f, row in ex.map(one, todo):
                info[f] = row
        with db.conn() as c:
            c.executemany("INSERT OR REPLACE INTO media_files VALUES (?,?,?,?,?,?)",
                          [(r["path"], r["size"], r["mtime"], r["duration"], r["audio"], r["video"])
                           for f, r in info.items() if any(f == t[0] for t in todo)])
    items = []
    for f, root in files:
        r = info.get(f)
        if not r or not r["video"] or r["duration"] < 20:
            continue
        show, sub, ep = parse_name(f, root)
        items.append((cid, len(items), f, r["duration"], r["audio"], show or Path(f).stem, sub, ep,
                      _show_key(f, root)))
    total = sum(i[3] for i in items)
    if missing and not items:
        status = "error: can't open " + ", ".join(missing)
    elif not items:
        status = "error: no videos found"
    else:
        length = f"{total / 3600:.1f} hours" if total >= 3600 else f"{max(1, round(total / 60))} min"
        status = f"ok: {len(items)} videos, {length}" + (f" (can't open {', '.join(missing)})" if missing else "")
    with db.conn() as c:
        c.execute("DELETE FROM media_items WHERE channel_id=?", (cid,))
        c.executemany("INSERT INTO media_items VALUES (?,?,?,?,?,?,?,?,?)", items)
        c.execute("UPDATE media_channels SET status=?, item_count=?, total_secs=?, updated_at=? WHERE id=?",
                  (status, len(items), total, int(time.time()), cid))
    _order.cache_clear()
    write_epg(cid)
    log.info("Local channel %s: %s", ch["name"], status)
    return status


def _show_key(path, root):
    """The show a file belongs to, for 'rotate': its first folder below the channel folder."""
    rel = os.path.relpath(path, root)
    parts = Path(rel).parts
    return parts[0] if len(parts) > 1 else ""


# ------------------------------------------------------------------ schedule
def _items(cid):
    return db.rows("SELECT * FROM media_items WHERE channel_id=? ORDER BY idx", (cid,))


@lru_cache(maxsize=64)
def _order(cid, cycle, mode, count):
    """Item indexes in play order for one pass through the channel (each pass reshuffles)."""
    items = _items(cid)
    idx = list(range(len(items)))
    if mode == "shuffle":
        random.Random(f"{cid}:{cycle}").shuffle(idx)
    elif mode == "rotate":       # one episode from each show in turn, every show in episode order
        shows = {}
        for i in idx:
            shows.setdefault(items[i]["show"], []).append(i)
        queues = [shows[k] for k in sorted(shows, key=_natural)]
        random.Random(f"{cid}:{cycle}").shuffle(queues)
        idx, pos = [], [0] * len(queues)
        while len(idx) < len(items):
            for q, lst in enumerate(queues):
                if pos[q] < len(lst):
                    idx.append(lst[pos[q]])
                    pos[q] += 1
    return tuple(idx)


def schedule(cid, start, stop, ch=None):
    """[(item, air start, offset into the file)] covering start..stop."""
    ch = ch or channel(cid)
    items = _items(cid)
    if not ch or not items:
        return []
    total = sum(i["duration"] for i in items)
    if total <= 0:
        return []
    epoch = ch["epoch"] or 0
    elapsed = max(0.0, start - epoch)
    cycle = int(elapsed // total)
    t = epoch + cycle * total
    out = []
    while t < stop and len(out) < 5000:
        for i in _order(cid, cycle, ch["play_order"], len(items)):
            it = items[i]
            end = t + it["duration"]
            if end > start:
                out.append((it, t, max(0.0, start - t)))
            t = end
            if t >= stop:
                break
        cycle += 1
    return out


def now_playing(cid):
    s = schedule(cid, time.time(), time.time() + 1)
    return s[0] if s else None


def write_epg(cid=None):
    """Guide listings for the next two days (and the last few hours)."""
    now = int(time.time())
    for ch in ([channel(cid)] if cid else channels()):
        if not ch:
            continue
        rows = []
        for it, t, _ in schedule(ch["id"], now - 3 * 3600, now + EPG_HOURS * 3600, ch):
            start = int(round(t))
            rows.append((f"m:{ch['id']}", start, int(round(t + it["duration"])), it["title"], it["subtitle"] or "",
                         "", it["episode"] or "", "Local", "", "", 0, 0))
        with db.conn() as c:
            c.execute("DELETE FROM programs WHERE channel=?", (f"m:{ch['id']}",))
            c.executemany("INSERT OR REPLACE INTO programs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.set_meta("media_epg", now)


# ------------------------------------------------------------------ playback
_self = {"port": None}


def set_port(port):
    if port:
        _self["port"] = port


def _base():
    return f"http://127.0.0.1:{_self['port'] or config.get('port', 8642)}"


def canvas():
    return (1280, 720) if str(config.get("media_resolution", "1080")) == "720" else (1920, 1080)


def ffmpeg_args(cid, buffer=0):
    """(input args, video args, audio args) for the outer ffmpeg of a local channel."""
    LIST_DIR.mkdir(parents=True, exist_ok=True)
    for old in LIST_DIR.glob("*.txt"):           # tidy lists from earlier sessions
        try:
            if time.time() - old.stat().st_mtime > 86400:
                old.unlink()
        except OSError:
            pass
    now = time.time()
    lines = ["ffconcat version 1.0"]
    for it, t, off in schedule(cid, now, now + 12 * 3600):
        lines.append(f"file '{_base()}/api/media/piece/{cid}/{it['idx']}?ss={off:.2f}'")
        lines.append(f"duration {max(0.5, it['duration'] - off):.3f}")
    lst = LIST_DIR / f"{cid}-{int(now * 1000)}.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    burst = max(3, int(buffer or 0) + 2)
    inp = ["-readrate", "1", "-readrate_initial_burst", str(burst), "-f", "concat", "-safe", "0",
           "-protocol_whitelist", "file,http,tcp", "-i", str(lst), "-map", "0:v:0", "-map", "0:a:0?"]
    return inp, ["-c:v", "copy"], ["-c:a", "copy"]


def piece_cmd(cid, idx, ss=0.0):
    """ffmpeg that turns one file into the channel's common format, starting `ss` seconds in."""
    from .streaming import ffmpeg_bin
    it = db.one("SELECT * FROM media_items WHERE channel_id=? AND idx=?", (cid, idx))
    if not it:
        return None
    w, h = canvas()
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if ss > 0.5:
        cmd += ["-ss", f"{ss:.2f}"]
    cmd += ["-i", it["path"]]
    if it["audio"]:
        amap = ["-map", "0:a:0"]
    else:                                         # silent files still need an audio track
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = ["-map", "1:a:0", "-shortest"]
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"setsar=1,fps=30000/1001,format=yuv420p")
    cmd += ["-map", "0:v:0", *amap, "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "10M", "-bufsize", "20M",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-profile:v", "high",
            "-af", "aresample=48000:async=1,aformat=channel_layouts=stereo",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-sn", "-dn", "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]
    return cmd
