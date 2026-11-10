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


def _gn_event(start, title):
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return {"startTime": iso(start), "endTime": iso(start + 1800), "program": {"title": title}}


def test_failed_gracenote_block_keeps_previous_listings(monkeypatch):
    """A timed-out block must not wipe that window's listings (the guide went blank for 'now')."""
    from tunerbox import config
    config.update({"gracenote": {"enabled": True, "lineup_id": "L", "headend_id": "H", "hours": 12}})
    t0 = int(epg.time.time()) // 1800 * 1800
    old = [("gn:5", t0, t0 + 1800, "Old morning show", "", "", "", "", "", "", 0, 0),
           ("gn:5", t0 + 6 * 3600, t0 + 6 * 3600 + 1800, "Old afternoon show", "", "", "", "", "", "", 0, 0)]
    epg._store(old, [(t0, None)])

    def block(c, g, t, i):
        if i == 0:
            return None                                   # timed out on every try
        return {"channels": [{"channelNo": "5", "events": [_gn_event(t, "New afternoon show")]}]}

    monkeypatch.setattr(epg, "_grid_block", block)
    monkeypatch.setattr(epg, "_applied", lambda chans: None)
    monkeypatch.setattr(epg.time, "sleep", lambda s: None)
    epg.fetch_gracenote()
    titles = [r["title"] for r in db.rows("SELECT title FROM programs WHERE channel='gn:5' ORDER BY start")]
    assert titles == ["Old morning show", "New afternoon show"]


def test_gracenote_block_is_retried(monkeypatch):
    calls = []

    class Client:
        def get(self, *a, **k):
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("The read operation timed out")
            return type("R", (), {"raise_for_status": lambda s: None, "content": b'{"channels": []}'})()

    monkeypatch.setattr(epg.time, "sleep", lambda s: None)
    assert epg._grid_block(Client(), {"lineup_id": "L", "headend_id": "H"}, 0, 0) == {"channels": []}
    assert len(calls) == 3


def test_failed_xmltv_download_keeps_name_matches(monkeypatch):
    with db.conn() as c:
        c.executemany("INSERT INTO epg_names(norm, channel) VALUES (?,?)",
                      [("fox news channel", "x:other.us"), ("cnn", "x:cnn.us")])
    monkeypatch.setattr(epg, "xmltv_urls", lambda: ["good", "bad"])

    def parse_url(url, ids, names, claimed, lo, hi):
        if url == "bad":
            raise OSError("peer closed connection")
        return {"cnn.us": [("x:cnn.us", lo + 3600, lo + 7200, "News", "", "", "", "", "", "", 0, 0)]}

    monkeypatch.setattr(epg, "_parse_xmltv", parse_url)
    assert epg.fetch_xmltv() == 1
    kept = {(r["norm"], r["channel"]) for r in db.rows("SELECT norm, channel FROM epg_names")}
    assert kept == {("fox news channel", "x:other.us")}      # cnn.us was re-read, so only fresh matches count

    monkeypatch.setattr(epg, "xmltv_urls", lambda: ["good"])  # everything downloads: stale matches go
    epg.fetch_xmltv()
    assert db.rows("SELECT norm, channel FROM epg_names") == []


def test_updated_time_only_moves_when_listings_arrive(monkeypatch):
    monkeypatch.setattr(epg, "fetch_gracenote", lambda progress=None: 0)
    monkeypatch.setattr(epg, "fetch_xmltv", lambda progress=None: 0)
    monkeypatch.setattr(epg, "xmltv_urls", lambda: ["http://guide"])
    epg.refresh_all()
    assert not db.get_meta("epg_updated") and db.get_meta("epg_tried")   # nothing came in

    monkeypatch.setattr(epg, "fetch_xmltv", lambda progress=None: 5)
    epg.refresh_all()
    assert db.get_meta("epg_updated")

    db.set_meta("epg_updated", 0)
    monkeypatch.setattr(epg, "fetch_xmltv", lambda progress=None: 0)
    monkeypatch.setattr(epg, "xmltv_urls", lambda: [])                   # no guides set up at all
    monkeypatch.setattr(epg, "gracenote_ready", lambda: False)
    epg.refresh_all()
    assert int(db.get_meta("epg_updated")) > 0
