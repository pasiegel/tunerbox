"""The Roku app (separate repo, tunerbox-roku) is a second front end on this API. These checks fail
when a change here would break it: a route it calls is renamed or removed, or a field it reads is
dropped. If one fails on purpose, update the Roku app in the same change (and this list with it).
The endpoints are also listed in ideas/IMPROVEMENT-IDEAS.md under "Roku app"."""
import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

from tunerbox import api, db, gamestats, home, scoreboard, sports

# (method, path) exactly as the Roku calls them; {x} marks a value the Roku fills in
ROKU_ROUTES = [
    ("GET", "/playlist.m3u"),
    ("GET", "/epg.xml"),
    ("GET", "/guide.json"),
    ("GET", "/channels.json"),
    ("GET", "/api/stream/{number}"),
    ("GET", "/api/channels/{number}/feeds"),
    ("GET", "/api/feed/stream"),
    ("GET", "/api/favorites"),
    ("POST", "/api/favorites"),
    ("GET", "/api/settings"),
    ("GET", "/api/search"),
    ("POST", "/api/recordings"),
    ("GET", "/api/sports"),
    ("GET", "/api/sports/group"),
    ("GET", "/api/sports/check"),
    ("GET", "/api/sports/stats"),
    ("POST", "/api/mv/plan"),           # multiview (richakev's fork only: the Roku checks /api/mv/status first)
    ("GET", "/api/mv/stream"),
    ("GET", "/api/mv/status"),
    ("POST", "/api/mv/sound"),
    ("GET", "/api/mv/room"),
    ("POST", "/api/stop"),             # leaving multiview frees every picture first
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
    ("POST", "/api/vod/forget"),
    ("GET", "/api/vod/tracks/{item_id}"),
    ("GET", "/api/vod/subs/{item_id}/{sub_id}"),
    # catch-up TV (MainScene.brs: Start over, past shows in the Guide)
    ("GET", "/api/catchup"),
    ("GET", "/api/guide"),
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


def test_guide_json_rows(client, monkeypatch):
    # LoadTask.brs loadGuide / jsonProgs: {"programs": {"<number>": [[start, stop, title, subtitle, desc, cat, ep, rating]]}}
    from tunerbox import epg, guide
    monkeypatch.setattr(guide, "channels", lambda *a: [{"number": 5, "epg": "x:a"}, {"number": 6, "epg": ""}])
    p = {"start": 100, "stop": 200, "title": "News", "subtitle": None, "descr": "d", "category": "News", "episode": "",
         "rating": "TV-G"}
    monkeypatch.setattr(epg, "programs", lambda keys, a, b: {"x:a": [p]})
    r = client.get("/guide.json?hours=12", headers={"Accept-Encoding": "identity"})
    assert r.json() == {"programs": {"5": [[100, 200, "News", "", "d", "News", "", "TV-G"]]}}     # no listings: left out
    z = client.get("/guide.json", headers={"Accept-Encoding": "gzip"})
    assert z.headers.get("content-encoding") == "gzip" and z.json() == r.json()       # the Roku asks for gzip
    # desc=0 (the Roku): the same rows with the description column empty
    n = client.get("/guide.json?hours=12&desc=0", headers={"Accept-Encoding": "identity"})
    assert n.json() == {"programs": {"5": [[100, 200, "News", "", "", "News", "", "TV-G"]]}}


def test_channels_json_rows(client, monkeypatch):
    # LoadTask.brs loadBoot / rowsToChannels: {"channels": [[number, name, logo, group, url, tvg-id, tvg-name,
    # backups]]} (backups: feeds after the main one, for failover and Switch feed),
    # by number; unmapped=0 leaves out the extra channels (Roku: Hide unmapped channels)
    from tunerbox import guide
    monkeypatch.setattr(guide, "channels", lambda *a: [
        {"number": 20001, "name": "Extra", "logo": "", "kind": "extra", "group": "Sports"},
        {"number": 7, "name": "Seven", "logo": "l.png", "kind": "lineup", "streams": ["a", "b", "c"]},
        {"number": 3, "name": "Three", "logo": None, "kind": "media", "group": "My Channels"}])
    rows = client.get("/channels.json?unmapped=0").json()["channels"]
    assert [r[0] for r in rows] == ["3", "7"]
    assert rows[1][:4] == ["7", "Seven", "l.png", "Local Lineup"] and rows[1][4].endswith("/api/stream/7")
    assert rows[1][5:] == ["ch7", "Seven", 2] and rows[0][7] == 0
    assert [r[0] for r in client.get("/channels.json?unmapped=1").json()["channels"]] == ["3", "7", "20001"]


def test_favorites_is_a_list(client):
    # the Roku keeps the reply only when it's a JSON array (LoadTask.brs loadFavs / setFav)
    assert isinstance(client.get("/api/favorites").json(), list)


def test_settings_has_sports_show_unmatched(client):
    s = client.get("/api/settings").json()
    assert isinstance(s.get("sports_show_unmatched"), bool)


def test_settings_has_home_screen(client):
    # the Roku shows Home only when this is true (MainScene.brs applySettings)
    assert isinstance(client.get("/api/settings").json().get("home_screen"), bool)


def test_settings_has_theme(client):
    # the Roku's default colour theme (MainScene.brs applySettings; "light" means Midnight there, as on TV screens)
    assert client.get("/api/settings").json().get("theme") in ("midnight", "classic", "retro", "contrast", "light")


# the row ids HomeView.brs rowIds() / makeRow() draws; a row it doesn't know is left off the Roku's Home
ROKU_HOME_ROWS = {"fav", "teams", "cont", "contrec", "libnew", "sports", "recent", "recorded", "coming"}


def test_home_lists(client):
    d = client.get("/api/home").json()
    for key in ("favorites", "teams", "recent", "continue", "library_new", "sports", "recorded", "coming"):   # HomeView.brs makeRow
        assert isinstance(d.get(key), list), key
    assert isinstance(d.get("rows"), list) and all(isinstance(r, str) for r in d["rows"])     # the order HomeView.brs draws


def test_catchup_is_days_by_channel(client, monkeypatch):
    # MainScene.brs cuDaysOf: { "<channel number>": days }, only channels with an archive
    from tunerbox import guide
    monkeypatch.setattr(guide, "channels", lambda *a: [{"number": 5, "catchup": 3}, {"number": 7, "catchup": 0}])
    assert client.get("/api/catchup").json() == {"5": 3}


def test_vod_tracks_shape(client):
    # MainScene.brs vodTracks: audio and subs lists (empty for a playlist title or a catch-up show)
    d = client.get("/api/vod/tracks/cu-5-1700000000").json()
    assert isinstance(d.get("audio"), list) and isinstance(d.get("subs"), list)


def test_home_rows_known_to_roku():
    assert set(home.ROWS) == ROKU_HOME_ROWS, "a Home row was added or renamed; add it to HomeView.brs makeRow too"


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


def test_sports_stats_fields(client, monkeypatch):
    # StatsView.brs reads these: the game (header, team tabs, Team stats, Plays) and the other live games
    from test_gamestats import _ev, _nba
    monkeypatch.setattr(gamestats, "game_for", lambda ch="", feed="": "nba:1")
    monkeypatch.setattr(gamestats, "summary", lambda gid: {**gamestats.parse("nba", _nba()), "id": gid})
    gamestats._lookup["t"] = 0
    feed = {"key": "f9", "name": "NBA 03", "group": "NBA League Pass", "status": "ok", "slot": False}
    monkeypatch.setattr(sports, "overview", lambda tab="": {"on_now": [
        {"event": _ev("nba:2"), "ch": {"number": 206, "name": "ESPN"}, "also": [{"number": 207, "name": "ESPN2"}], "feeds": [feed]}]})
    monkeypatch.setattr(gamestats, "_live_scores", lambda lg, now: {})
    monkeypatch.setattr(scoreboard, "selected", lambda: ["nba"])
    d = client.get("/api/sports/stats", params={"ch": "5"}).json()
    assert d["enabled"] is True                  # false: StatsView says to turn sports on in Settings
    gamestats._lookup["t"] = 0
    g = d["game"]
    for key in ("id", "league", "state", "detail", "teams", "players", "team_stats", "plays"):
        assert key in g, key
    for key in ("name", "short", "abbr", "color", "logo", "score", "record"):   # StatsView drawTeams: one-line scoreline
        assert key in g["teams"][0], key
    tb = g["players"][0]["tables"][0]
    assert {"abbr"} <= g["players"][0].keys() and {"title", "cols", "rows"} <= tb.keys()
    assert {"name", "num", "stats"} <= tb["rows"][0].keys()
    assert {"label", "values"} <= g["team_stats"][0].keys() and {"text", "when", "score"} <= g["plays"][0].keys()
    o = d["others"][0]
    for key in ("id", "name", "league", "detail", "teams", "ch", "also", "feeds"):
        assert key in o, key
    assert {"abbr", "color", "logo", "score"} <= o["teams"][0].keys()   # StatsView drawGame: the tile's logos
    assert {"number", "name"} <= o["ch"].keys() and {"key", "name", "group", "status", "slot"} <= o["feeds"][0].keys()


@pytest.mark.parametrize("path", ["/api/stream/{number}", "/api/feed/stream", "/api/vod/stream/{item_id}"])
def test_stream_takes_roku_client_and_name(path):
    # the Roku adds ?client=roku-<id>&name=<its name> (MainScene.brs withDevice / playVodAt) so
    # Setup › Tuners can say which Roku is watching
    route = next(r for r in api.app.routes if isinstance(r, Route) and r.path == path)
    params = {p.name for p in route.dependant.query_params}
    assert {"client", "name"} <= params
