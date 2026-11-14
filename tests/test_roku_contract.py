"""The Roku app (separate repo, tunerbox-roku) is a second front end on this API. These checks fail
when a change here would break it: a route it calls is renamed or removed, or a field it reads is
dropped. If one fails on purpose, update the Roku app in the same change (and this list with it).
The endpoints are also listed in ideas/IMPROVEMENT-IDEAS.md under "Roku app"."""
import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

from tunerbox import api, db

# (method, path) exactly as the Roku calls them; {x} marks a value the Roku fills in
ROKU_ROUTES = [
    ("GET", "/playlist.m3u"),
    ("GET", "/epg.xml"),
    ("GET", "/api/stream/{number}"),
    ("GET", "/api/feed/stream"),
    ("GET", "/api/favorites"),
    ("POST", "/api/favorites"),
    ("GET", "/api/settings"),
    ("GET", "/api/search"),
    ("POST", "/api/recordings"),
    ("GET", "/api/sports"),
    ("GET", "/api/sports/group"),
    ("GET", "/api/sports/check"),
    ("POST", "/api/sports/check"),
    ("GET", "/api/vod"),
    ("GET", "/api/vod/categories"),
    ("GET", "/api/vod/titles"),
    ("GET", "/api/vod/search"),
    ("GET", "/api/vod/title/{title_id}"),
    ("GET", "/api/vod/poster/{title_id}"),
    ("GET", "/api/vod/play/{item_id}"),
    ("GET", "/api/vod/stream/{item_id}"),
    ("POST", "/api/vod/progress"),
    ("POST", "/api/vod/watched"),
    ("POST", "/api/vod/list"),
    # Home (HomeView.brs / MainScene.brs)
    ("GET", "/api/home"),
    ("GET", "/api/art/{number}/{start}"),
    ("GET", "/api/recordings/{rid}/art"),
    ("GET", "/api/recordings/{rid}/file"),
    ("POST", "/api/recordings/{rid}/progress"),
    ("DELETE", "/api/recordings/{rid}"),
    ("DELETE", "/api/reminders/{rid}"),
]


@pytest.fixture
def client():
    return TestClient(api.app)        # not as a context manager: no startup jobs or network refreshes


def _routes():
    return {(m, r.path) for r in api.app.routes if isinstance(r, Route) for m in (r.methods or ())}


@pytest.mark.parametrize("method,path", ROKU_ROUTES, ids=[f"{m} {p}" for m, p in ROKU_ROUTES])
def test_route_the_roku_calls_exists(method, path):
    assert (method, path) in _routes(), f"the Roku app calls {method} {path}; update tunerbox-roku too"


def test_playlist_and_epg_formats(client):
    assert client.get("/playlist.m3u").text.startswith("#EXTM3U")
    assert client.get("/epg.xml").text.lstrip().startswith("<")


def test_favorites_is_a_list(client):
    # the Roku keeps the reply only when it's a JSON array (LoadTask.brs loadFavs / setFav)
    assert isinstance(client.get("/api/favorites").json(), list)


def test_settings_has_sports_show_unmatched(client):
    s = client.get("/api/settings").json()
    assert isinstance(s.get("sports_show_unmatched"), bool)


def test_settings_has_home_screen(client):
    # the Roku shows Home only when this is true (MainScene.brs applySettings)
    assert isinstance(client.get("/api/settings").json().get("home_screen"), bool)


def test_home_lists(client):
    d = client.get("/api/home").json()
    for key in ("favorites", "recent", "continue", "sports", "recorded", "coming"):   # HomeView.brs buildRows
        assert isinstance(d.get(key), list), key


def test_sports_lists(client):
    d = client.get("/api/sports").json()
    for key in ("on_now", "upcoming", "channels", "groups"):     # all four or the Roku shows an error
        assert key in d, key


def test_sports_group_feeds(client):
    sid = db.run("INSERT INTO sources(name, type, url) VALUES ('p', 'm3u', 'http://x')")
    db.run("INSERT INTO streams(key, source_id, name, url, grp) "
           "VALUES ('e1', ?, 'UFC 300: Main Card', 'http://s', 'PPV Events')", (sid,))
    d = client.get("/api/sports/group", params={"name": "PPV Events"}).json()
    f = d["feeds"][0]
    for key in ("key", "name", "group"):       # MainScene.brs plays /api/feed/stream?key=<key>
        assert key in f, key


def test_search_has_channels_and_programs(client):
    d = client.get("/api/search", params={"q": "news"}).json()
    assert "channels" in d and "programs" in d


def test_vod_counts(client):
    # OnDemandView.brs: enabled, and total / list / continue for each kind
    c = client.get("/api/vod").json()
    assert "enabled" in c
    for kind in ("movie", "series"):
        for key in ("total", "list", "continue"):
            assert key in c[kind], f"{kind}.{key}"
