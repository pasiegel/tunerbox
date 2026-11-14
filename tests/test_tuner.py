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


def test_a_slow_reader_alone_on_its_tuner_is_waited_for(fake, monkeypatch):
    monkeypatch.setattr(tuner, "QUEUE_CHUNKS", 5)
    a = tuner.join("fake:1000:0.001:0", "copy", name="alone")
    time.sleep(1.5)                                 # far more than 5 chunks produced meanwhile
    assert a in a.tuner.subs                        # held the tuner back instead of being dropped
    assert _read(a, 188 * 300)
    a.close()


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


# ---------------------------------------------------------------- connection limits (connections.py)
def _account(limit, kind="xtream", own=0):
    """A playlist allowing `limit` connections (Xtream's max_connections, or an HDHomeRun's tuners)."""
    from tunerbox import db
    sid = db.run("INSERT INTO sources(name, type, url, max_conn) VALUES ('Prov', ?, 'http://prov', ?)", (kind, own))
    db.set_meta(f"{'xtream_max' if kind == 'xtream' else 'hdhr_tuners'}_{sid}", limit)
    return sid


def test_full_account_refuses_a_second_channel(fake):
    from tunerbox.connections import Full
    sid = _account(1)
    a = tuner.join("fake:1000:0.05:0", "copy", name="tv1", source_id=sid)
    b = tuner.join("fake:1000:0.05:0", "copy", name="tv2", source_id=sid)     # same channel: shared, free
    with pytest.raises(Full, match="Prov allows 1 connection"):
        tuner.join("fake:1000:0.05:1", "copy", name="tv3", source_id=sid)
    assert len(fake) == 1
    tuner.join("fake:1000:0.05:2", "copy", name="tv3", source_id=None).close()  # another playlist: no limit
    a.close(); b.close()


def test_a_screens_own_channel_doesnt_count_against_it(fake):
    from tunerbox import connections
    sid = _account(1)
    a = tuner.join("fake:1000:0.05:0", "copy", name="tv1", source_id=sid)
    connections.check(sid, "fake:1000:0.05:1", "copy", client="tv1")            # tv1 gives it up when it tunes
    with pytest.raises(connections.Full):
        connections.check(sid, "fake:1000:0.05:1", "copy", client="tv2")
    a.close()


def test_own_limit_overrides_what_the_provider_says(fake):
    from tunerbox import connections, db
    sid = _account(1, own=3)
    assert connections.limit_of(db.one("SELECT * FROM sources WHERE id=?", (sid,))) == 3
    subs = [tuner.join(f"fake:1000:0.05:{i}", "copy", name=f"tv{i}", source_id=sid) for i in range(3)]
    with pytest.raises(connections.Full):
        tuner.join("fake:1000:0.05:9", "copy", name="tv9", source_id=sid)
    for s in subs:
        s.close()


def test_recording_takes_the_connection_from_live_tv(fake):
    sid = _account(1, "hdhomerun")
    tv = tuner.join("fake:1000:0.05:0", "copy", name="tv1", source_id=sid)
    _read(tv, 20000)
    rec = tuner.join("fake:1000:0.05:1", "copy", any_mode=True, name="dvr:7", source_id=sid, take=True)
    assert rec.tuner is not tv.tuner and tv.tuner.proc.poll() is not None    # live TV stopped
    end = time.monotonic() + 5
    while time.monotonic() < end and tv.get(timeout=5) != b"":
        pass                                        # the screen sees the end of its stream
    assert [t["viewers"] for t in tuner.running()] == [["dvr:7"]]
    rec.close()


def test_recording_never_takes_another_recordings_connection(fake):
    from tunerbox.connections import Full
    sid = _account(1)
    first = tuner.join("fake:1000:0.05:0", "copy", any_mode=True, name="dvr:1", source_id=sid, take=True)
    with pytest.raises(Full):
        tuner.join("fake:1000:0.05:1", "copy", any_mode=True, name="dvr:2", source_id=sid, take=True)
    assert first.tuner.proc.poll() is None
    first.close()


def test_api_refuses_a_full_playlist_and_says_whats_using_it(fake):
    from fastapi.testclient import TestClient

    from tunerbox import api, db, guide
    sid = _account(1)
    for n, url in ((8, "fake:1000:0.05:0"), (9, "fake:1000:0.05:1")):
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, ?)", (f"k{n}", sid, f"Ch {n}", url))
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (n, f"Ch {n}"))
        db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, ?, 'manual')", (n, f"k{n}"))
    guide.invalidate()
    rec = tuner.join("fake:1000:0.05:0", "copy", any_mode=True, name="dvr:5", source_id=sid)
    c = TestClient(api.app)
    r = c.get("/api/stream/9?client=tv2")
    assert r.status_code == 503 and r.headers["X-HDHomeRun-Error"].startswith("805")
    busy = c.get("/api/busy?number=9&client=tv2")
    full = busy.json()["full"]
    assert full[0]["source"] == "Prov" and full[0]["limit"] == 1
    assert full[0]["using"][0]["number"] == 8 and full[0]["using"][0]["viewers"] == [{"kind": "dvr", "name": "Recording"}]
    assert "fake:" not in busy.text                                     # never a provider address
    assert c.get("/api/busy?number=8&client=tv2").json()["full"] == []  # channel 8 is on: it can be watched
    rec.close()


def test_two_screens_tuning_at_once_cant_both_take_the_last_connection(fake, monkeypatch):
    import threading

    from tunerbox import connections
    sid = _account(1)
    real = connections.check
    gate = threading.Barrier(2, timeout=5)

    def slow_check(*a, **k):                    # both screens pass the check before either starts a tuner
        real(*a, **k)
        try:
            gate.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
    monkeypatch.setattr(connections, "check", slow_check)
    got, refused = [], []

    def tune(i):
        try:
            got.append(tuner.join(f"fake:1000:0.05:{i}", "copy", name=f"tv{i}", source_id=sid))
        except connections.Full:
            refused.append(i)
    ts = [threading.Thread(target=tune, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(got) == 1 and len(refused) == 1 and len(fake) == 1
    for s in got:
        s.close()


def test_screen_stopped_for_a_recording_cant_take_the_connection_back(fake, monkeypatch):
    import threading

    from tunerbox import connections
    sid = _account(1)
    tv = tuner.join("fake:1000:0.05:0", "copy", name="tv1", source_id=sid)
    real_make_room = connections.make_room
    retried = []

    def make_room(*a, **k):                     # the stopped screen retunes the moment its channel stops
        ok = real_make_room(*a, **k)
        t = threading.Thread(target=lambda: retried.append(_try_join(sid)))
        t.start()
        t.join(0.5)                             # it must wait for the recording to start its tuner
        return ok
    monkeypatch.setattr(connections, "make_room", make_room)
    rec = tuner.join("fake:1000:0.05:1", "copy", any_mode=True, name="dvr:7", source_id=sid, take=True)
    end = time.monotonic() + 5
    while not retried and time.monotonic() < end:
        time.sleep(0.05)
    assert retried == ["full"]
    assert [t["viewers"] for t in tuner.running()] == [["dvr:7"]]
    rec.close()
    tv.close()


def _try_join(sid):
    from tunerbox import connections
    try:
        tuner.join("fake:1000:0.05:2", "copy", name="tv1", source_id=sid).close()
        return "joined"
    except connections.Full:
        return "full"


def test_hls_channel_stops_the_screens_on_demand_stream(fake, monkeypatch, tmp_path):
    from tunerbox import connections, hls
    sid = _account(1)
    monkeypatch.setattr(hls, "ROOT", tmp_path)
    proc = streaming.start_cmd([sys.executable, "-c", "import time; time.sleep(30)"], "tv1", "http://prov/movie.mkv",
                               "A movie", sid)
    assert connections.used(sid) == 1
    monkeypatch.setattr(hls, "_cmd", lambda *a, **k: [sys.executable, "-c", "import sys; sys.stdin.read()"])
    s = hls.start("fake:1000:0.05:0", "tv1", "copy", 0, source_id=sid)
    assert streaming.playing() == [] and proc.wait(5) is not None   # the movie's connection was given up
    assert connections.used(sid) == 1                                 # only the channel's tuner
    hls._stop(s)


def test_busy_uses_the_mode_the_screen_asked_for(fake):
    from tunerbox import connections
    sid = _account(1)
    a = tuner.join("fake:1000:0.05:0", "copy", name="tv1", source_id=sid)
    row = {"url": "fake:1000:0.05:0", "source_id": sid, "video_mode": "copy"}
    assert connections.busy([row], "tv2") == []                           # copy is already on: free to watch
    full = connections.busy([row], "tv2", mode="roku720")                 # 720p needs a tuner of its own
    assert full and full[0]["limit"] == 1
    a.close()


def test_joining_a_running_tuner_is_decided_and_done_in_one_step(fake, monkeypatch):
    from tunerbox import connections
    sid = _account(1)
    a = tuner.join("fake:1000:0.05:0", "copy", name="tv1", source_id=sid)
    real = connections.check

    def no_check(*a_, **k):     # sharing is settled under tuner._lock, not by a check that can go stale
        raise AssertionError("checked")
    monkeypatch.setattr(connections, "check", no_check)
    b = tuner.join("fake:1000:0.05:0", "copy", name="tv2", source_id=sid)
    assert b.tuner is a.tuner and len(fake) == 1
    monkeypatch.setattr(connections, "check", real)
    a.close()
    b.close()                   # the tuner stops: the next viewer needs a counted, new connection
    rec = tuner.join("fake:1000:0.05:1", "copy", any_mode=True, name="dvr:1", source_id=sid)
    with pytest.raises(connections.Full):
        tuner.join("fake:1000:0.05:0", "copy", name="tv3", source_id=sid)
    rec.close()


def test_full_playlist_doesnt_cost_the_screen_its_channel(fake):
    from tunerbox import connections
    sid = _account(1)
    rec = tuner.join("fake:1000:0.05:0", "copy", any_mode=True, name="dvr:1", source_id=sid)
    tv = tuner.open_for("fake:1000:0.05:1", "tv1", "copy", source_id=None)     # tv1 on another playlist
    with pytest.raises(connections.Full):
        tuner.open_for("fake:1000:0.05:2", "tv1", "copy", source_id=sid)
    assert tuner._clients["tv1"] is tv and not tv.tuner.ended                  # still watching
    tuner.stop_client("tv1")
    rec.close()


def test_stopping_a_tuner_for_a_recording_doesnt_wait_for_ffmpeg(fake, monkeypatch):
    sid = _account(1)
    tv = tuner.join("fake:1000:0.05:0", "copy", name="tv1", source_id=sid)
    slow = []
    monkeypatch.setattr(tuner, "kill", lambda p: (time.sleep(1), slow.append(p), p.kill()))
    t0 = time.monotonic()
    tv.tuner.stop()
    assert time.monotonic() - t0 < 0.5 and tuner.running() == []
    end = time.monotonic() + 3
    while not slow and time.monotonic() < end:
        time.sleep(0.05)
    assert slow                                         # ffmpeg still stopped, in the background
