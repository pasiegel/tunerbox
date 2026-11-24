"""Multiview tiled into one stream for screens that play one video at a time (the Roku): the request, the
places, the ffmpeg command, and each screen's pictures (kept, replaced, given up, stopped)."""
import json
import queue
import time

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, config, connections, db, guide, multiview, mvstream, streaming, tuner

URL = "http://p.example"


class FakeSub:
    def __init__(self, url, client, data=True):
        self.url, self.client, self.closed = url, client, False
        self.q = queue.Queue()
        if data:
            self.q.put(b"\x47" * 188)

    def get(self, timeout=None):
        return self.q.get(timeout=timeout)

    def close(self, wait=True, why=""):
        self.closed = True


class FakeProc:
    stdout = stderr = None


@pytest.fixture
def screens(monkeypatch):
    """Channels 702 and 705 on one playlist (no limit), 720 with no signal; tuner, ffmpeg and the
    sockets faked. `opened`: every picture joined, in order."""
    s = db.run("INSERT INTO sources(name, type, url, username) VALUES ('P', 'xtream', ?, 'me')", (URL,))
    for num, key in ((702, "espn"), (705, "fs1"), (720, "cnn")):
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, ?)", (key, s, key, f"{URL}/{num}.ts"))
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (num, key.upper()))
        db.run("INSERT INTO mappings(number, stream_key, alt_keys, mode) VALUES (?, ?, '[]', 'manual')", (num, key))
    guide.invalidate()
    opened = []

    def open_for(url, client, mode, audio, buffer, source_id):
        assert mode == "copy"
        sub = FakeSub(url, client, data="/720." not in url)
        opened.append(sub)
        tuner._clients[client] = sub
        return sub
    monkeypatch.setattr(tuner, "open_for", open_for)
    monkeypatch.setattr(mvstream, "_spawn", lambda cmd: FakeProc())
    monkeypatch.setattr(streaming, "kill", lambda proc: None)
    monkeypatch.setattr(mvstream, "FIRST_DATA", 0.3)
    monkeypatch.setattr(mvstream, "ACCEPT", 0.2)
    yield opened
    for scr in list(mvstream._screens.values()):
        with scr.lock:
            mvstream._drop(scr)


def tiles(*specs):
    return [{"slot": slot, "number": num, "key": None, "alt": 0} for slot, num in specs]


# ---------------------------------------------------------------- the request
def test_parse_tiles():
    t = mvstream.parse_tiles(json.dumps([{"slot": 2, "number": 702}, {"slot": 1, "key": "f:abc", "alt": 1}]))
    assert t == [{"slot": 2, "number": 702, "key": None, "alt": 0, "vod": None, "at": 0.0},
                 {"slot": 1, "number": None, "key": "f:abc", "alt": 1, "vod": None, "at": 0.0}]
    for bad in ("nope", "[]", json.dumps([{"slot": 5, "number": 1}]), json.dumps([{"slot": 1}]),
                json.dumps([{"slot": 1, "number": 1}, {"slot": 1, "number": 2}]), json.dumps([{"slot": 1, "number": 1}] * 5)):
        with pytest.raises(ValueError):
            mvstream.parse_tiles(bad)


# ---------------------------------------------------------------- places and the command
@pytest.mark.parametrize("layout", list(mvstream.PLACES))
def test_boxes_fit_the_picture(layout):
    for w, h in ((1280, 720), (1920, 1080)):
        for x, y, bw, bh in mvstream.boxes(layout, w, h):
            assert x % 2 == y % 2 == bw % 2 == bh % 2 == 0
            assert 0 <= x and 0 <= y and x + bw <= w and y + bh <= h and bw > 0 and bh > 0


def test_grid_quarters():
    assert mvstream.boxes("grid", 1280, 720) == [(0, 0, 640, 360), (640, 0, 640, 360), (0, 360, 640, 360), (640, 360, 640, 360)]


def test_command_inputs_placeholders_and_one_sound_track():
    cmd = mvstream.build_cmd("grid", [1, 3, 2], 3, 1280, 720, {1: "tcp://127.0.0.1:1", 3: "tcp://127.0.0.1:3"})
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"] == ["tcp://127.0.0.1:1", "tcp://127.0.0.1:3"]
    assert "-nostdin" not in cmd                                # set_sound talks to it on stdin
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v:0]" in graph and "[1:v:0]" in graph and "color=c=0x141414:s=640x360" in graph   # 2: no signal
    assert "anullsrc" in graph
    assert "volume@v3=volume=1" in graph and "volume@v1=volume=0" in graph and "volume@v2=volume=0" in graph
    assert "amix=inputs=3" in graph and "normalize=0" in graph
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"] == ["[vout]", "[aout]"]
    assert cmd[-1] == "pipe:1" and "mpegts" in cmd


def test_one_picture_needs_no_mix():
    graph = mvstream.build_cmd("pip", [1], 1, 1280, 720, {1: "tcp://x"})
    graph = graph[graph.index("-filter_complex") + 1]
    assert "amix" not in graph and "[a1]anull[aout]" in graph


def test_sound_commands():
    assert mvstream.sound_cmds([2, 1], 2) == b"cvolume@v1 -1 volume 0\ncvolume@v2 -1 volume 1\n"
    assert mvstream.heard([2, 1], 4) == 1                       # not on the screen: the first one


def test_set_sound_talks_to_the_running_stream(screens, monkeypatch):
    import io
    proc = FakeProc()
    proc.stdin = io.BytesIO()
    monkeypatch.setattr(mvstream, "_spawn", lambda cmd: proc)
    mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    assert mvstream.set_sound("tv", 2)
    assert proc.stdin.getvalue() == b"cvolume@v1 -1 volume 0\ncvolume@v2 -1 volume 1\n"
    assert not mvstream.set_sound("tv", 3) and not mvstream.set_sound("nobody", 1)
    r = TestClient(api.app).post("/api/mv/sound", json={"client": "tv", "slot": 1})
    assert r.json() == {"ok": True}


# ---------------------------------------------------------------- each screen's pictures
def test_pictures_open_with_their_own_client_ids(screens):
    got = mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    assert got is not None
    assert [s.client for s in screens] == ["tv", "tv~2"]
    assert mvstream.status("tv") == {"on": True, "tiles": [{"slot": 1, "ok": True, "why": ""},
                                                           {"slot": 2, "ok": True, "why": ""}]}


def test_a_new_layout_keeps_the_pictures_and_a_new_channel_replaces_only_its_own(screens):
    mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    first, second = screens
    first.q.put(b"\x47" * 188)              # more data for the next stream (the first chunk went to ffmpeg)
    second.q.put(b"\x47" * 188)
    mvstream.start("tv", "pip", tiles((2, 705), (1, 702)), 2, 1280, 720)      # swapped, other layout
    assert len(screens) == 2 and not first.closed and not second.closed
    first.q.put(b"\x47" * 188)
    mvstream.start("tv", "pip", tiles((1, 702), (2, 720)), 1, 1280, 720)      # picture 2 changes channel
    assert second.closed and not first.closed
    assert [s.client for s in screens] == ["tv", "tv~2", "tv~2"]


def test_a_picture_with_no_signal_goes_dark_and_says_so(screens):
    got = mvstream.start("tv", "split", tiles((1, 702), (2, 720)), 1, 1280, 720)
    assert got is not None
    st = mvstream.status("tv")
    assert st["tiles"][1] == {"slot": 2, "ok": False, "why": "No Signal"}
    assert screens[1].closed                 # its tuner isn't kept for nothing


def test_no_picture_with_a_signal_starts_nothing(screens):
    assert mvstream.start("tv", "split", tiles((1, 720)), 1, 1280, 720) is None


def test_the_screen_tuning_something_else_stops_every_picture(screens):
    mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    streaming.stop_client("tv")              # e.g. it plays a single channel again
    assert all(s.closed for s in screens)
    assert mvstream.status("tv") == {"on": False, "tiles": []}


def test_a_tiles_own_client_doesnt_stop_the_screen(screens):
    mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    mvstream.stop_screen("tv~2")
    assert not any(s.closed for s in screens)


def test_ended_waits_for_the_next_stream_then_stops(screens, monkeypatch):
    _scr, gen, _proc = mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    monkeypatch.setattr(mvstream, "GRACE", 0)
    mvstream.ended("tv", gen + 1)            # an older stream's end: nothing happens
    assert mvstream.status("tv")["on"]
    mvstream.ended("tv", gen)                # nothing took over: after GRACE every picture stops
    for _ in range(50):
        if all(s.closed for s in screens):
            break
        time.sleep(0.02)
    assert all(s.closed for s in screens)
    assert mvstream.status("tv") == {"on": False, "tiles": []}


def test_a_new_stream_within_the_grace_keeps_the_pictures(screens, monkeypatch):
    _scr, gen, _proc = mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    for s in screens:
        s.q.put(b"\x47" * 188)
    mvstream.ended("tv", gen)                # the screen hung up to ask for another layout...
    mvstream.start("tv", "pip", tiles((1, 702), (2, 705)), 1, 1280, 720)     # ...which came at once
    mvstream._expire(mvstream._screens["tv"], gen)
    assert not any(s.closed for s in screens)


# ---------------------------------------------------------------- the endpoints
def test_stream_endpoint_refuses_bad_requests(screens):
    c = TestClient(api.app)
    assert c.get("/api/mv/stream", params={"client": "tv~2", "tiles": "[]"}).status_code == 400
    assert c.get("/api/mv/stream", params={"client": "tv", "layout": "nine", "tiles": '[{"slot":1,"number":702}]'}).status_code == 400
    assert c.get("/api/mv/stream", params={"client": "tv", "tiles": "nope"}).status_code == 400
    two = json.dumps([{"slot": 1, "number": 702}, {"slot": 2, "number": 705}, {"slot": 3, "number": 720}])
    assert c.get("/api/mv/stream", params={"client": "tv", "layout": "split", "tiles": two}).status_code == 409


def test_stream_endpoint_off_in_setup(screens, monkeypatch):
    monkeypatch.setattr(config, "get", lambda k, d=None: False if k == "multiview_enabled" else d)
    r = TestClient(api.app).get("/api/mv/stream", params={"client": "tv", "tiles": '[{"slot":1,"number":702}]'})
    assert r.status_code == 409 and "turned off" in r.json()["detail"]


def test_status_endpoint(screens):
    assert TestClient(api.app).get("/api/mv/status", params={"client": "nobody"}).json() == {"on": False, "tiles": []}


def test_connections_name_a_tile():
    assert connections._who("tv~2", {"tv": {"name": "Living room"}}, {})["name"] == "Living room · multiview 2"
    # a Roku's pictures: the name the Roku gave itself, and which picture
    connections.name_client("roku-abc", "Bedroom Roku")
    assert connections._who("roku-abc~3", {}, {}) == {"kind": "roku", "name": "Bedroom Roku · multiview 3"}
    assert connections._who("roku-abc", {}, {}) == {"kind": "roku", "name": "Bedroom Roku"}
    assert multiview.tile_client("tv", 2) == "tv~2"


# ---------------------------------------------------------------- On Demand in a picture
class VodProc:
    """A stand-in for the On Demand ffmpeg: some bytes, then nothing more (until it's released)."""
    def __init__(self):
        import io
        self.stdout = io.BytesIO(b"\x47" * 188 * 10)
        self.stderr = io.BytesIO(b"")


@pytest.fixture
def vods(screens, monkeypatch):
    started, released = [], []

    def vod_open(item_id, at):
        if item_id == "gone":
            raise LookupError("Can't play this")
        return {"url": f"http://p.example/movie/{item_id}.mkv", "cmd": ["ffmpeg"], "label": item_id,
                "source_id": 1, "duration": 5400, "at": at}

    def start_cmd(cmd, client, url, label, source_id):
        started.append(client)
        return VodProc()
    monkeypatch.setattr(mvstream, "vod_open", vod_open)
    monkeypatch.setattr(streaming, "start_cmd", start_cmd)
    monkeypatch.setattr(streaming, "release", lambda proc, client=None: released.append(client))
    return {"started": started, "released": released}


def test_parse_an_on_demand_picture():
    (t,) = mvstream.parse_tiles(json.dumps([{"slot": 2, "vod": "m:42", "at": 61.5}]))
    assert t["vod"] == "m:42" and t["at"] == 61.5 and t["number"] is None


def test_an_on_demand_picture_plays_and_says_where_it_is(vods):
    t = tiles((1, 702)) + [{"slot": 2, "vod": "m:42", "at": 600.0}]
    assert mvstream.start("tv", "pip", t, 1, 1280, 720) is not None
    assert vods["started"] == ["tv~2"]
    tile = mvstream.status("tv")["tiles"][1]
    assert tile["ok"] and tile["duration"] == 5400 and 600 <= tile["pos"] < 700


def test_a_new_layout_keeps_the_movie_going(vods, screens):
    t = tiles((1, 702)) + [{"slot": 2, "vod": "m:42", "at": 600.0}]
    mvstream.start("tv", "pip", t, 1, 1280, 720)
    screens[0].q.put(b"\x47" * 188)
    mvstream.start("tv", "split", list(reversed(t)), 2, 1280, 720)
    assert vods["started"] == ["tv~2"] and vods["released"] == []


def test_replacing_the_movie_stops_it(vods, screens):
    mvstream.start("tv", "pip", tiles((1, 702)) + [{"slot": 2, "vod": "m:42", "at": 0.0}], 1, 1280, 720)
    screens[0].q.put(b"\x47" * 188)
    mvstream.start("tv", "pip", tiles((1, 702), (2, 705)), 1, 1280, 720)
    assert vods["released"] == ["tv~2"]


def test_an_on_demand_title_that_cant_play_says_so(vods):
    mvstream.start("tv", "pip", tiles((1, 702)) + [{"slot": 2, "vod": "gone", "at": 0.0}], 1, 1280, 720)
    assert mvstream.status("tv")["tiles"][1] == {"slot": 2, "ok": False, "why": "Can't play this right now",
                                                 "pos": 0.0, "duration": 0}


# ---------------------------------------------------------------- the log
class EndedProc:
    """An ffmpeg that has already ended: `code` when it stopped, its last words on stderr."""
    def __init__(self, code, err=b"[h264 @ 0x1] co located POCs unavailable\n\n"):
        import io
        self.stdout, self.stderr, self.returncode = io.BytesIO(b""), io.BytesIO(err), code

    def poll(self):
        return self.returncode


def _drain(proc):
    import asyncio

    async def go():
        async for _ in mvstream.read(proc):
            pass
        await asyncio.sleep(0.05)            # the stderr reader's last lines
    asyncio.run(go())


def test_a_stream_stopped_on_purpose_isnt_logged_as_a_failure(caplog):
    proc = EndedProc(255)
    proc.mv_halted = True                    # a new layout took over
    _drain(proc)
    assert "stopped by itself" not in caplog.text


def test_a_stream_that_stops_by_itself_is_logged(caplog):
    _drain(EndedProc(1, b"Conversion failed!\n"))
    assert "stopped by itself (exit 1): Conversion failed!" in caplog.text


# ---------------------------------------------------------------- review fixes
def test_an_on_demand_title_in_picture_1_doesnt_lock_up_the_screen(vods, monkeypatch):
    # picture 1 runs as the screen's own client id: starting it stops that client's last stream, which
    # must not try to stop the multiview being started (it holds the screen's lock)
    import threading as th
    monkeypatch.setattr(streaming, "start_cmd", lambda cmd, client, url, label, sid: (streaming.stop_client(client), VodProc())[1])
    done = []
    t = th.Thread(target=lambda: done.append(mvstream.start("tv", "pip", [{"slot": 1, "vod": "m:42", "at": 0.0}], 1, 1280, 720)),
                  daemon=True)
    t.start()
    t.join(5)
    assert done and done[0] is not None


def test_ffmpeg_that_wont_start_leaves_no_picture_open(screens, monkeypatch):
    def no_ffmpeg(cmd):
        raise OSError("ffmpeg not found")
    monkeypatch.setattr(mvstream, "_spawn", no_ffmpeg)
    with pytest.raises(OSError):
        mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    assert all(s.closed for s in screens) and "tv" not in mvstream._screens


def test_a_screen_dropped_while_waiting_isnt_used(screens):
    mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    old = mvstream._screens["tv"]
    with old.lock:                           # dropped (GRACE ran out) while another request waits for it
        mvstream._drop(old)
    for s in screens:
        s.q.put(b"\x47" * 188)
    mvstream.start("tv", "split", tiles((1, 702), (2, 705)), 1, 1280, 720)
    assert mvstream._screens["tv"] is not old and mvstream.status("tv")["on"]
