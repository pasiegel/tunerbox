import io
from datetime import datetime, timezone

from tunerbox import db, epg

XMLTV = b"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="cnn.us"><display-name>CNN</display-name></channel>
  <channel id="other.us"><display-name>Fox News Channel</display-name></channel>
  <channel id="skip.us"><display-name>Nobody Wants This</display-name></channel>
  <programme start="20261109200000 +0000" stop="20261109210000 +0000" channel="cnn.us">
    <title>Anderson Cooper 360</title><sub-title>Tonight</sub-title><desc>News.</desc>
    <episode-num system="xmltv_ns">0.1.</episode-num><category>News</category>
    <icon src="http://i/ac.jpg"/><new/>
  </programme>
  <programme start="20261109210000 -0500" stop="20261109220000 -0500" channel="other.us">
    <title>Late Show</title><episode-num system="onscreen">S03E07</episode-num>
    <episode-num system="xmltv_ns">9.9.</episode-num>
  </programme>
  <programme start="20261109200000 +0000" stop="20261109210000 +0000" channel="skip.us">
    <title>Not kept</title>
  </programme>
  <programme start="20200101000000 +0000" stop="20200101010000 +0000" channel="cnn.us">
    <title>Too old</title>
  </programme>
</tv>"""

LO = int(datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp())
HI = LO + 3 * 86400


def parse(ids=("cnn.us",), names=("fox news channel",), claimed=()):
    return epg._parse_open(io.BytesIO(XMLTV), set(ids), set(names), set(claimed), LO, HI)


def test_xmltv_time_with_offsets():
    utc = epg._xmltv_time("20261109200000 +0000")
    assert utc == int(datetime(2026, 11, 9, 20, tzinfo=timezone.utc).timestamp())
    assert epg._xmltv_time("20261109150000 -0500") == utc
    assert epg._xmltv_time("20261109200000") == utc       # no offset = UTC
    assert epg._xmltv_time("garbage") == 0


def test_programmes_kept_by_id_and_by_display_name():
    out = parse()
    assert set(out) == {"cnn.us", "other.us"}               # skip.us neither wanted nor named
    titles = [p[3] for p in out["cnn.us"]]
    assert titles == ["Anderson Cooper 360"]                 # 2020 airing is outside the window


def test_programme_fields_and_episode_numbers():
    out = parse()
    (row,) = out["cnn.us"]
    channel, start, stop, title, sub, desc, ep, cat, rating, icon, new, live = row
    assert (channel, sub, desc, ep, cat, icon, new, live) == (
        "x:cnn.us", "Tonight", "News.", "S01E02", "News", "http://i/ac.jpg", 1, 0)
    (late,) = out["other.us"]
    assert late[6] == "S03E07"                               # onscreen wins over xmltv_ns


def test_display_name_matches_are_remembered():
    parse()
    assert db.rows("SELECT norm, channel FROM epg_names") == [{"norm": "fox news channel", "channel": "x:other.us"}]


def test_claimed_channels_are_left_alone():
    assert "cnn.us" not in parse(claimed=("cnn.us",))
