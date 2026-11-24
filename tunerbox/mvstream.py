"""Multiview for screens that play one video at a time (a Roku: its player can't run two at once), so
TunerBox tiles the pictures into one stream for it.

Each picture is read from the shared tuner like any screen's channel, as "<screen>~<slot>" (the main
picture, slot 1, as "<screen>"): the client ids the web's multiview tiles use, so the playlists' connection
limits and multiview.plan count them the same way. One ffmpeg reads the pictures (over local sockets, so
it works on Windows and in Docker alike) and puts each in its place of the layout. The sound is one track
mixed from every picture with all but the highlighted one at volume 0; set_sound() turns them up and down in
the running ffmpeg (its runtime filter commands, on stdin), so moving the highlight changes the sound
without the picture stopping.

A picture can also play an On Demand title (a movie or an episode, from a playlist or My Library), as the
web's small pictures can: it runs the same ffmpeg as the full-screen player (streaming.start_cmd, as
"<screen>~<slot>", so it counts against its playlist too), and status() says how far in it is, so the screen
can keep its place and carry on full screen from there.

Changing the layout, the pictures' places or the sound restarts only that ffmpeg; a picture whose channel
didn't change keeps its tuner. A picture with no signal is a dark box, and status() says why, so the screen
can say so on it. When the screen goes (its stream ends and no other replaces it within GRACE seconds, or
it plays something else), every picture's stream stops."""
import asyncio
import json
import logging
import queue
import socket
import subprocess
import threading
import time

from . import connections, guide, multiview, streaming, tuner
from .streaming import NO_WINDOW

log = logging.getLogger("tunerbox.mvstream")

# where each place of a layout is (left, top, width, height in % of the screen): the web's MV_PLACES
PLACES = {
    "split": [(0, 25, 50, 50), (50, 25, 50, 50)],
    "pip": [(0, 0, 100, 100), (67, 4, 30, 30)],
    "big": [(0, 16.7, 66.7, 66.7), (66.7, 16.7, 33.3, 33.3), (66.7, 50, 33.3, 33.3)],
    "big3": [(0, 12.5, 75, 75), (75, 12.5, 25, 25), (75, 37.5, 25, 25), (75, 62.5, 25, 25)],
    "grid": [(0, 0, 50, 50), (50, 0, 50, 50), (0, 50, 50, 50), (50, 50, 50, 50)],
}
FPS = 30
FIRST_DATA = 12         # seconds a picture has to send something before the stream starts without it
FIRST_DATA_VOD = 25     # an On Demand title (a provider can take a while to start one)
STALL = 10              # seconds without data before a picture is given up (it goes dark, the rest go on)
GRACE = 10              # seconds the pictures' tuners wait for the screen's next tiled stream
ACCEPT = 30             # seconds ffmpeg has to connect to a picture's socket


# ---------------------------------------------------------------- the request
def parse_tiles(text):
    """The pictures, in place order (place 1 is the big one), from the screen's JSON:
    [{"slot": 1-4, "number": n | "key": feed key, "alt": n} | {"slot", "vod": On Demand id, "at": seconds}].
    Raises ValueError."""
    try:
        raw = json.loads(text or "[]")
    except ValueError:
        raise ValueError("tiles isn't JSON") from None
    if not isinstance(raw, list) or not 1 <= len(raw) <= multiview.MAX_TILES:
        raise ValueError(f"Between 1 and {multiview.MAX_TILES} pictures")
    out, seen = [], set()
    for t in raw:
        if not isinstance(t, dict) or not isinstance(t.get("slot"), int) or not 1 <= t["slot"] <= multiview.MAX_TILES:
            raise ValueError("Each picture needs a slot from 1 to 4")
        if t["slot"] in seen:
            raise ValueError("A slot is listed twice")
        seen.add(t["slot"])
        if t.get("vod"):
            at = t.get("at") if isinstance(t.get("at"), (int, float)) and not isinstance(t.get("at"), bool) else 0
            out.append({"slot": t["slot"], "number": None, "key": None, "alt": 0, "vod": str(t["vod"]), "at": max(0.0, float(at))})
            continue
        number = t.get("number") if isinstance(t.get("number"), int) and not isinstance(t.get("number"), bool) else None
        key = str(t.get("key") or "") or None
        if number is None and not key:
            raise ValueError("Each picture needs a channel number or a feed key")
        alt = t.get("alt") if isinstance(t.get("alt"), int) and t.get("alt", 0) >= 0 else 0
        out.append({"slot": t["slot"], "number": None if key else number, "key": key, "alt": alt, "vod": None, "at": 0.0})
    return out


def _spec(t):
    """What a picture plays: the same spec again keeps its stream (a movie goes on from where it is)."""
    return (t.get("number"), t.get("key"), t.get("alt") or 0, t.get("vod"), round(float(t.get("at") or 0), 1))


# ---------------------------------------------------------------- the picture: places and ffmpeg
def _even(v):
    return int(round(v / 2)) * 2


def boxes(layout, w, h):
    """Each place of `layout` on a w x h picture: (x, y, width, height), even numbers (yuv420p)."""
    out = []
    for left, top, bw, bh in PLACES[layout]:
        x, y = _even(w * left / 100), _even(h * top / 100)
        out.append((x, y, min(_even(w * bw / 100), w - x), min(_even(h * bh / 100), h - y)))
    return out


def heard(slots, sound):
    """The picture whose sound is heard: `sound`, else (it isn't on the screen) the first one."""
    return sound if sound in slots else sorted(slots)[0]


def sound_cmds(slots, sound):
    """The runtime commands (ffmpeg's "c" key on stdin) that make picture `sound` the one heard."""
    on = heard(slots, sound)
    return b"".join(f"cvolume@v{s} -1 volume {1 if s == on else 0}\n".encode() for s in sorted(slots))


def build_cmd(layout, places, sound, w, h, inputs):
    """ffmpeg tiling the pictures. `places`: the slot in each place, in place order. `inputs`: {slot: its
    input address} for the pictures that have a signal (the others are a dark box and a silent track)."""
    # no -nostdin: set_sound sends filter commands on stdin
    cmd = [streaming.ffmpeg_bin(), "-hide_banner", "-loglevel", "error"]
    idx = {}
    for slot in places:
        if slot in inputs:
            idx[slot] = len(idx)
            # -re: read at the picture's own pace. A picture starts with a few seconds already waiting (what the
            # tuner keeps, or what queued while the stream restarted); read at once, the tiled stream would run
            # that far ahead of the screen for good, and every sound change would wait out those seconds in the
            # screen's buffer. Read in real time, the screen holds only what it needs.
            cmd += ["-re", "-thread_queue_size", "1024", "-analyzeduration", "2000000", "-probesize", "2000000",
                    "-fflags", "+genpts+discardcorrupt", "-f", "mpegts", "-i", inputs[slot]]
    graph = [f"color=c=black:s={w}x{h}:r={FPS}[base]"]
    last = "base"
    for n, (slot, (x, y, bw, bh)) in enumerate(zip(places, boxes(layout, w, h))):
        if slot in idx:     # deinterlaced if it's interlaced, fitted inside its place without stretching
            graph.append(f"[{idx[slot]}:v:0]yadif=0:-1:1,scale={bw}:{bh}:force_original_aspect_ratio=decrease,"
                         f"pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}[v{slot}]")
        else:
            graph.append(f"color=c=0x141414:s={bw}x{bh}:r={FPS}[v{slot}]")
        out = "vout" if n == len(places) - 1 else f"o{n}"
        graph.append(f"[{last}][v{slot}]overlay=x={x}:y={y}:eof_action=pass[{out}]")
        last = out
    # every picture's sound, all but the heard one at volume 0, mixed into one track (set_sound changes which)
    on = heard(places, sound)
    for slot in places:
        src = (f"[{idx[slot]}:a:0]aresample=48000:async=1:first_pts=0," if slot in idx
               else "anullsrc=r=48000:cl=stereo,")
        graph.append(f"{src}aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume@v{slot}=volume={1 if slot == on else 0}[a{slot}]")
    if len(places) > 1:
        graph.append("".join(f"[a{s}]" for s in places)
                     + f"amix=inputs={len(places)}:duration=longest:dropout_transition=0:normalize=0[aout]")
    else:
        graph.append(f"[a{places[0]}]anull[aout]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]"]
    rate, buf = ("6M", "12M") if h <= 720 else ("10M", "20M")
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", "23", "-maxrate", rate,
            "-bufsize", buf, "-g", str(FPS * 2), "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    # no muxer delay either (0.7 s by default): what's made goes out at once
    cmd += ["-muxdelay", "0", "-muxpreload", "0", "-flush_packets", "1",
            "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]
    return cmd


# ---------------------------------------------------------------- each screen's pictures
class _Tile:
    def __init__(self, slot, spec):
        self.slot, self.spec = slot, spec
        self.vod = spec[3]      # an On Demand title's id (None: a channel or event feed)
        self.sub = None         # its tuner Subscriber (or _ProcSub) while it has a signal
        self.why = ""           # why it has none ("No Signal", "All connections in use"...)
        self.first = b""        # what it sent before the stream started (handed to ffmpeg first)
        self.at = 0.0           # an On Demand title: where it started (seconds into it)
        self.dur = 0            # ...and its length (0: not known)
        self.since = None       # when its first data went to a tiled stream (time.monotonic)

    def pos(self):
        """How far into the title an On Demand picture is (it plays at normal speed once it's shown)."""
        return self.at + (time.monotonic() - self.since if self.since is not None else 0)


class _ProcSub:
    """An On Demand title in a picture: its ffmpeg (started like any screen's, streaming.start_cmd) read into a
    queue, so the feeders take from it as from a tuner's Subscriber. A full queue holds ffmpeg back, as a
    player that's ahead does."""

    def __init__(self, proc, client):
        self.proc, self.client = proc, client
        self.q = queue.Queue(maxsize=128)
        self.gone = threading.Event()
        threading.Thread(target=streaming._read_err, args=(proc, []), daemon=True).start()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while not self.gone.is_set():
                chunk = self.proc.stdout.read(65536)
                while not self.gone.is_set():
                    try:
                        self.q.put(chunk, timeout=0.5)
                        break
                    except queue.Full:
                        continue
                if not chunk:
                    return
        except (OSError, ValueError):       # closed under us
            pass
        try:
            self.q.put_nowait(b"")
        except queue.Full:
            pass

    def get(self, timeout=None):
        return self.q.get(timeout=timeout)

    def close(self, wait=True, why=""):
        self.gone.set()
        streaming.release(self.proc, self.client)


# set by api.py: an On Demand title for a picture -> {"url", "cmd", "label", "source_id", "duration", "at"};
# raises when it can't be played
vod_open = None


class _Screen:
    def __init__(self, screen):
        self.screen = screen
        self.tiles = {}         # slot -> _Tile
        self.gen = 0            # which tiled stream is the current one (a newer request takes over)
        self.proc = None
        self.stop = None        # threading.Event: the current stream's feeders stop
        self.places = []        # the slots in the current stream (set_sound's commands go to theirs)
        self.sound = 1          # the picture heard
        self.lock = threading.Lock()


_screens = {}
_lock = threading.Lock()
_quiet = set()                  # screens opening their own pictures: stop_screen leaves them alone


def _row(spec):
    number, key, alt = spec[:3]
    if key:
        rows = guide.stream_rows([key])
    else:
        c = guide.channel(number) if number is not None else None
        rows = guide.stream_rows(c["streams"]) if c and c["streams"] else []
    return rows[alt] if 0 <= alt < len(rows) else None


def _open_vod(scr, t):
    """Start On Demand picture `t` (its own client id), or say why it can't."""
    client = multiview.tile_client(scr.screen, t.slot)
    with _lock:                             # (slot 1 is the screen's own id: start_cmd stops its last stream)
        _quiet.add(scr.screen)
    try:
        v = vod_open(t.vod, t.spec[4])
        proc = streaming.start_cmd(v["cmd"], client, v["url"], v["label"], v["source_id"])
    except connections.Stopped:
        t.why = "Stopped from Setup › Tuners"
        return
    except connections.Full:
        t.why = "All connections in use"
        return
    except Exception:  # noqa: BLE001 - gone from the playlist, or no ffmpeg: this picture's problem only
        log.exception("Multiview On Demand %s in picture %s of %s", t.vod, t.slot, scr.screen)
        t.why = "Can't play this right now"
        return
    finally:
        with _lock:
            _quiet.discard(scr.screen)
    t.sub = _ProcSub(proc, client)
    t.at, t.dur, t.since = float(v.get("at") or t.spec[4]), int(v.get("duration") or 0), None


def _open(scr, t):
    """Join the tuner for picture `t` (its own client id), or say why it can't."""
    t.why, t.first = "", b""
    if t.vod:
        _open_vod(scr, t)
        return
    row = _row(t.spec)
    if not row:
        t.why = "Not live right now" if t.spec[1] else "No Signal"
        return
    client = multiview.tile_client(scr.screen, t.slot)
    with _lock:
        _quiet.add(scr.screen)
    try:
        t.sub = tuner.open_for(row["url"], client, "copy", "aac", 0, row.get("source_id"))
    except connections.Stopped:             # (a kind of Full: first)
        t.why = "Stopped from Setup › Tuners"
    except connections.Full:
        t.why = "All connections in use"
    except Exception:  # noqa: BLE001 - one picture's problem, never the others'
        log.exception("Multiview picture %s of %s", t.slot, scr.screen)
        t.why = "No Signal"
    finally:
        with _lock:
            _quiet.discard(scr.screen)


def _close(scr, t, why=""):
    sub, t.sub, t.first = t.sub, None, b""
    if why:
        t.why = why
    if sub is None:
        return
    if t.vod and t.since is not None:       # an On Demand picture closed: it starts from here next time
        t.at, t.since = t.pos(), None
    client = multiview.tile_client(scr.screen, t.slot)
    if tuner._clients.get(client) is sub:
        tuner._clients.pop(client, None)
    sub.close(wait=False, why="multiview picture closed")


def _first(t):
    """Wait (up to FIRST_DATA seconds) for the picture's first data, so ffmpeg never waits on a dead one."""
    if t.first:
        return
    until = time.monotonic() + (FIRST_DATA_VOD if t.vod else FIRST_DATA)
    while t.sub is not None and time.monotonic() < until:
        try:
            chunk = t.sub.get(timeout=0.5)
        except queue.Empty:
            continue
        if chunk:
            t.first = chunk
            return
        break
    t.why = t.why or "No Signal"


def _gone_why(scr, t):
    if connections.was_stopped(multiview.tile_client(scr.screen, t.slot)):
        return "Stopped from Setup › Tuners"
    if t.vod:
        return "The end" if t.dur and t.pos() >= t.dur - 90 else "Stopped playing"
    return "No Signal"


def _feed(scr, t, srv, stop):
    """Hand picture `t`'s stream to ffmpeg's connection on `srv`. A picture that stops sending (or ends) is
    given up: its socket closes, so ffmpeg goes on without it, and status() says why."""
    try:
        srv.settimeout(ACCEPT)
        conn, _ = srv.accept()
    except OSError:
        return
    finally:
        srv.close()
    sub, last = t.sub, time.monotonic()
    if t.since is None:
        t.since = last
    try:
        with conn:
            if t.first:
                conn.sendall(t.first)
                t.first = b""
            while not stop.is_set() and sub is not None:
                try:
                    chunk = sub.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() - last > STALL:
                        break
                    continue
                if not chunk:
                    break
                last = time.monotonic()
                conn.sendall(chunk)
    except OSError:                         # ffmpeg went (a newer stream, or the screen left)
        return
    if not stop.is_set() and t.sub is sub:  # it stopped sending, not us stopping it
        with scr.lock:
            if t.sub is sub:
                _close(scr, t, _gone_why(scr, t))
                log.info("Multiview picture %s of %s: %s", t.slot, scr.screen, t.why)


def _spawn(cmd):
    return streaming.track(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            stdin=subprocess.PIPE, creationflags=NO_WINDOW, bufsize=0))


def _halt(scr):
    """Stop the tiled stream that's running (call with scr.lock held); the pictures' tuners stay."""
    if scr.stop is not None:
        scr.stop.set()
    proc, scr.proc, scr.stop = scr.proc, None, None
    if proc is not None:
        proc.mv_halted = True               # stopped on purpose: read() doesn't call it a failure
        threading.Thread(target=streaming.kill, args=(proc,), daemon=True).start()


def _claim(screen):
    """The screen's _Screen, locked. Checked again once locked: one dropped meanwhile (_expire, stop_screen)
    is no longer in _screens, and whatever was opened on it would never be stopped."""
    while True:
        with _lock:
            scr = _screens.get(screen) or _screens.setdefault(screen, _Screen(screen))
        scr.lock.acquire()
        with _lock:
            if _screens.get(screen) is scr:
                return scr
        scr.lock.release()


def start(screen, layout, tiles, sound, w, h):
    """Start screen `screen`'s tiled stream (replacing the one it has): (screen, gen, proc), or None
    when none of the pictures has a signal."""
    scr = _claim(screen)
    try:
        return _start(scr, layout, tiles, sound, w, h)
    finally:
        scr.lock.release()


def _start(scr, layout, tiles, sound, w, h):
    """start(), with scr.lock held."""
    screen = scr.screen
    scr.gen += 1
    gen = scr.gen
    _halt(scr)
    want = {t["slot"]: _spec(t) for t in tiles}
    for slot, t in list(scr.tiles.items()):        # pictures taken off, or showing something else now
        if want.get(slot) != t.spec:
            _close(scr, t)
            del scr.tiles[slot]
    for slot, spec in want.items():
        t = scr.tiles.get(slot)
        if t is None:
            t = scr.tiles[slot] = _Tile(slot, spec)
        if t.sub is None:                           # new, or asked for again after it failed
            _open(scr, t)
    waits = [threading.Thread(target=_first, args=(t,), daemon=True) for t in scr.tiles.values() if t.sub]
    for th in waits:
        th.start()
    for th in waits:
        th.join()
    for t in scr.tiles.values():
        if t.sub is not None and not t.first:
            _close(scr, t, t.why or "No Signal")
    live = [t for t in scr.tiles.values() if t.sub is not None]
    if not live:
        _drop(scr)
        return None
    servers, inputs = {}, {}
    for t in live:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        servers[t.slot] = srv
        inputs[t.slot] = f"tcp://127.0.0.1:{srv.getsockname()[1]}"
    places = [t["slot"] for t in tiles]
    cmd = build_cmd(layout, places, sound, w, h, inputs)
    try:
        proc = _spawn(cmd)
    except OSError:                     # no ffmpeg: nothing is left holding a connection
        for srv in servers.values():
            srv.close()
        _drop(scr)
        raise
    scr.proc, scr.stop = proc, threading.Event()
    scr.places, scr.sound = places, heard(places, sound)
    for t in live:
        threading.Thread(target=_feed, args=(scr, t, servers[t.slot], scr.stop), daemon=True).start()
    log.info("Multiview for %s: %s, pictures %s, sound from %s", screen, layout,
             ", ".join(f"{s}{'' if s in inputs else ' (no signal)'}" for s in places), sound)
    return scr, gen, proc


async def read(proc, request=None):
    """MPEG-TS bytes of a tiled stream, until it ends or the screen goes."""
    err = []
    threading.Thread(target=streaming._read_err, args=(proc, err), daemon=True).start()
    q = queue.Queue(maxsize=64)
    stop = threading.Event()
    ready = asyncio.Event()
    threading.Thread(target=streaming._pump, args=(proc, q, stop, streaming._waker(asyncio.get_running_loop(), ready)),
                     daemon=True).start()
    last_check = time.monotonic()
    eof = False                             # ffmpeg's output ended (rather than the screen leaving)
    try:
        while True:
            try:
                chunk = q.get_nowait()
            except queue.Empty:
                ready.clear()
                try:
                    chunk = q.get_nowait()
                except queue.Empty:
                    try:
                        await asyncio.wait_for(ready.wait(), 2)
                    except asyncio.TimeoutError:
                        if request is not None and await request.is_disconnected():
                            break
                    continue
            if not chunk:
                eof = True
                break
            yield chunk
            if request is not None and time.monotonic() - last_check > 2:
                last_check = time.monotonic()
                if await request.is_disconnected():
                    break
    finally:
        stop.set()
        # Only when ffmpeg stopped by itself: one stopped for a newer stream (a new layout, channel or swap)
        # or because the screen left isn't a failure, and what it said last is just a decoder joining a
        # channel part-way ("co located POCs unavailable", "Invalid frame dimensions 0x0"), not news.
        if eof and not getattr(proc, "mv_halted", False):
            err = [e for e in err if e.strip() and not e.startswith(("Enter command", "Command reply"))]
            log.warning("Multiview stream stopped by itself (exit %s): %s", proc.returncode,
                        " | ".join(err[-3:]) or "no error given")


def ended(screen, gen):
    """The screen's tiled stream `gen` ended (it left, or a newer one took over). Unless a newer one
    has, the pictures' tuners wait GRACE seconds for the next one, then stop."""
    scr = _screens.get(screen)
    if scr is None:
        return
    with scr.lock:
        if scr.gen != gen:
            return
        _halt(scr)
    timer = threading.Timer(GRACE, _expire, args=(scr, gen))
    timer.daemon = True
    timer.start()


def _expire(scr, gen):
    with scr.lock:
        if scr.gen != gen or scr.proc is not None:
            return
        _drop(scr)


def _drop(scr):
    """Every picture of `scr` stops (call with scr.lock held)."""
    _halt(scr)
    for t in scr.tiles.values():
        _close(scr, t)
    scr.tiles = {}
    with _lock:
        if _screens.get(scr.screen) is scr:
            _screens.pop(scr.screen)


def stop_screen(client):
    """streaming.stop_client: the screen tuned something else, or Setup / the screen stopped it: its
    multiview stops at once (freeing every picture's connection). Not while it opens its own pictures."""
    if not client or multiview.SEP in client:
        return
    with _lock:
        if client in _quiet:
            return
        scr = _screens.get(client)
    if scr is None:
        return
    with scr.lock:
        scr.gen += 1
        _drop(scr)


def set_sound(screen, slot):
    """Picture `slot`'s sound is heard now, in the running stream (no restart). False when there's no
    stream, or it isn't one of its pictures."""
    scr = _screens.get(screen or "")
    if scr is None:
        return False
    with scr.lock:
        proc = scr.proc
        if proc is None or slot not in scr.places or proc.stdin is None:
            return False
        try:
            proc.stdin.write(sound_cmds(scr.places, slot))
            proc.stdin.flush()
        except (OSError, ValueError):       # ffmpeg just went (a newer stream is on its way)
            return False
        scr.sound = slot
    return True


def status(screen):
    """How the screen's pictures are doing: {"on", "tiles": [{"slot", "ok", "why"}]}; an On Demand picture
    also has "pos" (seconds into the title) and "duration"."""
    scr = _screens.get(screen or "")
    if scr is None:
        return {"on": False, "tiles": []}
    out = []
    for t in sorted(list(scr.tiles.values()), key=lambda t: t.slot):     # a copy: start() may be changing them
        x = {"slot": t.slot, "ok": t.sub is not None, "why": "" if t.sub is not None else t.why}
        if t.vod:
            x.update(pos=round(t.pos(), 1), duration=t.dur)
        out.append(x)
    return {"on": scr.proc is not None, "tiles": out}
