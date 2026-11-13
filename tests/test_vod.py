"""On Demand: reading movies and shows from a playlist, browsing them, and remembering what was watched."""
import time

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, db, jobs, sources, vod

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
    vod.set_watched(movie)
    assert [t["id"] for t in vod.titles(tab="continue")["items"]] == [f"{sid}:s7"]
    assert vod.item(movie)["position"] == 0                 # watched: plays from the start again
    vod.set_watched(f"{sid}:s7")                            # a whole show
    assert all(e["watched"] for e in vod.title(f"{sid}:s7")["episodes"])
    assert vod.set_listed(movie)["in_list"] and vod.set_listed("nope") is None
    assert [t["id"] for t in vod.titles(tab="list")["items"]] == [movie]
    assert vod.counts()["movie"]["list"] == 1 and vod.counts()["series"]["list"] == 0
    vod.set_listed(movie, False)
    assert vod.titles(tab="list")["total"] == 0


def test_play_address_and_command(xtream):
    sid, _ = xtream
    it = vod.item(f"{sid}:m10")
    assert it["url"] == "http://host:8080/movie/user/pass/10.mkv" and it["kind"] == "movie"
    vod.title(f"{sid}:s7")
    ep = vod.item(f"{sid}:e101")
    assert ep["url"] == "http://host:8080/series/user/pass/101.mp4" and ep["title"] == "Dragnet"
    assert ep["sub"] == "S1 E1 · The Human Bomb" and ep["next"] == {"id": f"{sid}:e102", "label": "S1 E2 · The Big Actor"}
    assert vod.item(f"{sid}:s7") is None                    # a show itself can't be played
    cmd = vod.stream_cmd(it["url"], 42.5)
    assert cmd.index("-re") < cmd.index("-ss") < cmd.index("-i") and cmd[cmd.index("-ss") + 1] == "42.50"
    assert "-ss" not in vod.stream_cmd(it["url"])


def test_hidden_categories(xtream):
    sid, _ = xtream
    assert [g["name"] for g in vod.cats(sid)["groups"]] == ["Adult", "Mystery"]       # movies
    assert [g["name"] for g in vod.cats(sid, "series")["groups"]] == ["Crime"]
    r = vod.set_hidden(sid, ["Adult", "no such"])
    assert r == {"hidden": 1, "newly_hidden": 1, "shown_again": 0} and vod.counts()["movie"]["total"] == 2
    vod.refresh_xtream(db.one("SELECT * FROM sources WHERE id=?", (sid,)))      # stays out after a scan
    assert vod.counts()["movie"]["total"] == 2
    stats = vod.source_stats(db.one("SELECT * FROM sources WHERE id=?", (sid,)))
    assert stats["cats"] == {"movie": [1, 2], "series": [1, 1]} and stats["movies"] == 2
    assert vod.cats(sid)["groups"][0]["hidden"]
    vod.set_hidden(sid, ["Crime"], "series")                       # the TV show list doesn't touch the movie one
    assert vod.cats(sid)["groups"][0]["hidden"] and vod.cats(sid, "series")["groups"][0]["hidden"]
    assert vod.set_hidden(sid, [])["shown_again"] == 1
    assert vod.cats(sid, "series")["groups"][0]["hidden"]


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
    monkeypatch.setattr(jobs, "vod_scan", lambda source_id=None: started.append(source_id) or True)
    r = client.patch(f"/api/sources/{sid}", json={"vod_movies": 0})
    assert r.json()["job"] == "" and vod.counts()["movie"]["total"] == 0 and vod.counts()["series"]["total"] == 1
    assert client.patch(f"/api/sources/{sid}", json={"vod_movies": 0}).json()["job"] == "" and not started     # no change
    assert client.patch(f"/api/sources/{sid}", json={"vod_movies": 1}).json()["job"] == "vod" and started == [sid]
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
