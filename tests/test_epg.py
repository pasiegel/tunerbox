import io
from datetime import datetime, timedelta, timezone

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


# ---------------------------------------------------------------- unchanged guides aren't read again
def _local_guide(tmp_path):
    p = tmp_path / "guide.xml"
    p.write_bytes(XMLTV.replace(b"20261109", (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y%m%d").encode()))
    return str(p)


def test_unchanged_guide_is_not_parsed_again(monkeypatch, tmp_path):
    url = _local_guide(tmp_path)
    monkeypatch.setattr(epg, "xmltv_urls", lambda: [url])
    monkeypatch.setattr(epg, "wanted_ids", lambda: ({"cnn.us"}, {"fox news channel"}))
    first = epg.fetch_xmltv()
    assert first >= 1
    parses = []
    real = epg._parse_open
    monkeypatch.setattr(epg, "_parse_open", lambda *a, **k: parses.append(1) or real(*a, **k))
    assert epg.fetch_xmltv() == first                         # same count, so the refresh counts as a success
    assert parses == []
    assert {r["channel"] for r in db.rows("SELECT channel FROM epg_names")} == {"x:other.us"}
    assert db.one("SELECT count(*) n FROM programs WHERE channel='x:cnn.us'")["n"] >= 1

    # a newly wanted name that the file doesn't have: still nothing to read
    monkeypatch.setattr(epg, "wanted_ids", lambda: ({"cnn.us", "nowhere"}, {"fox news channel", "ppv 01 ufc"}))
    epg.fetch_xmltv()
    assert parses == []
    # a newly wanted channel the file does have: read it again
    monkeypatch.setattr(epg, "wanted_ids", lambda: ({"cnn.us", "skip.us"}, {"fox news channel"}))
    epg.fetch_xmltv()
    assert parses == [1]


def test_changed_guide_is_parsed_again(monkeypatch, tmp_path):
    url = _local_guide(tmp_path)
    monkeypatch.setattr(epg, "xmltv_urls", lambda: [url])
    monkeypatch.setattr(epg, "wanted_ids", lambda: ({"cnn.us"}, set()))
    epg.fetch_xmltv()
    import os
    st = os.stat(url)
    os.utime(url, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    parses = []
    real = epg._parse_open
    monkeypatch.setattr(epg, "_parse_open", lambda *a, **k: parses.append(1) or real(*a, **k))
    epg.fetch_xmltv()
    assert parses == [1]


def test_web_guide_not_modified_reuses_the_cached_copy(monkeypatch):
    sent = []

    class Resp:
        def __init__(self, code, body=b"", headers=None):
            self.status_code, self.body, self.headers = code, body, headers or {}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)
        def iter_bytes(self, n): yield self.body

    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def stream(self, method, url, headers=None):
            sent.append(headers or {})
            if headers and headers.get("If-None-Match") == '"v1"':
                return Resp(304)
            return Resp(200, XMLTV, {"etag": '"v1"'})

    monkeypatch.setattr(epg, "_client", lambda timeout=90: Client())
    path, v1 = epg._download("http://guide/x.xml")
    path2, v2 = epg._download("http://guide/x.xml")
    assert (path2, v2) == (path, v1)
    assert sent[1] == {"If-None-Match": '"v1"'}
    assert open(path, "rb").read() == XMLTV


def test_genres_from_any_guide_category():
    from tunerbox.epg import genre
    assert [genre(c) for c in ("Movie", "Sports", "Sports event", "News", "Newsmagazine", "Kids", "Children",
                                "Family", "Animation", "Series", "", None)] == \
        ["movies", "sports", "sports", "news", "news", "kids", "kids", "kids", "kids", "", "", ""]


def test_channels_showing_a_genre(monkeypatch):
    from fastapi.testclient import TestClient
    from tunerbox import api, db, guide
    chans = [{"number": n, "epg": e} for n, e in ((4, "gn:4"), (24, "gn:24"), (60, "x:tnt"), (99, None))]
    monkeypatch.setattr(guide, "channels", lambda include_unmapped=None: chans)
    for ch, start, cat in (("gn:4", 1000, "News"), ("gn:24", 1000, "Sports"), ("x:tnt", 5000, "Sports event"), ("gn:4", 5000, "Series")):
        db.run("INSERT INTO programs(channel, start, stop, title, category) VALUES (?, ?, ?, 't', ?)", (ch, start, start + 3600, cat))
    c = TestClient(api.app)
    assert c.get("/api/guide/genre?genre=sports&start=0&stop=4000").json() == [24]
    assert c.get("/api/guide/genre?genre=sports&start=0&stop=9000").json() == [24, 60]
    assert c.get("/api/guide/genre?genre=news&start=5000&stop=9000").json() == []
    assert c.get("/api/guide/genre?genre=drama&start=0&stop=1").status_code == 400
