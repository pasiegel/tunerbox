import time

import pytest

from tunerbox import db, guide, sports


@pytest.mark.parametrize("title, category, want", [
    ("NFL Football", "Sports", "Football"),
    ("College Basketball", "Sports event", "Basketball"),
    ("Premier League", "Football", "Soccer"),          # UK-style category: the title wins
    ("Premier League Football", "Sports", "Soccer"),
    ("Lakers at Celtics", "Basketball", "Basketball"),  # nothing in the title: the category decides
    ("NASCAR Cup Series", "Sports", "Motorsports"),
    ("UFC Fight Night", "Sports", "MMA"),
    ("SportsCenter", "Sports", ""),
])
def test_sport_type(title, category, want):
    assert sports.sport_type({"title": title, "category": category, "subtitle": ""}) == want


def test_on_now_live_first_then_start_sport_name():
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    now = int(time.time())
    shows = [  # (channel, minutes ago it started, title, live)
        (2, 30, "NHL Hockey", 0),
        (3, 30, "College Basketball", 0),
        (4, 30, "NBA Basketball", 1),
        (5, 90, "Golf Central Live", 0),
        (6, 30, "SportsCenter", 0),
        (7, 90, "MLB Baseball", 0),
        (8, 60, "NFL Football", 1),
        (9, 30, "NHL Hockey Night", 1),
    ]
    for n, ago, title, live in shows:
        db.run("INSERT INTO streams(key, source_id, name, url) VALUES (?, ?, ?, 'http://s')", (f"k{n}", sid, f"C{n}"))
        db.run("INSERT INTO lineup(number, name) VALUES (?, ?)", (n, f"C{n}"))
        db.run("INSERT INTO mappings(number, stream_key, mode) VALUES (?, ?, 'manual')", (n, f"k{n}"))
        db.run("INSERT INTO programs(channel, start, stop, title, category, is_live) VALUES (?, ?, ?, ?, 'Sports', ?)",
               (f"gn:{n}", now - ago * 60, now + 3600, title, live))
    guide.invalidate()
    on_now = sports.overview()["on_now"]
    assert [p["title"] for p in on_now] == [
        # LIVE
        "NFL Football",                                            # started 60 min ago
        "NBA Basketball", "NHL Hockey Night",                      # 30 min ago: Basketball < Hockey
        # ON
        "MLB Baseball", "Golf Central Live",                       # 90 min ago: Baseball < Golf
        "College Basketball", "NHL Hockey",                        # 30 min ago: Basketball < Hockey
        "SportsCenter",                                            # unknown sport last
    ]
    assert on_now[0]["sport"] == "Football"
