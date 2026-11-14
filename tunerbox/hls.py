"""Live HLS output for players without Media Source Extensions (iPad/iPhone Safari,
older smart TVs). ffmpeg writes a rolling playlist + 2-second segments into a temp
folder per viewer; sessions nobody asks for in a while are stopped. Channels and feeds are read
from a shared tuner (tuner.py), so this ffmpeg only cuts segments; On Demand titles read their own."""
import asyncio
import logging
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config
from .streaming import NO_WINDOW, buffer_seconds, ffmpeg_bin, kill, play_args, track

log = logging.getLogger("tunerbox.hls")
ROOT = Path(tempfile.gettempdir()) / "tunerbox-hls"
IDLE = 30                       # seconds without a request before a session is stopped
SAFE_FILE = re.compile(r"^(index\.m3u8|seg\d{5}\.ts)$")

_sessions = {}                  # sid -> dict(proc, dir, last, client)
_lock = threading.Lock()
_reaper = None


def _cmd(raw_url, mode=None, buffer=0, pre=()):
    if raw_url is None:         # from a tuner on stdin: already in the right format
        src = ["-f", "mpegts", "-i", "pipe:0", "-c", "copy"]
    else:
        inp, vid, aud = play_args(raw_url, mode or config.get("video_mode", "copy"), buffer)
        src = ["-nostdin", *pre, *inp, "-sn", "-dn", *vid, *aud]
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *src,
            "-f", "hls", "-hls_time", "2", "-hls_list_size", str(8 + buffer // 2), "-hls_allow_cache", "0",
            "-hls_flags", "delete_segments+independent_segments+omit_endlist",
            "-hls_segment_filename", "seg%05d.ts", "index.m3u8"]


def _stop(sid):
    with _lock:
        s = _sessions.pop(sid, None)
    if s:
        if s.get("sub"):
            s["sub"].close()
        kill(s["proc"])
        shutil.rmtree(s["dir"], ignore_errors=True)


def stop_client(client):
    for sid in [k for k, s in list(_sessions.items()) if client and s["client"] == client]:
        _stop(sid)


def stop_all():
    for sid in list(_sessions):
        _stop(sid)


def _reap():
    while True:
        time.sleep(5)
        now = time.monotonic()
        for sid, s in list(_sessions.items()):
            if now - s["last"] > IDLE or s["proc"].poll() is not None and now - s["last"] > 5:
                log.info("HLS session %s ended", sid)
                _stop(sid)


def start(raw_url, client=None, mode=None, buffer=None, pre=()):
    """`pre`: extra ffmpeg input options (On Demand: play at normal speed, from a start time)."""
    global _reaper
    buffer = buffer_seconds(buffer)
    if client:
        stop_client(client)               # one channel per viewer
    sid = secrets.token_hex(6)
    d = ROOT / sid
    d.mkdir(parents=True, exist_ok=True)
    sub = None
    try:
        if not pre:             # a channel or feed: share its tuner
            from . import tuner
            sub = tuner.join(raw_url, mode, "aac", buffer, name=f"hls:{client or sid}")
        proc = subprocess.Popen(_cmd(None if sub else raw_url, mode, buffer, pre), cwd=d,
                                stdin=subprocess.PIPE if sub else subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    except OSError:
        if sub:
            sub.close()
        shutil.rmtree(d, ignore_errors=True)
        raise
    track(proc)
    if sub:
        threading.Thread(target=_feed, args=(sub, proc), daemon=True).start()
    err = []
    threading.Thread(target=lambda: err.extend(proc.stderr.read().decode("utf-8", "ignore").splitlines()[-5:]),
                     daemon=True).start()
    with _lock:
        _sessions[sid] = {"proc": proc, "dir": d, "last": time.monotonic(), "client": client, "err": err,
                          "buffer": buffer, "sub": sub}
        if _reaper is None:
            _reaper = threading.Thread(target=_reap, daemon=True)
            _reaper.start()
    return sid


def _feed(sub, proc):
    """Tuner -> this session's ffmpeg. Ends (and ffmpeg with it) when the tuner does."""
    try:
        while True:
            chunk = sub.get()
            if not chunk:
                break
            proc.stdin.write(chunk)
    except (OSError, ValueError):   # ffmpeg stopped
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        sub.close()


async def wait_ready(sid, timeout=25.0):
    """True once the playlist lists enough segments: two to start smoothly, more with the extra cache."""
    s = _sessions.get(sid)
    want = 2 + (s["buffer"] // 2 if s else 0)
    end = time.monotonic() + timeout + (s["buffer"] if s else 0)
    while time.monotonic() < end:
        s = _sessions.get(sid)
        if not s:
            return False
        pl = s["dir"] / "index.m3u8"
        if pl.exists() and pl.read_text(errors="ignore").count(".ts") >= want:
            return True
        if s["proc"].poll() is not None:
            log.warning("HLS ffmpeg exited: %s", " | ".join(s["err"]))
            _stop(sid)
            return False
        s["last"] = time.monotonic()
        await asyncio.sleep(0.25)
    _stop(sid)
    return False


def playlist(sid, path):
    """The live playlist; with the extra cache on, Safari is told to start that far behind live."""
    text = path.read_text(errors="ignore")
    s = _sessions.get(sid)
    if s and s["buffer"] and "#EXT-X-START" not in text:
        text = text.replace("#EXTM3U", f"#EXTM3U\n#EXT-X-START:TIME-OFFSET=-{s['buffer'] + 4},PRECISE=NO", 1)
    return text


def file(sid, name):
    s = _sessions.get(sid)
    if not s or not SAFE_FILE.match(name):
        return None
    s["last"] = time.monotonic()
    p = s["dir"] / name
    return p if p.exists() else None


def cleanup_stale():
    """Remove folders left behind by a previous run."""
    if ROOT.exists():
        for d in ROOT.iterdir():
            if d.name not in _sessions:
                shutil.rmtree(d, ignore_errors=True)
