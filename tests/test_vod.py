"""On Demand: reading movies and shows from a playlist, browsing them, and remembering what was watched."""
import time

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, config, db, jobs, sources, streaming, vod

NOW = int(time.time())
XTREAM = {
    "get_vod_categories": [{"category_id": "1", "category_name": "Mystery"}, {"category_id": "2", "category_name": "Adult"}],
    "get_vod_streams": [
        {"stream_id": 10, "name": "Charade (1963)", "category_id": "1", "container_extension": "mkv",
         "stream_icon": "http://img/charade.jpg", "added": str(NOW - 3600), "rating": "7.9"},
        {"stream_id": 11, "name": "The 39 Steps", "category_id": "1", "container_extension": "mp4", "added": "1500000000"},
        {"stream_id": 12, "name": "Nope", "category_id": "2"},
    ],
    "get_series_categories": [{"category_id": "5", "category_name": "Crime"}],
    "get_series": [{"series_id": 7, "name": "Dragnet", "category_id": "5", "cover": "http://img/d.jpg", "releaseDate": "1951-12-16"}],
    "get_vod_info": {"info": {"plot": "A woman in Paris.", "cast": "Cary Grant", "duration_secs": 6780, "genre": "Mystery"},
                     "movie_data": {"stream_id": 10, "container_extension": "mkv"}},
    "get_series_info": {"info": {"plot": "Just the facts."}, "episodes": {
        "1": [{"id": "101", "episode_num": 1, "season": 1, "title": "Dragnet - S01E01 - The Human Bomb", "container_extension": "mp4",
               "info": {"duration_secs": 1500, "plot": "A bomb."}},
              {"id": "102", "episode_num": 2, "season": 1, "title": "The Big Actor", "container_extension": "mp4", "info": []}],
        "2": [{"id": "201", "episode_num": 1, "season": 2, "title": "The Big Seventeen", "container_extension": "mkv",
               "info": {"duration": "00:26:00"}}]}},
}


@pytest.fixture
def xtream(monkeypatch):
    calls = []

    def fake(src, action=None, **params):
        calls.append(action)
        return XTREAM[action]
    monkeypatch.setattr(sources, "xtream_api", fake)
    sid = sources.add_source("My Provider", "xtream", "http://host:8080", "user", "pass")
    db.run("UPDATE sources SET vod_movies=1, vod_series=1 WHERE id=?", (sid,))
    vod.refresh_xtream(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    return sid, calls


@pytest.fixture
def client():
    return TestClient(api.app)


def test_xtream_titles_and_categories(xtream):
    sid, _ = xtream
    assert vod.counts() == {"enabled": True, "tags": 1, "movie": {"total": 3, "continue": 0, "list": 0},
                            "series": {"total": 1, "continue": 0, "list": 0}}
    cats = vod.categories("movie")
    assert cats["total"] == 3 and [c["name"] for c in cats["cats"]] == ["Adult", "Mystery"]
    tiles = vod.titles("movie", "Mystery")["items"]
    assert [(t["name"], t["year"], t["new"]) for t in tiles] == [("Charade", "1963", True), ("The 39 Steps", "", False)]
    assert vod.titles("movie", "*recent")["items"][0]["name"] == "Charade"
    assert vod.titles("series")["items"][0]["year"] == "1951"


def test_only_what_is_turned_on_is_read(monkeypatch):
    monkeypatch.setattr(sources, "xtream_api", lambda src, action=None, **p: XTREAM[action])
    sid = sources.add_source("P", "xtream", "http://host", "u", "p")
    assert not vod.enabled()
    db.run("UPDATE sources SET vod_series=1 WHERE id=?", (sid,))
    vod.refresh_all()
    assert vod.enabled() and vod.counts()["movie"]["total"] == 0 and vod.counts()["series"]["total"] == 1


def test_details_are_fetched_once_when_a_title_is_opened(xtream):
    sid, calls = xtream
    t = vod.title(f"{sid}:m10")
    assert t["plot"] == "A woman in Paris." and t["duration"] == 6780 and t["source_name"] == "My Provider"
    vod.title(f"{sid}:m10")
    assert calls.count("get_vod_info") == 1


def test_show_episodes_and_next_up(xtream):
    sid, _ = xtream
    show = vod.title(f"{sid}:s7")
    assert [(s["season"], s["count"]) for s in show["seasons"]] == [(1, 2), (2, 1)]
    assert [e["name"] for e in show["episodes"]] == ["The Human Bomb", "The Big Actor", "The Big Seventeen"]
    assert show["episodes"][2]["duration"] == 1560 and show["next"] == f"{sid}:e101"
    vod.set_progress(f"{sid}:e101", 1490, 1500)             # watched to the end
    assert vod.title(f"{sid}:s7")["next"] == f"{sid}:e102"
    vod.set_progress(f"{sid}:e102", 300, 1500)
    show = vod.title(f"{sid}:s7")
    assert show["next"] == f"{sid}:e102" and show["seasons"][0]["watched"] == 1 and show["episodes"][1]["position"] == 300


def test_progress_continue_watching_and_my_list(xtream):
    sid, _ = xtream
    movie, ep = f"{sid}:m10", f"{sid}:e201"
    vod.title(f"{sid}:s7")
    assert vod.set_progress("nope", 10) is None
    vod.set_progress(movie, 20, 6780)                       # barely started: not in Continue Watching
    assert vod.titles(tab="continue")["items"] == []
    vod.set_progress(movie, 2538, 6780)
    vod.set_progress(ep, 600, 1560)
    cont = vod.titles(tab="continue")["items"]              # movies and shows together
    assert {t["id"] for t in cont} == {f"{sid}:s7", movie}
    assert vod.counts()["movie"]["continue"] == 1 and vod.counts()["series"]["continue"] == 1
    show = next(t for t in cont if t["kind"] == "series")
    assert show["resume"] == ep and show["sub"] == "S2 E1 · The Big Seventeen" and show["pct"] == 38
    assert next(t for t in vod.titles("movie", "Mystery")["items"] if t["id"] == movie)["pct"] == 37
    vod.forget(movie)                                       # off Continue Watching, not marked watched
    assert [t["id"] for t in vod.titles(tab="continue")["items"]] == [f"{sid}:s7"]
    assert vod.item(movie)["position"] == 0 and not db.one("SELECT 1 FROM vod_progress WHERE item_id=?", (movie,))
    vod.set_progress(movie, 2538, 6780)
    vod.set_watched(movie)
    assert [t["id"] for t in vod.titles(tab="continue")["items"]] == [f"{sid}:s7"]
    assert vod.item(movie)["position"] == 0                 # watched: plays from the start again
    vod.set_watched(f"{sid}:e101")
    vod.forget(f"{sid}:s7")                                 # a show: every episode part-way through, watched ones kept
    assert vod.titles(tab="continue")["items"] == []
    assert db.one("SELECT watched FROM vod_progress WHERE item_id=?", (f"{sid}:e101",))["watched"] == 1
    vod.set_watched(f"{sid}:s7")                            # a whole show
    assert all(e["watched"] for e in vod.title(f"{sid}:s7")["episodes"])
    assert vod.set_listed(movie)["in_list"] and vod.set_listed("nope") is None
    assert [t["id"] for t in vod.titles(tab="list")["items"]] == [movie]
    assert vod.counts()["movie"]["list"] == 1 and vod.counts()["series"]["list"] == 0
    vod.set_listed(movie, False)
    assert vod.titles(tab="list")["total"] == 0


def test_a_finished_episode_leaves_the_next_one_up_next(xtream):
    sid, _ = xtream
    show, e1, e2 = f"{sid}:s7", f"{sid}:e101", f"{sid}:e102"
    vod.title(show)
    db.run("UPDATE vod_progress SET updated=0")
    vod.set_progress(e1, 1490, 1500)                        # watched to the end
    [t] = vod.titles(tab="continue")["items"]
    assert (t["id"], t["resume"], t["upnext"], t["pct"]) == (show, e2, True, 0)
    assert t["sub"].startswith("Up next · S1 E2") and t["ep"].startswith("S1 E2")
    db.run("UPDATE vod_progress SET updated=1")             # forgotten after it was watched
    vod.forget(show)
    assert vod.titles(tab="continue")["items"] == []        # stays away...
    db.run("UPDATE vod_dismissed SET at=1")
    vod.set_progress(e2, 600, 1500)                         # ...until another episode is played
    [t] = vod.titles(tab="continue")["items"]
    assert (t["resume"], t["upnext"]) == (e2, False)
    vod.set_watched(show)                                   # the whole show: caught up, nothing next
    assert vod.titles(tab="continue")["items"] == []
    movie = f"{sid}:m10"
    vod.set_watched(movie)                                  # a finished film has no Up next
    assert vod.titles(tab="continue")["items"] == []


def test_play_address_and_command(xtream, monkeypatch):
    sid, _ = xtream
    monkeypatch.setattr(streaming, "has_option", lambda name: True)        # the same on any ffmpeg
    it = vod.item(f"{sid}:m10")
    assert it["url"] == "http://host:8080/movie/user/pass/10.mkv" and it["kind"] == "movie"
    vod.title(f"{sid}:s7")
    ep = vod.item(f"{sid}:e101")
    assert ep["url"] == "http://host:8080/series/user/pass/101.mp4" and ep["title"] == "Dragnet"
    assert ep["sub"] == "S1 E1 · The Human Bomb" and ep["next"] == {"id": f"{sid}:e102", "label": "S1 E2 · The Big Actor"}
    assert vod.item(f"{sid}:s7") is None                    # a show itself can't be played
    cmd = vod.stream_cmd(it["url"], 42.5)
    assert cmd.index("-readrate") < cmd.index("-ss") < cmd.index("-i") and cmd[cmd.index("-ss") + 1] == "42.50"
    assert "-ss" not in vod.stream_cmd(it["url"])


def test_on_demand_starts_with_a_burst_then_plays_at_speed(monkeypatch):
    monkeypatch.setattr(streaming, "has_option", lambda name: True)
    burst = ["-readrate", "1", "-readrate_initial_burst", "10"]
    cmd = vod.stream_cmd("http://p/x.mkv", 42.5)
    i = cmd.index("-readrate")
    assert cmd[i:i + 4] == burst and "-re" not in cmd and i < cmd.index("-ss") < cmd.index("-i")
    assert vod.hls_pre(42.5) == burst + ["-ss", "42.50"]                   # iPhone/iPad: the same
    assert vod.hls_pre(0) == burst
    assert vod.hls_pre(5, ["-re"]) == ["-re", "-ss", "5.00"]                # a caller's own pace wins
    monkeypatch.setattr(streaming, "has_option", lambda name: False)       # ffmpeg before 6.1
    cmd = vod.stream_cmd("http://p/x.mkv", 42.5)
    assert "-readrate" not in cmd and cmd.index("-re") < cmd.index("-ss") < cmd.index("-i")
    assert vod.hls_pre(42.5) == ["-re", "-ss", "42.50"]


def test_hidden_categories(xtream):
    sid, _ = xtream
    assert [g["name"] for g in vod.cats(sid)["groups"]] == ["Adult", "Mystery"]       # movies
    assert [g["name"] for g in vod.cats(sid, "series")["groups"]] == ["Crime"]
    r = vod.set_hidden(sid, ["Adult", "no such"])
    assert r == {"hidden": 1, "newly_hidden": 1, "shown_again": 0, "missing": 0} and vod.counts()["movie"]["total"] == 2
    vod.refresh_xtream(db.one("SELECT * FROM sources WHERE id=?", (sid,)))      # stays out after a scan
    assert vod.counts()["movie"]["total"] == 2
    stats = vod.source_stats(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    assert stats["cats"] == {"movie": [1, 2], "series": [1, 1]} and stats["movies"] == 2
    assert vod.cats(sid)["groups"][0]["hidden"]
    vod.set_hidden(sid, ["Crime"], "series")                       # the TV show list doesn't touch the movie one
    assert vod.cats(sid)["groups"][0]["hidden"] and vod.cats(sid, "series")["groups"][0]["hidden"]
    r = vod.set_hidden(sid, [])
    assert r["shown_again"] == 1 and r["missing"] == 0           # kept, only marked: back at once, nothing to read
    assert vod.counts()["movie"]["total"] == 3
    assert vod.cats(sid, "series")["groups"][0]["hidden"]


def test_hidden_categories_the_provider_stops_listing_stay_hidden(xtream):
    sid, _ = xtream
    vod.set_hidden(sid, ["Adult"])
    db.run("DELETE FROM vod_cats WHERE name='Adult'")              # not listed right now
    assert vod.set_hidden(sid, [])["shown_again"] == 0            # saving the rest doesn't un-hide it
    assert vod.hidden_cats(db.one("SELECT * FROM sources WHERE id=?", (sid,))) == {("movie", "Adult")}


def test_showing_a_category_that_was_never_read_reads_it(xtream, client, monkeypatch):
    sid, _ = xtream
    vod.set_hidden(sid, ["Adult"])
    db.run("DELETE FROM vod_titles WHERE category='Adult'")       # hidden by an older version: titles never kept
    started = []
    monkeypatch.setattr(jobs, "vod_scan", lambda source_id=None, kinds=vod.KINDS: started.append((source_id, kinds)) or True)
    r = client.put(f"/api/sources/{sid}/vod-cats?kind=movie", json={"hidden": []}).json()
    assert r["job"] == "vod" and started == [(sid, ("movie",))] and "missing" not in r


def test_search(xtream):
    sid, _ = xtream
    vod.title(f"{sid}:s7")
    assert vod.search("c") == [] and vod.search("39") == []         # too short / a channel number
    assert [(r["kind"], r["name"]) for r in vod.search("char")] == [("movie", "Charade")]
    got = vod.search("big")
    assert [(r["kind"], r["sub"]) for r in got] == [("episode", "S1 E2 · The Big Actor"), ("episode", "S2 E1 · The Big Seventeen")]
    assert got[0]["title_id"] == f"{sid}:s7"


# ---------------------------------------------------------------- plain M3U playlists
M3U = [
    {"name": "CNN", "url": "http://s/cnn.ts", "tvg_id": "", "logo": "", "grp": "News"},
    {"name": "Charade (1963)", "url": "http://s/movies/charade.mkv", "tvg_id": "", "logo": "http://img/c.jpg", "grp": "Movies"},
    {"name": "Dragnet S02 E04 The Big Lamp", "url": "http://s/x/d204.mp4?token=1", "tvg_id": "", "logo": "", "grp": "Shows"},
    {"name": "Dragnet S02E05", "url": "http://s/x/d205.mp4", "tvg_id": "", "logo": "", "grp": "Shows"},
    {"name": "Secret", "url": "http://s/x/secret.mp4", "tvg_id": "", "logo": "", "grp": "Adult"},
]


def _m3u(monkeypatch, **cols):
    monkeypatch.setattr(sources, "fetch_m3u", lambda src: ([dict(e) for e in M3U], ""))
    sid = sources.add_source("List", "m3u", "http://s/list.m3u")
    for k, v in cols.items():
        db.run(f"UPDATE sources SET {k}=? WHERE id=?", (v, sid))
    sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    return sid


def test_m3u_video_files_stay_channels_until_turned_on(monkeypatch):
    sid = _m3u(monkeypatch)
    assert db.one("SELECT count(*) n FROM streams")["n"] == 5 and vod.counts()["movie"]["total"] == 0
    assert vod.source_stats(db.one("SELECT * FROM sources WHERE id=?", (sid,)))["files"] == 4


def test_m3u_video_files_become_titles(monkeypatch):
    sid = _m3u(monkeypatch, vod_movies=1, hidden_groups='["Adult"]')
    assert [r["name"] for r in db.rows("SELECT name FROM streams")] == ["CNN"]
    assert [(t["name"], t["year"]) for t in vod.titles("movie")["items"]] == [("Charade", "1963")]     # the hidden group stays out
    show = vod.titles("series")["items"][0]
    eps = vod.title(show["id"])["episodes"]
    assert show["name"] == "Dragnet" and [(e["season"], e["episode"], e["name"]) for e in eps] == [(2, 4, "The Big Lamp"), (2, 5, "")]
    assert vod.item(eps[0]["id"])["url"] == "http://s/x/d204.mp4?token=1"
    # the same ids after the next read of the playlist, so progress survives
    vod.set_progress(eps[0]["id"], 400, 1500)
    sources.refresh_source(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    assert vod.title(show["id"])["episodes"][0]["position"] == 400


# ---------------------------------------------------------------- HTTP
def test_api_never_sends_the_provider_login(xtream, client, monkeypatch):
    sid, _ = xtream
    monkeypatch.setattr(vod, "probe_duration", lambda url: 5400)
    r = client.get(f"/api/vod/play/{sid}:m11")
    assert r.status_code == 200 and r.json()["duration"] == 5400 and "url" not in r.json() and "pass" not in r.text
    assert vod.item(f"{sid}:m11")["duration"] == 5400       # measured once, remembered
    assert client.get("/api/vod/play/nope").status_code == 404
    assert "pass" not in client.get(f"/api/vod/title/{sid}:m10").text
    assert [v["name"] for v in client.get("/api/search?q=charade").json()["vod"]] == ["Charade"]
    src = client.get("/api/sources").json()[0]
    assert src["vod"]["movies"] == 3 and "vod_hidden" not in src


def test_api_turning_on_demand_off_and_on(xtream, client, monkeypatch):
    sid, _ = xtream
    started = []
    monkeypatch.setattr(jobs, "vod_scan", lambda source_id=None, kinds=vod.KINDS: started.append((source_id, kinds)) or True)
    r = client.patch(f"/api/sources/{sid}", json={"vod_movies": 0})
    assert r.json()["job"] == "" and vod.counts()["movie"]["total"] == 0 and vod.counts()["series"]["total"] == 1
    assert client.patch(f"/api/sources/{sid}", json={"vod_movies": 0}).json()["job"] == "" and not started     # no change
    assert client.patch(f"/api/sources/{sid}", json={"vod_movies": 1}).json()["job"] == "vod"
    assert started == [(sid, ("movie",))]                          # only the movie list is read again
    client.delete(f"/api/sources/{sid}")
    assert vod.counts()["series"]["total"] == 0


# ---------------------------------------------------------------- search
def _titles(monkeypatch, movies, series=()):
    api_data = {**XTREAM,
                "get_vod_categories": [{"category_id": str(i), "category_name": c} for i, c in enumerate({c for _, c in movies})],
                "get_series_categories": [{"category_id": str(i), "category_name": c} for i, c in enumerate({c for _, c in series})]}
    cat_id = {d["category_name"]: d["category_id"] for d in api_data["get_vod_categories"]}
    ser_id = {d["category_name"]: d["category_id"] for d in api_data["get_series_categories"]}
    api_data["get_vod_streams"] = [{"stream_id": i, "name": n, "category_id": cat_id[c], "added": str(NOW - i)}
                                   for i, (n, c) in enumerate(movies)]
    api_data["get_series"] = [{"series_id": i, "name": n, "category_id": ser_id[c]} for i, (n, c) in enumerate(series)]
    monkeypatch.setattr(sources, "xtream_api", lambda src, action=None, **p: api_data[action])
    sid = sources.add_source("P", "xtream", "http://h", "u", "p")
    db.run("UPDATE sources SET vod_movies=1, vod_series=1 WHERE id=?", (sid,))
    vod.refresh_xtream(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    return sid


MOVIES = [("EN - Charade (1963)", "EN - MYSTERY"), ("EN - The French Connection", "EN - ACTION"),
          ("FR - Le Samouraï", "FR - POLICIER"), ("FR - Du rififi chez les hommes", "FR - POLICIER"),
          ("AMZ - Amélie", "NETFLIX MOVIES"), ("DE - Das Boot", "DE - FILME"), ("Spider-Man: Far from Home", "NETFLIX MOVIES"),
          ("قلب الأسد", "أفلام عربية")]


def test_search_words_accents_typos_and_years(monkeypatch):
    _titles(monkeypatch, MOVIES, [("Dragnet (1951)", "ENGLISH SERIES")])
    names = lambda q, **kw: [r["name"] for r in vod.search(q, **kw)]     # noqa: E731
    assert names("amelie") == ["AMZ - Amélie"]                            # accents don't matter
    assert names("home spider") == ["Spider-Man: Far from Home"]          # words in any order
    assert names("charade 1963") == ["EN - Charade"] and names("charade 1990") == []
    assert names("samourai") == ["FR - Le Samouraï"] and names("samurai")[:1] == ["FR - Le Samouraï"]     # a typo
    assert names("dragnet") == ["Dragnet"]                                # shows too


def test_search_typos_need_every_word(monkeypatch):
    _titles(monkeypatch, [("Hotel Transylvania", "KIDS"), ("Hotel Transylvania 2", "KIDS"), ("Hotel Rwanda", "DRAMA"),
                          ("Hotel Artemis", "ACTION"), ("Hostel", "HORROR"), ("Hostel: Part II", "HORROR")])
    names = lambda q: sorted(r["name"] for r in vod.search(q, limit=100))    # noqa: E731
    hotel_t = ["Hotel Transylvania", "Hotel Transylvania 2"]
    assert names("hotel trans") == hotel_t
    for q in ("hotels trans", "Hotels Trans", "hotels t", "hotels tr", "hotels tran"):    # while still typing too
        assert names(q) == hotel_t, q
    assert names("hotel transylvnia") == ["Hotel Transylvania", "Hotel Transylvania 2"]     # a typo still finds it
    assert "Hotel Rwanda" in names("hotel rwnda") and "Hostel" not in names("hotel rwnda")


def test_api_search_and_kinds(monkeypatch, client):
    _titles(monkeypatch, MOVIES)
    assert [r["name"] for r in client.get("/api/vod/search?q=boot").json()] == ["DE - Das Boot"]
    assert client.get("/api/vod/categories?kind=nope").status_code == 400
    assert client.get("/api/vod/titles?kind=nope").status_code == 400


def test_continue_and_list_cover_both_kinds(xtream, client):
    sid, _ = xtream
    vod.title(f"{sid}:s7")
    vod.set_progress(f"{sid}:m10", 600, 6780)
    vod.set_progress(f"{sid}:e101", 300, 1500)
    vod.set_listed(f"{sid}:m10")
    vod.set_listed(f"{sid}:s7")
    assert {t["kind"] for t in client.get("/api/vod/titles?tab=continue").json()["items"]} == {"movie", "series"}
    assert client.get("/api/vod/titles?tab=list").json()["total"] == 2
    assert client.get("/api/vod/titles").json()["total"] == 3          # no tab: movies


def test_search_lists_the_preferred_language_first(monkeypatch):
    _titles(monkeypatch, [("AL - Dune", "AL - FILMA"), ("ES - Dune", "ES - PELÍCULAS"), ("Dune: Part Two", "NETFLIX MOVIES"),
                          ("EN - Dune", "EN - ACTION"), ("DE - Dune", "DE - FILME"), ("4K-EN - Dune", "EN - MOVIES 4K")])
    names = [r["name"] for r in vod.search("dune")]
    assert names[:2] == ["EN - Dune", "4K-EN - Dune"]                  # the tag doesn't count against the match
    assert names.index("Dune: Part Two") > names.index("DE - Dune")     # an exact name beats a longer one
    config.update({"vod_search_first": "DE,ES"})
    assert [r["name"] for r in vod.search("dune")][:2] == ["ES - Dune", "DE - Dune"]


def test_adult_titles_left_out_unless_switched_on(monkeypatch):
    data = {**XTREAM, "get_vod_streams": XTREAM["get_vod_streams"] + [{"stream_id": 99, "name": "Adult film", "category_id": "1",
                                                                       "is_adult": "1"}],
            "get_series": XTREAM["get_series"] + [{"series_id": 98, "name": "Adult show", "category_id": "5", "is_adult": 1}]}
    monkeypatch.setattr(sources, "xtream_api", lambda src, action=None, **p: data[action])
    sid = sources.add_source("P", "xtream", "http://h", "u", "p")
    db.run("UPDATE sources SET vod_movies=1, vod_series=1 WHERE id=?", (sid,))
    vod.refresh_xtream(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    assert vod.search("adult") == [] and vod.counts()["movie"]["total"] == 3
    config.update({"vod_show_adult": True})
    vod.refresh_xtream(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    assert {r["name"] for r in vod.search("adult")} == {"Adult film", "Adult show"}


def test_switching_adult_titles_rereads_on_demand(client, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "vod_scan", lambda source_id=None: started.append(1) or True)
    assert client.post("/api/settings", json={"vod_search_first": "EN,FR"}).status_code == 200 and not started
    assert client.post("/api/settings", json={"vod_show_adult": True}).status_code == 200 and started == [1]


# ---------------------------------------------------------------- small posters for TVs
def _big_jpeg():
    import shutil
    import subprocess
    from tunerbox.streaming import ffmpeg_bin
    if not shutil.which(ffmpeg_bin()):
        pytest.skip("ffmpeg not installed")
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=1000x1500",
                        "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "pipe:1"], capture_output=True, check=True)
    return r.stdout


def _title(poster):
    sid = sources.add_source("P", "m3u", "http://host/list.m3u")
    db.run("INSERT INTO vod_titles(id, source_id, kind, name, poster) VALUES ('t1', ?, 'movie', 'Charade', ?)", (sid, poster))


def test_poster_is_shrunk_once_then_served_from_disk(monkeypatch):
    big = _big_jpeg()
    fetched = []
    monkeypatch.setattr(vod, "_download", lambda url: fetched.append(url) or big)
    _title("http://img/charade.jpg")
    client = TestClient(api.app)
    r = client.get("/api/vod/poster/t1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8" and len(r.content) < len(big)
    assert client.get("/api/vod/poster/t1").content == r.content
    assert fetched == ["http://img/charade.jpg"]           # the second time came from disk


def test_poster_falls_back_to_the_original_address(monkeypatch):
    monkeypatch.setattr(vod, "_download", lambda url: b"not an image")
    _title("http://img/broken.jpg")
    r = TestClient(api.app).get("/api/vod/poster/t1", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "http://img/broken.jpg"
    assert TestClient(api.app).get("/api/vod/poster/nope").status_code == 404


# ---------------------------------------------------------------- language / platform labels
LABELLED = {
    "get_vod_categories": [{"category_id": "1", "category_name": "EN - MOVIES"}, {"category_id": "2", "category_name": "DE - FILME"}],
    "get_vod_streams": (
        [{"stream_id": 100 + i, "name": f"EN - Film {i} (2020)", "category_id": "1"} for i in range(5)]
        + [{"stream_id": 200 + i, "name": f"DE - Film {i} (2020)", "category_id": "2"} for i in range(5)]
        + [{"stream_id": 300 + i, "name": f"4K-EN - Film {i} (2020)", "category_id": "1"} for i in range(5)]
        + [{"stream_id": 400, "name": "Dune (2021)", "category_id": "1"},
           {"stream_id": 401, "name": "Alien: Romulus (2024)", "category_id": "1"},
           {"stream_id": 402, "name": "X-Men - Days of Future Past (2014)", "category_id": "1"}]),
    "get_series_categories": [{"category_id": "5", "category_name": "NETFLIX SERIES"}],
    "get_series": [{"series_id": 70 + i, "name": f"NF - Show {i} (2023) (KR)", "category_id": "5"} for i in range(5)],
}


@pytest.fixture
def labelled(monkeypatch):
    monkeypatch.setattr(sources, "xtream_api", lambda src, action=None, **p: LABELLED[action])
    sid = sources.add_source("Big Provider", "xtream", "http://big:8080", "user", "pass")
    db.run("UPDATE sources SET vod_movies=1, vod_series=1 WHERE id=?", (sid,))
    vod.refresh_xtream(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    return sid


def test_labels_are_cut_off_the_names(labelled):
    rows = {r["id"]: r for r in db.rows("SELECT id, name, tag, year, country FROM vod_titles")}
    assert (rows["1:m100"]["name"], rows["1:m100"]["tag"], rows["1:m100"]["year"]) == ("Film 0", "EN", "2020")
    assert rows["1:m300"]["tag"] == "4K-EN"
    assert (rows["1:s70"]["name"], rows["1:s70"]["tag"], rows["1:s70"]["year"], rows["1:s70"]["country"]) == ("Show 0", "NF", "2023", "KR")
    # not labels: no label at all, a colon in a real name, and a "label" only one title has
    assert (rows["1:m400"]["name"], rows["1:m400"]["tag"]) == ("Dune", "")
    assert (rows["1:m401"]["name"], rows["1:m401"]["tag"]) == ("Alien: Romulus", "")
    assert (rows["1:m402"]["name"], rows["1:m402"]["tag"]) == ("X-Men - Days of Future Past", "")


def test_picking_labels_filters_everything_and_new_labels_start_hidden(labelled, client):
    g = client.get(f"/api/sources/{labelled}/vod-tags").json()["groups"]
    by = {x["name"]: x for x in g}
    assert by["EN"]["count"] == 5 and by["NF"]["series"] == 5 and by[vod.NO_TAG]["count"] == 3
    assert by["DE"]["example"] == "Film 0 (2020)" and not any(x["hidden"] for x in g)
    r = client.put(f"/api/sources/{labelled}/vod-tags", json={"hidden": ["DE", "4K-EN", "NF"]}).json()
    assert r == {"shown": 2, "hidden": 3}
    assert vod.counts()["movie"]["total"] == 8 and vod.counts()["series"]["total"] == 0      # EN 5 + 3 unlabelled
    assert {c["name"] for c in vod.categories("movie")["cats"]} == {"EN - MOVIES"}
    assert all(t["tag"] in ("EN", "") for t in vod.search("film"))
    src = db.one("SELECT * FROM sources WHERE id=?", (labelled,))
    assert vod.source_stats(src)["tags"] == [2, 5]
    # a language the provider adds later stays out until it's ticked
    db.run("INSERT INTO vod_titles(id, source_id, kind, name, tag, search) VALUES ('1:m999', ?, 'movie', 'Film X', 'FR', 'film x')",
           (labelled,))
    vod._changed()
    assert vod.counts()["movie"]["total"] == 8
    # nothing left out: back to everything
    client.put(f"/api/sources/{labelled}/vod-tags", json={"hidden": []})
    assert db.one("SELECT vod_tags FROM sources WHERE id=?", (labelled,))["vod_tags"] == ""
    assert vod.counts()["movie"]["total"] == 19


def test_search_lists_a_film_once_with_its_versions(labelled):
    hits = [h for h in vod.search("film 2") if h["name"] == "Film 2"]
    assert len(hits) == 1
    assert {v["tag"] for v in hits[0]["versions"]} == {"EN", "DE", "4K-EN"}
    t = vod.title(hits[0]["id"])
    assert len(t["versions"]) == 3 and sum(v["current"] for v in t["versions"]) == 1
    vod.set_tags(labelled, ["DE", "4K-EN"])
    hit = [h for h in vod.search("film 2") if h["name"] == "Film 2"][0]
    assert (hit["tag"], hit["versions"]) == ("EN", [])


def test_continue_watching_lists_a_film_once_as_the_version_played_last(labelled):
    ids = [v["id"] for v in vod.title([h for h in vod.search("film 2") if h["name"] == "Film 2"][0]["id"])["versions"]]
    a, b = ids[0], ids[1]
    vod.set_progress(a, 600, 6000)
    vod.set_progress(b, 900, 6000)
    db.run("UPDATE vod_progress SET updated=? WHERE item_id=?", (1000, a))
    db.run("UPDATE vod_progress SET updated=? WHERE item_id=?", (2000, b))
    cont = vod.titles(tab="continue")["items"]
    assert [(t["id"], t["resume"]) for t in cont] == [(b, b)]
    assert vod.counts()["movie"]["continue"] == 1
    db.run("UPDATE vod_progress SET updated=? WHERE item_id=?", (3000, a))
    assert [t["id"] for t in vod.titles(tab="continue")["items"]] == [a]


def test_continue_watching_can_show_every_version(labelled):
    ids = [v["id"] for v in vod.title([h for h in vod.search("film 2") if h["name"] == "Film 2"][0]["id"])["versions"]]
    a, b = ids[0], ids[1]
    vod.set_progress(a, 600, 6000)
    vod.set_progress(b, 900, 6000)
    db.run("UPDATE vod_progress SET updated=? WHERE item_id=?", (1000, a))
    db.run("UPDATE vod_progress SET updated=? WHERE item_id=?", (2000, b))
    config.update({"vod_continue_versions": True})
    assert [t["id"] for t in vod.titles(tab="continue")["items"]] == [b, a]      # both, the latest first
    config.update({"vod_continue_versions": False})
    assert [t["id"] for t in vod.titles(tab="continue")["items"]] == [b]


def test_preferred_language_leads_the_versions_and_hidden_categories_stay_out(labelled):
    config.update({"vod_search_first": "DE"})
    hit = [h for h in vod.search("film 2") if h["name"] == "Film 2"][0]
    assert hit["tag"] == "DE" and hit["versions"][0]["tag"] == "DE"
    vod.set_hidden(labelled, ["DE - FILME"])                          # category unticked: its version goes too
    hit = [h for h in vod.search("film 2") if h["name"] == "Film 2"][0]
    assert hit["tag"] != "DE" and "DE" not in {v["tag"] for v in hit["versions"]}


def test_your_own_copy_comes_first(labelled):
    """A film that's also in one of your libraries: Search, the title and Other versions lead with your copy,
    even ahead of a preferred language, and it's named by its library."""
    from tunerbox import library
    config.update({"vod_search_first": "DE"})
    db.run("INSERT INTO lib_shares(name, kind, type, server, share, path) VALUES ('35mm', 'movie', 'smb', 'nas', 'media', '35mm')")
    sid = db.one("SELECT max(id) n FROM lib_shares")["n"]
    hit = [h for h in vod.search("film 2") if h["name"] == "Film 2"][0]
    row = dict(db.one("SELECT * FROM vod_titles WHERE id=?", (hit["id"],)))
    row.update(id=f"L{sid}:mfilm2", source_id=-sid, tag="", country="")
    db.run(f"INSERT INTO vod_titles({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    library.init()
    vod._tag_rank.clear()
    hit = [h for h in vod.search("film 2") if h["name"] == "Film 2"][0]
    assert hit["id"] == f"L{sid}:mfilm2" and len(hit["versions"]) == 4
    vs = vod.title(f"L{sid}:mfilm2")["versions"]
    assert vs[0]["id"] == f"L{sid}:mfilm2" and vs[0]["where"] == "35mm" and vs[0]["current"]
    assert vs[1]["tag"] == "4K-EN"         # then the best picture, ahead of a preferred language (Pick a version only)
    assert hit["versions"][1]["tag"] == "DE"                         # Search still leads with the preferred language


# ---------------------------------------------------------------- Pick a version: bitrate, TMDB id, original title
def test_length_prefers_the_text_when_duration_secs_is_minutes():
    assert vod._secs({"duration_secs": "92", "duration": "01:32:00"}) == 5520     # a panel that put minutes there
    assert vod._secs({"duration_secs": 5492, "duration": "01:31:32"}) == 5492
    assert vod._secs({"duration_secs": 5492}) == 5492
    assert vod._secs({"duration": "00:01:30"}) == 90
    assert vod._secs({"episode_run_time": "45"}) == 2700


def _versions_api(monkeypatch, info_by_id, calls=None):
    def fake(src, action=None, **p):
        if action == "get_vod_info":
            if calls is not None:
                calls.append(p["vod_id"])
            return info_by_id.get(p["vod_id"], {})
        return LABELLED[action]
    monkeypatch.setattr(sources, "xtream_api", fake)


def test_pick_a_version_fetches_each_versions_bitrate_once(labelled, monkeypatch, client):
    calls = []
    _versions_api(monkeypatch, {
        "102": {"info": {"bitrate": "2596", "duration_secs": 5251, "tmdb_id": "585083", "o_name": "Film Deux"},
                "movie_data": {"container_extension": "mkv"}},
        "202": {"info": {"bitrate": 4103, "duration": "01:27:31"}, "movie_data": {}},
        "302": {"info": {"bitrate": 16436, "duration_secs": 5492}, "movie_data": {}},
    }, calls)
    vs = client.get("/api/vod/title/1:m102/versions").json()
    by = {v["id"]: v for v in vs}
    assert (by["1:m102"]["bitrate"], by["1:m202"]["bitrate"], by["1:m302"]["bitrate"]) == (2596, 4103, 16436)
    assert by["1:m302"]["duration"] == 5492 and by["1:m102"]["current"]
    assert sorted(calls) == ["102", "202", "302"]
    client.get("/api/vod/title/1:m102/versions")
    assert len(calls) == 3                                  # kept: not asked again
    t = db.one("SELECT tmdb, o_search FROM vod_titles WHERE id='1:m102'")
    assert (t["tmdb"], t["o_search"]) == ("585083", "film deux")
    assert client.get("/api/vod/title/1:m102").json()["bitrate"] == 2596
    assert client.get("/api/vod/title/nope/versions").status_code == 404


def test_an_impossible_bitrate_from_the_panel_isnt_kept(labelled, monkeypatch):
    _versions_api(monkeypatch, {"102": {"info": {"bitrate": 987060, "duration_secs": 5491}, "movie_data": {}}}, [])
    vod.check_versions("1:m102")
    assert db.one("SELECT bitrate FROM vod_titles WHERE id='1:m102'")["bitrate"] == 0


def test_an_empty_reply_is_tried_again(labelled, monkeypatch):
    calls = []
    _versions_api(monkeypatch, {}, calls)                   # the provider answers {} for everything
    vod.check_versions("1:m102")
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:m102'")["info_at"] == 0
    vod.check_versions("1:m102")
    assert calls.count("102") == 2


def test_tmdb_id_ties_a_differently_named_copy_and_the_original_title_is_searchable(labelled, monkeypatch):
    _versions_api(monkeypatch, {"102": {"info": {"tmdb_id": "585083", "o_name": "Le Film Deux"}, "movie_data": {}}})
    db.run("""INSERT INTO vod_titles(id, source_id, kind, name, tag, search, year, tmdb)
              VALUES ('1:m999', ?, 'movie', 'Film II', 'EN', 'film ii', '2020', '585083')""", (labelled,))
    vod._changed()
    vod.check_versions("1:m102")
    assert "1:m999" in {v["id"] for v in vod.title("1:m102")["versions"]}
    assert "1:m999" not in {v["id"] for v in vod.title("1:m101")["versions"]}
    assert any(h["id"] == "1:m102" or "1:m102" in {v["id"] for v in h["versions"]} for h in vod.search("le film deux"))
    # "0" is a panel's way of saying none: it doesn't tie every film together
    _versions_api(monkeypatch, {"101": {"info": {"tmdb_id": "0"}, "movie_data": {}}})
    vod.check_versions("1:m101")
    assert db.one("SELECT tmdb FROM vod_titles WHERE id='1:m101'")["tmdb"] == ""


def test_details_fetched_before_bitrate_existed_are_fetched_again_once(labelled, monkeypatch):
    db.run("UPDATE vod_titles SET info_at=? WHERE id IN ('1:m102', '1:m202')", (NOW,))
    db.run("UPDATE vod_titles SET bitrate=4103, tmdb='1' WHERE id='1:m202'")       # already has the new details
    db.run("DELETE FROM meta WHERE k='vod_details_v2'")                            # a database from before
    vod.init()
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:m102'")["info_at"] == 0
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:m202'")["info_at"] == NOW
    calls = []
    _versions_api(monkeypatch, {"102": {"info": {"bitrate": 2596}, "movie_data": {}}}, calls)
    vod.check_versions("1:m102")
    assert "102" in calls and "202" not in calls
    db.run("UPDATE vod_titles SET info_at=?, bitrate=0 WHERE id='1:m102'", (NOW,))   # a provider that sends no bitrate
    vod.init()                                                                     # not reset again on every start
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:m102'")["info_at"] == NOW


# ---------------------------------------------------------------- picture and sound, read while playing
PLAYING = """Input #0, matroska,webm, from 'http://host:8080/movie/user/pass/640714.mkv':
  Metadata:
    title           : Hotel Transylvania
  Duration: 01:31:32.00, start: 0.000000, bitrate: 15800 kb/s
  Stream #0:0: Video: hevc (Main 10), yuv420p10le(tv, bt2020nc/bt2020/smpte2084), 3840x2160 [SAR 1:1 DAR 16:9], 23.98 fps, 23.98 tbr
      Side data:
        DOVI configuration record: version: 1.0, profile: 8, level: 6
  Stream #0:1(eng): Audio: eac3, 48000 Hz, 5.1(side), fltp, 768 kb/s (default)
  Stream #0:2(spa): Audio: ac3, 48000 Hz, stereo, fltp, 192 kb/s
  Stream #0:3: Video: mjpeg (Baseline), yuvj420p, 600x900, 90k tbr (attached pic)
Stream mapping:
  Stream #0:0 -> #0:0 (copy)
Output #0, mpegts, to 'pipe:1':
  Stream #0:0: Video: hevc (Main 10), yuv420p10le, 3840x2160, q=2-31, 23.98 fps
"""


def test_av_summary_reads_the_input_not_the_output():
    from tunerbox import tracks
    av = tracks.av_summary(PLAYING)
    assert av == {"w": 3840, "h": 2160, "vcodec": "hevc", "bits": 10, "hdr": "Dolby Vision", "fps": 23.98, "kbps": 15800,
                  "acodec": "eac3", "layout": "5.1"}
    hlg = PLAYING.replace("smpte2084", "arib-std-b67").replace("DOVI configuration record", "x")
    assert tracks.av_summary(hlg)["hdr"] == "HLG"
    sd = "Input #0, mpegts, from 'x':\n  Stream #0:0[0x100]: Video: h264 (High), yuv420p(progressive), 720x576, 25 fps\n"
    assert tracks.av_summary(sd) == {"w": 720, "h": 576, "vcodec": "h264", "bits": 8, "hdr": "", "fps": 25.0, "kbps": 0}
    assert tracks.av_summary("Input #0, mp3, from 'x':\n  Stream #0:0: Audio: mp3, 44100 Hz, stereo\n") == {}
    assert tracks.av_summary("") == {}


class _Proc:
    def __init__(self, text):
        import io
        self.stderr = io.BytesIO(text.encode())


def test_the_stream_description_is_handed_over_and_kept_out_of_the_log():
    seen, err = [], []
    streaming._read_err(_Proc(PLAYING + "[mpegts @ 0x1] Packet corrupt\n"), err, seen.append)
    for _ in range(100):                                     # handed over on its own thread
        if seen:
            break
        time.sleep(0.01)
    assert len(seen) == 1 and "3840x2160" in seen[0] and "Output #0" not in seen[0].split("Stream mapping")[0]
    assert err == ["[mpegts @ 0x1] Packet corrupt"]
    # it stopped before it started: the error is kept, the address with the login never is
    err = []
    streaming._read_err(_Proc("Input #0, matroska, from 'http://host/movie/user/pass/1.mkv':\n"
                              "  Duration: N/A\nhttp: Connection reset by peer\n"), err, seen.append)
    assert err == ["http: Connection reset by peer"] and len(seen) == 1
    err = []
    streaming._read_err(_Proc(PLAYING), err)                   # no callback (catch-up): still kept out
    assert not any("pass" in x or "Stream #" in x for x in err)


def test_playing_a_movie_keeps_its_picture_and_sound(labelled):
    db.run("UPDATE vod_titles SET bitrate=16436 WHERE id='1:m302'")
    assert vod.save_av("1:m302", PLAYING) and vod.save_av("1:m102", PLAYING)
    assert db.one("SELECT bitrate FROM vod_titles WHERE id='1:m302'")["bitrate"] == 16436     # the provider's stays
    assert db.one("SELECT bitrate FROM vod_titles WHERE id='1:m102'")["bitrate"] == 15800     # none given: ffmpeg's
    by = {v["id"]: v for v in vod.title("1:m202")["versions"]}
    assert by["1:m302"]["av"]["hdr"] == "Dolby Vision" and by["1:m202"]["av"] is None
    assert vod.title("1:m302")["av"]["h"] == 2160
    assert not vod.save_av("1:m202", "nothing useful")
    assert "-nostats" in vod.stream_cmd("http://x/1.mkv")       # described, without progress lines


# ---------------------------------------------------------------- Pick a version: a quick look at each version
def _peeking(monkeypatch, status=206, total=11283525339, probed=None):
    """The provider answering a 2 MB read with `status`; ffmpeg reading a version itself answering `probed`
    ((secs, text, why); default: it can't). -> what was asked: ranges, and "ffmpeg" for each read by ffmpeg."""
    import httpx
    asked = []
    monkeypatch.setattr(vod, "probe_why", lambda url, timeout=None: asked.append("ffmpeg") or
                        (probed or (0, "", "Server returned 404 Not Found")))

    def handler(req):
        asked.append(req.headers.get("range"))
        if status != 206:
            return httpx.Response(status, content=b"x" * 100)
        return httpx.Response(206, content=b"\0" * vod.PEEK_BYTES,
                              headers={"content-range": f"bytes 0-{vod.PEEK_BYTES - 1}/{total}"})
    real = httpx.Client
    monkeypatch.setattr(vod.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(vod, "_describe", lambda data: PLAYING)
    vod._peeked.clear()
    return asked


def test_peek_reads_the_start_of_a_version_once(labelled, monkeypatch, client):
    asked = _peeking(monkeypatch)
    db.run("UPDATE vod_titles SET duration=5492 WHERE id IN ('1:m102', '1:m302')")
    db.run("UPDATE vod_titles SET bitrate=16436 WHERE id='1:m302'")
    v = client.get("/api/vod/peek/1:m102").json()
    assert v["av"]["hdr"] == "Dolby Vision" and v["bitrate"] == 16436      # no bitrate given: size over length
    assert asked == [f"bytes=0-{vod.PEEK_BYTES - 1}"]
    assert vod.peek("1:m302")["bitrate"] == 16436                          # the provider's stays
    vod.peek("1:m102")
    assert len(asked) == 2                                                 # read already: not again
    assert client.get("/api/vod/peek/nope").status_code == 404
    vod.peek("1:m102", force=True)
    assert len(asked) == 2                                                 # Read details: still not again
    client.get("/api/vod/peek/1:m102?force=1&again=1")
    assert len(asked) == 3                                                 # Read again: it is


def test_peek_never_takes_a_connection_in_use_or_reads_a_whole_film(labelled, monkeypatch):
    from tunerbox import connections
    asked = _peeking(monkeypatch)
    src = db.one("SELECT * FROM sources WHERE id=?", (labelled,))

    def full(source_id, *a, **k):
        raise connections.Full(src, 1, 1)
    monkeypatch.setattr(connections, "check", full)
    assert vod.peek("1:m102")["av"] is None and asked == []
    monkeypatch.undo()
    asked = _peeking(monkeypatch, status=200)                             # a provider that ignores Range
    v = vod.peek("1:m102")
    assert v["av"] is None and asked == [f"bytes=0-{vod.PEEK_BYTES - 1}", "ffmpeg"]    # 200: ffmpeg reads it instead
    assert v["why"] == "Couldn't read it: Server returned 404 Not Found"
    assert vod.peek("1:m102")["why"] == v["why"] and len(asked) == 2      # not tried again today, still says why
    vod.peek("1:m102", force=True)
    assert len(asked) == 4                                                 # Read details again: tried now


def test_a_version_the_start_says_nothing_about_is_read_by_ffmpeg(labelled, monkeypatch, client):
    """An MP4 keeps its track list at the end: the first 2 MB say nothing, ffmpeg finds it."""
    asked = _peeking(monkeypatch, probed=(5492, PLAYING, ""))
    monkeypatch.setattr(vod, "_describe", lambda data: "pipe:0: Invalid data found when processing input")
    v = client.get("/api/vod/peek/1:m102").json()
    assert asked == [f"bytes=0-{vod.PEEK_BYTES - 1}", "ffmpeg"] and v["av"]["hdr"] == "Dolby Vision" and v["why"] == ""
    no_picture = "Input #0, mov,mp4 from 'x':\n  Duration: 01:31:32.00"            # found, but no video in it
    monkeypatch.setattr(vod, "probe_why", lambda url, timeout=None: asked.append("ffmpeg") or (5492, no_picture, ""))
    v = vod.peek("1:m202")
    assert v["why"] == "Nothing in the file says" and len(asked) == 4
    vod.peek("1:m202")
    assert len(asked) == 4                                                 # '{}': read, not again unless asked


def test_no_connection_free_says_so_and_is_tried_again_next_time(labelled, monkeypatch):
    from tunerbox import connections
    asked = _peeking(monkeypatch)
    src = db.one("SELECT * FROM sources WHERE id=?", (labelled,))
    real = connections.check

    def full(source_id, *a, **k):
        raise connections.Full(src, 1, 1)
    monkeypatch.setattr(connections, "check", full)
    assert vod.peek("1:m102")["why"] == "No connection free on this playlist right now" and asked == []
    monkeypatch.setattr(connections, "check", real)
    assert vod.peek("1:m102")["av"]["hdr"] == "Dolby Vision"              # not remembered as a failure


def _own_copy(of="1:m202", path="Film 2 (2020)/Film 2 (2020).mkv"):
    """A copy of `of` in one of your libraries, with its file. -> its id."""
    from tunerbox import library
    db.run("INSERT INTO lib_shares(name, kind, type, server, share, path) VALUES ('NAS', 'movie', 'smb', 'nas', 'media', 'm')")
    sid = db.one("SELECT max(id) n FROM lib_shares")["n"]
    row = dict(db.one("SELECT * FROM vod_titles WHERE id=?", (of,)))
    row.update(id=f"L{sid}:m{of}", source_id=-sid, tag="", country="", av="")
    db.run(f"INSERT INTO vod_titles({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    db.run("INSERT INTO lib_files(item_id, title_id, share_id, path, size, mtime) VALUES (?, ?, ?, ?, 5, 0)",
           (row["id"], row["id"], sid, path))
    library.init()
    vod._changed()
    return row["id"]


def test_reading_details_automatically_only_when_asked_or_never(labelled, monkeypatch):
    from tunerbox import library
    asked, measured = _peeking(monkeypatch), []
    monkeypatch.setattr(library, "measure_one", lambda item_id: measured.append(item_id) or True)
    mine = _own_copy()
    config.update({"vod_details": "off"})
    vod.peek("1:m102")
    vod.peek("1:m102", force=True)
    vod.peek(mine, force=True)
    assert asked == [] and measured == []                                  # off: nothing read at all, even asked
    config.update({"vod_details": "ask"})
    assert vod.peek("1:m102")["av"] is None and asked == []                # only when Read details is pressed
    assert vod.peek("1:m102", force=True)["av"]["hdr"] == "Dolby Vision" and len(asked) == 1
    config.update({"vod_details": "auto"})
    vod.peek(mine)
    assert len(asked) == 1 and measured == [mine]                          # your own: from the NAS, not the provider
    assert vod.peek("1:s70") is None                                       # shows: not read


def test_pausing_the_screen_for_the_reading_can_be_turned_off(labelled, monkeypatch):
    from tunerbox import connections
    _peeking(monkeypatch)
    src = db.one("SELECT * FROM sources WHERE id=?", (labelled,))

    def check(source_id, url=None, client="", **k):
        if client != "tv-1":
            raise connections.Full(src, 1, 1)
    monkeypatch.setattr(connections, "check", check)
    config.update({"vod_details_pause": False})
    v = vod.peek("1:m102", client="tv-1")
    assert "mine" not in v and v["why"] == vod.NO_CONNECTION


# what an Xtream panel sends in get_vod_info's info (ffprobe's fields)
PANEL = {"bitrate": 8421,
         "video": {"codec_name": "hevc", "width": 3840, "height": 1608, "pix_fmt": "yuv420p10le",
                   "color_transfer": "smpte2084", "avg_frame_rate": "24000/1001"},
         "audio": {"codec_name": "eac3", "channels": 6, "channel_layout": "5.1(side)"}}


def test_the_panels_own_reading_of_a_movie_is_kept_and_ffmpegs_wins(labelled, monkeypatch):
    _versions_api(monkeypatch, {"102": {"info": PANEL, "movie_data": {}},
                                "202": {"info": {**PANEL, "video": []}, "movie_data": {}}})   # a panel that didn't probe
    vod.check_versions("1:m102")
    by = {v["id"]: v for v in vod.title("1:m102")["versions"]}
    assert by["1:m102"]["av"] == {"w": 3840, "h": 1608, "vcodec": "hevc", "bits": 10, "hdr": "HDR10",
                                  "fps": 23.976, "acodec": "eac3", "layout": "5.1"}
    assert by["1:m202"]["av"] is None
    db.run("UPDATE vod_titles SET av='{\"w\":1920}', info_at=0 WHERE id='1:m102'")      # read by ffmpeg since
    vod.check_versions("1:m102")
    assert vod.title("1:m102")["av"] == {"w": 1920}
    asked = _peeking(monkeypatch)
    db.run("UPDATE vod_titles SET av='' WHERE id='1:m102'")
    db.run("UPDATE vod_titles SET info_at=0 WHERE id='1:m102'")
    vod.check_versions("1:m102")
    vod.peek("1:m102")
    assert asked == []                    # the panel said already: no connection used to read it


def test_details_fetched_before_the_panels_reading_was_kept_are_fetched_again_once(labelled):
    db.run("UPDATE vod_titles SET info_at=?", (NOW,))
    db.run("UPDATE vod_titles SET av='{}' WHERE id='1:m202'")
    db.run("DELETE FROM meta WHERE k='vod_details_v3'")
    vod.init()
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:m102'")["info_at"] == 0
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:m202'")["info_at"] == NOW     # read already
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:s70'")["info_at"] == NOW      # shows: no
    db.run("UPDATE vod_titles SET info_at=? WHERE id='1:m102'", (NOW,))
    vod.init()
    assert db.one("SELECT info_at FROM vod_titles WHERE id='1:m102'")["info_at"] == NOW     # once


def test_a_version_not_read_yet_shows_what_its_file_name_or_label_says(labelled):
    mine = _own_copy(path="Film 2 (2020)/Film.2.2020.1080p.BluRay.x264.DTS-HD.MA.5.1-GRP.mkv")
    by = {v["id"]: v for v in vod.title("1:m202")["versions"]}
    assert by[mine]["hint"] == {"w": 1920, "h": 1080, "vcodec": "h264", "acodec": "dts", "layout": "5.1"}
    assert by["1:m302"]["hint"] == {"w": 3840, "h": 2160}                  # its label: 4K-EN
    assert by["1:m202"]["hint"] is None                                    # DE says nothing about the picture
    assert vod.title(mine)["hint"]["vcodec"] == "h264"
    db.run("""UPDATE vod_titles SET av='{"w":1280,"h":720}' WHERE id=?""", (mine,))
    assert {v["id"]: v for v in vod.title("1:m202")["versions"]}[mine]["hint"] is None    # read: no guess


def test_pick_a_version_lists_the_best_first(labelled):
    """Your own copy, then the best picture (what ffmpeg read, else the label), then the highest bitrate."""
    db.run("UPDATE vod_titles SET bitrate=2596 WHERE id='1:m102'")      # EN
    db.run("UPDATE vod_titles SET bitrate=13969 WHERE id='1:m202'")     # DE: no resolution known
    db.run("UPDATE vod_titles SET bitrate=16436 WHERE id='1:m302'")     # 4K-EN, by its label
    assert [v["id"] for v in vod.title("1:m102")["versions"]] == ["1:m302", "1:m202", "1:m102"]
    vod.save_av("1:m302", PLAYING.replace("3840x2160", "1920x1080"))    # read: really 1080p
    vod.save_av("1:m102", PLAYING.replace("3840x2160", "1280x720"))
    assert [v["id"] for v in vod.title("1:m102")["versions"]] == ["1:m302", "1:m102", "1:m202"]
    assert vod._quality({"av": "", "tag": "UHD"}) == 4 and vod._quality({"av": "", "tag": "FHD"}) == 3
    assert vod._quality({"av": "", "tag": "HD"}) == 2 and vod._quality({"av": "", "tag": "EN"}) == 1.5


def test_pick_a_version_gives_each_files_type_and_size(labelled):
    db.run("UPDATE vod_titles SET ext='MKV' WHERE id='1:m102'")
    db.run("INSERT INTO lib_files(item_id, title_id, share_id, path, size) VALUES ('1:m102', '1:m102', 1, 'x.mkv', 11283525339)")
    by = {v["id"]: v for v in vod.title("1:m202")["versions"]}
    assert (by["1:m102"]["ext"], by["1:m102"]["size"]) == ("mkv", 11283525339)
    assert by["1:m202"]["size"] == 0                       # a provider's: worked out on the screen from its bitrate


def test_a_file_that_cant_be_read_says_why(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    def ran(err=b"", timeout=False, missing=False):
        def run(cmd, **k):
            assert "-reconnect" not in cmd                     # a one-off read: an error isn't retried until 25 s
            if missing:
                raise FileNotFoundError("ffmpeg")
            if timeout:
                raise subprocess.TimeoutExpired(cmd, 25, stderr=err)
            return SimpleNamespace(stderr=err)
        monkeypatch.setattr(vod.subprocess, "run", run)
    ran(b"[http @ 0x1] HTTP error 502 Bad Gateway\nhttp://127.0.0.1/api/library/file/x: Server returned 5XX Server Error reply\n")
    assert vod.probe_why("http://127.0.0.1/api/library/file/x") == \
        (0, "", "http://127.0.0.1/api/library/file/x: Server returned 5XX Server Error reply")   # not "read, nothing in it"
    ran(timeout=True)
    assert vod.probe_why("http://h/x")[2] == "no answer within 25 s"
    ran(missing=True)
    assert vod.probe_why("http://h/x")[2].startswith("ffmpeg didn't start")
    ran(b"Input #0, matroska,webm, from 'x':\n  Duration: 01:00:00.00, start: 0.000000, bitrate: 5000 kb/s\n")
    secs, text, why = vod.probe_why("http://h/x")
    assert secs == 3600 and text and why == ""


def test_a_panel_giving_a_films_length_in_minutes_is_read_as_minutes():
    assert vod._secs({"duration_secs": "92"}, movie=True) == 92 * 60
    assert vod._secs({"duration": "00:01:32"}, movie=True) == 92 * 60      # minutes in the text too
    assert vod._secs({"duration_secs": "5520", "duration": "01:32:00"}, movie=True) == 5520
    assert vod._secs({"duration_secs": "92"}) == 92                          # an episode can be that short


def test_the_files_own_length_fixes_a_wrong_one_and_no_impossible_bitrate_is_kept(labelled, monkeypatch):
    asked = _peeking(monkeypatch, total=7_400_000_000)
    db.run("UPDATE vod_titles SET duration=60 WHERE id='1:m102'")          # the panel said "1 min"
    v = vod.peek("1:m102")
    assert len(asked) == 1 and v["duration"] == 5492                       # PLAYING: Duration 01:31:32
    assert v["bitrate"] == int(7_400_000_000 * 8 / 5492 / 1000)            # ~10.8 Mbps, not 987 Mbps
    monkeypatch.setattr(vod, "_describe", lambda data: PLAYING.replace("Duration: 01:31:32.00", "Duration: 00:00:30.00"))
    db.run("UPDATE vod_titles SET duration=61, bitrate=0, av='' WHERE id='1:m202'")
    assert vod.peek("1:m202")["bitrate"] in (0, 15800)                     # 30 s: not a length to work it out from
    assert db.one("SELECT bitrate FROM vod_titles WHERE id='1:m202'")["bitrate"] <= vod.MAX_KBPS


def test_lengths_and_bitrates_stored_wrong_before_are_cleared_once(labelled):
    db.run("UPDATE vod_titles SET duration=60, bitrate=987100, info_at=? WHERE id='1:m102'", (NOW,))
    db.run("UPDATE vod_titles SET duration=5492, bitrate=4100, info_at=? WHERE id='1:m202'", (NOW,))
    db.run("UPDATE vod_titles SET bitrate=500000 WHERE id='1:m302'")
    db.run("DELETE FROM meta WHERE k='vod_details_v4'")
    vod.init()
    r = {x["id"]: x for x in db.rows("SELECT id, duration, bitrate, info_at FROM vod_titles")}
    assert (r["1:m102"]["duration"], r["1:m102"]["bitrate"], r["1:m102"]["info_at"]) == (0, 0, 0)
    assert (r["1:m202"]["duration"], r["1:m202"]["bitrate"], r["1:m202"]["info_at"]) == (5492, 4100, NOW)
    assert r["1:m302"]["bitrate"] == 0


def test_a_connection_held_only_by_the_screen_asking_can_be_let_go_of(labelled, monkeypatch, client):
    """Live TV in this screen's corner uses the playlist's only connection: peek says so ("mine"), reads
    nothing, and reads once the screen has let go of it. Another screen's stream is never "mine"."""
    from tunerbox import connections
    asked = _peeking(monkeypatch)
    src = db.one("SELECT * FROM sources WHERE id=?", (labelled,))
    holder = {"client": "tv-1"}

    def check(source_id, url=None, client="", **k):
        if holder["client"] and client != holder["client"]:
            raise connections.Full(src, 1, 1)
    monkeypatch.setattr(connections, "check", check)
    v = client.get("/api/vod/peek/1:m102?client=tv-1").json()
    assert v["mine"] is True and v["av"] is None and asked == []
    v = vod.peek("1:m102", client="tv-2")                                  # someone else's: left alone
    assert v["why"] == vod.NO_CONNECTION and "mine" not in v and asked == []
    holder["client"] = ""                                                  # tv-1 stopped its stream
    v = client.get("/api/vod/peek/1:m102?client=tv-1").json()
    assert v["av"]["hdr"] == "Dolby Vision" and "mine" not in v and len(asked) == 1


def test_a_placeholder_length_is_unknown_when_the_other_copies_disagree():
    v = lambda d, mine=False: {"duration": d, "mine": mine, "len_unknown": False}     # noqa: E731
    vs = [v(5491, True), v(3600), v(5491)]
    vod._unknown_lengths(vs)
    assert [(x["duration"], x["len_unknown"]) for x in vs] == [(5491, False), (0, True), (5491, False)]
    vs = [v(3600), v(3610)]                     # an hour-long film: the copies agree, it's real
    vod._unknown_lengths(vs)
    assert vs[0]["duration"] == 3600 and not vs[0]["len_unknown"]


def test_pick_a_version_gives_each_versions_own_progress(labelled):
    vod.set_progress("1:m202", 1800, 5400)                  # DE: stopped half an hour in
    vod.set_progress("1:m302", 5300, 5400)                  # 4K-EN: played to the end
    by = {v["id"]: v for v in vod.title("1:m102")["versions"]}
    assert (by["1:m202"]["position"], by["1:m202"]["pdur"], by["1:m202"]["watched"]) == (1800, 5400, False)
    assert by["1:m202"]["stopped"] > 0
    assert by["1:m302"]["watched"] and by["1:m302"]["position"] == 0
    assert (by["1:m102"]["position"], by["1:m102"]["watched"], by["1:m102"]["stopped"]) == (0, False, 0)


# ------------------------------------------------------------------ the guide's show in On Demand
def test_a_guide_listing_finds_its_movie_or_episode(xtream):
    sid, calls = xtream
    assert vod.for_program("Charade") == {"id": f"{sid}:m10", "kind": "movie", "title_id": f"{sid}:m10"}
    assert vod.for_program("CHARADE (1963)", category="Movie")["id"] == f"{sid}:m10"
    assert vod.for_program("Charade (1990)") is None                       # another film with the name
    assert vod.for_program("Charades") is None and vod.for_program("") is None
    # a show's episode: by season and episode (read from the provider the first time), else its episode title
    assert vod.for_program("Dragnet", "The Human Bomb", "S01E01")["id"] == f"{sid}:e101"
    assert calls.count("get_series_info") == 1
    assert vod.for_program("Dragnet", "", "Season 2 Episode 1")["id"] == f"{sid}:e201"
    assert vod.for_program("Dragnet", "", "2x01")["id"] == f"{sid}:e201"
    assert vod.for_program("dragnet", "The Big Actor")["id"] == f"{sid}:e102"
    assert calls.count("get_series_info") == 1                              # read once, today
    # the show, but not that episode (or no way to tell which): the show, to open in On Demand
    assert vod.for_program("Dragnet", "", "S09E09") == {"show": f"{sid}:s7"}
    assert vod.for_program("Dragnet") == {"show": f"{sid}:s7"}


def test_a_missing_episode_rereads_a_list_more_than_a_day_old(xtream):
    sid, calls = xtream
    vod.title(f"{sid}:s7")
    db.run("UPDATE vod_titles SET info_at=? WHERE id=?", (NOW - 2 * 86400, f"{sid}:s7"))
    assert vod.for_program("Dragnet", "", "S09E09") == {"show": f"{sid}:s7"}
    assert calls.count("get_series_info") == 2
    assert vod.for_program("Dragnet", "", "S09E09") == {"show": f"{sid}:s7"}
    assert calls.count("get_series_info") == 2                              # just read: not again


def test_for_program_endpoint(xtream, client, monkeypatch):
    sid, _ = xtream
    from tunerbox import guide
    monkeypatch.setattr(guide, "channel", lambda n: {"number": n, "epg": "x:five"} if n == 5 else None)
    db.run("INSERT INTO programs(channel, start, stop, title, subtitle, episode, category) VALUES "
           "('x:five', ?, ?, 'Dragnet', 'The Human Bomb', 'S01E01', 'Series')", (NOW - 600, NOW + 1200))
    vod.title(f"{sid}:s7")
    vod.set_progress(f"{sid}:e101", 400, 1500)
    d = client.get(f"/api/vod/for-program/5/{NOW - 600}").json()
    assert d["slot"] == {"start": NOW - 600, "stop": NOW + 1200}
    assert d["match"] == {"id": f"{sid}:e101", "kind": "episode", "title_id": f"{sid}:s7", "title": "Dragnet",
                          "sub": "S1 E1 · The Human Bomb", "duration": 1500, "position": 400}
    assert "url" not in d["match"]                                          # never the provider's address
    assert client.get(f"/api/vod/for-program/5/{NOW + 5000}").json() == {"match": None, "slot": None}
    assert client.get(f"/api/vod/for-program/6/{NOW}").json() == {"match": None, "slot": None}
