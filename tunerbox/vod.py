"""On Demand: the movies and TV shows that come with a playlist.

Xtream logins list them through the API (get_vod_streams, get_series); the details of one title
(plot, cast, a show's episodes) are only fetched when it is opened. In a plain M3U playlist, entries
whose address is a video file (.mp4, .mkv…) are titles instead of channels once it is turned on.

Playing goes through ffmpeg like a live channel (so MKV, AC-3 sound and the playlist's video setting
all work); skipping starts a new stream at that time (`stream_cmd`).
"""
import hashlib
import json
import logging
import re
import os
import subprocess
import threading
import time
import unicodedata
from urllib.parse import urlparse

import httpx
from rapidfuzz import fuzz, process

from . import config, db, sources
from .streaming import NO_WINDOW, ffmpeg_bin, input_args, play_args

log = logging.getLogger("tunerbox.vod")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vod_titles (
    id TEXT PRIMARY KEY,           -- <source id>:m<movie> or <source id>:s<show>, stable across scans
    source_id INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- movie | series
    xid TEXT DEFAULT '',           -- Xtream stream_id / series_id
    name TEXT NOT NULL,
    category TEXT DEFAULT '',
    year TEXT DEFAULT '',
    rating TEXT DEFAULT '',
    genre TEXT DEFAULT '',
    plot TEXT DEFAULT '',
    actors TEXT DEFAULT '',
    director TEXT DEFAULT '',
    poster TEXT DEFAULT '',
    duration INTEGER DEFAULT 0,    -- seconds, 0 = not known yet
    ext TEXT DEFAULT '',
    url TEXT DEFAULT '',           -- M3U only: Xtream addresses are built from the login when played
    added INTEGER DEFAULT 0,
    info_at INTEGER DEFAULT 0,     -- when the details were fetched (Xtream)
    seen_at INTEGER DEFAULT 0,
    search TEXT,                   -- the name folded for matching what people type
    hidden INTEGER DEFAULT 0,      -- its category is unticked in Setup: kept, so ticking it again is instant
    tag TEXT DEFAULT '',           -- the provider's label in front of the name: "EN", "4K-FR", "NF" (see split_tag)
    country TEXT DEFAULT ''        -- a show's country from the end of its name: "(US)" -> "US"
);
CREATE INDEX IF NOT EXISTS ix_vod_category ON vod_titles(source_id, kind, category);
CREATE TABLE IF NOT EXISTS vod_episodes (
    id TEXT PRIMARY KEY,           -- <source id>:e<episode>
    title_id TEXT NOT NULL,
    xid TEXT DEFAULT '',
    season INTEGER DEFAULT 1,
    episode INTEGER DEFAULT 0,
    name TEXT DEFAULT '',
    plot TEXT DEFAULT '',
    duration INTEGER DEFAULT 0,
    ext TEXT DEFAULT '',
    url TEXT DEFAULT '',
    aired TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_vod_episodes ON vod_episodes(title_id, season, episode);
CREATE TABLE IF NOT EXISTS vod_progress (
    item_id TEXT PRIMARY KEY,      -- a movie's title id, or an episode id
    title_id TEXT NOT NULL,
    position REAL DEFAULT 0,
    duration REAL DEFAULT 0,
    watched INTEGER DEFAULT 0,
    updated INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS vod_list (title_id TEXT PRIMARY KEY, added INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS vod_cats (     -- every category of a playlist, shown or not
    source_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (source_id, kind, name)
);
"""

KINDS = ("movie", "series")
VIDEO_FILE = re.compile(r"\.(mp4|mkv|m4v|avi|mov|wmv|flv|mpg|mpeg|webm)$", re.I)
YEAR = re.compile(r"\s*[\(\[]((?:19|20)\d{2})[\)\]]\s*$")
EPISODE = re.compile(r"^(.*?)[\s._-]*\bS(\d{1,2})[\s._-]*E(\d{1,3})\b[\s._:-]*(.*)$", re.I)
DUR = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
NEW_DAYS = 14                     # "NEW" on a title added this recently
RECENT = 60                       # titles in Recently Added
RESUME_MIN = 30                   # seconds in before a title counts as started
WATCHED_AT = 0.92                 # this far through = watched
INFO_TTL = 30 * 86400


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)
        db._add_column(c, "vod_titles", "hidden", "INTEGER DEFAULT 0")     # test builds made the table without it
        db._add_column(c, "vod_titles", "tag", "TEXT DEFAULT ''")         # 1.5: language / platform labels
        db._add_column(c, "vod_titles", "country", "TEXT DEFAULT ''")
        for old in ("ix_vod_titles", "ix_vod_lang"):                       # indexes from test builds
            c.execute(f"DROP INDEX IF EXISTS {old}")
        c.execute("CREATE INDEX IF NOT EXISTS ix_vod_browse ON vod_titles(kind, hidden, name COLLATE NOCASE)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_vod_same ON vod_titles(kind, search, year)")
    _changed()


_names = []          # ([ids], [folded names]) for typo-tolerant search; emptied on any change
_tag_rank = {}       # tag -> how many titles have it (picks which version leads a search result)


def _changed():
    _names.clear()
    _tag_rank.clear()


# ---------------------------------------------------------------- small helpers
def fold(s):
    """'Amélie, ou le Fabuleux…' -> 'amelie ou le fabuleux': for matching what people type."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower().replace("&", " and ")
    return re.sub(r"[\W_]+", " ", s).strip()


def _s(v):
    return str(v).strip() if v not in (None, [], {}) else ""


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _hash(text):
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]


def _split_year(name):
    m = YEAR.search(name)
    return (name[:m.start()].rstrip(" -"), m.group(1)) if m and m.start() else (name, "")


# Most big providers put a label in front of every title: a language ("EN - Dune", "DE - …"), a quality and
# language ("4K-FR - …", "IN-EN - …") or a platform ("NF + …", "4K-D+ - …"). It is cut off the name and kept
# as the title's tag, so On Demand can show only the languages you pick and list one Dune instead of six.
TAG = re.compile(r"^\s*([A-Za-z0-9+]{1,5}(?:-[A-Za-z0-9+]{1,5}){0,2})\s*(?:\s-|-\s|\||:|\+)\s*(?=\S)")
TAG_MIN = 5                       # a label must start at least this many names: "X-Men - …" alone isn't one
COUNTRY = re.compile(r"\s*\(([A-Z]{2})\)\s*$")


def split_tag(name):
    """'EN - Dune (2021)' -> ('EN', 'Dune (2021)'); ('', name) when there's no label."""
    m = TAG.match(name or "")
    # a label is in capitals ("Nf" too short to tell): "Alien: Romulus" keeps its name
    if (not m or not re.search(r"[A-Za-z]", m.group(1)) or not name[m.end():].strip()
            or (len(m.group(1)) > 2 and m.group(1) != m.group(1).upper())):
        return "", name
    return m.group(1).upper(), name[m.end():].strip()


def _split_country(name):
    """'Phony (2026) (US)' -> ('Phony (2026)', 'US')."""
    m = COUNTRY.search(name or "")
    return (name[:m.start()].rstrip(), m.group(1)) if m and m.start() else (name, "")


def _label(rows):
    """Cut the labels off the names (only labels shared by TAG_MIN or more titles) and the country off shows."""
    found = [split_tag(r["name"]) for r in rows]
    seen = {}
    for tag, _ in found:
        if tag:
            seen[tag] = seen.get(tag, 0) + 1
    for r, (tag, rest) in zip(rows, found):
        r.setdefault("tag", "")
        if tag and seen[tag] >= TAG_MIN:
            r["name"], r["tag"] = rest, tag
        r["name"], country = _split_country(r["name"])
        r["country"] = r.get("country") or country
        name, year = _split_year(r["name"])          # "Breaking Bad (2008)" with year 2008: once is enough
        if year and (not r.get("year") or r["year"] == year):
            r["name"], r["year"] = name, year


def _secs(info):
    """Length in seconds from Xtream's duration_secs / 'duration' (01:53:00) / episode_run_time (minutes)."""
    n = _int(info.get("duration_secs"))
    if n:
        return n
    parts = _s(info.get("duration")).split(":")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return _int(info.get("episode_run_time")) * 60


def is_file(url):
    """A playlist entry that is a video file (a movie or an episode), not a live channel."""
    path = urlparse((url or "").split("|")[0]).path
    return bool(VIDEO_FILE.search(path)) or "/movie/" in path or "/series/" in path


def hidden_cats(src):
    try:
        return {(k, n) for k, n in json.loads(src.get("vod_hidden") or "[]")}
    except (ValueError, TypeError):
        return set()


def _source(source_id):
    return db.one("SELECT * FROM sources WHERE id=?", (source_id,))


def enabled():
    return bool(db.one("SELECT 1 FROM sources WHERE enabled=1 AND (vod_movies=1 OR vod_series=1) LIMIT 1"))


# ---------------------------------------------------------------- reading the catalogue
_UPSERT = """INSERT INTO vod_titles(id, source_id, kind, xid, name, category, year, rating, genre, plot, actors,
                                    director, poster, duration, ext, url, added, seen_at, search, hidden, tag, country)
             VALUES (:id,:source_id,:kind,:xid,:name,:category,:year,:rating,:genre,:plot,:actors,
                     :director,:poster,:duration,:ext,:url,:added,:seen_at,:search,:hidden,:tag,:country)
             ON CONFLICT(id) DO UPDATE SET xid=excluded.xid, name=excluded.name, category=excluded.category,
               search=excluded.search, hidden=excluded.hidden, tag=excluded.tag, country=excluded.country,
               poster=excluded.poster, ext=excluded.ext, url=excluded.url, added=excluded.added, seen_at=excluded.seen_at,
               year=CASE WHEN excluded.year!='' THEN excluded.year ELSE vod_titles.year END,
               rating=CASE WHEN excluded.rating!='' THEN excluded.rating ELSE vod_titles.rating END,
               genre=CASE WHEN excluded.genre!='' THEN excluded.genre ELSE vod_titles.genre END,
               plot=CASE WHEN excluded.plot!='' THEN excluded.plot ELSE vod_titles.plot END,
               actors=CASE WHEN excluded.actors!='' THEN excluded.actors ELSE vod_titles.actors END,
               director=CASE WHEN excluded.director!='' THEN excluded.director ELSE vod_titles.director END,
               duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE vod_titles.duration END"""
_BLANK = {"tag": "", "country": "", "xid": "", "category": "", "year": "", "rating": "", "genre": "", "plot": "", "actors": "", "director": "",
          "poster": "", "duration": 0, "ext": "", "url": "", "added": 0}


def _store(src, kinds, rows):
    """Replace a playlist's titles of these kinds. Titles in unticked categories are kept but marked hidden.
    -> how many are shown."""
    hidden = hidden_cats(_source(src["id"]) or src)      # as it is now: it may have changed while the list downloaded
    now, counts, kept = int(time.time()), {}, []
    _label(rows)
    for r in rows:
        k = (r["kind"], r.get("category") or "")
        counts[k] = counts.get(k, 0) + 1
        kept.append({**_BLANK, **r, "search": fold(r["name"]), "source_id": src["id"], "seen_at": now,
                     "hidden": int(k in hidden)})
    q = ",".join("?" * len(kinds))
    with db.conn() as c:
        c.execute(f"DELETE FROM vod_cats WHERE source_id=? AND kind IN ({q})", (src["id"], *kinds))
        c.executemany("INSERT OR REPLACE INTO vod_cats(source_id, kind, name, count) VALUES (?,?,?,?)",
                      [(src["id"], k, n, cnt) for (k, n), cnt in counts.items()])
        c.executemany(_UPSERT, kept)
        c.execute(f"DELETE FROM vod_titles WHERE source_id=? AND kind IN ({q}) AND seen_at<?", (src["id"], *kinds, now))
        c.execute("DELETE FROM vod_episodes WHERE title_id NOT IN (SELECT id FROM vod_titles)")
    _changed()
    return sum(1 for r in kept if not r["hidden"])


def clear(source_id, kinds=KINDS):
    """Drop a playlist's titles (turned off, or the playlist was deleted). Progress and My List are kept
    in case it comes back."""
    q = ",".join("?" * len(kinds))
    with db.conn() as c:
        c.execute(f"DELETE FROM vod_titles WHERE source_id=? AND kind IN ({q})", (source_id, *kinds))
        c.execute(f"DELETE FROM vod_cats WHERE source_id=? AND kind IN ({q})", (source_id, *kinds))
        c.execute("DELETE FROM vod_episodes WHERE title_id NOT IN (SELECT id FROM vod_titles)")
    _changed()


def take_m3u(src, items):
    """Split a plain M3U playlist: -> the live channels. The video files become On Demand titles when
    that is turned on for the playlist (otherwise they stay channels, as before)."""
    files = [it for it in items if is_file(it["url"])]
    db.set_meta(f"vod_files_{src['id']}", len(files))
    if not src.get("vod_movies"):
        return items
    rx, hide = sources.exclude_regex(src), sources.hidden_groups(src)
    titles, episodes = {}, []
    for it in files:
        grp = it["grp"] or ""
        if grp in hide or (rx and rx.search(grp)):
            continue                    # left out as channels: left out here too
        m = EPISODE.match(it["name"])
        show = m.group(1).strip(" -:.") if m else ""
        if show:
            show, year = _split_year(show)
            tid = f"{src['id']}:s{_hash(show.lower())}"
            titles.setdefault(tid, {"id": tid, "kind": "series", "name": show, "year": year, "category": grp,
                                    "poster": it["logo"] or ""})
            season, ep = int(m.group(2)), int(m.group(3))
            episodes.append({"id": f"{src['id']}:e" + _hash(f"{show.lower()}|{season}|{ep}"), "title_id": tid,
                             "season": season, "episode": ep, "name": m.group(4).strip(), "url": it["url"]})
        else:
            name, year = _split_year(it["name"])
            tid = f"{src['id']}:m" + _hash(it["name"] + "|" + grp)
            titles[tid] = {"id": tid, "kind": "movie", "name": name, "year": year, "category": grp,
                           "poster": it["logo"] or "", "url": it["url"]}
    _store(src, KINDS, list(titles.values()))
    with db.conn() as c:
        c.execute("DELETE FROM vod_episodes WHERE title_id LIKE ?", (f"{src['id']}:s%",))
        c.executemany("""INSERT OR REPLACE INTO vod_episodes(id, title_id, season, episode, name, url)
                         SELECT :id, :title_id, :season, :episode, :name, :url
                         WHERE EXISTS (SELECT 1 FROM vod_titles WHERE id=:title_id)""", episodes)
    log.info("Source %s: %d On Demand titles", src["name"], len(titles))
    return [it for it in items if not is_file(it["url"])]


def _adult(s):
    """Marked as adult by the provider ('is_adult': 1) while adult titles are switched off in Settings."""
    return not config.get("vod_show_adult") and _int(s.get("is_adult")) == 1


def _early_cats(src, kind, names):
    """The category list arrives long before the titles: list it in Setup straight away."""
    with db.conn() as c:
        c.executemany("INSERT OR IGNORE INTO vod_cats(source_id, kind, name, count) VALUES (?,?,?,0)",
                      [(src["id"], kind, n) for n in names])


def refresh_xtream(src, kinds=KINDS):
    """Read an Xtream login's movie and show lists (those turned on, of `kinds`). -> titles shown."""
    n = 0
    if src.get("vod_movies") and "movie" in kinds:
        cats = {str(c.get("category_id")): _s(c.get("category_name"))
                for c in sources.xtream_api(src, "get_vod_categories") or []}
        _early_cats(src, "movie", cats.values())
        rows = []
        for s in sources.xtream_api(src, "get_vod_streams") or []:
            sid = s.get("stream_id")
            if sid is None or _adult(s):
                continue
            name, year = _split_year(_s(s.get("name")) or str(sid))
            rows.append({"id": f"{src['id']}:m{sid}", "kind": "movie", "xid": str(sid), "name": name,
                         "category": cats.get(str(s.get("category_id")), ""), "year": _s(s.get("year"))[:4] or year,
                         "rating": _s(s.get("rating")), "genre": _s(s.get("genre")), "plot": _s(s.get("plot")),
                         "actors": _s(s.get("cast")), "director": _s(s.get("director")),
                         "poster": _s(s.get("stream_icon")), "ext": _s(s.get("container_extension")) or "mp4",
                         "added": _int(s.get("added"))})
        n += _store(src, ("movie",), rows)
    if src.get("vod_series") and "series" in kinds:
        cats = {str(c.get("category_id")): _s(c.get("category_name"))
                for c in sources.xtream_api(src, "get_series_categories") or []}
        _early_cats(src, "series", cats.values())
        rows = []
        for s in sources.xtream_api(src, "get_series") or []:
            sid = s.get("series_id")
            if sid is None or _adult(s):
                continue
            name, year = _split_year(_s(s.get("name")) or str(sid))
            released = _s(s.get("releaseDate") or s.get("release_date"))
            rows.append({"id": f"{src['id']}:s{sid}", "kind": "series", "xid": str(sid), "name": name,
                         "category": cats.get(str(s.get("category_id")), ""), "year": released[:4] or year,
                         "rating": _s(s.get("rating")), "genre": _s(s.get("genre")), "plot": _s(s.get("plot")),
                         "actors": _s(s.get("cast")), "director": _s(s.get("director")),
                         "poster": _s(s.get("cover")), "added": _int(s.get("last_modified"))})
        n += _store(src, ("series",), rows)
    return n


def refresh_all(progress=None, source_id=None, kinds=KINDS):
    """Re-read the On Demand lists of every Xtream login that has them on (M3U playlists are split
    whenever the playlist itself is read)."""
    srcs = [s for s in sources.enabled_sources() if s["type"] == "xtream" and (s.get("vod_movies") or s.get("vod_series"))
            and (source_id is None or s["id"] == source_id)]
    for i, s in enumerate(srcs):
        if progress:
            progress(f"Loading On Demand from {s['name']}", i, len(srcs))
        try:
            n = refresh_xtream(s, kinds)
            log.info("Source %s: %d On Demand titles", s["name"], n)
        except Exception:  # noqa: BLE001
            log.exception("On Demand list of %s failed", s["name"])
    c = counts()
    return f"On Demand: {c['movie']['total']:,} movies, {c['series']['total']:,} shows"


# ---------------------------------------------------------------- details of one title (fetched when opened)
def _fetch_movie_info(src, t):
    d = sources.xtream_api(src, "get_vod_info", vod_id=t["xid"]) or {}
    info = d.get("info") if isinstance(d.get("info"), dict) else {}
    data = d.get("movie_data") if isinstance(d.get("movie_data"), dict) else {}
    released = _s(info.get("releasedate") or info.get("release_date"))
    db.run("""UPDATE vod_titles SET plot=?, actors=?, director=?, genre=?, year=?, rating=?, duration=?, poster=?, ext=?,
              info_at=? WHERE id=?""",
           (_s(info.get("plot") or info.get("description")) or t["plot"], _s(info.get("cast") or info.get("actors")) or t["actors"],
            _s(info.get("director")) or t["director"], _s(info.get("genre")) or t["genre"], released[:4] or t["year"],
            _s(info.get("rating")) or t["rating"], _secs(info) or t["duration"],
            t["poster"] or _s(info.get("movie_image") or info.get("cover_big")),
            _s(data.get("container_extension")) or t["ext"], int(time.time()), t["id"]))


def _fetch_series_info(src, t):
    d = sources.xtream_api(src, "get_series_info", series_id=t["xid"]) or {}
    info = d.get("info") if isinstance(d.get("info"), dict) else {}
    eps = d.get("episodes") or {}
    groups = eps.values() if isinstance(eps, dict) else eps
    rows = []
    for group in groups:
        for e in group if isinstance(group, list) else []:
            if e.get("id") is None:
                continue
            ei = e.get("info") if isinstance(e.get("info"), dict) else {}
            name = _s(e.get("title"))
            m = EPISODE.match(name)                 # "Show - S01E02 - The Title" -> "The Title"
            if m:
                name = m.group(4).strip()
            rows.append((f"{src['id']}:e{e['id']}", t["id"], str(e["id"]), _int(e.get("season")) or 1,
                         _int(e.get("episode_num")), name, _s(ei.get("plot")), _secs(ei),
                         _s(e.get("container_extension")) or "mp4", _s(ei.get("releasedate") or ei.get("air_date"))))
    released = _s(info.get("releaseDate") or info.get("release_date"))
    with db.conn() as c:
        if rows:
            c.execute("DELETE FROM vod_episodes WHERE title_id=?", (t["id"],))
            c.executemany("""INSERT OR REPLACE INTO vod_episodes(id, title_id, xid, season, episode, name, plot, duration, ext, aired)
                             VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
        c.execute("UPDATE vod_titles SET plot=?, actors=?, director=?, genre=?, year=?, rating=?, info_at=? WHERE id=?",
                  (_s(info.get("plot")) or t["plot"], _s(info.get("cast")) or t["actors"], _s(info.get("director")) or t["director"],
                   _s(info.get("genre")) or t["genre"], released[:4] or t["year"], _s(info.get("rating")) or t["rating"],
                   int(time.time()), t["id"]))


def _row(title_id):
    return db.one("""SELECT t.*, src.name AS source_name, src.type AS source_type FROM vod_titles t
                     JOIN sources src ON src.id=t.source_id WHERE t.id=?""", (title_id,))


def ep_label(e):
    return f"S{e['season']} E{e['episode']}" + (f" · {e['name']}" if e["name"] else "")


def title(title_id):
    """Everything about one movie or show: details, where you stopped, and a show's episodes."""
    t = _row(title_id)
    if not t:
        return None
    if t["source_type"] == "xtream" and time.time() - t["info_at"] > INFO_TTL:
        try:
            (_fetch_movie_info if t["kind"] == "movie" else _fetch_series_info)(_source(t["source_id"]), t)
            t = _row(title_id)
        except Exception as e:  # noqa: BLE001
            log.warning("No details for %s: %s", t["name"], e)
    out = {k: t[k] for k in ("id", "kind", "name", "category", "year", "rating", "genre", "plot", "actors", "director",
                             "poster", "duration", "source_name", "tag", "country")}
    out["versions"] = versions(t)
    out["in_list"] = bool(db.one("SELECT 1 FROM vod_list WHERE title_id=?", (title_id,)))
    prog = {p["item_id"]: p for p in db.rows("SELECT * FROM vod_progress WHERE title_id=?", (title_id,))}
    if t["kind"] == "movie":
        p = prog.get(title_id) or {}
        out.update(position=p.get("position", 0), watched=bool(p.get("watched")),
                   duration=t["duration"] or int(p.get("duration") or 0))
        return out
    eps = db.rows("SELECT id, season, episode, name, plot, duration, aired FROM vod_episodes WHERE title_id=? "
                  "ORDER BY season, episode, id", (title_id,))
    seasons, latest = {}, None
    for e in eps:
        p = prog.get(e["id"]) or {}
        e.update(position=p.get("position", 0), watched=bool(p.get("watched")),
                 duration=e["duration"] or int(p.get("duration") or 0))
        s = seasons.setdefault(e["season"], {"season": e["season"], "count": 0, "watched": 0})
        s["count"] += 1
        s["watched"] += e["watched"]
        if p and (latest is None or p["updated"] > prog[latest["id"]]["updated"]):
            latest = e
    # next up: the episode you're in the middle of, else the one after the last you watched, else the first
    nxt = eps[0]["id"] if eps else None
    if latest:
        i = eps.index(latest)
        nxt = latest["id"] if not latest["watched"] else (eps[i + 1]["id"] if i + 1 < len(eps) else None)
    out.update(episodes=eps, seasons=list(seasons.values()), next=nxt)
    return out


# ---------------------------------------------------------------- browsing
# titles of enabled playlists, in ticked categories, whose label is one of the picked ones
# (sources.vod_tags: a JSON list, '' = all)
_FROM = ("FROM vod_titles t JOIN sources src ON src.id=t.source_id AND src.enabled=1 AND t.hidden=0 "
         "AND (src.vod_tags='' OR instr(src.vod_tags, '\"' || t.tag || '\"')>0) ")
_COLS = "t.id, t.kind, t.name, t.year, t.rating, t.category, t.poster, t.added, t.duration, t.tag, t.country"


def _tile(r, now):
    dur = r.get("duration") or r.get("pdur") or 0
    pos = r.get("position") or 0
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "year": r["year"], "rating": r["rating"],
            "category": r["category"], "poster": r["poster"], "tag": r.get("tag") or "", "country": r.get("country") or "",
            "new": bool(r["added"] and now - r["added"] < NEW_DAYS * 86400), "watched": bool(r.get("watched")),
            "pct": round(min(100, pos / dur * 100)) if dur and pos > RESUME_MIN and not r.get("watched") else 0}


def counts():
    """How many movies and shows, and what's in progress / in My List, for each."""
    n = {r["kind"]: r["n"] for r in db.rows(f"SELECT t.kind, count(*) n {_FROM} GROUP BY t.kind")}
    listed = {r["kind"]: r["n"] for r in db.rows(f"SELECT t.kind, count(*) n {_FROM} JOIN vod_list l ON l.title_id=t.id "
                                                 "GROUP BY t.kind")}
    out = {"enabled": enabled(), "tags": db.one(f"SELECT count(DISTINCT t.tag) n {_FROM}")["n"]}
    for k in KINDS:
        out[k] = {"total": n.get(k, 0), "continue": len(continue_watching(k)), "list": listed.get(k, 0)}
    return out


def categories(kind):
    rows = db.rows(f"SELECT t.category AS name, count(*) AS count {_FROM} WHERE t.kind=? GROUP BY t.category "
                   "ORDER BY t.category COLLATE NOCASE", (kind,))
    recent = db.one(f"SELECT count(*) n {_FROM} WHERE t.kind=? AND t.added>0", (kind,))["n"]
    return {"total": sum(r["count"] for r in rows), "recent": min(RECENT, recent), "cats": rows}


def continue_watching(kind=None):
    """Titles you're part-way through, the latest first. A show appears once, at its latest episode."""
    w, a = (" AND t.kind=?", [kind]) if kind else ("", [])
    rows = db.rows(f"""SELECT {_COLS}, p.item_id, p.position, p.duration AS pdur, e.season, e.episode, e.name AS ep_name,
                              e.duration AS ep_duration
                       {_FROM} JOIN vod_progress p ON p.title_id=t.id LEFT JOIN vod_episodes e ON e.id=p.item_id
                       WHERE p.watched=0 AND p.position>?{w} ORDER BY p.updated DESC""", (RESUME_MIN, *a))
    out, seen, now = [], set(), int(time.time())
    for r in rows:
        if r["id"] in seen or (r["kind"] == "series" and r["season"] is None):
            continue
        seen.add(r["id"])
        if r["kind"] == "series":
            r["duration"] = r["ep_duration"]
        tile = _tile(r, now)
        tile["resume"] = r["item_id"]
        if r["kind"] == "series":
            tile["sub"] = ep_label({"season": r["season"], "episode": r["episode"], "name": r["ep_name"]})
        out.append(tile)
    return out


def titles(kind="movie", cat=None, tab="", offset=0, limit=120):
    """One page of tiles. cat None = everything of that kind, '*recent' = Recently Added.
    The Continue Watching and My List tabs have movies and shows together."""
    now = int(time.time())
    if tab == "continue":
        items = continue_watching()
        return {"total": len(items), "items": items[offset:offset + limit]}
    join = "LEFT JOIN vod_progress p ON p.item_id=t.id "
    cols = f"{_COLS}, p.position, p.duration AS pdur, p.watched"
    if tab == "list":
        total = db.one(f"SELECT count(*) n {_FROM} JOIN vod_list l ON l.title_id=t.id")["n"]
        rows = db.rows(f"SELECT {cols} {_FROM} JOIN vod_list l ON l.title_id=t.id {join}"
                       "ORDER BY l.added DESC LIMIT ? OFFSET ?", (limit, offset))
    elif cat == "*recent":
        rows = db.rows(f"SELECT {cols} {_FROM} {join} WHERE t.kind=? AND t.added>0 ORDER BY t.added DESC, t.name LIMIT ?",
                       (kind, RECENT))
        total, rows = len(rows), rows[offset:offset + limit]
    else:
        where, args = "t.kind=?", [kind]
        if cat is not None:
            where, args = where + " AND t.category=?", args + [cat]
        total = db.one(f"SELECT count(*) n {_FROM} WHERE {where}", args)["n"]
        rows = db.rows(f"SELECT {cols} {_FROM} {join} WHERE {where} ORDER BY t.name COLLATE NOCASE, t.id LIMIT ? OFFSET ?",
                       (*args, limit, offset))
    return {"total": total, "items": [_tile(r, now) for r in rows]}


# ---------------------------------------------------------------- search
YEAR_WORD = re.compile(r"^(19|20)\d\d$")
NAME_TAG = re.compile(r"^\s*[\[|]?\s*([A-Z0-9][A-Z0-9+/_.-]{0,14})\s*(?:\]|\|)?\s*(?:-\s|\|)")     # "EN - ", "|ES| ", "4K-DE - "


def _first_tags():
    """The codes from Settings > On Demand > List these languages first ("EN", "EN,FR")."""
    return {c.strip().upper() for c in str(config.get("vod_search_first") or "").split(",") if c.strip()}


def _tag_order(r, first):
    """0 for a title tagged with a preferred code ('EN', '4K-EN'), 1 for one with no tag, 2 for another
    code ('ES'), so the same film in several languages lists the wanted one first. The label cut off the
    name is used; a name that still has one in front (too rare to be cut) is read too."""
    tag = r.get("tag") or ""
    if not tag:
        m = NAME_TAG.match(r.get("name") or "")
        if not m:
            return 1
        tag = m.group(1)
    return 0 if set(re.split(r"[-/_.+]", tag.upper())) & first else 2


def _rank(qf, words, name):
    """Higher = better: the whole name, the start of it, every word at a word start, anywhere.
    A language tag in front ('EN - ') doesn't count as part of the name."""
    name_f = fold(NAME_TAG.sub("", name or ""))
    if name_f == qf:
        return 100
    if name_f.startswith(qf):
        return 90
    padded = " " + name_f
    if all(" " + w in padded for w in words):
        return 80
    return 60


def _matches(words, limit):
    """Titles whose name holds every word (a year may match the year instead)."""
    where, args = ["1=1"], []
    for word in words:
        if YEAR_WORD.match(word):
            where.append("(t.year=? OR t.search LIKE ?)")
            args += [word, f"%{word}%"]
        else:
            where.append("t.search LIKE ?")
            args.append(f"%{word}%")
    return db.rows(f"SELECT {_COLS}, t.search {_FROM} WHERE {' AND '.join(where)} LIMIT ?", (*args, limit))


def _fuzzy(qf, limit):
    """Typos ('charad', 'samurai' for 'Le Samouraï'): the closest names, from a list kept in memory."""
    if not _names:
        rows = db.rows(f"SELECT t.id, t.search {_FROM}")
        _names.extend(([r["id"] for r in rows], [r["search"] or "" for r in rows]))
    ids, names = _names
    # scored against the best-matching part of each name
    hits = process.extract(qf, names, scorer=fuzz.partial_ratio, score_cutoff=85, limit=limit * 3)
    # partial_ratio finds "me" inside "oppenheimer": a name much shorter than what was typed isn't a typo of it
    return [ids[i] for _, _, i in hits if len(names[i]) >= 0.6 * len(qf)][:limit]


def _by_preference(rows, first=None):
    """The versions of one title, the most likely wanted first: a label from "List these languages first",
    then the label most of the shown titles have (with only English picked, "EN" before "4K-EN"), then alphabetically."""
    if not _tag_rank:
        _tag_rank.update({r["tag"]: r["n"] for r in db.rows(f"SELECT t.tag, count(*) n {_FROM} GROUP BY t.tag")})
    first = _first_tags() if first is None else first
    return sorted(rows, key=lambda r: (_tag_order(r, first), -_tag_rank.get(r["tag"], 0), r["tag"], r["id"]))


def versions(t):
    """The same film or show under the playlist's other labels ("EN", "4K-EN", "DE"…) that are shown."""
    rows = db.rows(f"SELECT t.id, t.tag, t.country {_FROM} WHERE t.kind=? AND t.search=? AND t.year=?",
                   (t["kind"], t["search"], t["year"]))
    if len(rows) < 2:
        return []
    return [{"id": r["id"], "tag": r["tag"], "current": r["id"] == t["id"]} for r in _by_preference(rows)]


def search(q, limit=12):
    """Movies, shows and (for shows that were opened once) episodes for what was typed.

    Accents and punctuation don't matter ('amelie' finds 'Amélie'), every word has to be there in any
    order, a year narrows it ('dune 2021'), small typos still find it, and movies are found by actor too."""
    qf = fold(q)
    words = qf.split()
    if len(qf) < 2 or qf.isdigit() and len(qf) < 4:
        return []
    found, seen = [], set()

    def add(rows, score=None):
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                found.append((score if score is not None else _rank(qf, words, r["name"]), r))
    add(_matches(words, 400))
    years = [w for w in words if YEAR_WORD.match(w)]
    text = " ".join(w for w in words if not YEAR_WORD.match(w))
    if len(found) < limit and len(text) >= 4:
        ids = [i for i in _fuzzy(text, limit * 2) if i not in seen]
        if ids:
            rows = db.rows(f"SELECT {_COLS}, t.search {_FROM} WHERE t.id IN ({','.join('?' * len(ids))})", ids)
            add([r for r in rows if not years or r["year"] in years], 40)
    if len(found) < limit and len(qf) >= 4:      # an actor or director
        like = f"%{q.strip()}%"
        add(db.rows(f"SELECT {_COLS}, t.search {_FROM} WHERE t.kind='movie' AND (t.actors LIKE ? OR t.director LIKE ?) "
                    "LIMIT ?", (like, like, limit)), 30)
    first = _first_tags()
    found.sort(key=lambda sr: (-sr[0], _tag_order(sr[1], first),
                               len(fold(NAME_TAG.sub("", sr[1]["name"] or ""))),
                               -(int(sr[1]["year"]) if str(sr[1]["year"]).isdigit() else 0)))
    # one result per film or show: its other languages / qualities ride along as versions
    groups, order = {}, []
    for _, r in found:
        k = (r["kind"], r["search"], r["year"])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    out = []
    for k in order[:limit]:
        vs = _by_preference(groups[k], first)
        r = vs[0]
        out.append({"id": r["id"], "kind": r["kind"], "name": r["name"], "year": r["year"], "category": r["category"],
                    "duration": r["duration"], "tag": r["tag"], "country": r["country"],
                    "versions": [{"id": v["id"], "tag": v["tag"]} for v in vs] if len(vs) > 1 else []})
    like = f"%{q.strip()}%"
    for e in db.rows(f"""SELECT e.id, e.season, e.episode, e.name, e.duration, t.id AS title_id, t.name AS show
                         {_FROM} JOIN vod_episodes e ON e.title_id=t.id WHERE e.name LIKE ?
                         ORDER BY t.name COLLATE NOCASE, e.season, e.episode LIMIT ?""", (like, max(4, limit // 3))):
        out.append({"id": e["id"], "kind": "episode", "name": e["show"], "sub": ep_label(e), "title_id": e["title_id"],
                    "duration": e["duration"]})
    return out


# ---------------------------------------------------------------- watching
def item(item_id):
    """What can be played: a movie or one episode. Includes the real address, so never send it to a page as is."""
    e = db.one("SELECT * FROM vod_episodes WHERE id=?", (item_id,))
    t = _row(e["title_id"] if e else item_id)
    if not t or (not e and t["kind"] != "movie"):
        return None
    src = _source(t["source_id"])
    part = e or t
    if src["type"] == "xtream":
        folder = "series" if e else "movie"
        url = f"{sources.xtream_base(src)}/{folder}/{src['username']}/{src['password']}/{part['xid']}.{part['ext'] or 'mp4'}"
    else:
        url = part["url"]
    p = db.one("SELECT * FROM vod_progress WHERE item_id=?", (item_id,)) or {}
    out = {"id": item_id, "title_id": t["id"], "kind": "episode" if e else "movie", "title": t["name"],
           "sub": ep_label(e) if e else t["year"], "url": url, "video_mode": src.get("video_mode") or "",
           "duration": part["duration"] or int(p.get("duration") or 0),
           "position": 0 if p.get("watched") else p.get("position", 0), "next": None}
    if e:
        nxt = db.one("""SELECT * FROM vod_episodes WHERE title_id=? AND (season>? OR (season=? AND episode>?))
                        ORDER BY season, episode LIMIT 1""", (t["id"], e["season"], e["season"], e["episode"]))
        if nxt:
            out["next"] = {"id": nxt["id"], "label": ep_label(nxt)}
    return out


def probe_duration(raw_url):
    """Length of a video file in seconds, read with ffmpeg (0 when it can't tell)."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", *input_args(raw_url)], capture_output=True,
                           timeout=25, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    m = DUR.search(r.stderr.decode("utf-8", "ignore"))
    return int(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0


def prepare(item_id):
    """Called right before playing: what it is, how long, and where to resume. A length the playlist
    didn't give is measured once and remembered."""
    it = item(item_id)
    if not it:
        return None
    if not it["duration"]:
        it["duration"] = probe_duration(it["url"])
        if it["duration"]:
            table = "vod_episodes" if it["kind"] == "episode" else "vod_titles"
            db.run(f"UPDATE {table} SET duration=? WHERE id=?", (it["duration"], item_id))
    return it


def stream_cmd(raw_url, start=0.0, mode=None):
    """ffmpeg command for a title from `start` seconds in: read at playing speed (a file would
    otherwise arrive all at once), MPEG-TS out like a live channel."""
    inp, vid, aud = play_args(raw_url, mode)
    seek = ["-ss", f"{start:.2f}"] if start > 0 else []
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-re", *seek, *inp,
            "-map", "0:v:0?", "-map", "0:a:0?", "-sn", "-dn", *vid, *aud,
            "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]


def hls_pre(start=0.0):
    """Input options for the HLS output (iPad/iPhone): the same speed limit and start time."""
    return ["-re"] + (["-ss", f"{start:.2f}"] if start > 0 else [])


def _title_of(item_id):
    e = db.one("SELECT title_id FROM vod_episodes WHERE id=?", (item_id,))
    if e:
        return e["title_id"]
    return item_id if db.one("SELECT 1 FROM vod_titles WHERE id=? AND kind='movie'", (item_id,)) else None


def set_progress(item_id, position, duration=0):
    """Remember where a movie or episode was stopped. Near the end counts as watched."""
    tid = _title_of(item_id)
    if not tid:
        return None
    position, duration = max(0.0, float(position)), max(0.0, float(duration or 0))
    watched = bool(duration and position >= duration * WATCHED_AT)
    db.run("""INSERT OR REPLACE INTO vod_progress(item_id, title_id, position, duration, watched, updated)
              VALUES (?,?,?,?,?,?)""", (item_id, tid, 0 if watched else position, duration, int(watched), int(time.time())))
    return {"watched": watched}


def set_watched(item_id, watched=True):
    """Mark as watched / not watched. For a show id: every episode that is known."""
    t = db.one("SELECT kind FROM vod_titles WHERE id=?", (item_id,))
    ids = ([e["id"] for e in db.rows("SELECT id FROM vod_episodes WHERE title_id=?", (item_id,))]
           if t and t["kind"] == "series" else [item_id])
    now = int(time.time())
    for i in ids:
        tid = _title_of(i)
        if not tid:
            continue
        if watched:
            db.run("""INSERT INTO vod_progress(item_id, title_id, position, watched, updated) VALUES (?,?,0,1,?)
                      ON CONFLICT(item_id) DO UPDATE SET position=0, watched=1, updated=excluded.updated""", (i, tid, now))
        else:
            db.run("DELETE FROM vod_progress WHERE item_id=?", (i,))
    return {"ok": True}


def set_listed(title_id, on=True):
    if on and not db.one("SELECT 1 FROM vod_titles WHERE id=?", (title_id,)):
        return None
    if on:
        db.run("INSERT OR IGNORE INTO vod_list(title_id, added) VALUES (?,?)", (title_id, int(time.time())))
    else:
        db.run("DELETE FROM vod_list WHERE title_id=?", (title_id,))
    return {"in_list": bool(on)}


# ---------------------------------------------------------------- posters (small copies for TVs)
# Providers' posters are often full-size (1000x1500 and up). A TV grid shows them at ~200 px wide, so the
# first request downloads one, shrinks it with ffmpeg and keeps the small JPEG; later requests are a local file.
POSTER_W = 240
POSTER_KEEP = 6000                      # small copies kept on disk (the least recently shown go first)
POSTER_MAX = 15 * 1024 * 1024           # bigger than this isn't a poster
_poster_slots = threading.BoundedSemaphore(4)
_poster_made = 0


def poster_dir():
    return config.data_path("posters")


def poster_url(title_id):
    r = db.one("SELECT poster FROM vod_titles WHERE id=?", (title_id,))
    return (r["poster"] or "").strip() if r else ""


def _shrink(data):
    """JPEG POSTER_W wide (kept small, never enlarged), or None when ffmpeg can't read it."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0",
                            "-vf", f"scale='min({POSTER_W},iw)':-2", "-frames:v", "1", "-q:v", "4",
                            "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                           input=data, capture_output=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 and r.stdout[:2] == b"\xff\xd8" else None


def _download(url):
    try:
        with httpx.stream("GET", url, timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                return None
            data = bytearray()
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > POSTER_MAX:
                    return None
            return bytes(data)
    except httpx.HTTPError as e:
        log.debug("Poster %s: %s", url[:80], e)
        return None


def _prune(folder):
    files = sorted(folder.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
    for f in files[:max(0, len(files) - POSTER_KEEP)]:
        f.unlink(missing_ok=True)


def poster_file(title_id):
    """Path of the small poster for a title (made on first use), or None (no poster, or it couldn't be read)."""
    global _poster_made
    url = poster_url(title_id)
    if not url.lower().startswith(("http://", "https://")):
        return None
    folder = poster_dir()
    path = folder / (hashlib.sha1(url.encode()).hexdigest()[:24] + ".jpg")     # by address: a new poster makes a new file
    if path.exists():
        try:
            os.utime(path)              # recently shown: keep it
        except OSError:
            pass
        return path
    with _poster_slots:
        if path.exists():
            return path
        data = _download(url)
        small = _shrink(data) if data else None
        if not small:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(small)
        tmp.replace(path)
        _poster_made += 1
        if _poster_made % 200 == 0:
            _prune(folder)
    return path


# ---------------------------------------------------------------- setup
def source_stats(src):
    """Numbers for a playlist's card in Setup."""
    n = {r["kind"]: r["n"] for r in db.rows("SELECT kind, count(*) n FROM vod_titles WHERE source_id=? AND hidden=0 GROUP BY kind",
                                             (src["id"],))}
    hidden = hidden_cats(src)
    cats = {k: [0, 0] for k in KINDS}       # kind -> [shown, all]
    for c in db.rows("SELECT kind, name FROM vod_cats WHERE source_id=?", (src["id"],)):
        cats[c["kind"]][1] += 1
        cats[c["kind"]][0] += (c["kind"], c["name"]) not in hidden
    tags = [r["tag"] for r in db.rows("SELECT DISTINCT tag FROM vod_titles WHERE source_id=?", (src["id"],))]
    picked = shown_tags(src)
    return {"movies": n.get("movie", 0), "series": n.get("series", 0), "cats": cats,
            "tags": [len(tags) if picked is None else sum(t in picked for t in tags), len(tags)],
            "files": _int(db.get_meta(f"vod_files_{src['id']}", 0))}


def cats(source_id, kind="movie"):
    """A playlist's movie (or TV show) categories, in the shape of sources.groups(): Setup uses the same picker."""
    src = _source(source_id)
    if not src:
        return None
    hidden = hidden_cats(src)
    out = [{"name": r["name"] or "(no category)", "count": r["count"], "hidden": (kind, r["name"]) in hidden, "pattern": False}
           for r in db.rows("SELECT name, count FROM vod_cats WHERE source_id=? AND kind=? ORDER BY name COLLATE NOCASE",
                            (source_id, kind))]
    return {"source": {"id": src["id"], "name": src["name"]}, "groups": out, "partial": False}


def set_hidden(source_id, names, kind="movie"):
    """Save which of a playlist's movie (or TV show) categories are hidden; the other kind's stay as they
    are, and so do hidden ones the provider isn't listing right now. Instant: the titles are only marked.
    -> counts, plus "missing": categories shown again whose titles were never read (they need a re-read)."""
    src = _source(source_id)
    if not src:
        return None
    by_label = {r["name"] or "(no category)": r["name"]
                for r in db.rows("SELECT name FROM vod_cats WHERE source_id=? AND kind=?", (source_id, kind))}
    listed = set(by_label.values())
    before = hidden_cats(src)
    old = {h for h in before if h[0] == kind and h[1] in listed}
    new = {(kind, by_label[n]) for n in names if n in by_label}
    db.run("UPDATE sources SET vod_hidden=? WHERE id=?", (json.dumps(sorted((before - old) | new)), source_id))
    with db.conn() as c:
        for flag, group in ((1, new - old), (0, old - new)):
            for _, name in group:
                c.execute("UPDATE vod_titles SET hidden=? WHERE source_id=? AND kind=? AND category=?",
                          (flag, source_id, kind, name))
        missing = [name for _, name in old - new
                   if not c.execute("SELECT 1 FROM vod_titles WHERE source_id=? AND kind=? AND category=? LIMIT 1",
                                    (source_id, kind, name)).fetchone()]
    _changed()
    return {"hidden": len(new), "newly_hidden": len(new - old), "shown_again": len(old - new), "missing": len(missing)}



def shown_tags(src):
    """The labels a playlist shows in On Demand, or None for all of them."""
    try:
        v = json.loads(src.get("vod_tags") or "null")
    except ValueError:
        return None
    return set(v) if isinstance(v, list) else None


NO_TAG = "(no label)"


def tags(source_id):
    """A playlist's labels with how many movies and shows have each and an example, in the shape of
    sources.groups() so Setup uses the same picker."""
    src = _source(source_id)
    if not src:
        return None
    picked = shown_tags(src)
    # example: the label's most recently added title (SQLite returns the name from the row with the max)
    rows = db.rows("""SELECT tag, count(*) n, sum(kind='movie') movies, max(added) newest,
                      name || CASE WHEN year!='' THEN ' (' || year || ')' ELSE '' END AS example
                      FROM vod_titles WHERE source_id=? GROUP BY tag ORDER BY n DESC""", (source_id,))
    out = [{"name": r["tag"] or NO_TAG, "count": r["n"], "movies": r["movies"], "series": r["n"] - r["movies"],
            "example": r["example"], "hidden": picked is not None and r["tag"] not in picked, "pattern": False} for r in rows]
    return {"source": {"id": src["id"], "name": src["name"]}, "groups": out, "partial": False}


def set_tags(source_id, hidden):
    """Save which labels are left out. Stored as the labels shown, so labels that turn up later (a
    provider adding a language) start hidden; leaving nothing out goes back to showing everything."""
    src = _source(source_id)
    if not src:
        return None
    every = {r["tag"] for r in db.rows("SELECT DISTINCT tag FROM vod_titles WHERE source_id=?", (source_id,))}
    hide = {"" if h == NO_TAG else h for h in hidden}
    shown = every - hide
    db.run("UPDATE sources SET vod_tags=? WHERE id=?", ("" if not hide & every else json.dumps(sorted(shown)), source_id))
    _changed()
    return {"shown": len(shown), "hidden": len(hide & every)}
