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
