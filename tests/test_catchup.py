"""Catch-up TV: channels with a provider archive, the archive address for a show, and playing it."""
import json
import time

from fastapi.testclient import TestClient

from tunerbox import api, catchup, db, epg, guide, sources, streaming

NOW = 1794844800                  # 2026-11-16 16:00 UTC


# ------------------------------------------------------------------ what playlists say
def test_xtream_archive_days():
    assert catchup.xtream_days({"tv_archive": 1, "tv_archive_duration": "3"}) == 3
    assert catchup.xtream_days({"tv_archive": "1", "tv_archive_duration": 7}) == 7
    assert catchup.xtream_days({"tv_archive": "1"}) == 1                      # archive, length not given
    assert catchup.xtream_days({"tv_archive": 0, "tv_archive_duration": "3"}) == 0
    assert catchup.xtream_days({}) == 0
    assert catchup.xtream_days({"tv_archive": 1, "tv_archive_duration": "90"}) == catchup.MAX_DAYS


def test_m3u_attributes():
    days, rule = catchup.m3u_info({"catchup": "shift", "catchup-days": "5"})
    assert days == 5 and json.loads(rule) == {"type": "shift", "source": ""}
    days, rule = catchup.m3u_info({"catchup-source": "?utc={utc}&lutc={lutc}", "catchup-days": "2"})
    assert days == 2 and json.loads(rule)["type"] == "default"
    assert catchup.m3u_info({"timeshift": "3"})[0] == 3                     # older attribute: shift
    assert catchup.m3u_info({"tvg-rec": "4"})[0] == 4
    assert catchup.m3u_info({}) == (0, "")
    assert catchup.m3u_info({"catchup": "disabled", "catchup-days": "3"}) == (0, "")
    # the #EXTM3U line's attributes apply to every channel
    days, rule = catchup.m3u_info({}, {"catchup": "append", "catchup-source": "&s={start}", "catchup-days": "7"})
    assert days == 7 and json.loads(rule) == {"type": "append", "source": "&s={start}"}


# ------------------------------------------------------------------ archive addresses
def test_xtream_address_is_in_the_server_clock():
    src = {"id": 1, "url": "http://p:80/", "username": "u", "password": "pw"}
    assert catchup.xtream_url(src, 42, NOW, 60, offset=0) == "http://p:80/timeshift/u/pw/60/2026-11-16:16-00/42.ts"
    assert catchup.xtream_url(src, 42, NOW, 60, offset=3600) == "http://p:80/timeshift/u/pw/60/2026-11-16:17-00/42.ts"


def test_server_clock_is_read_from_the_login_answer():
    sid = db.run("INSERT INTO sources(name, type, url, username, password) VALUES ('P', 'xtream', 'http://p:80', 'u', 'p')")
    s = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    sources.save_account(s, {"user_info": {"status": "Active"},
                             "server_info": {"timezone": "Europe/Amsterdam", "time_now": "2026-11-16 17:04:28",
                                             "timestamp_now": 1794845068}})
    assert catchup.server_offset(sid) == 3600
    sources.save_account(s, {"user_info": {"status": "Active"}, "server_info": {"time_now": "garbage", "timestamp_now": 1}})
    assert catchup.server_offset(sid) == 3600                               # a bad answer keeps the old value


def test_m3u_addresses():
    start, stop, now = NOW, NOW + 3600, NOW + 7200
    live = "http://h/live/ch1.m3u8"
    assert catchup.m3u_url(live, {"type": "shift"}, start, stop, now) == f"{live}?utc={start}&lutc={now}"
    assert catchup.m3u_url("http://h/a?x=1", {"type": "shift"}, start, stop, now) == f"http://h/a?x=1&utc={start}&lutc={now}"
    assert (catchup.m3u_url(live, {"type": "append", "source": "?start={utc}&end={utcend}&d={duration:60}"}, start, stop, now)
            == f"{live}?start={start}&end={stop}&d=60")
    assert (catchup.m3u_url(live, {"type": "default", "source": "http://arch/{Y}/{m}/{d}/{H}-{M}.ts?o=${offset}"}, start, stop, now)
            == "http://arch/2026/11/16/16-00.ts?o=7200")
    assert (catchup.m3u_url(live, {"type": "default", "source": "http://arch/x?t={utc:Y-m-dTH:M:S}"}, start, stop, now)
            == "http://arch/x?t=2026-11-16T16:00:00")
    assert (catchup.m3u_url("http://f/ch/index.m3u8", {"type": "flussonic"}, start, stop, now)
            == f"http://f/ch/archive-{start}-3600.m3u8")
    assert catchup.m3u_url("http://f/ch/mpegts", {"type": "fs"}, start, stop, now) == f"http://f/ch/timeshift_abs-{start}.ts"
    assert (catchup.m3u_url("http://x:8080/u/p/77", {"type": "xc"}, start, stop, now)
            == "http://x:8080/timeshift/u/p/60/2026-11-16:16-00/77.ts")
    # a playlist address with player headers keeps them
    assert (catchup.m3u_url(live + "|User-Agent=Box", {"type": "shift"}, start, stop, now)
            == f"{live}?utc={start}&lutc={now}|User-Agent=Box")
    assert catchup.m3u_url("http://f/ch/other", {"type": "flussonic"}, start, stop, now) == ""


# ------------------------------------------------------------------ channels, the guide, playing
def _setup(days=3, kind="xtream"):
    url = "http://p:80" if kind == "xtream" else "http://h/list.m3u"
    sid = db.run("INSERT INTO sources(name, type, url, username, password) VALUES ('P', ?, ?, 'u', 'pw')", (kind, url))
    key = f"{sid}:x497" if kind == "xtream" else f"{sid}:abc"
    db.run("""INSERT INTO streams(key, source_id, name, url, archive_days, catchup) VALUES (?,?,?,?,?,?)""",
           (key, sid, "BBC One", "http://p:80/live/u/pw/497.ts" if kind == "xtream" else "http://h/live/1.ts",
            days, "" if kind == "xtream" else json.dumps({"type": "shift"})))
    db.run("INSERT INTO lineup(number, name) VALUES (101, 'BBC One')")
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (101, ?, 'manual')", (key,))
    db.run("INSERT INTO lineup(number, name) VALUES (102, 'Plain')")
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?,?,?,?)", (f"{sid}:x9", sid, "Plain", "http://p/9.ts"))
    db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (102, ?, 'manual')", (f"{sid}:x9",))
    db.set_meta(f"xtream_tz_{sid}", 3600)
    guide.invalidate()
    return sid


def _show(number, start, stop, title="News"):
    db.run("INSERT INTO programs(channel, start, stop, title, episode, subtitle) VALUES (?,?,?,?,?,?)",
           (f"gn:{number}", start, stop, title, "S1E2", "Part two"))
    guide.invalidate()


def test_channels_say_how_much_catch_up_they_have():
    _setup(days=3)
    _show(101, NOW, NOW + 1800)
    _show(102, NOW, NOW + 1800)
    chans = {c["number"]: c for c in TestClient(api.app).get("/api/channels").json()}
    assert chans[101]["catchup"] == 3 and chans[102]["catchup"] == 0


def test_a_show_from_the_archive(monkeypatch):
    sid = _setup(days=3)
    t = int(time.time())
    start = t - 7200 - t % 60
    _show(101, start, start + 3600)
    _show(101, t - 5 * 86400, t - 5 * 86400 + 3600, "Too old")
    _show(102, start, start + 3600)
    c = TestClient(api.app)
    r = c.get(f"/api/vod/play/cu-101-{start}")
    assert r.status_code == 200
    it = r.json()
    assert it["catchup"] and it["title"] == "News" and it["sub"] == "S1E2 · Part two" and it["duration"] == 3600
    assert "row" not in it and "url" not in it and not it["airing"]
    assert c.get(f"/api/vod/play/cu-101-{t - 5 * 86400}").status_code == 404      # past the 3 days
    assert c.get(f"/api/vod/play/cu-102-{start}").status_code == 404             # no catch-up on that channel
    assert c.get("/api/vod/play/cu-101-123").status_code == 404                  # no such show

    # skipping in asks the archive for that minute, in the server's clock, and ffmpeg skips the rest
    full = catchup.item(f"cu-101-{start}")
    url, rest = catchup.url_at(full, 125.5)
    hhmm = time.strftime("%Y-%m-%d:%H-%M", time.gmtime(start + 120 + 3600))
    assert url == f"http://p:80/timeshift/u/pw/{(3600 - 120 + 59) // 60 + 5}/{hhmm}/497.ts" and abs(rest - 5.5) < 1e-6

    seen = []

    def start_cmd(cmd, *a, **k):
        seen.append(cmd)
        raise OSError("no ffmpeg here")
    monkeypatch.setattr(streaming, "start_cmd", start_cmd)
    assert c.get(f"/api/vod/stream/cu-101-{start}?ss=60").status_code == 502
    assert any("/timeshift/u/pw/" in a for a in seen[0])
    # not kept in Continue Watching
    assert c.post("/api/vod/progress", json={"id": f"cu-101-{start}", "position": 300, "duration": 3600}).json() == {"watched": False}
    assert db.one("SELECT COUNT(*) AS n FROM vod_progress")["n"] == 0
    assert sid


def test_a_show_on_now_can_start_over_and_m3u_channels_too():
    _setup(days=2, kind="m3u")
    t = int(time.time())
    start = t - 600
    _show(101, start, start + 3600)
    it = catchup.item(f"cu-101-{start}")
    assert it and it["airing"]
    url, rest = catchup.url_at(it, 0)
    assert url.startswith(f"http://h/live/1.ts?utc={start - start % 60}&lutc=")
    assert catchup.item(f"cu-101-{t + 600}") is None                            # hasn't started yet


def test_guide_keeps_past_shows_only_for_catch_up_channels():
    _setup(days=3)
    t = int(time.time())
    _show(101, t - 2 * 86400, t - 2 * 86400 + 1800, "Old, archived")
    _show(101, t - 4 * 86400, t - 4 * 86400 + 1800, "Older than the archive")
    _show(102, t - 2 * 86400, t - 2 * 86400 + 1800, "Old, no catch-up")
    _show(102, t - 3600, t - 1800, "An hour ago")
    epg._store([], [])
    left = {r["title"] for r in db.rows("SELECT title FROM programs")}
    assert left == {"Old, archived", "An hour ago"}


def test_archive_is_read_fast_at_first_then_at_playing_speed(monkeypatch):
    monkeypatch.setattr(streaming, "has_option", lambda name: True)
    assert catchup.pace(0) == ["-readrate", "1", "-readrate_initial_burst", "10"]
    assert catchup.pace(37.5) == ["-readrate", "1", "-readrate_initial_burst", "47"]
    monkeypatch.setattr(streaming, "has_option", lambda name: False)        # older ffmpeg
    assert catchup.pace(37) == ["-re"]
    from tunerbox import vod
    cmd = vod.stream_cmd("http://p/x.ts", 0, None, ["-readrate", "1"])
    assert "-re" not in cmd and cmd.index("-readrate") < cmd.index("-i")
    assert "-re" in vod.stream_cmd("http://p/x.ts")                        # On Demand titles as before
