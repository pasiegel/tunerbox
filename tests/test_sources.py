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
