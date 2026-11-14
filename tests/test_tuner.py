"""Shared tuners, with a small Python script writing MPEG-TS standing in for ffmpeg."""
import asyncio
import sys
import time

import pytest

from tunerbox import streaming, tuner

# Fake ffmpeg: PAT, PMT, then "GOPs" of a video keyframe + 20 video packets + 5 audio packets (audio
# packets carry the random-access flag too, as ffmpeg's do: they must not count as keyframes).
# Every video packet's payload starts with its GOP number. Written in 1000-byte pieces, so reads
# never line up with packets.
FAKE = r'''
import sys, time
V, A, PMT = 0x100, 0x101, 0x1000
def pkt(pid, payload=b"", start=False, rai=False):
    b1 = (0x40 if start else 0) | (pid >> 8)
    if rai:
        return (bytes([0x47, b1, pid & 0xFF, 0x30, 1, 0x40]) + payload).ljust(188, b"\xff")
    return (bytes([0x47, b1, pid & 0xFF, 0x10]) + payload).ljust(188, b"\xff")
PAT = pkt(0, bytes([0, 0, 0xB0, 13, 0, 1, 0xC1, 0, 0, 0, 1, 0xF0, 0x00, 0, 0, 0, 0]), start=True)
PMTP = pkt(PMT, bytes([0, 2, 0xB0, 23, 0, 1, 0xC1, 0, 0, 0xE1, 0x00, 0xF0, 0,
                      0x1B, 0xE1, 0x00, 0xF0, 0, 0x0F, 0xE1, 0x01, 0xF0, 0, 0, 0, 0, 0]), start=True)
gops, delay = int(sys.argv[1]), float(sys.argv[2])
out = sys.stdout.buffer
for g in range(gops):
    data = (PAT + PMTP + pkt(V, g.to_bytes(4, "big"), start=True, rai=True)
            + b"".join(pkt(V, g.to_bytes(4, "big")) for _ in range(20))
            + b"".join(pkt(A, b"a", start=True, rai=True) for _ in range(5)))
    for i in range(0, len(data), 1000):
        out.write(data[i:i + 1000]); out.flush()
    time.sleep(delay)
time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 0)
'''


@pytest.fixture
def fake(monkeypatch):
    """Use the fake ffmpeg; returns the list of commands started."""
    started = []

    def cmd(url, mode=None, audio="aac", buffer=0):
        gops, delay, linger = url.split(":")[1:]
        started.append((url, mode, audio))
        return [sys.executable, "-c", FAKE, gops, delay, linger]
    monkeypatch.setattr(tuner, "live_cmd", cmd)
    yield started
    for t in list(tuner._tuners):
        tuner.kill(t.proc)


def _read(sub, n_bytes, timeout=5):
    out = b""
    end = time.monotonic() + timeout
    while len(out) < n_bytes and time.monotonic() < end:
        try:
            chunk = sub.get(timeout=0.5)
        except Exception:
            continue
        if not chunk:
            break
        out += chunk
    return out


def _packets(data):
    assert len(data) % 188 == 0
    assert all(data[i] == 0x47 for i in range(0, len(data), 188))
    return [data[i:i + 188] for i in range(0, len(data), 188)]


def _pid(p):
    return ((p[1] & 0x1F) << 8) | p[2]


def test_two_viewers_share_one_connection(fake):
    a = tuner.join("fake:1000:0.05:0", "copy", name="tv1")
    b = tuner.join("fake:1000:0.05:0", "copy", name="tv2")
    assert len(fake) == 1 and a.tuner is b.tuner
    assert _read(a, 20000) and _read(b, 20000)
    proc = a.tuner.proc
    a.close()
    assert proc.poll() is None                      # tv2 is still watching
    b.close()
    assert proc.poll() is not None                  # last one out stops it
    assert not tuner._tuners


def test_a_screen_joining_late_starts_at_a_keyframe(fake):
    a = tuner.join("fake:1000:0.05:0", "copy")
    _read(a, 60000)                                 # a few GOPs in
    b = tuner.join("fake:1000:0.05:0", "copy")
    pk = _packets(_read(b, 188 * 3))[:3]
    assert [_pid(p) for p in pk] == [0, 0x1000, 0x100]          # PAT, PMT, then video...
    assert pk[2][1] & 0x40 and pk[2][5] & 0x40                  # ...that is a keyframe
    gop = int.from_bytes(pk[2][6:10], "big")
    assert gop >= 2                                             # the newest one, not the first
    a.close(); b.close()


def test_output_stays_packet_aligned_and_in_order(fake):
    a = tuner.join("fake:1000:0.01:0", "copy")
    seen = [int.from_bytes(p[4:8], "big") for p in _packets(_read(a, 188 * 600))
            if _pid(p) == 0x100 and not p[1] & 0x40]
    assert seen == sorted(seen) and seen[-1] > seen[0]
    a.close()


def test_stream_cache_joiner_gets_older_keyframe(fake):
    a = tuner.join("fake:1000:0.1:0", "copy", buffer=0)
    _read(a, 188 * 27 * 12)                         # about a second of GOPs
    b = tuner.join("fake:1000:0.1:0", "copy", buffer=0)
    c = tuner.join("fake:1000:0.1:0", "copy", buffer=5)
    gb = int.from_bytes(_packets(_read(b, 188 * 3))[2][6:10], "big")
    gc = int.from_bytes(_packets(_read(c, 188 * 3))[2][6:10], "big")
    assert gc < gb                                  # the 5-second cache starts further back
    for s in (a, b, c):
        s.close()


def test_modes_audio_and_the_dvr(fake):
    url = "fake:1000:0.05:0"
    copy = tuner.join(url, "copy")
    h264 = tuner.join(url, "h264")                  # a TV that needs re-encoding: its own tuner
    assert copy.tuner is not h264.tuner and len(fake) == 2
    dvr = tuner.join(url, "roku720", any_mode=True)  # the DVR takes any picture going
    assert dvr.tuner in (copy.tuner, h264.tuner) and len(fake) == 2
    plex = tuner.join(url, "copy", audio="copy")    # Plex takes AAC rather than a second connection
    assert plex.tuner is copy.tuner and len(fake) == 2
    aac = tuner.join("fake:1000:0.05:1", "copy", audio="copy")
    tv = tuner.join("fake:1000:0.05:1", "copy")     # but a TV can't play the original audio
    assert tv.tuner is not aac.tuner
    for s in (copy, h264, dvr, plex, aac, tv):
        s.close()


def test_feed_ending_ends_every_reader(fake):
    a = tuner.join("fake:3:0:0", "copy")
    b = tuner.join("fake:3:0:0", "copy")
    for s in (a, b):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if s.get(timeout=5) == b"":
                break
        else:
            pytest.fail("no end of stream")
    time.sleep(0.2)
    assert not tuner._tuners


def test_new_channel_on_the_same_screen_releases_the_old_one(fake):
    async def main():
        g1 = streaming.ffmpeg_stream("fake:1000:0.05:0", "screen1")
        assert await g1.__anext__()
        first = tuner._clients["screen1"].tuner
        g2 = streaming.ffmpeg_stream("fake:1000:0.05:1", "screen1")
        assert await g2.__anext__()
        assert first.proc.poll() is not None        # stopped before the new one started
        await g1.aclose(); await g2.aclose()
    asyncio.run(main())
    time.sleep(0.5)
    assert "screen1" not in tuner._clients


def test_a_reader_that_falls_behind_is_dropped(fake, monkeypatch):
    monkeypatch.setattr(tuner, "QUEUE_CHUNKS", 50)
    a = tuner.join("fake:1000:0.01:0", "copy", name="slow")
    b = tuner.join("fake:1000:0.01:0", "copy", name="fast")
    end = time.monotonic() + 5
    while a in a.tuner.subs and time.monotonic() < end:
        b.get(timeout=1)
    assert a not in a.tuner.subs and b in b.tuner.subs
    b.close()


def test_recording_shares_the_tuner_a_tv_is_on(fake, tmp_path):
    from tunerbox import dvr
    tv = tuner.join("fake:1000:0.02:0", "copy", name="tv")
    _read(tv, 30000)
    rec = tuner.join("fake:1000:0.02:0", "copy", any_mode=True, name="dvr:1")
    assert rec.tuner is tv.tuner and len(fake) == 1
    ts = tmp_path / "Show.ts"
    dvr._capture(rec, ts, time.time() + 1)
    rec.close()
    pk = _packets(ts.read_bytes())
    assert len(pk) > 100 and _pid(pk[0]) == 0 and pk[2][5] & 0x40     # starts clean, on a keyframe
    assert tv.tuner.proc.poll() is None                                # the TV is still watching
    tv.close()


def test_hls_session_that_cant_start_leaves_no_folder(monkeypatch, tmp_path):
    from tunerbox import hls

    def fail(*a, **k):
        raise OSError("ffmpeg not found")
    monkeypatch.setattr(hls, "ROOT", tmp_path)
    monkeypatch.setattr(tuner, "join", fail)
    with pytest.raises(OSError):
        hls.start("http://x/1")
    assert list(tmp_path.iterdir()) == []
