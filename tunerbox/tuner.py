"""Shared tuners: one ffmpeg, and so one provider connection, per channel, however many TVs, phones,
Plex and the DVR are on it. Xtream accounts often allow only 1-2 connections, so two TVs on the same
channel, or a TV and a recording, must not each open their own.

A tuner keeps the stream from its last video keyframe (further back with the stream cache on), so a
screen that joins a channel someone is already watching gets a picture at once and a cache fills
straight away. Each viewer reads through its own queue, so a slow one never holds up the others or
the provider connection; one that falls too far behind is dropped and reconnects."""
import itertools
import logging
import queue
import subprocess
import threading
import time
from collections import deque

from . import config
from .probe import split_url
from . import streaming
from .streaming import NO_WINDOW, _waker, buffer_seconds, kill, live_cmd, track

log = logging.getLogger("tunerbox.tuner")
TS = 188                                    # MPEG-TS packet size
MAX_BACKLOG = 64_000_000                    # most stream kept per tuner for viewers who join later
MIN_RETAIN = 10                             # seconds always kept, so a cache can fill at once
QUEUE_CHUNKS = 256                          # per reader: 16 MB and up, rides out Wi-Fi hiccups
VIDEO_TYPES = {0x01, 0x02, 0x10, 0x1B, 0x24, 0x42, 0xEA}   # MPEG-1/2, MPEG-4, H.264, HEVC, AVS, VC-1

_tuners = []                                # running tuners
_lock = threading.Lock()                    # guards _tuners; taken before a tuner's own lock
_clients = {}                               # client id -> its Subscriber (one channel per screen)


class Subscriber:
    """One reader of a tuner: a screen, an HLS session or a recording."""

    def __init__(self, tuner, buffer, name):
        self.tuner, self.name = tuner, name
        self.q = queue.Queue(maxsize=QUEUE_CHUNKS + buffer * 40)
        self.wake = None                                   # set by an async reader

    def put(self, chunk):
        """From the tuner's thread. False when this reader has fallen too far behind."""
        try:
            self.q.put_nowait(chunk)
        except queue.Full:
            return False
        if self.wake:
            self.wake()
        return True

    def end(self):
        """No more data: b"" tells the reader (making room for it if the queue is full)."""
        while True:
            try:
                self.q.put_nowait(b"")
                break
            except queue.Full:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    pass
        if self.wake:
            self.wake()

    def get(self, timeout=None):
        """Next chunk (b"" at the end); raises queue.Empty after `timeout` seconds."""
        return self.q.get(timeout=timeout)

    def close(self, wait=True):
        """Leave the tuner; the last one out stops it. wait=False stops ffmpeg in the background."""
        self.tuner.leave(self, wait)


class Tuner:
    def __init__(self, url, mode, audio, buffer):
        self.url, self.mode, self.audio = url, mode, audio
        self.retain = max(buffer, MIN_RETAIN)   # seconds of stream kept for viewers who join later
        self.subs = []
        self.lock = threading.Lock()
        self.chunks = deque()               # (seq, arrival time, bytes); seqs are consecutive
        self.keys = deque()                 # (seq, time) of chunks that start at a video keyframe
        self.seq = 0
        self.size = 0
        self.psi = {}                       # pid -> latest PAT / PMT packet, sent first to a joiner
        self.pmt_pids = set()
        self.video_pid = None
        self.ended = False
        self.sent = 0
        self.err = []
        cmd = live_cmd(url, mode, audio, buffer)
        self.proc = track(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, bufsize=0))
        self._errt = threading.Thread(target=self._stderr, daemon=True)
        self._errt.start()
        threading.Thread(target=self._read, daemon=True).start()
        log.info("Tuner start (%s, %s): %s", mode, audio, split_url(url)[0][-60:])

    def error(self):
        return " | ".join(self.err)

    # ------------------------------------------------------------ viewers
    def add(self, buffer, name):
        """New reader (call with _lock held). It first gets the kept stream from a keyframe."""
        sub = Subscriber(self, buffer, name)
        with self.lock:
            self.retain = max(self.retain, buffer)
            seed = self._seed(buffer)
            if seed:
                sub.q.put_nowait(seed)
            self.subs.append(sub)
            n = len(self.subs)
        if n > 1:
            log.info("%s joined %s (%d on this tuner)", name or "viewer", split_url(self.url)[0][-40:], n)
        return sub

    def leave(self, sub, wait=True):
        with _lock:
            with self.lock:
                if sub in self.subs:
                    self.subs.remove(sub)
                last = not self.subs and not self.ended
                if last:                    # nobody can join a tuner that's stopping
                    self.ended = True
                    if self in _tuners:
                        _tuners.remove(self)
        sub.end()
        if last:
            if wait:
                kill(self.proc)
            else:                           # taskkill takes a moment: never make the server wait
                threading.Thread(target=kill, args=(self.proc,), daemon=True).start()

    def _seed(self, buffer):
        """PAT/PMT, then the kept stream from the newest keyframe at least `buffer` seconds old."""
        if not self.chunks:
            return b""
        cutoff = time.monotonic() - buffer
        start = None
        for seq, t in reversed(self.keys):
            start = seq
            if t <= cutoff:
                break
        if start is None:                   # no keyframe seen (or no video): what there is
            start = self.chunks[0][0]
        first = self.chunks[0][0]
        body = [c[2] for c in itertools.islice(self.chunks, max(0, start - first), None)]
        psi = [self.psi[p] for p in (0, *sorted(self.pmt_pids)) if p in self.psi]
        return b"".join(psi + body)

    # ------------------------------------------------------------ ffmpeg output
    def _stderr(self):
        try:
            self.err.extend(self.proc.stderr.read().decode("utf-8", "ignore").splitlines()[-5:])
        except (OSError, ValueError):
            pass

    def _read(self):
        carry = b""
        try:
            while True:
                data = self.proc.stdout.read(65536)
                if not data:
                    break
                data = carry + data
                if data[0] != 0x47:
                    data = _resync(data)
                n = len(data) - len(data) % TS
                carry = data[n:]
                if n:
                    self._feed(data[:n])
        except (OSError, ValueError):       # pipe closed under us (ffmpeg killed)
            pass
        self._end()

    def _feed(self, buf):
        cuts = []
        vp = self.video_pid
        for off in range(0, len(buf), TS):
            b1 = buf[off + 1]
            pid = ((b1 & 0x1F) << 8) | buf[off + 2]
            if pid == vp:
                # start of a PES with the random-access flag: ffmpeg marks video keyframes this way
                if b1 & 0x40 and buf[off + 3] & 0x20 and buf[off + 4] and buf[off + 5] & 0x40:
                    cuts.append(off)
            elif pid == 0 or pid in self.pmt_pids:
                self._psi(pid, buf[off:off + TS])
                vp = self.video_pid
        now = time.monotonic()
        with self.lock:
            bounds = sorted({0, *cuts, len(buf)})
            for a, b in zip(bounds, bounds[1:]):
                if a in cuts:
                    self.keys.append((self.seq, now))
                self.chunks.append((self.seq, now, buf[a:b]))
                self.size += b - a
                self.seq += 1
            self._trim(now)
            subs = list(self.subs)
        self.sent += len(buf)
        for s in subs:
            if not s.put(buf):
                log.warning("%s fell too far behind on %s: dropped", s.name or "viewer", split_url(self.url)[0][-40:])
                s.close()

    def _trim(self, now):
        cutoff = now - self.retain
        while len(self.keys) >= 2 and self.keys[1][1] <= cutoff:
            self.keys.popleft()
        if self.keys:
            head = self.keys[0][0]
            while self.chunks and self.chunks[0][0] < head:
                self.size -= len(self.chunks.popleft()[2])
        else:                               # no keyframes (yet): keep about the last second
            while self.chunks and self.chunks[0][1] < cutoff - 1:
                self.size -= len(self.chunks.popleft()[2])
        while self.size > MAX_BACKLOG and len(self.chunks) > 1:
            self.size -= len(self.chunks.popleft()[2])
            while self.keys and self.keys[0][0] < self.chunks[0][0]:
                self.keys.popleft()

    def _psi(self, pid, pkt):
        """Remember PAT/PMT packets and find the video stream's PID from them."""
        if not pkt[1] & 0x40:               # only sections that start here (they fit in one packet)
            return
        self.psi[pid] = pkt
        p = 4
        if pkt[3] & 0x20:
            p += 1 + pkt[4]
        if p >= TS:
            return
        p += 1 + pkt[p]                     # pointer field
        if p + 12 > TS:
            return
        end = min(p + 3 + (((pkt[p + 1] & 0x0F) << 8) | pkt[p + 2]) - 4, TS)    # up to the CRC
        if pid == 0:
            pids = set()
            for i in range(p + 8, end - 3, 4):
                if (pkt[i] << 8) | pkt[i + 1]:          # program 0 is the network PID
                    pids.add(((pkt[i + 2] & 0x1F) << 8) | pkt[i + 3])
            if pids:
                self.pmt_pids = pids
        else:
            i = p + 12 + (((pkt[p + 10] & 0x0F) << 8) | pkt[p + 11])
            while i + 5 <= end:
                if pkt[i] in VIDEO_TYPES:
                    self.video_pid = ((pkt[i + 1] & 0x1F) << 8) | pkt[i + 2]
                    return
                i += 5 + (((pkt[i + 3] & 0x0F) << 8) | pkt[i + 4])

    def _end(self):
        with _lock:
            if self in _tuners:
                _tuners.remove(self)
            with self.lock:
                self.ended = True
                subs, self.subs = self.subs, []
                self.chunks.clear()
                self.keys.clear()
        kill(self.proc)
        try:
            self.proc.stdout.close()
        except OSError:
            pass
        self._errt.join(2)          # readers (the DVR) report ffmpeg's last words
        for s in subs:
            s.end()
        log.info("Tuner end after %.1f MB: %s", self.sent / 1e6, split_url(self.url)[0][-60:])
        if self.sent == 0:
            log.warning("No data from %s: %s", split_url(self.url)[0][:120], self.error())


def _resync(data):
    """Drop bytes up to the next pair of TS sync bytes."""
    for i in range(len(data)):
        if data[i] == 0x47 and (i + TS >= len(data) or data[i + TS] == 0x47):
            return data[i:]
    return b""


def _find(url, mode, audio, any_mode):
    """A running tuner this reader can use. Anyone takes AAC audio (Plex asks for the original, but
    sharing beats a second connection); `any_mode` (the DVR) takes whatever video mode is running."""
    best = None
    for t in _tuners:
        if t.url != url or t.ended or (t.audio != audio and t.audio != "aac"):
            continue
        if t.mode == mode and t.audio == audio:
            return t
        if t.mode == mode or any_mode:
            best = best or t
    return best


def join(url, mode=None, audio="aac", buffer=0, any_mode=False, name=""):
    """A Subscriber on the tuner for `url`, starting one if none suits."""
    mode = mode or config.get("video_mode", "copy")
    buffer = buffer_seconds(buffer)
    with _lock:
        t = _find(url, mode, audio, any_mode)
        if t is None:
            t = Tuner(url, mode, audio, buffer)
            _tuners.append(t)
        return t.add(buffer, name)


def stop_client(client_id):
    """Take a screen off its channel (before it tunes another: Xtream allows few connections)."""
    sub = _clients.pop(client_id, None)
    if sub:
        sub.close()


async def watch(url, client_id=None, mode=None, audio="aac", request=None, buffer=None):
    """Async generator of MPEG-TS bytes from a shared tuner. Waiting never holds a worker thread."""
    import asyncio

    import anyio
    if client_id:               # this screen's previous channel or movie, whichever it was
        await anyio.to_thread.run_sync(streaming.stop_client, client_id)
    sub = await anyio.to_thread.run_sync(lambda: join(url, mode, audio, buffer, name=client_id or ""))
    if client_id:
        _clients[client_id] = sub
    ready = asyncio.Event()
    sub.wake = _waker(asyncio.get_running_loop(), ready)
    last_check = time.monotonic()
    try:
        while True:
            try:
                chunk = sub.q.get_nowait()
            except queue.Empty:
                ready.clear()
                try:                # re-check after clearing, so a chunk put just before isn't missed
                    chunk = sub.q.get_nowait()
                except queue.Empty:
                    try:
                        await asyncio.wait_for(ready.wait(), 2)
                    except asyncio.TimeoutError:
                        if request is not None and await request.is_disconnected():
                            break
                    continue
            if not chunk:
                break
            yield chunk
            if request is not None and time.monotonic() - last_check > 2:
                last_check = time.monotonic()
                if await request.is_disconnected():
                    break
    finally:
        if client_id and _clients.get(client_id) is sub:
            _clients.pop(client_id, None)
        sub.close(wait=False)
