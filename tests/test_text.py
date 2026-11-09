import pytest

from tunerbox.probe import split_url
from tunerbox.text import core, find_callsigns, is_foreign, is_west, norm, parse_callsign, tvg_stem


@pytest.mark.parametrize("raw, want", [
    ("US: Fox News Channel HD (1080p)", "fox news channel"),
    ("[US] ESPN2 FHD", "espn 2"),
    ("USA | A&E", "a and e"),
    ("Nick Jr.", "nick jr"),
    ("CNN East", "cnn"),
    ("", ""),
])
def test_norm(raw, want):
    assert norm(raw) == want


def test_core_drops_filler_words():
    assert core("fox news channel") == "fox news"
    assert core("the channel") == "the channel"       # nothing left: keep the original


def test_tvg_stem():
    assert tvg_stem("Fox.News.Channel.HD.us2") == "fox news channel"
    assert tvg_stem("CNN.us@east") == "cnn"


@pytest.mark.parametrize("cs, want", [
    ("WOODDT2", ("WOOD", 2)),
    ("WXMI", ("WXMI", 1)),
    ("WOOD-TV", ("WOOD", 1)),
    ("CNN", None),
    ("", None),
])
def test_parse_callsign(cs, want):
    assert parse_callsign(cs) == want


def test_find_callsigns():
    assert find_callsigns("US: NBC 8 WOOD-DT2 Grand Rapids") == {("WOOD", 2)}
    assert find_callsigns("Wood Working Channel") == set()        # prose, not a callsign
    assert find_callsigns("WEST Coast Feed") == set()             # blocked word


def test_foreign_and_west():
    assert is_foreign("UK: Sky Sports")
    assert is_foreign("Canal 5 (México)")
    assert not is_foreign("US: ESPN")
    assert is_west("HBO (West)")
    assert not is_west("HBO East")


def test_split_url_kodi_headers():
    url, headers = split_url("http://x/y.m3u8|User-Agent=foo&Referer=http://r/")
    assert url == "http://x/y.m3u8"
    assert headers["User-Agent"] == "foo" and headers["Referer"] == "http://r/"
    url, headers = split_url("http://x/plain.ts")
    assert url == "http://x/plain.ts" and "Mozilla" in headers["User-Agent"]
