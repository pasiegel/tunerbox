from tunerbox.sources import parse_m3u, xtream_from_get_url

PLAYLIST = """#EXTM3U x-tvg-url="http://guide/epg.xml.gz"
#EXTINF:-1 tvg-id="CNN.us" tvg-logo="http://l/cnn.png" group-title="News, US",CNN HD
http://s/cnn.m3u8
#EXTINF:-1 tvg-name="Name From Attr",
#EXTVLCOPT:http-user-agent=MyAgent/1.0
#EXTVLCOPT:http-referrer=http://ref/
http://s/two.ts
#EXTINF:-1,Grouped
#EXTGRP:Sports
http://s/three.ts

http://s/bare/four.ts
"""


def test_parse_m3u_header_and_entries():
    header, entries = parse_m3u(PLAYLIST)
    assert header["x-tvg-url"] == "http://guide/epg.xml.gz"
    assert [e["name"] for e in entries] == ["CNN HD", "Name From Attr", "Grouped", "four.ts"]


def test_parse_m3u_comma_inside_quotes_is_not_the_name():
    _, entries = parse_m3u(PLAYLIST)
    assert entries[0]["attrs"]["group-title"] == "News, US"
    assert entries[0]["attrs"]["tvg-id"] == "CNN.us"


def test_parse_m3u_vlc_options_become_kodi_headers():
    _, entries = parse_m3u(PLAYLIST)
    assert entries[1]["url"] == "http://s/two.ts|User-Agent=MyAgent/1.0&Referer=http://ref/"
    # options apply to one entry only
    assert entries[2]["url"] == "http://s/three.ts"


def test_parse_m3u_extgrp():
    _, entries = parse_m3u(PLAYLIST)
    assert entries[2]["attrs"]["group-title"] == "Sports"


def test_xtream_from_get_url():
    assert xtream_from_get_url("http://h:8080/get.php?username=u&password=p&type=m3u_plus") == ("http://h:8080", "u", "p")
    assert xtream_from_get_url("http://h/playlist.m3u") is None
    assert xtream_from_get_url("http://h/get.php?type=m3u") is None


# ---------------------------------------------------------------- stable keys across refreshes
from tunerbox import db, matcher, sources  # noqa: E402


def _refresh(monkeypatch, sid, entries):
    monkeypatch.setattr(sources, "fetch_m3u", lambda src: (entries, ""))
    sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    return {r["url"]: r["key"] for r in db.rows("SELECT key, url FROM streams WHERE source_id=?", (sid,))}


def _e(name, url, grp="PPV"):
    return {"name": name, "url": url, "tvg_id": "", "logo": "", "grp": grp}


def test_renamed_m3u_entry_keeps_its_key_and_extra_number(monkeypatch):
    sid = sources.add_source("p", "m3u", "http://x/list.m3u")
    before = _refresh(monkeypatch, sid, [_e("PPV 03", "http://s/3.ts"), _e("PPV 04", "http://s/4.ts")])
    matcher.rebuild_extras()
    nums = {r["stream_key"]: r["number"] for r in db.rows("SELECT * FROM extras")}
    after = _refresh(monkeypatch, sid, [_e("PPV 03: UFC 310", "http://s/3.ts"), _e("PPV 04", "http://s/4.ts")])
    assert after == before
    assert db.one("SELECT name FROM streams WHERE key=?", (after["http://s/3.ts"],))["name"] == "PPV 03: UFC 310"
    matcher.rebuild_extras()
    assert {r["stream_key"]: r["number"] for r in db.rows("SELECT * FROM extras WHERE active=1")} == nums


def test_unchanged_name_wins_over_url_reuse(monkeypatch):
    sid = sources.add_source("p", "m3u", "http://x/list.m3u")
    before = _refresh(monkeypatch, sid, [_e("A", "http://s/1.ts"), _e("B", "http://s/2.ts")])
    # the provider swapped the two URLs: each name keeps its own key
    after = _refresh(monkeypatch, sid, [_e("A", "http://s/2.ts"), _e("B", "http://s/1.ts")])
    assert after == {"http://s/2.ts": before["http://s/1.ts"], "http://s/1.ts": before["http://s/2.ts"]}


def test_new_entry_on_a_new_url_gets_a_new_key(monkeypatch):
    sid = sources.add_source("p", "m3u", "http://x/list.m3u")
    before = _refresh(monkeypatch, sid, [_e("A", "http://s/1.ts")])
    after = _refresh(monkeypatch, sid, [_e("A", "http://s/1.ts"), _e("New", "http://s/9.ts")])
    assert after["http://s/1.ts"] == before["http://s/1.ts"]
    assert after["http://s/9.ts"] not in before.values()


def test_hdhomerun_that_moved_is_found_by_its_device_id(monkeypatch):
    from tunerbox import db, sources
    monkeypatch.setattr(sources, "_moving", {})          # each test looks afresh (once a minute per source)
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('antenna', 'hdhomerun', 'http://192.168.1.122')")
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES ('2:xhd3.1', ?, 'WOOD', 'http://192.168.1.122:5004/auto/v3.1')", (sid,))
    db.set_meta(f"hdhr_device_{sid}", "1050ABCD")

    def info(addr, timeout=6):
        if "192.168.1.122" in addr:
            raise OSError("Connection refused")
        return {"DeviceID": "1050ABCD"}
    monkeypatch.setattr(sources, "hdhr_info", info)
    monkeypatch.setattr(sources, "discover_hdhomerun", lambda timeout=2.0: [
        {"ip": "192.168.1.50", "id": "99999999"}, {"ip": "192.168.1.131", "id": "1050abcd"}])
    assert sources.hdhr_moved(sid, now=1000)
    assert db.one("SELECT url FROM sources WHERE id=?", (sid,))["url"] == "http://192.168.1.131"
    assert db.one("SELECT url FROM streams WHERE key='2:xhd3.1'")["url"] == "http://192.168.1.131:5004/auto/v3.1"
    assert not sources.hdhr_moved(sid, now=1030)            # looked a moment ago: not again yet
    assert not sources.hdhr_moved(sid, now=2000)            # answers where it is now: nothing to move


def test_hdhomerun_not_found_elsewhere_stays_put(monkeypatch):
    from tunerbox import db, sources
    monkeypatch.setattr(sources, "_moving", {})          # each test looks afresh (once a minute per source)
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('antenna', 'hdhomerun', 'http://192.168.1.122')")
    db.set_meta(f"hdhr_device_{sid}", "1050ABCD")
    monkeypatch.setattr(sources, "hdhr_info", lambda addr, timeout=6: (_ for _ in ()).throw(OSError("refused")))
    monkeypatch.setattr(sources, "discover_hdhomerun", lambda timeout=2.0: [])
    assert not sources.hdhr_moved(sid, now=5000)
    assert db.one("SELECT url FROM sources WHERE id=?", (sid,))["url"] == "http://192.168.1.122"


def test_hdhomerun_check_notes_the_device_id_and_looks_for_one_that_moved(monkeypatch):
    from tunerbox import db, sources
    monkeypatch.setattr(sources, "_moving", {})          # each test looks afresh (once a minute per source)
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('antenna', 'hdhomerun', 'http://192.168.1.131')")
    monkeypatch.setattr(sources, "hdhr_info", lambda addr, timeout=6: {"DeviceID": "1050ABCD"})
    sources.hdhr_check()
    assert db.get_meta(f"hdhr_device_{sid}") == "1050ABCD"
    looked = []
    monkeypatch.setattr(sources, "hdhr_info", lambda addr, timeout=6: (_ for _ in ()).throw(OSError("refused")))
    monkeypatch.setattr(sources, "hdhr_moved", lambda source_id, now=None: looked.append(source_id))
    sources.hdhr_check()
    assert looked == [sid]


def test_hdhomerun_check_runs_every_15_minutes_in_the_background(monkeypatch):
    from tunerbox import sources
    started = []
    monkeypatch.setattr(sources, "_hdhr_checked", {"t": 0})
    monkeypatch.setattr(sources.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: started.append(target)})())
    sources.hdhr_tick(10_000)
    sources.hdhr_tick(10_000 + 60)
    sources.hdhr_tick(10_000 + sources.HDHR_CHECK)
    assert started == [sources.hdhr_check, sources.hdhr_check]


def test_hdhomerun_that_moved_before_its_id_was_noted_is_found_by_its_lineup(monkeypatch):
    from tunerbox import db, sources
    monkeypatch.setattr(sources, "_moving", {})          # each test looks afresh (once a minute per source)
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('antenna', 'hdhomerun', 'http://192.168.1.131')")
    for n in ("3.1", "8.1", "17.1"):
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, ?)",
               (f"{sid}:xhd{n}", sid, n, f"http://192.168.1.131:5004/auto/v{n}"))

    def info(addr, timeout=6):
        if "192.168.1.131" in addr:
            raise OSError("Connection refused")
        return {"DeviceID": "106B8BC9" if "130" in addr else "999", "LineupURL": f"http://{addr}/lineup.json"}
    monkeypatch.setattr(sources, "hdhr_info", info)
    lineups = {"192.168.1.130": ["3.1", "8.1", "17.1", "17.2"], "192.168.1.140": ["5.1", "6.1"]}
    monkeypatch.setattr(sources, "_lineup_numbers", lambda ip: set(lineups[ip]))
    monkeypatch.setattr(sources, "discover_hdhomerun", lambda timeout=2.0: [
        {"ip": "192.168.1.140", "id": "999"}, {"ip": "192.168.1.130", "id": "106B8BC9"}])
    assert sources.hdhr_moved(sid, now=1000)               # another tuner is there too: the one with our channels
    assert db.one("SELECT url FROM sources WHERE id=?", (sid,))["url"] == "http://192.168.1.130"
    assert db.one("SELECT url FROM streams WHERE key=?", (f"{sid}:xhd17.1",))["url"] == "http://192.168.1.130:5004/auto/v17.1"
    assert db.get_meta(f"hdhr_device_{sid}") == "106B8BC9"  # known by its id from now on


def test_hdhomerun_with_no_id_and_two_tuners_that_fit_is_left_alone(monkeypatch):
    from tunerbox import db, sources
    monkeypatch.setattr(sources, "_moving", {})          # each test looks afresh (once a minute per source)
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('antenna', 'hdhomerun', 'http://192.168.1.131')")
    db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, '3.1', 'http://192.168.1.131:5004/auto/v3.1')",
           (f"{sid}:xhd3.1", sid))
    monkeypatch.setattr(sources, "hdhr_info", lambda addr, timeout=6: (_ for _ in ()).throw(OSError("refused")))
    monkeypatch.setattr(sources, "_lineup_numbers", lambda ip: {"3.1"})
    monkeypatch.setattr(sources, "discover_hdhomerun", lambda timeout=2.0: [
        {"ip": "192.168.1.130", "id": "A"}, {"ip": "192.168.1.140", "id": "B"}])
    assert not sources.hdhr_moved(sid, now=1000)
    assert db.one("SELECT url FROM sources WHERE id=?", (sid,))["url"] == "http://192.168.1.131"
