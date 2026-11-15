"""Local library: your own movies and shows (a network share or a folder) in On Demand, with TMDB details."""
import pytest
from fastapi.testclient import TestClient

from tunerbox import api, db, jobs, library, vod


# ---------------------------------------------------------------- names
@pytest.mark.parametrize("raw,name,year", [
    ("Dune Part Two (2024)", "Dune Part Two", "2024"),
    ("Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.x265-GRP", "Dune Part Two", "2024"),
    ("2001 A Space Odyssey (1968)", "2001 A Space Odyssey", "1968"),
    ("Blade.Runner.2049.2017.1080p.BluRay.x264", "Blade Runner 2049", "2017"),
    ("Blade Runner 2049", "Blade Runner", "2049") if False else ("Heat [1995] {tmdb-949}", "Heat", "1995"),
    ("The Matrix 1080p", "The Matrix", ""),
    ("1917 (2019)", "1917", "2019"),
    ("Amélie", "Amélie", ""),
])
def test_parse_name(raw, name, year):
    assert library.parse_name(raw) == (name, year)


def test_parse_episode():
    assert library.parse_episode("Severance - S02E04 - Woe's Hollow.mkv") == ("Severance", 2, 4, "Woe's Hollow")
    assert library.parse_episode("the.bear.s03e01.1080p.web.h264-grp.mkv") == ("the bear", 3, 1, "")
    assert library.parse_episode("Frasier 1x02 The Crucible.avi")[1:3] == (1, 2)
    assert library.parse_episode("04 - The Fight.mkv", season_hint=2) == ("", 2, 4, "The Fight")
    assert library.parse_episode("Behind the music.mkv") is None


def test_parse_location():
    assert library.parse_location(r"\\nas\Media\Movies") == ("nas", "Media", "Movies")
    assert library.parse_location("smb://192.168.1.20/Media/TV/Kids") == ("192.168.1.20", "Media", "TV/Kids")
    assert library.parse_location("//nas:1445/Media") == ("nas:1445", "Media", "")
    assert library.parse_location("/media/movies") == ("", "", "/media/movies")


# ---------------------------------------------------------------- a library on disk
def _touch(root, rel, size=1000):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


@pytest.fixture
def movies(tmp_path):
    root = tmp_path / "Movies"
    _touch(root, "Dune Part Two (2024)/Dune.Part.Two.2024.2160p.mkv", 5000)
    _touch(root, "Dune Part Two (2024)/Dune.Part.Two.2024.sample.mkv", 100)       # left out
    _touch(root, "Dune Part Two (2024)/Extras/Making of.mkv")                     # left out
    _touch(root, "Heat (1995)/Heat.mp4")
    _touch(root, "Loose.Movie.2010.720p.mkv")
    _touch(root, "Collection/Alien (1979).mkv")
    _touch(root, "Collection/Aliens (1986).mkv")
    _touch(root, "notes.txt")
    sid = library.add({"name": "NAS Movies", "kind": "movie", "location": str(root)})
    return sid, root


@pytest.fixture
def shows(tmp_path):
    root = tmp_path / "TV"
    _touch(root, "Severance (2022)/Season 01/Severance - S01E01 - Good News About Hell.mkv")
    _touch(root, "Severance (2022)/Season 02/Severance - S02E04.mkv")
    _touch(root, "The Bear/S3/04 - Violet.mkv")
    _touch(root, "Frasier - S01E02 - The Crucible.mkv")
    _touch(root, "Home video.mkv")
    sid = library.add({"name": "NAS TV", "kind": "series", "location": str(root)})
    return sid, root


def test_scan_movies(movies):
    sid, _ = movies
    msg = library.scan_share(library.share(sid))
    assert msg == "5 movies"
    rows = {r["name"]: r for r in db.rows("SELECT * FROM vod_titles WHERE source_id=?", (-sid,))}
    assert set(rows) == {"Dune Part Two", "Heat", "Loose Movie", "Alien", "Aliens"}
    assert rows["Dune Part Two"]["url"] == "Dune Part Two (2024)/Dune.Part.Two.2024.2160p.mkv"
    assert rows["Heat"]["year"] == "1995" and rows["Alien"]["category"] == "NAS Movies"
    # On Demand lists them like playlist titles
    assert vod.enabled()
    names = [t["name"] for t in vod.titles("movie")["items"]]
    assert names == ["Alien", "Aliens", "Dune Part Two", "Heat", "Loose Movie"]
    assert vod.categories("movie")["cats"] == [{"name": "NAS Movies", "count": 5}]
    assert vod.search("dune")[0]["name"] == "Dune Part Two"


def test_scan_shows(shows):
    sid, _ = shows
    msg = library.scan_share(library.share(sid))
    assert msg.startswith("3 shows, 4 episodes") and "1 file" in msg
    sev = db.one("SELECT * FROM vod_titles WHERE source_id=? AND name='Severance'", (-sid,))
    t = vod.title(sev["id"])
    assert [(e["season"], e["episode"], e["name"]) for e in t["episodes"]] == [(1, 1, "Good News About Hell"), (2, 4, "")]
    bear = db.one("SELECT id FROM vod_titles WHERE name='The Bear'")
    assert [(e["season"], e["episode"]) for e in vod.title(bear["id"])["episodes"]] == [(3, 4)]


def test_rescan_keeps_progress_and_drops_missing(movies):
    sid, root = movies
    library.scan_share(library.share(sid))
    heat = db.one("SELECT id FROM vod_titles WHERE name='Heat'")["id"]
    vod.set_progress(heat, 600, 6000)
    (root / "Loose.Movie.2010.720p.mkv").unlink()
    _touch(root, "New One (2025)/new.one.mkv")
    library.scan_share(library.share(sid))
    names = {r["name"] for r in db.rows("SELECT name FROM vod_titles WHERE source_id=?", (-sid,))}
    assert "Loose Movie" not in names and "New One" in names
    assert vod.continue_watching()[0]["id"] == heat


def test_turned_off_and_deleted(movies):
    sid, _ = movies
    library.scan_share(library.share(sid))
    library.update(sid, {"enabled": False})
    assert vod.titles("movie")["total"] == 0
    library.update(sid, {"enabled": True})
    assert vod.titles("movie")["total"] == 5
    library.delete(sid)
    assert not db.rows("SELECT 1 FROM vod_titles WHERE source_id=?", (-sid,))


def test_bad_folder_reports_error(tmp_path):
    sid = library.add({"name": "Gone", "kind": "movie", "location": str(tmp_path / "nope")})
    msg = library.scan_all()
    assert "Folder not found" in msg
    assert library.share(sid)["status"].startswith("error: Folder not found")


def test_clean_needs_kind_and_location():
    with pytest.raises(ValueError):
        library.clean({"location": r"\\nas\Media"})
    with pytest.raises(ValueError):
        library.clean({"kind": "movie", "location": r"\\nas"})
    d = library.clean({"kind": "series", "location": r"\\nas\Media\TV Shows", "username": "john", "password": "pw"})
    assert (d["type"], d["server"], d["share"], d["path"], d["name"]) == ("smb", "nas", "Media", "TV Shows", "TV Shows")


def test_password_kept_and_hidden(tmp_path):
    sid = library.add({"kind": "movie", "location": r"\\nas\Media", "username": "u", "password": "secret"})
    library.update(sid, {"name": "Renamed", "password": ""})
    assert library.share(sid)["password"] == "secret"
    listed = library.shares()[0]
    assert listed["password"] == "" and listed["has_password"] and listed["location"] == r"\\nas\Media"


# ---------------------------------------------------------------- playing
def test_item_and_byte_ranges(movies, monkeypatch):
    sid, root = movies
    library.scan_share(library.share(sid))
    (root / "Heat (1995)/Heat.mp4").write_bytes(bytes(range(256)) * 4)       # 1024 bytes
    heat = db.one("SELECT id FROM vod_titles WHERE name='Heat'")["id"]
    it = vod.item(heat)
    assert it["url"].endswith(f"/api/library/file/{heat}") and it["source_id"] == -sid
    status, headers, body = library.open_range(heat, "bytes=1000-")
    assert status == 206 and headers["Content-Range"] == "bytes 1000-1023/1024" and b"".join(body) == bytes(range(232, 256))
    status, headers, body = library.open_range(heat, "")
    assert status == 200 and headers["Content-Length"] == "1024" and len(b"".join(body)) == 1024
    status, _, _ = library.open_range(heat, "bytes=5000-")
    assert status == 416


def test_file_endpoint_is_local_only(movies):
    sid, _ = movies
    library.scan_share(library.share(sid))
    heat = db.one("SELECT id FROM vod_titles WHERE name='Heat'")["id"]
    assert TestClient(api.app).get(f"/api/library/file/{heat}").status_code == 403       # from another device
    local = TestClient(api.app, client=("127.0.0.1", 5000))
    r = local.get(f"/api/library/file/{heat}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == b"x" * 10 and r.headers["accept-ranges"] == "bytes"


# ---------------------------------------------------------------- TMDB
MOVIES = {
    "Heat": [{"id": 949, "title": "Heat", "release_date": "1995-12-15", "popularity": 50},
             {"id": 1, "title": "Heat", "release_date": "1986-03-14", "popularity": 5}],
    "Dune Part Two": [{"id": 693134, "title": "Dune: Part Two", "release_date": "2024-02-27", "popularity": 300}],
    "Loose Movie": [{"id": 77, "title": "A Completely Different Film", "release_date": "2010-01-01", "popularity": 1}],
}


@pytest.fixture
def tmdb(monkeypatch):
    calls = []

    def fake(path, key=None, **params):
        calls.append((path, params))
        if path == "/configuration":
            return {"images": {}}
        if path.startswith("/search/movie"):
            return {"results": MOVIES.get(params["query"], [])}
        if path.startswith("/search/tv"):
            return {"results": [{"id": 95396, "name": "Severance", "first_air_date": "2022-02-17"}]
                    if params["query"] == "Severance" else []}
        if path.startswith("/movie/"):
            mid = int(path.split("/")[2])
            return {"id": mid, "title": {949: "Heat", 693134: "Dune: Part Two", 77: "Different"}.get(mid, "X"),
                    "release_date": "1995-12-15" if mid == 949 else "2024-02-27", "overview": f"Plot {mid}",
                    "genres": [{"name": "Crime"}, {"name": "Drama"}], "vote_average": 8.25, "vote_count": 900,
                    "runtime": 170, "poster_path": f"/p{mid}.jpg", "backdrop_path": f"/b{mid}.jpg",
                    "credits": {"cast": [{"name": "Al Pacino"}, {"name": "Robert De Niro"}],
                                "crew": [{"job": "Director", "name": "Michael Mann"}]}}
        if path == "/tv/95396":
            return {"id": 95396, "name": "Severance", "first_air_date": "2022-02-17", "overview": "Work-life balance.",
                    "genres": [{"name": "Drama"}], "vote_average": 8.4, "vote_count": 500, "poster_path": "/sev.jpg",
                    "created_by": [{"name": "Dan Erickson"}], "credits": {"cast": [{"name": "Adam Scott"}]}}
        if path.startswith("/tv/95396/season/"):
            n = int(path.rsplit("/", 1)[1])
            return {"episodes": [{"episode_number": 4, "name": "Woe's Hollow", "overview": "Outdoors.", "air_date": "2025-02-07",
                                  "runtime": 50}] if n == 2 else [{"episode_number": 1, "name": "Good News About Hell",
                                                                    "overview": "Mark.", "runtime": 57}]}
        return None
    monkeypatch.setattr(library, "_tmdb", fake)
    library.save_settings({"tmdb_key": "abc123"})
    return calls


def test_tmdb_details_for_movies(movies, tmdb):
    sid, _ = movies
    msg = library.scan_all()
    assert "Details for" in msg
    heat = db.one("SELECT * FROM vod_titles WHERE source_id=? AND year='1995'", (-sid,))
    assert heat["name"] == "Heat" and heat["plot"] == "Plot 949" and heat["director"] == "Michael Mann"
    assert heat["rating"] == "8.2" and heat["genre"] == "Crime, Drama" and heat["duration"] == 0      # measured when played
    assert heat["poster"] == "https://image.tmdb.org/t/p/w500/p949.jpg"
    dune = db.one("SELECT * FROM vod_titles WHERE source_id=? AND name LIKE 'Dune%'", (-sid,))
    assert dune["name"] == "Dune: Part Two"
    states = {r["title_id"]: r["state"] for r in db.rows("SELECT * FROM lib_meta")}
    loose = db.one("SELECT id FROM vod_titles WHERE url LIKE 'Loose%'")["id"]
    alien = db.one("SELECT id FROM vod_titles WHERE url LIKE '%Alien (1979)%'")["id"]
    assert states[loose] == "weak" and states[alien] == "none" and states[heat["id"]] == "ok"
    review = {r["id"]: r for r in library.review(sid)}
    assert set(review) >= {loose, alien} and heat["id"] not in review
    assert review[loose]["file_name"] == "Loose Movie"       # what it was looked up as, not TMDB's name
    # a rescan keeps the TMDB name and doesn't look anything up again
    n = len(tmdb)
    library.scan_all()
    assert len(tmdb) == n and db.one("SELECT name FROM vod_titles WHERE id=?", (dune["id"],))["name"] == "Dune: Part Two"
    assert library.counts()["review"] == 3


def test_fix_match_ignore_unmatch(movies, tmdb):
    sid, _ = movies
    library.scan_all()
    loose = db.one("SELECT id FROM vod_titles WHERE url LIKE 'Loose%'")["id"]
    library.pin(loose, 949)
    t = db.one("SELECT * FROM vod_titles WHERE id=?", (loose,))
    assert t["name"] == "Heat" and db.one("SELECT state FROM lib_meta WHERE title_id=?", (loose,))["state"] == "pinned"
    library.scan_all(full_refresh=True)                          # pinned survives a full refresh
    assert db.one("SELECT tmdb_id, state FROM lib_meta WHERE title_id=?", (loose,)) == {"tmdb_id": 949, "state": "pinned"}
    library.unmatch(loose)
    assert db.one("SELECT name, plot FROM vod_titles WHERE id=?", (loose,)) == {"name": "Loose Movie", "plot": ""}
    library.ignore(loose)
    assert loose not in [x["id"] for x in vod.titles("movie")["items"]]
    library.scan_share(library.share(sid))                       # stays left out after a rescan
    assert loose not in [x["id"] for x in vod.titles("movie")["items"]]
    library.ignore(loose, False)
    assert loose in [x["id"] for x in vod.titles("movie")["items"]]


def test_tmdb_for_shows(shows, tmdb):
    sid, _ = shows
    library.scan_all()
    sev = db.one("SELECT * FROM vod_titles WHERE source_id=? AND name='Severance'", (-sid,))
    assert sev["plot"] == "Work-life balance." and sev["director"] == "Dan Erickson"
    eps = vod.title(sev["id"])["episodes"]
    assert [(e["episode"], e["name"], e["plot"]) for e in eps] == [(1, "Good News About Hell", "Mark."), (4, "Woe's Hollow", "Outdoors.")]


def test_settings_hide_key():
    s = library.save_settings({"tmdb_key": "0123456789abcdef", "auto_scan_hours": "12"})
    assert "tmdb_key" not in s and s["tmdb_key_set"] and s["tmdb_key_hint"] == "…cdef" and s["auto_scan_hours"] == 12
    with pytest.raises(ValueError):
        library.save_settings({"nope": 1})


# ---------------------------------------------------------------- HTTP
def test_api_add_scan_and_review(movies, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(jobs, "library_scan", lambda sid=None, full=False: started.append(sid) or True)
    c = TestClient(api.app)
    root = tmp_path / "More"
    _touch(root, "Heat (1995)/heat.mkv")
    r = c.post("/api/library/browse", json={"location": str(tmp_path), "kind": "movie"})
    assert r.json()["ok"] and "More" in r.json()["folders"]
    r = c.post("/api/library/shares", json={"kind": "movie", "location": str(root), "name": "More"})
    assert r.status_code == 200 and started == [r.json()["id"]]
    assert c.post("/api/library/shares", json={"location": str(root)}).status_code == 400
    data = c.get("/api/library").json()
    assert {s["name"] for s in data["shares"]} == {"NAS Movies", "More"} and all(s["password"] == "" for s in data["shares"])
    library.scan_share(library.share(movies[0]))
    assert len(c.get("/api/library/titles?filter=all").json()) == 5
    r = c.post("/api/library/settings", json={"tmdb_key": "", "language": "en-GB"})
    assert r.json()["language"] == "en-GB" and not r.json()["tmdb_key_set"]
