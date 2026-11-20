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

from . import config, db, sources, streaming
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
    country TEXT DEFAULT '',       -- a show's country from the end of its name: "(US)" -> "US"
    bitrate INTEGER DEFAULT 0,     -- kbit/s, from the title's details (Xtream); 0 = not known
    tmdb TEXT DEFAULT '',          -- The Movie Database id from the details: ties versions with different names together
    o_search TEXT DEFAULT '',      -- the original-language title from the details, folded like search
    av TEXT DEFAULT ''             -- picture and sound as ffmpeg read them when played or scanned (JSON, see tracks.av_summary)
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
    aired TEXT DEFAULT '',
    av TEXT DEFAULT ''             -- picture and sound of your own copy (My Library), as for a movie's av
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
-- shows taken off Continue Watching while their next episode was waiting (Forget); watching any
-- episode after `at` puts them back
CREATE TABLE IF NOT EXISTS vod_dismissed (title_id TEXT PRIMARY KEY, at INTEGER DEFAULT 0);
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
LIB_NEW_DAYS = 30                 # New in My Library: your own titles added (or shows with a new episode) this recently
RESUME_MIN = 30                   # seconds in before a title counts as started
WATCHED_AT = 0.92                 # this far through = watched
INFO_TTL = 30 * 86400


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)
        db._add_column(c, "vod_titles", "hidden", "INTEGER DEFAULT 0")     # test builds made the table without it
        db._add_column(c, "vod_titles", "tag", "TEXT DEFAULT ''")         # 1.5: language / platform labels
        db._add_column(c, "vod_titles", "country", "TEXT DEFAULT ''")
        db._add_column(c, "vod_titles", "bitrate", "INTEGER DEFAULT 0")   # 1.10: details shown in Pick a version
        db._add_column(c, "vod_titles", "tmdb", "TEXT DEFAULT ''")
        db._add_column(c, "vod_titles", "o_search", "TEXT DEFAULT ''")
        db._add_column(c, "vod_titles", "av", "TEXT DEFAULT ''")
        db._add_column(c, "vod_episodes", "av", "TEXT DEFAULT ''")      # 1.10: an episode's picture and sound
        for old in ("ix_vod_titles", "ix_vod_lang"):                       # indexes from test builds
            c.execute(f"DROP INDEX IF EXISTS {old}")
        c.execute("CREATE INDEX IF NOT EXISTS ix_vod_browse ON vod_titles(kind, hidden, name COLLATE NOCASE)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_vod_same ON vod_titles(kind, search, year)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_vod_tmdb ON vod_titles(tmdb) WHERE tmdb!=''")
        # details fetched before bitrate / tmdb / o_search were kept: fetch those again (once, when next needed)
        if not c.execute("SELECT 1 FROM meta WHERE k='vod_details_v2'").fetchone():
            c.execute("UPDATE vod_titles SET info_at=0 WHERE source_id>0 AND info_at>0 AND bitrate=0 AND tmdb=''")
            c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('vod_details_v2', '1')")
        if not c.execute("SELECT 1 FROM meta WHERE k='vod_details_v3'").fetchone():     # and the panel's av
            c.execute("UPDATE vod_titles SET info_at=0 WHERE source_id>0 AND kind='movie' AND info_at>0 AND av=''")
            c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('vod_details_v3', '1')")
        if not c.execute("SELECT 1 FROM meta WHERE k='vod_details_v4'").fetchone():
            # a panel's minutes taken as seconds, and the bitrate worked out from them (size over a minute):
            # cleared, and the details fetched again when next needed
            c.execute(f"""UPDATE vod_titles SET duration=0, info_at=0, bitrate=CASE WHEN bitrate>{MAX_KBPS} THEN 0 ELSE bitrate END
                          WHERE source_id>0 AND kind='movie' AND duration BETWEEN 1 AND {MOVIE_MIN - 1}""")
            c.execute(f"UPDATE vod_titles SET bitrate=0 WHERE source_id>0 AND bitrate>{MAX_KBPS}")
            c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('vod_details_v4', '1')")
        if not c.execute("SELECT 1 FROM meta WHERE k='vod_details_v5'").fetchone():
            # a panel's own bitrate was kept as sent, even an impossible one (987 Mbps): cleared
            c.execute(f"UPDATE vod_titles SET bitrate=0 WHERE source_id>0 AND bitrate>{MAX_KBPS}")
            c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('vod_details_v5', '1')")
    from . import library
    library.init()             # your own movies and shows: listed through the vod_sources view
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


MOVIE_MIN = 300     # a film "shorter" than this (5 minutes) from a panel is its length in minutes


def _secs(info, movie=False):
    """Length in seconds from Xtream's duration_secs / 'duration' (01:53:00) / episode_run_time (minutes).

    When duration_secs and the text disagree the text wins: some panels put minutes in duration_secs
    ("92" next to "01:32:00"). A `movie` under MOVIE_MIN is taken as minutes: panels that put minutes in
    both ("92", "00:01:32") would otherwise list a feature film as a minute and a half."""
    n = _int(info.get("duration_secs"))
    parts = _s(info.get("duration")).split(":")
    text = (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 3 and all(p.isdigit() for p in parts) else 0)
    secs = text if text and (not n or abs(text - n) > 120) else n or _int(info.get("episode_run_time")) * 60
    return secs * 60 if movie and 0 < secs < MOVIE_MIN else secs


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
    return bool(db.one("SELECT 1 FROM sources WHERE enabled=1 AND (vod_movies=1 OR vod_series=1) LIMIT 1")
                or db.one("SELECT 1 FROM lib_shares WHERE enabled=1 LIMIT 1"))


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
def _extra(info, t):
    """The details kept for Pick a version and Search: bitrate, TMDB id, the original title."""
    tmdb = _s(info.get("tmdb_id") or info.get("tmdb"))
    kbps = _int(info.get("bitrate"))
    if kbps > MAX_KBPS:         # a panel's nonsense (987060 for a 1 GB film): not shown, and no size made from it
        kbps = 0
    return (kbps or t["bitrate"], tmdb if tmdb not in ("", "0") else t["tmdb"],
            fold(_s(info.get("o_name"))) or t["o_search"])


def _panel_av(info):
    """The panel's own reading of the movie's picture and sound (info.video / info.audio), as JSON for av; ''
    when it sent none. ffmpeg's reading (played, or a peek) is kept over it."""
    from . import tracks
    av = tracks.xtream_summary(info)
    return json.dumps(av, separators=(",", ":")) if av else ""


def _fetch_movie_info(src, t, timeout=120):
    d = sources.xtream_api(src, "get_vod_info", timeout=timeout, vod_id=t["xid"])
    d = d if isinstance(d, dict) else {}
    info = d.get("info") if isinstance(d.get("info"), dict) else {}
    data = d.get("movie_data") if isinstance(d.get("movie_data"), dict) else {}
    if not info and not data:      # a busy panel can answer with nothing: try again next time, not in 30 days
        raise ValueError("the provider sent an empty reply")
    released = _s(info.get("releasedate") or info.get("release_date"))
    db.run("""UPDATE vod_titles SET plot=?, actors=?, director=?, genre=?, year=?, rating=?, duration=?, poster=?, ext=?,
              bitrate=?, tmdb=?, o_search=?, av=CASE WHEN av='' THEN ? ELSE av END, info_at=? WHERE id=?""",
           (_s(info.get("plot") or info.get("description")) or t["plot"], _s(info.get("cast") or info.get("actors")) or t["actors"],
            _s(info.get("director")) or t["director"], _s(info.get("genre")) or t["genre"], released[:4] or t["year"],
            _s(info.get("rating")) or t["rating"], _secs(info, movie=True) or t["duration"],
            t["poster"] or _s(info.get("movie_image") or info.get("cover_big")),
            _s(data.get("container_extension")) or t["ext"], *_extra(info, t), _panel_av(info), int(time.time()), t["id"]))


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
        c.execute("""UPDATE vod_titles SET plot=?, actors=?, director=?, genre=?, year=?, rating=?, bitrate=?, tmdb=?,
                     o_search=?, info_at=? WHERE id=?""",
                  (_s(info.get("plot")) or t["plot"], _s(info.get("cast")) or t["actors"], _s(info.get("director")) or t["director"],
                   _s(info.get("genre")) or t["genre"], released[:4] or t["year"], _s(info.get("rating")) or t["rating"],
                   *_extra(info, t), int(time.time()), t["id"]))


def _row(title_id):
    return db.one("""SELECT t.*, src.name AS source_name, src.type AS source_type FROM vod_titles t
                     JOIN vod_sources src ON src.id=t.source_id WHERE t.id=?""", (title_id,))


def ep_label(e):
    return f"S{e['season']} E{e['episode']}" + (f" · {e['name']}" if e["name"] else "")


DESCRIBED = ("plot", "rating", "genre", "actors", "director", "poster")


def _borrow_details(out, t):
    """Your own copy (a library file) often has no plot, rating or poster while the playlist's copy of the same
    film does: what this one lacks comes from its other versions (the ones with the most said first)."""
    if all(out.get(k) for k in DESCRIBED):
        return
    others = [r for r in _version_rows(t) if r["id"] != t["id"]]
    others.sort(key=lambda r: -sum(bool(r[k]) for k in DESCRIBED))
    for k in DESCRIBED:
        if not out.get(k):
            out[k] = next((r[k] for r in others if r[k]), out.get(k) or "")


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
                             "poster", "duration", "source_name", "tag", "country", "bitrate")}
    out["av"] = _av(t["av"])
    out["ext"] = (t["ext"] or "").lower()
    f = db.one("SELECT size, path FROM lib_files WHERE item_id=?", (title_id,)) or {}
    out["size"] = f.get("size") or 0
    out["hint"] = None if out["av"] else _hint({"path": f.get("path"), "tag": t["tag"]})
    out["versions"] = versions(t)
    _borrow_details(out, t)
    out["in_list"] = bool(db.one("SELECT 1 FROM vod_list WHERE title_id=?", (title_id,)))
    prog = {p["item_id"]: p for p in db.rows("SELECT * FROM vod_progress WHERE title_id=?", (title_id,))}
    if t["kind"] == "movie":
        p = prog.get(title_id) or {}
        out.update(position=p.get("position", 0), watched=bool(p.get("watched")),
                   duration=t["duration"] or int(p.get("duration") or 0))
        return out
    eps = db.rows("""SELECT e.id, e.season, e.episode, e.name, e.plot, e.duration, e.aired, e.av, e.ext,
                            COALESCE(f.size, 0) size
                     FROM vod_episodes e LEFT JOIN lib_files f ON f.item_id=e.id
                     WHERE e.title_id=? ORDER BY e.season, e.episode, e.id""", (title_id,))
    seasons, latest = {}, None
    for e in eps:
        e["av"], e["ext"] = _av(e["av"]), (e["ext"] or "").lower()       # an episode of your own: what was read
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
_FROM = ("FROM vod_titles t JOIN vod_sources src ON src.id=t.source_id AND src.enabled=1 AND t.hidden=0 "
         "AND (src.vod_tags='' OR instr(src.vod_tags, '\"' || t.tag || '\"')>0) ")
_COLS = "t.id, t.kind, t.name, t.year, t.rating, t.category, t.poster, t.added, t.duration, t.tag, t.country, t.source_id"


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
    # your own libraries (My Library, negative source ids) first, so they aren't lost among a provider's groups
    rows = db.rows(f"SELECT t.category AS name, count(*) AS count, MIN(t.source_id) < 0 AS library {_FROM} WHERE t.kind=? "
                   "GROUP BY t.category ORDER BY MIN(t.source_id) >= 0, t.category COLLATE NOCASE", (kind,))
    recent = db.one(f"SELECT count(*) n {_FROM} WHERE t.kind=? AND t.added>0", (kind,))["n"]
    lib_new = db.one(f"SELECT count(*) n {_FROM} WHERE t.kind=? AND t.source_id<0 AND t.added>=?",
                     (kind, int(time.time()) - LIB_NEW_DAYS * 86400))["n"]
    return {"total": sum(r["count"] for r in rows), "recent": min(RECENT, recent), "lib_new": min(RECENT, lib_new), "cats": rows}


def library_new(kind=None, limit=RECENT, unwatched=False):
    """New in My Library: your own titles found in the last LIB_NEW_DAYS (and shows with a new episode), newest
    first. `unwatched`: leave out movies you've watched (Home)."""
    now = int(time.time())
    w, a = (" AND t.kind=?", [kind]) if kind else ("", [])
    if unwatched:
        w += " AND COALESCE(p.watched, 0)=0"
    rows = db.rows(f"""SELECT {_COLS}, p.position, p.duration AS pdur, p.watched {_FROM}
                       LEFT JOIN vod_progress p ON p.item_id=t.id
                       WHERE t.source_id<0 AND t.added>=?{w} ORDER BY t.added DESC, t.name LIMIT ?""",
                   (now - LIB_NEW_DAYS * 86400, *a, limit))
    return [_tile(r, now) for r in rows]


def continue_watching(kind=None):
    """Titles you're part-way through, the latest first. A show appears once, at its latest episode, and a film
    or show with several versions (languages, 4K…) appears once, as the version played last, unless
    Settings › Screens › Show every version is on: then each version you've started has its own tile.
    A show whose last-played episode you finished stays, as Up next: the episode after it (`upnext`)."""
    w, a = (" AND t.kind=?", [kind]) if kind else ("", [])
    versions = bool(config.get("vod_continue_versions"))
    # ties (a whole show marked watched at once) go to the latest episode, so Up next follows the last one
    rows = db.rows(f"""SELECT {_COLS}, t.search, p.item_id, p.position, p.duration AS pdur, p.updated, p.watched,
                              e.season, e.episode, e.name AS ep_name, e.duration AS ep_duration, d.at AS dismissed
                       {_FROM} JOIN vod_progress p ON p.title_id=t.id LEFT JOIN vod_episodes e ON e.id=p.item_id
                       LEFT JOIN vod_dismissed d ON d.title_id=t.id
                       WHERE ((p.watched=0 AND p.position>?) OR (p.watched=1 AND t.kind='series')){w}
                       ORDER BY p.updated DESC, e.season DESC, e.episode DESC""", (RESUME_MIN, *a))
    out, seen, now = [], set(), int(time.time())
    for r in rows:
        # its other versions count as the same title (or, showing every version, only this one does)
        same = r["id"] if versions else (r["kind"], r["search"], r["year"])
        if same in seen or (r["kind"] == "series" and r["season"] is None):
            continue
        seen.add(same)
        upnext = None
        if r["watched"]:                    # the last episode played was finished: the one after it, if any
            if (r["dismissed"] or 0) >= (r["updated"] or 0):
                continue
            upnext = db.one("""SELECT id, season, episode, name, duration FROM vod_episodes WHERE title_id=?
                               AND (season>? OR (season=? AND episode>?)) ORDER BY season, episode LIMIT 1""",
                            (r["id"], r["season"], r["season"], r["episode"]))
            if not upnext:
                continue                    # caught up with the show
            r = {**r, "item_id": upnext["id"], "season": upnext["season"], "episode": upnext["episode"],
                 "ep_name": upnext["name"], "ep_duration": upnext["duration"], "position": 0, "watched": 0}
        if r["kind"] == "series":
            r["duration"] = r["ep_duration"]
        tile = _tile(r, now)
        tile["resume"] = r["item_id"]
        tile["at"] = r["updated"] or 0
        tile["upnext"] = bool(upnext)
        if r["kind"] == "series":
            label = ep_label({"season": r["season"], "episode": r["episode"], "name": r["ep_name"]})
            tile["sub"] = f"Up next · {label}" if upnext else label
            tile["ep"] = label
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
    elif cat == "*libnew":
        rows = library_new(kind)
        return {"total": len(rows), "items": rows[offset:offset + limit]}
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
            where.append("(t.year=? OR t.search LIKE ? OR t.o_search LIKE ?)")
            args += [word, f"%{word}%", f"%{word}%"]
        else:      # the original title too ("amelie" finds a copy listed in English), once its details were read
            where.append("(t.search LIKE ? OR t.o_search LIKE ?)")
            args += [f"%{word}%", f"%{word}%"]
    return db.rows(f"SELECT {_COLS}, t.search {_FROM} WHERE {' AND '.join(where)} LIMIT ?", (*args, limit))


def _fuzzy(qf, limit):
    """Typos ('charad', 'samurai' for 'Le Samouraï'): the closest names, from a list kept in memory."""
    if not _names:
        rows = db.rows(f"SELECT t.id, t.search {_FROM}")
        _names.extend(([r["id"] for r in rows], [r["search"] or "" for r in rows]))
    ids, names = _names
    # scored against the best-matching part of each name
    hits = process.extract(qf, names, scorer=fuzz.partial_ratio, score_cutoff=85, limit=limit * 3)
    # partial_ratio finds "me" inside "oppenheimer": a name much shorter than what was typed isn't a typo of it.
    # And the score is for the words together: "hotel trans" is close to "hotel rwanda" (and "hostel") only
    # because of "hotel", so every word typed has to be close to a word of the name on its own too.
    words = qf.split()
    return [ids[i] for _, _, i in hits
            if len(names[i]) >= 0.6 * len(qf) and all(_word_close(w, names[i].split()) for w in words)][:limit]


def _word_close(w, name_words):
    """A word typed is in the name, or is a small typo of one of its words or of the start of one
    ('samurai' ~ 'samourai', 'transylvnia' ~ 'transylvania', 'tranz' ~ 'transylvania' while still typing).
    It has to be the start of a word ('t' is not in 'hotel rwanda' even though 'hotel' has a t), and short
    words ('of', 'tr') have to be there as typed: too short to tell a typo from another word.
    'hotels' ~ 'hotel' (91) but not 'hostel' (83)."""
    if any(nw.startswith(w) for nw in name_words):
        return True
    if len(w) < 4:
        return False
    return any(fuzz.ratio(w, nw) >= 85 or fuzz.ratio(w, nw[:len(w)]) >= 85 for nw in name_words)


def _mine(r):
    """1 for a copy in one of your own libraries (source -<id>), 0 for a playlist's: yours come first."""
    try:
        return 1 if int(r["source_id"]) < 0 else 0
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def _by_preference(rows, first=None):
    """The versions of one title, the most likely wanted first: your own copy (a library), then a label from
    "List these languages first", then the label most of the shown titles have (with only English picked,
    "EN" before "4K-EN"), then alphabetically."""
    if not _tag_rank:
        _tag_rank.update({r["tag"]: r["n"] for r in db.rows(f"SELECT t.tag, count(*) n {_FROM} GROUP BY t.tag")})
    first = _first_tags() if first is None else first
    return sorted(rows, key=lambda r: (-_mine(r), _tag_order(r, first), -_tag_rank.get(r["tag"], 0), r["tag"], r["id"]))


def _version_rows(t):
    # the same name and year, or (once details were fetched) the same TMDB id under a different name.
    # kind is repeated in each branch so SQLite uses ix_vod_same and ix_vod_tmdb (a MULTI-INDEX OR), not a scan
    return db.rows(f"""SELECT t.id, t.tag, t.country, t.source_id, t.bitrate, t.duration, t.added, t.info_at,
                              src.name AS place, src.type AS source_type, t.kind, t.xid, t.plot, t.actors, t.director,
                              t.genre, t.year, t.rating, t.poster, t.ext, t.tmdb, t.o_search, t.av, lf.size, lf.path
                       {_FROM} LEFT JOIN lib_files lf ON lf.item_id=t.id       -- your own copy's file size
                       WHERE (t.kind=? AND t.search=? AND t.year=?) OR (t.kind=? AND t.tmdb!='' AND t.tmdb=?)""",
                   (t["kind"], t["search"], t["year"], t["kind"], t["tmdb"] or ""))


RES_4K = re.compile(r"4K|UHD|2160", re.I)
RES_1080 = re.compile(r"1080|FHD", re.I)
RES_720 = re.compile(r"720|(?<![UF])HD", re.I)


def _quality(r):
    """How good a version's picture is, for Pick a version's order: 4 = 4K, 3 = 1080p, 2 = 720p, 1 = SD. From
    what ffmpeg read when there is that, else from the label; unknown (1.5) goes between 720p and SD."""
    av = _av(r["av"]) or _hint(r)
    if av and av.get("w"):
        w = av["w"]
        return 4 if w >= 3200 else 3 if w >= 1800 else 2 if w >= 1200 else 1
    tag = r["tag"] or ""
    return 4 if RES_4K.search(tag) else 3 if RES_1080.search(tag) else 2 if RES_720.search(tag) else 1.5


def versions(t):
    """The same film or show under the playlist's other labels ("EN", "4K-EN", "DE"…) that are shown,
    the best first: your own copy, then the best picture, then the highest bitrate."""
    rows = _version_rows(t)
    if len(rows) < 2:
        return []
    rows = sorted(_by_preference(rows), key=lambda r: (-_mine(r), -_quality(r), -(r["bitrate"] or 0)))
    # "where": the playlist or library it's in, so copies with no label can be told apart ("Movies", "35mm")
    out = [{"id": r["id"], "tag": r["tag"], "where": r["place"] or "", "current": r["id"] == t["id"], "mine": r["source_id"] < 0,
            "bitrate": r["bitrate"], "duration": r["duration"], "added": r["added"], "av": _av(r["av"]),
            "hint": None if _av(r["av"]) else _hint(r), "ext": (r["ext"] or "").lower(), "size": r["size"] or 0,
            "len_unknown": False}
           for r in rows]
    _unknown_lengths(out)
    return out


PLACEHOLDER_SECS = 3600


def _unknown_lengths(vs):
    """A panel that doesn't know a film's length often says exactly 1:00:00. When the other copies agree on
    something else, that length is taken as unknown (len_unknown; duration 0, so no size is made from it)."""
    real = [v["duration"] for v in vs if v["duration"] and v["duration"] != PLACEHOLDER_SECS]
    if not real:
        return
    for v in vs:
        if v["duration"] == PLACEHOLDER_SECS and not v["mine"] and all(abs(d - PLACEHOLDER_SECS) > 120 for d in real):
            v["duration"], v["len_unknown"] = 0, True


def _hint(r):
    """Until a version is read: what its file name says (your own copies: "….1080p.BluRay.x264.DTS.5.1.mkv"),
    else its label ("4K-EN"). Shown like av; None when neither says anything about the picture."""
    from . import tracks
    return (tracks.name_summary((r.get("path") or "").rsplit("/", 1)[-1]) if r.get("path") else {}) or \
        tracks.name_summary(r.get("tag") or "") or None


def check_versions(title_id, budget=12.0):
    """Pick a version: fetch the details (bitrate, length) of the versions not fetched yet, one at a time
    (a provider may refuse several at once), for at most `budget` seconds. -> versions(), or None."""
    t = _row(title_id)
    if not t:
        return None
    end = time.monotonic() + budget
    for r in _version_rows(t):
        if time.monotonic() > end:
            break
        # movies only: a show's details are its whole episode list, and shows have no bitrate
        if r["kind"] != "movie" or r["source_type"] != "xtream" or time.time() - r["info_at"] <= INFO_TTL:
            continue
        try:
            _fetch_movie_info(_source(r["source_id"]), r, timeout=15)
        except Exception as e:  # noqa: BLE001
            log.warning("No details for version %s: %s", r["id"], e)
    return versions(_row(title_id))


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
    found.sort(key=lambda sr: (-sr[0], -_mine(sr[1]), _tag_order(sr[1], first),
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
    part = e or t
    if t["source_id"] < 0:                  # a local library (library.py): served to ffmpeg by TunerBox itself
        from . import library
        src = {"video_mode": library.settings()["video_mode"]}
        url = library.play_url(item_id)
    elif (src := _source(t["source_id"]))["type"] == "xtream":
        folder = "series" if e else "movie"
        url = f"{sources.xtream_base(src)}/{folder}/{src['username']}/{src['password']}/{part['xid']}.{part['ext'] or 'mp4'}"
    else:
        url = part["url"]
    p = db.one("SELECT * FROM vod_progress WHERE item_id=?", (item_id,)) or {}
    out = {"id": item_id, "title_id": t["id"], "source_id": t["source_id"], "kind": "episode" if e else "movie", "title": t["name"],
           "sub": ep_label(e) if e else t["year"], "url": url, "video_mode": src.get("video_mode") or "",
           "duration": part["duration"] or int(p.get("duration") or 0),
           "position": 0 if p.get("watched") else p.get("position", 0), "next": None}
    if e:
        nxt = db.one("""SELECT * FROM vod_episodes WHERE title_id=? AND (season>? OR (season=? AND episode>?))
                        ORDER BY season, episode LIMIT 1""", (t["id"], e["season"], e["season"], e["episode"]))
        if nxt:
            out["next"] = {"id": nxt["id"], "label": ep_label(nxt)}
    return out


PROBE_TIMEOUT = 25
TIMED_OUT = "no answer within"      # probe_why's reason when the file didn't answer in time (try again soon)
_ERR_LINE = re.compile(r"(?im)^(?!\s*(?:Input|Output|Stream|Duration|Metadata|  ))(.*(?:error|failed|invalid|denied|not found|"
                       r"refused|timed out|unable|no such|server returned|end of file).*)$")


def probe_why(raw_url, timeout=PROBE_TIMEOUT):
    """(length in seconds, ffmpeg's description of the input, why it couldn't be read: '' when it could).
    ffmpeg's description is '' unless it found the file's contents ("Input #0"): an error isn't a description."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", *input_args(raw_url, reconnect=False)],
                           capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        text = r.stderr.decode("utf-8", "ignore")
    except subprocess.TimeoutExpired as e:
        text = (e.stderr or b"").decode("utf-8", "ignore")
        return 0, "", f"{TIMED_OUT} {timeout} s" + (f" ({_last_error(text)})" if _last_error(text) else "")
    except OSError as e:
        return 0, "", f"ffmpeg didn't start: {e}"
    if "Input #" not in text:
        return 0, "", _last_error(text) or "ffmpeg found nothing it could read"
    m = DUR.search(text)
    secs = int(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0
    return secs, text, ""


def _last_error(text):
    found = _ERR_LINE.findall(text or "")
    return found[-1].strip()[:200] if found else ""


def probe(raw_url):
    """(length in seconds, ffmpeg's description of the input) of a video file (0, '' when it can't tell)."""
    secs, text, _ = probe_why(raw_url)
    return secs, text


def probe_duration(raw_url):
    """Length of a video file in seconds, read with ffmpeg (0 when it can't tell)."""
    return probe(raw_url)[0]


MAX_KBPS = 200_000      # 200 Mbps: more than any film file; a bitrate worked out above this came from a wrong length


def _file_secs(text):
    """The length ffmpeg read from a file's header ("Duration: 01:31:32.00"), 0 when it didn't say."""
    m = DUR.search(text or "")
    return int(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0


def _fix_length(item_id, text):
    """A provider's movie: the length in its file wins over the panel's when they're more than 2 minutes apart.
    -> the length now known."""
    secs = _file_secs(text)
    if secs > 60:
        db.run("UPDATE vod_titles SET duration=? WHERE id=? AND source_id>0 AND kind='movie' AND abs(duration-?)>120",
               (secs, item_id, secs))
    return (db.one("SELECT duration FROM vod_titles WHERE id=?", (item_id,)) or {}).get("duration") or 0


def save_av(item_id, text, empty=False, episode=False):
    """Keep a movie's picture and sound as ffmpeg described them (playing it, or the library scan), for
    Pick a version. A bitrate the provider gave stays (ffmpeg's is only an estimate for a stream); for your
    own files ffmpeg's is the only one. `empty`: keep '{}' when there's no picture to describe, so the
    library scan doesn't read the file again."""
    from . import tracks
    av = tracks.av_summary(text or "")
    if episode:                 # an episode of your own (My Library): its bitrate goes with the rest ("kbps")
        js = json.dumps(av, separators=(",", ":")) if av else ("{}" if empty else "")
        if js:
            db.run("UPDATE vod_episodes SET av=? WHERE id=?", (js, item_id))
        return bool(av)
    if not av:
        if empty:
            db.run("UPDATE vod_titles SET av='{}' WHERE id=? AND kind='movie' AND av=''", (item_id,))
        return False
    _fix_length(item_id, text)
    kbps = av.pop("kbps", 0)
    js = json.dumps(av, separators=(",", ":"))
    db.run("""UPDATE vod_titles SET av=?, bitrate=CASE WHEN bitrate=0 OR source_id<0 THEN ? ELSE bitrate END
              WHERE id=? AND kind='movie' AND (av!=? OR bitrate=0 OR source_id<0)""", (js, kbps, item_id, js))
    return True


# ---------------------------------------------------------------- Pick a version: a quick look at each version
PEEK_BYTES = 2 * 1024 * 1024    # an MKV's track list (codec, size, HDR, sound) is well inside its first 2 MB
PEEK_RETRY = 86400              # a version that couldn't be read isn't tried again for a day
CONTENT_RANGE = re.compile(r"bytes\s+\d+-\d+/(\d+)")
_peeked = {}                    # version id -> (when reading it failed, why)


def _read_start(raw_url, timeout=8):
    """The first PEEK_BYTES of a file on the provider, with a Range request. -> (bytes, total size), or
    None when the provider ignores Range (a plain 200 would be the whole film: it's never read)."""
    url, headers = streaming.split_url(raw_url)
    with httpx.Client(follow_redirects=True, timeout=timeout) as c:
        with c.stream("GET", url, headers={**headers, "Range": f"bytes=0-{PEEK_BYTES - 1}"}) as r:
            if r.status_code != 206:
                log.info("Pick a version: the provider answered %s to a range request, not 206", r.status_code)
                return None
            m = CONTENT_RANGE.match(r.headers.get("content-range", ""))
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) >= PEEK_BYTES:
                    break
    return bytes(buf[:PEEK_BYTES]), int(m.group(1)) if m else 0


def _describe(data):
    """ffmpeg's description of the start of a file."""
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", "pipe:0"], input=data, capture_output=True,
                       timeout=15, creationflags=NO_WINDOW)
    return r.stderr.decode("utf-8", "ignore")


PEEK_PROBE_TIMEOUT = 20        # seconds ffmpeg may take reading a version itself (after the 2 MB read found nothing)


NO_CONNECTION = "No connection free on this playlist right now"


def peek(version_id, force=False, client="", again=False):
    """Pick a version: what one version is, read without playing it (your own copy: as the library task would).
    First the start of its file (2 MB, one request); when that says nothing (an MP4 keeps its track list at the
    end, or the provider won't send part of a file) ffmpeg reads the file itself, only as far as it needs.
    Skipped when the setting (vod_details) is off, or "ask" without `force` (Read details pressed), when it was
    read already, it couldn't be read today (`force`: try anyway) or its playlist has no connection free. When the connection is taken only by `client`'s own stream (the screen
    asking: live TV in the corner, or a movie), nothing is read: "mine" says the screen can let go of it first
    (unless vod_details_pause is off).
    `again` (Read again, with `force`): read a version that was read already too, and keep what the file says now
    (a panel's own description of its copy can be wrong).
    -> the version's {id, bitrate, duration, av, why, mine} (`why`: why it wasn't read), or None when it isn't a movie."""
    from . import connections
    t = _row(version_id)
    if not t or t["kind"] != "movie":
        return None

    def out(why=""):
        r = _row(version_id)
        return {"id": r["id"], "bitrate": r["bitrate"], "duration": r["duration"], "av": _av(r["av"]),
                "why": "" if r["av"] not in ("", "{}") else why}

    def failed(why):
        _peeked[version_id] = (time.time(), why)
        return out(why)
    mode = config.get("vod_details", "auto")
    again = again and force
    if (t["av"] not in ("", "{}") and not again) or mode == "off" or (mode == "ask" and not force):
        return out()
    when, why = _peeked.get(version_id, (0, ""))
    if not force and (t["av"] == "{}" or time.time() - when < PEEK_RETRY):
        return out(why or "Nothing in the file says")
    if t["source_id"] < 0:          # your own copy the library task hasn't got to yet: read it now, the same way
        from . import library
        return out() if library.measure_one(version_id) else failed("Couldn't read it from the NAS (see Setup › Log)")
    try:
        connections.check(t["source_id"])                     # never at the cost of something playing
    except connections.Full:                                  # not remembered: tried again next time
        try:
            if not client or not config.get("vod_details_pause", True):
                raise
            connections.check(t["source_id"], client=client)  # free but for this screen's own stream?
        except connections.Full:
            return out(NO_CONNECTION)
        return {**out("This screen is using the playlist's connection"), "mine": True}
    it = item(version_id)
    if not it or not it["url"].lower().startswith("http"):
        return out()
    text, total = "", 0
    try:
        got = _read_start(it["url"])
        if got:
            data, total = got
            text = _describe(data)
    except Exception as e:  # noqa: BLE001
        log.info("Pick a version: the start of %s couldn't be read (%s); reading it with ffmpeg", version_id, e)
    if not tracks_av(text):
        secs, text, why = probe_why(it["url"], PEEK_PROBE_TIMEOUT)
        if not text:
            log.warning("Pick a version: couldn't read %s: %s", _vod_name(t), why)
            return failed(f"Couldn't read it: {why}")
    # no bitrate from the provider: the file's size over its length (the file's own length first, then before
    # ffmpeg's guess from 2 MB). Nothing when that comes out impossible: the length is wrong, not the film
    secs = _fix_length(version_id, text)
    kbps = int(total * 8 / secs / 1000) if total and secs > 60 else 0
    if 0 < kbps <= MAX_KBPS:
        db.run("UPDATE vod_titles SET bitrate=? WHERE id=? AND bitrate=0", (kbps, version_id))
    if not save_av(version_id, text, empty=True):
        return failed("Nothing in the file says")
    _peeked.pop(version_id, None)
    return out()


def tracks_av(text):
    from . import tracks
    return tracks.av_summary(text or "")


def _vod_name(t):
    return " ".join(str(x) for x in (t.get("tag"), t.get("name"), f"({t['year']})" if t.get("year") else "") if x)


def _av(v):
    try:
        return json.loads(v) or None if v else None      # '{}': read, nothing to show
    except ValueError:
        return None


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


def stream_cmd(raw_url, start=0.0, mode=None, pace=None, audio=0):
    """ffmpeg command for a title from `start` seconds in: read at playing speed (a file would
    otherwise arrive all at once; `pace` = other reading options), MPEG-TS out like a live channel.
    `audio`: which of its audio tracks (0 = the first)."""
    inp, vid, aud = play_args(raw_url, mode)
    seek = ["-ss", f"{start:.2f}"] if start > 0 else []
    # info, not just errors: ffmpeg describes the input first, which save_av keeps (no progress lines: -nostats)
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "info", "-nostats", "-nostdin", *(pace or streaming.pace()), *seek, *inp,
            "-map", "0:v:0?", "-map", f"0:a:{max(0, int(audio))}?", "-sn", "-dn", *vid, *aud,
            "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]


def hls_pre(start=0.0, pace=None):
    """Input options for the HLS output (iPad/iPhone): the same speed limit and start time."""
    return (pace or streaming.pace()) + (["-ss", f"{start:.2f}"] if start > 0 else [])


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


def forget(title_id):
    """Take a movie or show off Continue Watching: drop where it was stopped, keep what was watched. A show's
    Up next tile stays away until you watch another of its episodes."""
    db.run("DELETE FROM vod_progress WHERE title_id=? AND watched=0", (title_id,))
    db.run("INSERT OR REPLACE INTO vod_dismissed(title_id, at) VALUES (?,?)", (title_id, int(time.time())))
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
    """The title's poster, else (your own copy often has none) another version's: _borrow_details."""
    r = db.one("SELECT kind, search, year, tmdb, poster FROM vod_titles WHERE id=?", (title_id,))
    if not r:
        return ""
    if (r["poster"] or "").strip():
        return r["poster"].strip()
    o = db.one("""SELECT poster FROM vod_titles WHERE poster!='' AND
                  ((kind=? AND search=? AND year=?) OR (kind=? AND tmdb!='' AND tmdb=?)) LIMIT 1""",
               (r["kind"], r["search"], r["year"], r["kind"], r["tmdb"] or ""))
    return (o["poster"] or "").strip() if o else ""


def _shrink(data, width=POSTER_W):
    """JPEG `width` wide (kept small, never enlarged), or None when ffmpeg can't read it."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0",
                            "-vf", f"scale='min({width},iw)':-2", "-frames:v", "1", "-q:v", "4",
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
    return small_image(poster_url(title_id))


def small_image(url, width=POSTER_W):
    """Path of a small JPEG copy of the image at `url` (made on first use), or None. Also used for
    the guide's show pictures."""
    global _poster_made
    if not url.lower().startswith(("http://", "https://")):
        return None
    folder = poster_dir()
    key = url if width == POSTER_W else f"{width}:{url}"
    path = folder / (hashlib.sha1(key.encode()).hexdigest()[:24] + ".jpg")     # by address: a new poster makes a new file
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
        small = _shrink(data, width) if data else None
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
