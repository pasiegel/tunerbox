"""ffmpeg-based live playback: any input (HLS, TS, RTMP...) -> MPEG-TS with AAC audio."""
import asyncio
import logging
import os
import queue
import shutil
import subprocess
import threading
import time

import anyio

from pathlib import Path

from . import config
from .probe import split_url

log = logging.getLogger("tunerbox.stream")
_sessions = {}
_playing = {}           # id(proc) -> what a ready-made-command stream (On Demand) plays, for Setup › Tuners
_lock = threading.Lock()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ffmpeg_path = None


def ffmpeg_bin():
    """Real ffmpeg executable. Chocolatey/scoop 'shims' spawn a child process that
    survives when the shim is killed, so look through them to the actual binary."""
    global _ffmpeg_path
    want = config.get("ffmpeg", "ffmpeg")
    if _ffmpeg_path and _ffmpeg_path[0] == want:
        return _ffmpeg_path[1]
    path = shutil.which(want) or want
    if want == "ffmpeg" and config.bundled_ffmpeg():
        path = str(config.bundled_ffmpeg())
    p = Path(path)
    if os.name == "nt" and p.exists():
        root = p.parent.parent  # e.g. chocolatey/bin/ffmpeg.exe or scoop/shims/ffmpeg.exe
        for pattern in ("lib/ffmpeg*/tools/**/ffmpeg.exe", "apps/ffmpeg/current/bin/ffmpeg.exe"):
            hits = sorted(root.glob(pattern))
            if p.parent.name.lower() in ("bin", "shims") and hits:
                path = str(hits[0])
                break
    _ffmpeg_path = (want, path)
    return path


def _make_job():
    """Windows job object: every ffmpeg we start is killed when TunerBox exits,
    even if it crashes or is force-closed."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        info = EXT()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        return k32, job
    except Exception as e:                      # not fatal: just no crash cleanup
        log.debug("job object unavailable: %s", e)
        return None


_job = _make_job()


def track(proc):
    """Tie a child process's life to TunerBox's."""
    if _job:
        import ctypes
        from ctypes import wintypes
        k32, job = _job
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x0001 | 0x0100, False, proc.pid)   # PROCESS_TERMINATE | SET_QUOTA
        if h:
            k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(h))
            k32.CloseHandle(wintypes.HANDLE(h))
    return proc


def kill(proc):
    """Stop ffmpeg (and any children) right away."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=NO_WINDOW)
    else:
        proc.kill()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def buffer_seconds(value=None):
    try:
        v = int(config.get("stream_buffer", 0) if value is None or value < 0 else value)
    except (TypeError, ValueError):
        v = 0
    return max(0, min(v, 120))


def input_args(raw_url, buffer=0):
    url, headers = split_url(raw_url)
    args = ["-user_agent", headers.pop("User-Agent")]
    if headers:
        args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    if url.lower().startswith("http"):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                 "-reconnect_delay_max", "4", "-rw_timeout", "15000000"]
        if buffer and ".m3u8" in url.lower().split("?")[0]:
            # HLS sources keep a few segments of history: start further back so the cache fills at once
            args += ["-live_start_index", str(-(3 + buffer // 4))]
    args += ["-fflags", "+genpts+discardcorrupt", "-i", url]
    return args


def video_args(mode=None):
    mode = mode or config.get("video_mode", "copy")
    if mode == "h264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "22",
                "-g", "60", "-pix_fmt", "yuv420p", "-vf", "scale=-2:'min(1080,ih)'"]
    if mode == "roku720":
        # deinterlace only if the source is interlaced, never upscale, cap bitrate for old Roku sticks
        return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "23",
                "-maxrate", "8M", "-bufsize", "16M", "-g", "60", "-profile:v", "high",
                "-pix_fmt", "yuv420p", "-vf", "yadif=0:-1:1,scale=-2:'min(720,ih)'"]
    return ["-c:v", "copy"]


def play_args(raw_url, mode=None, buffer=0, audio="aac"):
    """(input args, video args, audio args) for anything TunerBox plays: a playlist/tuner URL,
    or a local channel ('media:<id>')."""
    if raw_url.startswith("media:"):
        from . import media
        return media.ffmpeg_args(int(raw_url[6:]), buffer)
    a = ["-c:a", "aac", "-ac", "2", "-b:a", "192k"] if audio == "aac" else ["-c:a", "copy"]
    return input_args(raw_url, buffer), video_args(mode), a


def live_cmd(raw_url, mode=None, audio="aac", buffer=0):
    inp, vid, aud = play_args(raw_url, mode, buffer, audio)
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
            *inp, "-sn", "-dn", *vid, *aud,
            "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]


def _finish(proc):
    kill(proc)
    try:
        proc.stdout.close()
    except OSError:
        pass


def stop_client(client_id):
    from . import tuner
    with _lock:
        p = _sessions.pop(client_id, None)
    kill(p)
    tuner.stop_client(client_id)


def _pump(proc, q, stop, wake):
    """Read ffmpeg as fast as it produces, so a viewer that falls behind (Wi-Fi hiccup) is fed from
    this cache instead of making ffmpeg - and the provider connection - wait. Runs in its own thread;
    `wake()` tells the event loop a chunk is ready. b"" marks the end."""
    try:
        while not stop.is_set():
            chunk = proc.stdout.read(65536)
            while not stop.is_set():
                try:
                    q.put(chunk, timeout=0.5)
                    break
                except queue.Full:
                    continue
            else:
                break               # viewer gone: nobody is reading any more
            wake()
            if not chunk:
                return              # end of stream, already queued
    except (OSError, ValueError):   # pipe closed under us (ffmpeg killed)
        pass
    while not stop.is_set():        # read failed: make sure the reader sees an end, even with a full cache
        try:
            q.put(b"", timeout=0.5)
            wake()
            break
        except queue.Full:
            continue


def _waker(loop, event):
    def wake():
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:        # event loop already closed (server shutting down)
            pass
    return wake


def playing():
    """On Demand (and other ready-made-command) streams running now: [{url, label, client, since}].
    `url` holds the provider login: never send it to a page as is."""
    with _lock:
        return [dict(v) for v in _playing.values()]


async def ffmpeg_stream(raw_url, client_id=None, mode=None, audio="aac", request=None, buffer=None, cmd=None, label=""):
    """Async generator yielding MPEG-TS bytes. Kills ffmpeg when the viewer goes away.
    `cmd` runs a ready-made ffmpeg command instead (On Demand, local channel pieces); `label` names
    what it plays in Setup › Tuners.

    Waiting for data never holds a worker thread: the reader thread wakes this coroutine through an
    asyncio.Event, so many viewers can't starve the thread pool the rest of the API runs on.

    Channels and feeds (no `cmd`) play from a shared tuner (tuner.py): one provider connection
    however many screens watch the same one."""
    if cmd is None:
        from . import tuner
        gen = tuner.watch(raw_url, client_id, mode, audio, request, buffer)
        try:
            async for chunk in gen:
                yield chunk
        finally:
            await gen.aclose()
        return
    if client_id:   # stop this viewer's previous channel first (Xtream allows few connections), off the event loop
        await anyio.to_thread.run_sync(stop_client, client_id)
    buffer = buffer_seconds(buffer)
    log.info("Stream start for %s: %s", client_id or "-", split_url(raw_url)[0][-60:])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, bufsize=0)
    track(proc)
    with _lock:
        if client_id:
            _sessions[client_id] = proc
        if raw_url:
            _playing[id(proc)] = {"url": raw_url, "label": label, "client": client_id or "", "since": int(time.time())}
    err = []
    threading.Thread(target=lambda: err.extend(proc.stderr.read().decode("utf-8", "ignore").splitlines()[-5:]),
                     daemon=True).start()
    # read-ahead cache: about 2 MB normally, up to ~80 MB with the extra cache turned on
    q = queue.Queue(maxsize=32 + buffer * 40)
    stop = threading.Event()
    ready = asyncio.Event()
    threading.Thread(target=_pump, args=(proc, q, stop, _waker(asyncio.get_running_loop(), ready)),
                     daemon=True).start()
    sent = 0
    last_check = time.monotonic()
    try:
        while True:
            try:
                chunk = q.get_nowait()
            except queue.Empty:
                ready.clear()
                try:                # re-check after clearing, so a chunk put just before isn't missed
                    chunk = q.get_nowait()
                except queue.Empty:
                    try:
                        await asyncio.wait_for(ready.wait(), 2)
                    except asyncio.TimeoutError:
                        # nothing from the feed for a while: still notice a viewer who left
                        if request is not None and await request.is_disconnected():
                            break
                    continue
            if not chunk:
                break
            sent += len(chunk)
            yield chunk
            if request is not None and time.monotonic() - last_check > 2:
                last_check = time.monotonic()
                if await request.is_disconnected():
                    break
    finally:
        stop.set()
        with _lock:
            if client_id and _sessions.get(client_id) is proc:
                _sessions.pop(client_id, None)
            _playing.pop(id(proc), None)
        # taskkill takes a moment: never make the whole server wait for it
        threading.Thread(target=_finish, args=(proc,), daemon=True).start()
        log.info("Stream end for %s after %.1f MB", client_id or "-", sent / 1e6)
        if sent == 0:
            log.warning("No data from %s: %s", split_url(raw_url)[0][:120], " | ".join(err))
