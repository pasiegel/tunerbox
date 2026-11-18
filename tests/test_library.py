"""Local library: your own movies and shows (a network share or a folder) in On Demand, with TMDB details."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from tunerbox import api, db, jobs, libtasks, library, tracks, vod


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
    assert vod.categories("movie")["cats"] == [{"name": "NAS Movies", "count": 5, "library": 1}]
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


def test_rescan_keeps_progress_hides_then_removes_missing(movies):
    sid, root = movies
    library.scan_share(library.share(sid))
    heat = db.one("SELECT id FROM vod_titles WHERE name='Heat'")["id"]
    vod.set_progress(heat, 600, 6000)
    (root / "Loose.Movie.2010.720p.mkv").unlink()
    _touch(root, "New One (2025)/new.one.mkv", 2222)
    msg = library.scan_share(library.share(sid))
    assert msg == "5 movies; 1 new, 1 missing"
    listed = {t["name"] for t in vod.titles("movie")["items"]}
    assert "Loose Movie" not in listed and "New One" in listed
    assert vod.continue_watching()[0]["id"] == heat
    # kept, hidden, for remove_after_days: a file that comes back is as it was
    assert library.counts()["missing"] == 1 and [r["name"] for r in library.review(sid, "missing")] == ["Loose Movie"]
    assert library.remove_missing() == "Nothing to remove; 1 still missing, kept for now"
    assert library.remove_missing(now=time.time() + 8 * 86400) == "Nothing to remove; 1 still missing, kept for now"
    assert library.remove_missing(now=time.time() + 31 * 86400) == "Removed 1 missing title"     # 30 days by default
    assert not db.one("SELECT 1 FROM vod_titles WHERE name='Loose Movie'")


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
    # a folder on the TunerBox computer: only this PC or a paired device may point it there
    assert c.post("/api/library/browse", json={"location": str(tmp_path), "kind": "movie"}).status_code == 403
    assert c.post("/api/library/shares", json={"kind": "movie", "location": str(root)}).status_code == 403
    from tunerbox import remote
    monkeypatch.setattr(remote, "is_pc", lambda request: True)
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


def test_saved_password_not_sent_to_a_new_server():
    sid = library.add({"kind": "movie", "location": r"\\nas\Media", "username": "u", "password": "secret"})
    with pytest.raises(ValueError):          # someone points it at another machine to collect the login
        library.update(sid, {"location": r"\\evil\Media"})
    with pytest.raises(ValueError):
        library.clean({"location": r"\\evil\x", "kind": "movie"}, library.share(sid))
    library.update(sid, {"location": r"\\nas\Media\Movies"})    # same server, other folder: kept
    assert library.share(sid)["password"] == "secret"


def test_my_library_categories_come_first():
    """Your own libraries are listed before a playlist's groups in On Demand, whatever their names."""
    from tunerbox import db, vod
    sid = db.run("INSERT INTO sources(name, type, url, vod_movies) VALUES ('P', 'xtream', 'http://p', 1)")
    lib = db.run("INSERT INTO lib_shares(name, kind, type, path, enabled) VALUES ('Zed Movies', 'movie', 'folder', '/x', 1)")
    for i, (src, cat) in enumerate(((sid, "Action"), (sid, "EN - Drama"), (-lib, "Zed Movies"))):
        db.run("INSERT INTO vod_titles(id, source_id, kind, name, category, search) VALUES (?, ?, 'movie', ?, ?, '')",
               (f"t{i}", src, f"Title {i}", cat))
    cats = vod.categories("movie")["cats"]
    assert [c["name"] for c in cats] == ["Zed Movies", "Action", "EN - Drama"]
    assert cats[0]["library"] and not cats[1]["library"]


# ---------------------------------------------------------------- missing, renamed and moved files
def _move(root, a, b):
    """Rename like a person would: the file keeps its size and modified time."""
    (root / b).parent.mkdir(parents=True, exist_ok=True)
    os.rename(root / a, root / b)


def test_missing_file_comes_back_as_it_was(movies, tmdb):
    sid, root = movies
    library.scan_all()
    heat = db.one("SELECT id FROM vod_titles WHERE name='Heat'")["id"]
    _move(root, "Heat (1995)", "../Elsewhere/Heat (1995)")
    library.scan_share(library.share(sid))
    assert heat not in [t["id"] for t in vod.titles("movie")["items"]]
    _move(root, "../Elsewhere/Heat (1995)", "Heat (1995)")
    assert library.scan_share(library.share(sid)) == "5 movies"
    back = db.one("SELECT * FROM vod_titles WHERE id=?", (heat,))
    assert back["hidden"] == 0 and back["plot"] == "Plot 949" and library.counts()["missing"] == 0


def test_renamed_movie_keeps_progress_list_and_match(movies, tmdb):
    sid, root = movies
    library.scan_all()
    old = db.one("SELECT id FROM vod_titles WHERE name='Heat'")["id"]
    vod.set_progress(old, 600, 6000)
    db.run("INSERT INTO vod_list(title_id, added) VALUES (?, 1)", (old,))
    calls = len(tmdb)
    _move(root, "Heat (1995)/Heat.mp4", "Heat (1995) [1080p]/Heat.1995.1080p.mp4")      # same name, new folder
    assert library.scan_share(library.share(sid)) == "5 movies; 1 renamed or moved"
    new = db.one("SELECT * FROM vod_titles WHERE url LIKE 'Heat (1995) [1080p]/%'")
    assert new["id"] != old and new["plot"] == "Plot 949" and new["hidden"] == 0
    assert db.one("SELECT item_id, position FROM vod_progress") == {"item_id": new["id"], "position": 600}
    assert db.one("SELECT title_id FROM vod_list")["title_id"] == new["id"]
    assert db.one("SELECT state, tmdb_id FROM lib_meta WHERE title_id=?", (new["id"],)) == {"state": "ok", "tmdb_id": 949}
    assert not db.one("SELECT 1 FROM vod_titles WHERE id=?", (old,)) and library.counts()["missing"] == 0
    library.scan_all()
    assert len(tmdb) == calls                     # nothing looked up again


def test_renamed_to_fix_a_match_is_looked_up_again(movies, tmdb):
    sid, root = movies
    library.scan_all()
    loose = db.one("SELECT id FROM vod_titles WHERE url LIKE 'Loose%'")["id"]
    vod.set_progress(loose, 60, 6000)
    _move(root, "Loose.Movie.2010.720p.mkv", "Heat (1995) copy/Heat.1995.mkv")
    library.scan_share(library.share(sid))
    new = db.one("SELECT id FROM vod_titles WHERE url LIKE 'Heat (1995) copy/%'")["id"]
    assert db.one("SELECT item_id FROM vod_progress")["item_id"] == new           # where you stopped follows
    assert not db.one("SELECT 1 FROM lib_meta WHERE title_id=?", (new,))         # the old wrong match doesn't
    library.fetch_details()
    assert db.one("SELECT state, tmdb_id FROM lib_meta WHERE title_id=?", (new,))["tmdb_id"] == 949


def test_renamed_show_folder_keeps_episode_progress(shows):
    sid, root = shows
    library.scan_share(library.share(sid))
    ep = db.one("SELECT id FROM vod_episodes WHERE season=2 AND episode=4")["id"]
    vod.set_progress(ep, 300, 3000)
    _move(root, "Severance (2022)", "Severance")
    assert "1 renamed or moved" in library.scan_share(library.share(sid))
    new_ep = db.one("SELECT e.id, e.title_id FROM vod_episodes e JOIN vod_titles t ON t.id=e.title_id "
                    "WHERE t.name='Severance' AND season=2 AND episode=4")
    assert new_ep["id"] != ep
    assert db.one("SELECT item_id, title_id FROM vod_progress") == {"item_id": new_ep["id"], "title_id": new_ep["title_id"]}


def test_two_ways_to_match_is_not_a_move(tmp_path):
    root = tmp_path / "M"
    _touch(root, "A (2001)/a.mkv")
    _touch(root, "B (2002)/b.mkv")
    for p in root.rglob("*.mkv"):
        os.utime(p, (1_600_000_000, 1_600_000_000))       # same size, same time: can't tell them apart
    sid = library.add({"kind": "movie", "location": str(root)})
    library.scan_share(library.share(sid))
    _move(root, "A (2001)", "C (2003)")
    _move(root, "B (2002)", "D (2004)")
    assert library.scan_share(library.share(sid)) == "2 movies; 2 new, 2 missing"


def test_empty_folder_keeps_titles(movies):
    """A drive that isn't mounted looks like an empty folder: nothing is hidden or removed."""
    sid, root = movies
    library.scan_share(library.share(sid))
    for p in sorted(root.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    with pytest.raises(library.LibraryError, match="Found no videos"):
        library.scan_share(library.share(sid))
    assert vod.titles("movie")["total"] == 5 and library.share(sid)["status"].startswith("error: Found no videos")


def test_remove_at_once_when_set_to_zero(movies):
    sid, root = movies
    library.save_settings({"remove_after_days": 0})
    library.scan_share(library.share(sid))
    (root / "Heat (1995)/Heat.mp4").unlink()
    library.scan_share(library.share(sid))
    assert not db.one("SELECT 1 FROM vod_titles WHERE name='Heat'") and library.counts()["missing"] == 0


def test_measure_lengths(movies, monkeypatch):
    sid, _ = movies
    library.scan_share(library.share(sid))
    monkeypatch.setattr(vod, "probe_duration", lambda url: 5400)
    assert library.measure_lengths() == "Measured 5 videos"
    assert {r["duration"] for r in db.rows("SELECT duration FROM vod_titles WHERE source_id=?", (-sid,))} == {5400}
    assert library.measure_lengths() == "Every video's length is known"
    db.run("UPDATE vod_titles SET duration=0")
    monkeypatch.setattr(vod, "probe_duration", lambda url: 0)
    assert library.measure_lengths() == "Every video's length is known"          # tried today: not again yet


def test_tidy(movies):
    sid, _ = movies
    library.scan_share(library.share(sid))
    heat = db.one("SELECT id FROM vod_titles WHERE name='Heat'")["id"]
    vod.set_progress(heat, 600, 6000)
    db.run("INSERT INTO vod_progress(item_id, title_id, position, updated) VALUES ('L999:mgone', 'L999:mgone', 10, ?)",
           (int(time.time()),))                                     # a library that was removed
    db.run("INSERT INTO vod_list(title_id, added) VALUES ('L999:mgone', 1)")
    sub = tracks._cache_dir() / f"{tracks._key('L999:mgone')}-s0.json"
    sub.write_text("[]")
    kept = tracks._cache_dir() / f"{tracks._key(heat)}-s0.json"
    kept.write_text("[]")
    msg = library.tidy()
    assert msg == "Removed 1 saved position, 1 My List entry, 1 cached subtitle file"
    assert not sub.exists() and kept.exists()
    assert db.one("SELECT item_id FROM vod_progress")["item_id"] == heat
    assert library.tidy() == "Nothing to clean up"


# ---------------------------------------------------------------- automatic tasks
def test_tasks_are_set_up_and_due_in_order(movies, monkeypatch):
    rows = {t["key"]: t for t in libtasks.listing()}
    assert list(rows) == ["scan", "details", "lengths", "remove"]
    assert rows["scan"]["every_hours"] == 0.5 and all(t["enabled"] for t in rows.values())
    # all start at 4 am
    assert [rows[k]["at_hour"] for k in rows] == [-1, 4, 4, 4]
    db.run("UPDATE lib_tasks SET at_hour=-1")
    now = time.time()
    assert libtasks.due(now) == "scan"
    monkeypatch.setattr(vod, "probe_duration", lambda url: 60)
    assert libtasks.run("scan") == "NAS Movies: 5 movies"
    assert libtasks.due(now) == "details"
    assert libtasks.run("details") == "Skipped: no TMDB key"
    assert libtasks.run("lengths") == "Measured 5 videos"
    assert libtasks.run("remove") == "Nothing to remove"
    assert libtasks.due(now) is None and libtasks.due(now + 6 * 3600 + 1) == "scan"
    t = {t["key"]: t for t in libtasks.listing()}["scan"]
    assert t["status"] == "ok: NAS Movies: 5 movies" and t["next_run"] >= t["last_run"] + 1800 - 1
    libtasks.update("scan", {"enabled": False})
    assert libtasks.due(now + 7 * 3600) is None
    with pytest.raises(ValueError):
        libtasks.update("scan", {"every_hours": 0})


def test_task_failure_is_noted(movies, monkeypatch):
    def boom(progress):
        raise library.LibraryError("NAS is off")
    monkeypatch.setitem(libtasks.RUN, "scan", boom)
    with pytest.raises(library.LibraryError):
        libtasks.run("scan")
    t = libtasks.listing()[0]
    assert t["status"] == "error: NAS is off" and t["last_run"] > 0 and libtasks.due() != "scan"


def test_nothing_due_without_a_library():
    assert libtasks.due() is None


def test_old_scan_setting_carries_over():
    db.run("DELETE FROM lib_tasks")
    library.save_settings({"auto_scan_hours": 0})
    libtasks.init()
    assert {t["key"]: t for t in libtasks.listing()}["scan"]["enabled"] == 0
    db.run("DELETE FROM lib_tasks")
    library.save_settings({"auto_scan_hours": 12})
    libtasks.init()
    assert {t["key"]: t for t in libtasks.listing()}["scan"]["every_hours"] == 12


def test_api_tasks(movies, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "library_task", lambda key: started.append(key) or True)
    c = TestClient(api.app)
    assert [t["key"] for t in c.get("/api/library").json()["tasks"]][0] == "scan"
    r = c.put("/api/library/tasks/details", json={"every_hours": 12, "enabled": False})
    assert r.status_code == 200 and {t["key"]: t for t in r.json()}["details"]["every_hours"] == 12
    assert c.put("/api/library/tasks/details", json={"every_hours": "x"}).status_code == 400
    assert c.put("/api/library/tasks/nope", json={}).status_code == 404
    assert c.post("/api/library/tasks/remove/run").json() == {"job": True} and started == ["remove"]
    monkeypatch.setattr(jobs, "library_task", lambda key: "queued")
    monkeypatch.setattr(jobs, "running_what", lambda: "the guide update")
    assert c.post("/api/library/tasks/remove/run").json() == {"job": True, "queued": True, "after": "the guide update"}
    assert c.post("/api/library/tasks/nope/run").status_code == 404


# ---------------------------------------------------------------- quick scans, New in My Library, quiet hours
def _age(root, days=3):
    """Every file and folder under `root` last changed `days` ago (settled: a quick scan can trust them)."""
    t = time.time() - days * 86400
    for p in [root, *root.rglob("*")]:
        os.utime(p, (t, t))


def _count_reads(monkeypatch):
    reads = []
    real = library.FS.listdir
    monkeypatch.setattr(library.FS, "listdir", lambda self, rel="": reads.append(rel) or real(self, rel))
    return reads


def test_quick_scan_skips_unchanged_folders_and_sees_new_files(movies, monkeypatch):
    sid, root = movies
    _age(root)
    library.scan_share(library.share(sid))
    reads = _count_reads(monkeypatch)
    msg = library.scan_share(library.share(sid), quick=True)
    assert msg == "5 movies; no changes"
    assert "Heat (1995)" not in reads and "" in reads           # the top folder is always read
    # a new file in a movie's folder changes that folder's time: it's read again, and nothing else
    reads.clear()
    _touch(root, "Heat (1995)/Heat.Directors.Cut.mkv", 9000)
    msg = library.scan_share(library.share(sid), quick=True)
    assert "Heat (1995)" in reads and "Collection" not in reads
    assert db.one("SELECT url FROM vod_titles WHERE id=?", (library.title_id(sid, "movie", "Heat (1995)"),))["url"].endswith("Cut.mkv")
    # a full scan reads everything
    reads.clear()
    library.scan_share(library.share(sid))
    assert "Collection" in reads


def test_quick_scan_reads_a_folder_again_until_it_settles(movies, monkeypatch):
    sid, root = movies
    library.scan_share(library.share(sid))           # files just written: not trusted yet
    reads = _count_reads(monkeypatch)
    library.scan_share(library.share(sid), quick=True)
    assert "Collection" in reads                     # read again, and found the same
    reads.clear()
    library.scan_share(library.share(sid), quick=True)
    assert "Collection" not in reads


def test_quick_scan_reads_folders_again_after_a_day(movies, monkeypatch):
    sid, root = movies
    _age(root)
    library.scan_share(library.share(sid))
    db.run("UPDATE lib_dirs SET listed_at=listed_at-? WHERE share_id=?", (library.DIRS_MAX_AGE + 1, sid))
    reads = _count_reads(monkeypatch)
    library.scan_share(library.share(sid), quick=True)
    assert "Collection" in reads


def test_folder_list_forgotten_when_the_library_moves(movies, tmp_path):
    sid, root = movies
    library.scan_share(library.share(sid))
    assert db.one("SELECT count(*) n FROM lib_dirs WHERE share_id=?", (sid,))["n"]
    library.update(sid, {**library.share(sid), "location": str(tmp_path)})
    assert not db.one("SELECT count(*) n FROM lib_dirs WHERE share_id=?", (sid,))["n"]


def test_added_is_when_it_turned_up(movies, shows):
    sid, root = movies
    _age(root, days=400)
    library.scan_share(library.share(sid))           # first scan: the files' dates
    heat = library.title_id(sid, "movie", "Heat (1995)")
    assert db.one("SELECT added FROM vod_titles WHERE id=?", (heat,))["added"] < time.time() - 300 * 86400
    p = _touch(root, "Old Copy (1990)/Old Copy (1990).mkv", 7777)
    os.utime(p, (time.time() - 900 * 86400,) * 2)    # copied with its old date
    library.scan_share(library.share(sid))
    new = library.title_id(sid, "movie", "Old Copy (1990)")
    assert db.one("SELECT added FROM vod_titles WHERE id=?", (new,))["added"] >= time.time() - 60
    assert db.one("SELECT added FROM vod_titles WHERE id=?", (heat,))["added"] < time.time() - 300 * 86400
    assert [t["id"] for t in vod.library_new("movie")] == [new]
    assert vod.categories("movie")["lib_new"] == 1
    assert [t["id"] for t in vod.titles("movie", "*libnew")["items"]] == [new]
    # a show with a new episode comes up too
    tsid, troot = shows
    _age(troot, days=400)
    library.scan_share(library.share(tsid))
    assert not vod.library_new("series")
    _touch(troot, "Severance (2022)/Season 02/Severance - S02E05.mkv", 3333)
    library.scan_share(library.share(tsid))
    assert [t["name"] for t in vod.library_new("series")] == ["Severance"]


def test_home_shows_new_in_my_library(movies):
    from tunerbox import home
    sid, root = movies
    _age(root, days=400)
    library.scan_share(library.share(sid))
    _touch(root, "Fresh (2025)/Fresh (2025).mkv", 4444)
    library.scan_share(library.share(sid))
    rows = home.overview()["library_new"]
    assert [r["name"] for r in rows] == ["Fresh"] and rows[0]["type"] == "vod"
    db.run("INSERT INTO vod_progress(item_id, title_id, position, duration, watched, updated) VALUES (?,?,?,?,1,?)",
           (rows[0]["id"], rows[0]["id"], 10, 100, int(time.time())))
    assert home.overview()["library_new"] == []      # watched: off Home, still in On Demand
    assert len(vod.library_new("movie")) == 1


def test_scan_task_is_quick(movies, monkeypatch):
    seen = {}
    monkeypatch.setattr(library, "scan_all", lambda progress=None, **k: seen.update(k) or "ok")
    libtasks.RUN["scan"](None)
    assert seen.get("quick") is True


def _at(day, h, m=0):
    return time.mktime((2026, 11, day, h, m, 0, 0, 0, -1))


def _ran(key, t):
    db.run("UPDATE lib_tasks SET last_run=? WHERE key=?", (int(t), key))


def _only(key):
    db.run("UPDATE lib_tasks SET enabled=0")
    db.run("UPDATE lib_tasks SET enabled=1 WHERE key=?", (key,))


def test_task_at_a_set_time_daily(movies):
    _only("details")
    libtasks.update("details", {"every_hours": 24, "at_hour": 4})
    _ran("details", _at(17, 4, 5))                     # ran yesterday at 4:05
    assert libtasks.due(_at(18, 3, 50)) is None
    assert libtasks.due(_at(18, 4, 0)) == "details"
    assert libtasks.due(_at(18, 5, 30)) == "details"   # missed (TunerBox was off): made up soon after
    assert libtasks.due(_at(18, 9, 0)) is None         # too late: waits for tomorrow's 4 am
    r = libtasks._row("details")
    assert libtasks.next_run(r, _at(18, 9, 0)) == _at(19, 4)
    _ran("details", _at(18, 15, 0))                    # Run now in the afternoon: 4 am still runs
    assert libtasks.due(_at(19, 4, 1)) == "details"


def test_task_every_6_hours_from_4(movies):
    _only("scan")
    libtasks.update("scan", {"every_hours": 6, "at_hour": 4})
    assert libtasks._slot_hours(libtasks._row("scan")) == {4, 10, 16, 22}
    _ran("scan", _at(18, 10, 2))
    assert libtasks.due(_at(18, 12, 0)) is None
    assert libtasks.due(_at(18, 16, 0)) == "scan"
    assert libtasks.next_run(libtasks._row("scan"), _at(18, 12, 0)) == _at(18, 16)


def test_task_weekly_at_a_set_time(movies):
    _only("remove")
    libtasks.update("remove", {"every_hours": 168, "at_hour": 4})
    _ran("remove", _at(9, 4, 10))
    assert libtasks.due(_at(15, 4, 0)) is None          # 6 days: not yet
    assert libtasks.due(_at(16, 4, 0)) == "remove"        # 7 days, at 4 am
    assert libtasks.next_run(libtasks._row("remove"), _at(12, 12, 0)) == _at(16, 4)


def test_run_all(movies, monkeypatch):
    ran = []
    for k in libtasks.KEYS:
        monkeypatch.setitem(libtasks.RUN, k, lambda p, k=k: ran.append(k) or f"{k} done")
    def boom(p):
        raise RuntimeError("NAS asleep")
    monkeypatch.setitem(libtasks.RUN, "lengths", boom)
    libtasks.update("details", {"enabled": False})
    msg = libtasks.run_all()
    assert ran == ["scan", "remove"]                               # in order; the one that's off is left out
    assert msg == "Ran 2 of 3 tasks. Failed: Measure video lengths: NAS asleep"
    assert libtasks._row("lengths")["status"] == "error: NAS asleep"
    assert libtasks._row("scan")["status"] == "ok: scan done"


def test_api_run_all(movies, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "library_tasks_all", lambda: started.append(1) or True)
    r = TestClient(api.app).post("/api/library/tasks/run-all")
    assert r.status_code == 200 and r.json() == {"job": True} and started


def test_set_time_checked(movies):
    with pytest.raises(ValueError):
        libtasks.update("scan", {"at_hour": 24})
    assert libtasks.update("scan", {"at_hour": ""})[0]["at_hour"] == -1


def test_weekly_on_a_day(movies):
    _only("remove")                                      # Nov 2026: the 15th is a Sunday, the 17th a Tuesday
    libtasks.update("remove", {"every_hours": 168, "on_day": 6, "at_hour": 4})
    _ran("remove", _at(10, 9, 0))                        # last ran Tuesday (Run now): still runs this Sunday
    assert libtasks.due(_at(14, 4, 0)) is None         # Saturday
    assert libtasks.due(_at(15, 4, 0)) == "remove"
    assert libtasks.due(_at(15, 9, 0)) is None         # missed by more than 4 hours: next Sunday
    _ran("remove", _at(15, 4, 10))
    assert libtasks.next_run(libtasks._row("remove"), _at(15, 12, 0)) == _at(22, 4)


def test_weekly_on_a_day_any_time(movies):
    _only("remove")
    libtasks.update("remove", {"every_hours": 168, "on_day": 1, "at_hour": -1})   # Tuesdays
    _ran("remove", _at(10, 1, 0))
    assert libtasks.due(_at(16, 23, 0)) is None
    assert libtasks.due(_at(17, 15, 0)) == "remove"      # any time on Tuesday
    _ran("remove", _at(17, 15, 5))
    assert libtasks.due(_at(17, 20, 0)) is None        # once a Tuesday
    assert libtasks.next_run(libtasks._row("remove"), _at(17, 20, 0)) == _at(24, 0)


def test_day_only_for_weekly(movies):
    with pytest.raises(ValueError):
        libtasks.update("remove", {"every_hours": 168, "on_day": 7})
    with pytest.raises(ValueError):
        libtasks.update("remove", {"every_hours": 720})          # every 30 days is gone
    assert {t["key"]: t for t in libtasks.update("details", {"every_hours": 24, "on_day": 3})}["details"]["on_day"] == -1


def test_every_30_days_becomes_weekly():
    db.run("UPDATE lib_tasks SET every_hours=720 WHERE key='remove'")
    libtasks.init()
    assert libtasks._row("remove")["every_hours"] == 168


def test_scan_now_is_quick_and_one_library_is_full(movies, monkeypatch):
    seen = []
    monkeypatch.setattr(library, "scan_all", lambda progress=None, share_id=None, full_refresh=False, only_new=False,
                        quick=False: seen.append((share_id, quick)) or "ok")
    monkeypatch.setattr(jobs, "_run", lambda name, fn, queue=False: fn() and True)
    jobs.library_scan()
    jobs.library_scan(movies[0])
    assert seen == [(None, True), (movies[0], False)]



def test_clean_up_task_folded_into_remove(movies):
    db.run("UPDATE lib_tasks SET enabled=0, every_hours=6 WHERE key='remove'")
    db.run("INSERT INTO lib_tasks(key, enabled, every_hours) VALUES ('tidy', 1, 1)")
    libtasks.init()
    rows = {t["key"]: t for t in libtasks.listing()}
    assert "tidy" not in rows and rows["remove"]["enabled"] == 0 and rows["remove"]["every_hours"] == 6


def test_remove_task_also_cleans_up(movies):
    sid, root = movies
    library.scan_share(library.share(sid))
    db.run("INSERT INTO vod_progress(item_id, title_id, position, duration, updated) VALUES ('L999:mgone', 'L999:mgone', 5, 50, 1)")
    assert libtasks.run("remove") == "Nothing to remove; cleaned up 1 saved position"



def test_any_time_tasks_move_to_4am_once(movies):
    db.run("UPDATE lib_tasks SET at_hour=-1")
    db.run("UPDATE lib_tasks SET every_hours=6 WHERE key='scan'")
    db.run("UPDATE lib_tasks SET at_hour=7 WHERE key='details'")
    db.run("DELETE FROM meta WHERE k='lib_tasks_at_default'")
    libtasks.init()
    rows = {t["key"]: t["at_hour"] for t in libtasks.listing()}
    assert rows == {"scan": 4, "details": 7, "lengths": 4, "remove": 4}          # a time you picked is kept
    libtasks.update("scan", {"at_hour": -1})
    libtasks.init()
    assert {t["key"]: t["at_hour"] for t in libtasks.listing()}["scan"] == -1    # only once


def test_tasks_queued_at_4am_still_run_after_long_ones(movies):
    _only("remove")
    _ran("remove", _at(17, 4, 5))
    assert libtasks.due(_at(18, 7, 30)) == "remove"     # the tasks before it ran 3 hours: it still runs
    assert libtasks.due(_at(18, 8, 30)) is None


def test_reset_tasks_to_defaults(movies):
    libtasks.update("scan", {"enabled": False, "every_hours": 1})
    libtasks.update("remove", {"every_hours": 168, "on_day": 2, "at_hour": 9})
    library.save_settings({"remove_after_days": 7})
    _ran("remove", 1234)
    r = TestClient(api.app).post("/api/library/tasks/reset")
    assert r.status_code == 200
    rows = {t["key"]: t for t in r.json()}
    assert [(t["enabled"], t["every_hours"], t["at_hour"], t["on_day"]) for t in rows.values()] == \
        [(1, 0.5, -1, -1), (1, 24, 4, -1), (1, 24, 4, -1), (1, 24, 4, -1)]
    assert library.settings()["remove_after_days"] == 30 and rows["remove"]["last_run"] == 1234



def test_every_30_minutes(movies):
    _only("scan")
    libtasks.update("scan", {"every_hours": 0.5, "at_hour": 4})     # a time means nothing this often
    r = libtasks._row("scan")
    assert r["every_hours"] == 0.5 and not libtasks.scheduled(r)
    _ran("scan", _at(18, 10, 0))
    assert libtasks.due(_at(18, 10, 20)) is None and libtasks.due(_at(18, 10, 30)) == "scan"
    assert libtasks.next_run(libtasks._row("scan"), _at(18, 10, 5)) == _at(18, 10, 30)
    for bad in (0.25, 1.5, 0, 169):
        with pytest.raises(ValueError):
            libtasks.update("scan", {"every_hours": bad})


def test_quick_scan_writes_only_changed_folders(movies, monkeypatch):
    sid, root = movies
    _age(root)
    library.scan_share(library.share(sid))
    library.scan_share(library.share(sid), quick=True)
    before = {r["rel"]: r["listed_at"] for r in db.rows("SELECT rel, listed_at FROM lib_dirs WHERE share_id=?", (sid,))}
    writes = []
    real = db.conn
    class Spy:
        def __init__(self): self.c = real()
        def __enter__(self):
            con = self.c.__enter__()
            con.set_trace_callback(lambda sql: "lib_dirs" in sql and ("INSERT" in sql or "DELETE" in sql) and writes.append(sql))
            return con
        def __exit__(self, *a):
            return self.c.__exit__(*a)
    monkeypatch.setattr(db, "conn", Spy)
    library.scan_share(library.share(sid), quick=True)          # only the top folder is read: at most it is written
    assert len(writes) <= 1
    monkeypatch.undo()
    after = {r["rel"]: r["listed_at"] for r in db.rows("SELECT rel, listed_at FROM lib_dirs WHERE share_id=?", (sid,))}
    assert set(after) == set(before) and after["Collection"] == before["Collection"]


def test_deleted_folder_leaves_the_folder_list(movies):
    import shutil
    sid, root = movies
    library.scan_share(library.share(sid))
    shutil.rmtree(root / "Heat (1995)")
    library.scan_share(library.share(sid))
    assert not db.one("SELECT 1 FROM lib_dirs WHERE share_id=? AND rel='Heat (1995)'", (sid,))
    assert db.one("SELECT 1 FROM lib_dirs WHERE share_id=? AND rel='Collection'", (sid,))
