"""An HDHomeRun puts every antenna channel in one group, so Setup lists and hides its channels one by one."""
from tunerbox import db, sources

LINEUP = [{"GuideNumber": "8.1", "GuideName": "WOOD-HD", "URL": "http://hd/auto/v8.1"},
          {"GuideNumber": "8.2", "GuideName": "WOOD-DT2", "URL": "http://hd/auto/v8.2"},
          {"GuideNumber": "13.1", "GuideName": "WZZM", "URL": "http://hd/auto/v13.1"}]


def _fake(monkeypatch, lineup):
    def fetch(src):
        return [{"name": it["GuideName"], "url": it["URL"], "tvg_id": "", "logo": "", "grp": "Antenna (DUO)",
                 "xid": f"hd{it['GuideNumber']}", "num": it["GuideNumber"],
                 "label": f"{it['GuideNumber']} {it['GuideName']}"} for it in lineup]
    monkeypatch.setattr(sources, "fetch_hdhomerun", lambda src: (fetch(src), ""))


def _src():
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('HdHomeRun', 'hdhomerun', 'http://hd')")
    return sid, lambda: sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))


def _names(sid):
    return sorted(r["name"] for r in db.rows("SELECT name FROM streams WHERE source_id=?", (sid,)))


def test_lists_each_channel_in_number_order(monkeypatch):
    _fake(monkeypatch, LINEUP)
    sid, scan = _src()
    scan()
    g = sources.groups(sid)
    assert g["channels"] is True
    assert [x["name"] for x in g["groups"]] == ["8.1 WOOD-HD", "8.2 WOOD-DT2", "13.1 WZZM"]


def test_hiding_a_channel_drops_it_and_keeps_it_out(monkeypatch):
    _fake(monkeypatch, LINEUP)
    sid, scan = _src()
    scan()
    r = sources.set_hidden_groups(sid, ["8.2 WOOD-DT2"])
    assert r["newly_hidden"] == 1
    assert _names(sid) == ["WOOD-HD", "WZZM"]                     # dropped right away
    # the tuner renames it: still left out, matched by its number
    _fake(monkeypatch, [dict(LINEUP[1], GuideName="WOOD-ME")] + [LINEUP[0], LINEUP[2]])
    scan()
    assert _names(sid) == ["WOOD-HD", "WZZM"]
    hidden = [x for x in sources.groups(sid)["groups"] if x["hidden"]]
    assert [x["name"] for x in hidden] == ["8.2 WOOD-ME"]


def test_whole_antenna_hidden_before_stays_listed(monkeypatch):
    _fake(monkeypatch, LINEUP)
    sid, scan = _src()
    db.run("UPDATE sources SET hidden_groups=? WHERE id=?", ('["Antenna (DUO)"]', sid))
    scan()
    assert _names(sid) == []
    g = sources.groups(sid)["groups"]
    assert {"name": "Antenna (DUO)", "count": 0, "hidden": True, "pattern": False} in g    # can be ticked again


def test_playlist_groups_unchanged():
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    db.run("INSERT INTO source_groups(source_id, grp, count) VALUES (?, 'US| NEWS', 4)", (sid,))
    g = sources.groups(sid)
    assert "channels" not in g and g["groups"][0]["name"] == "US| NEWS"


def test_picker_finds_antenna_channel_by_number(monkeypatch):
    from tunerbox import matcher
    _fake(monkeypatch, LINEUP)
    sid, scan = _src()
    scan()
    exact = matcher.suggest(q="8.1", source_id=sid)
    assert exact[0]["name"] == "WOOD-HD" and exact[0]["score"] == 100
    major = {s["name"] for s in matcher.suggest(q="8", source_id=sid) if s["score"] >= 96}
    assert major == {"WOOD-HD", "WOOD-DT2"}                         # 8.1 and 8.2, not 13.1
