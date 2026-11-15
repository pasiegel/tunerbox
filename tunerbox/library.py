"""Local library: your own movies and TV shows in On Demand, read straight from a network share (SMB)
or a folder TunerBox can see.

Nothing is written next to your videos. Each library is one folder of movies or one folder of TV shows;
a scan walks it, turns folder and file names into titles ("Dune Part Two (2024)", "Severance - S02E04")
and stores them in On Demand's own tables (vod_titles / vod_episodes) under a negative source id, so
browsing, search, Continue Watching and My List work exactly as for playlist titles. Details and
posters come from TMDB (The Movie Database) when an API key is set in Setup; text goes in the database,
pictures in the data folder's poster cache.

Network shares are read with a built-in SMB client (smbprotocol), so the share doesn't have to be
mounted on the computer or in Docker. Playing serves the file to TunerBox's own ffmpeg over HTTP
(/api/library/file, this computer only) with byte ranges, so skipping works.
"""
import hashlib
import json
import logging
import os
import re
import stat as statmod
import threading
import time
from pathlib import PurePosixPath

import httpx
from rapidfuzz import fuzz

from . import db

log = logging.getLogger("tunerbox.library")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lib_shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,              -- movie | series
    type TEXT NOT NULL DEFAULT 'smb',-- smb | folder
    server TEXT DEFAULT '',          -- smb: host name or address
    share TEXT DEFAULT '',           -- smb: share name
    path TEXT DEFAULT '',            -- smb: folder inside the share ('' = the whole share); folder: full path
    port INTEGER DEFAULT 445,
    username TEXT DEFAULT '',
    password TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    status TEXT DEFAULT '',          -- 'ok: …' or 'error: …' from the last scan
    scanned_at INTEGER DEFAULT 0,
    count INTEGER DEFAULT 0          -- titles found by the last scan
);
CREATE TABLE IF NOT EXISTS lib_meta (
    title_id TEXT PRIMARY KEY,
    tmdb_id INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,         -- how sure the automatic match was, 0-100
    state TEXT DEFAULT '',           -- '' not looked up | ok | weak | none | pinned | ignored
    query TEXT DEFAULT '',           -- the name and year it was looked up with
    backdrop TEXT DEFAULT '',
    fetched_at INTEGER DEFAULT 0,    -- details downloaded
    tried_at INTEGER DEFAULT 0       -- last lookup (a miss is retried after a while)
);
"""
# On Demand lists titles of enabled sources: playlists, plus each library as source -<id>
VIEW = """
DROP VIEW IF EXISTS vod_sources;
CREATE VIEW vod_sources AS
    SELECT id, name, type, enabled, vod_tags FROM sources
    UNION ALL SELECT -id, name, 'library', enabled, '' FROM lib_shares;
"""

KINDS = ("movie", "series")
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".mts", ".wmv", ".mpg", ".mpeg",
             ".webm", ".flv", ".vob", ".divx", ".ogv", ".3gp"}
SKIP_FILE = re.compile(r"(^|[\W_])(sample|trailer|featurette|behind[\W_]?the[\W_]?scenes|deleted[\W_]?scenes)([\W_]|$)", re.I)
SKIP_DIR = re.compile(r"^(extras?|featurettes?|behind the scenes|deleted scenes|interviews|scenes|shorts|trailers?|"
                      r"samples?|subs|subtitles|other|@eadir|#recycle|\$recycle\.bin|\.[^/]*)$", re.I)
JUNK = re.compile(r"\b(2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dolby[ .]?vision|dv|sdr|web[ .-]?dl|webrip|web|"
                  r"blu[ .-]?ray|bdrip|brrip|bdremux|remux|dvdrip|dvd|hdtv|hdrip|x26[45]|h[ .]?26[45]|hevc|avc|xvid|divx|"
                  r"aac|ac3|eac3|dts(-hd)?|truehd|atmos|ddp?[ .]?[257][ .][01]|10bit|8bit|proper|repack|extended|"
                  r"unrated|directors[ .]cut|imax|multi|subbed|dubbed|internal|limited)\b.*$", re.I)
YEAR_RX = re.compile(r"[\(\[]?\b((?:19|20)\d{2})\b[\)\]]?")
EPISODE_RX = (re.compile(r"^(.*?)[\s._-]*\bS(\d{1,2})[\s._-]*E(\d{1,3})(?:[\s._-]*E\d{1,3})*\b[\s._:-]*(.*)$", re.I),
              re.compile(r"^(.*?)[\s._-]*\b(\d{1,2})x(\d{2,3})\b[\s._:-]*(.*)$", re.I))
SEASON_DIR = re.compile(r"^(?:season|series|staffel|saison|temporada|s)[\s._-]*(\d{1,2})$", re.I)
BARE_EP = re.compile(r"^(?:e|ep|episode)?[\s._-]*(\d{1,3})\b[\s._-]*(.*)$", re.I)

TMDB = os.environ.get("TUNERBOX_TMDB_API", "https://api.themoviedb.org/3")      # can be pointed elsewhere for testing
IMG = "https://image.tmdb.org/t/p/"
GOOD_MATCH = 80             # an automatic match below this is listed under Needs review
REFRESH_DAYS = 30           # details are downloaded again after this long
RETRY_DAYS = 7              # a title TMDB didn't know is looked up again after this long
WALK_DEPTH = 6

DEFAULT_SETTINGS = {"tmdb_key": "", "language": "en-US", "video_mode": "", "auto_scan_hours": 24}
_meta_lock = threading.Lock()


class LibraryError(Exception):
    """Something the person can fix: wrong password, no such share, folder not found…"""


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)
        c.executescript(VIEW)


# ---------------------------------------------------------------- settings (kept in the database, not settings.json:
# the TMDB key and share passwords never go to pages that read the general settings)
def settings():
    try:
        saved = json.loads(db.get_meta("library_settings", "") or "{}")
    except (TypeError, ValueError):
        saved = {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def public_settings():
    s = settings()
    key = s.pop("tmdb_key")
    return {**s, "tmdb_key_set": bool(key), "tmdb_key_hint": ("…" + key[-4:]) if len(key) > 8 else ""}


def save_settings(changes):
    s = settings()
    for k, v in (changes or {}).items():
        if k not in DEFAULT_SETTINGS:
            raise ValueError(f"Unknown setting: {k}")
        if k == "auto_scan_hours":
            try:
                v = max(0, min(24 * 30, int(float(v))))
            except (TypeError, ValueError):
                raise ValueError("Scan every: a number of hours")
        elif k == "video_mode":
            v = str(v or "")
            if v not in ("", "copy", "h264", "roku720"):
                raise ValueError("Unknown video setting")
        else:
            v = str(v or "").strip()
        s[k] = v
    db.set_meta("library_settings", json.dumps(s))
    return public_settings()


# ---------------------------------------------------------------- where a library is
def parse_location(text):
    r"""'\\nas\Media\Movies', '//nas/Media/Movies' or 'smb://nas/Media/Movies' -> (server, share, path).
    Anything else is a folder path -> ('', '', path)."""
    t = (text or "").strip().strip('"')
    m = re.match(r"^(?:smb:)?[\\/]{2}([^\\/]+)[\\/]+([^\\/]+)[\\/]*(.*)$", t, re.I)
    if not m:
        return "", "", t
    server, share, rest = m.groups()
    port = None
    if ":" in server:
        server, _, p = server.partition(":")
        port = int(p) if p.isdigit() else None
    rest = "/".join(x for x in re.split(r"[\\/]+", rest) if x)
    return (server, share, rest) if port is None else (f"{server}:{port}", share, rest)


def location(sh):
    if sh["type"] == "folder":
        return sh["path"]
    server = sh["server"] + (f":{sh['port']}" if int(sh.get("port") or 445) != 445 else "")
    return "\\\\" + "\\".join(x for x in (server, sh["share"], sh["path"].replace("/", "\\")) if x)


class FS:
    """Reads one library: a network share over SMB, or a folder. Paths inside it are '/'-separated and
    relative to the library's folder."""

    def __init__(self, sh):
        self.sh = sh
        self.smb = sh["type"] != "folder"
        if self.smb:
            import smbclient  # noqa: F401  (fails early with a clear message if the package is missing)
            self.auth = {"username": sh.get("username") or None, "password": sh.get("password") or None,
                         "port": int(sh.get("port") or 445), "connection_timeout": 10}
            if not self.auth["username"]:
                self.auth["username"], self.auth["password"] = "guest", self.auth["password"] or ""

    def full(self, rel=""):
        rel = str(rel or "").strip("/")
        if not self.smb:
            return os.path.join(self.sh["path"], *rel.split("/")) if rel else self.sh["path"]
        parts = [self.sh["share"], *(p for p in (self.sh.get("path") or "").split("/") if p), *(p for p in rel.split("/") if p)]
        return "\\\\" + self.sh["server"] + "\\" + "\\".join(parts)

    def listdir(self, rel=""):
        """-> [(name, is_dir, size, mtime)]"""
        out = []
        try:
            if self.smb:
                import smbclient
                for e in smbclient.scandir(self.full(rel), **self.auth):
                    # what the folder listing says: entry.stat() would ask again, without the port and login
                    i = e.smb_info
                    is_dir = bool(i.file_attributes & 0x10)
                    try:
                        mtime = int(i.last_write_time.timestamp())
                    except (AttributeError, OverflowError, OSError, ValueError):
                        mtime = 0
                    out.append((e.name, is_dir, 0 if is_dir else int(i.end_of_file), mtime))
                return out
            for e in os.scandir(self.full(rel)):
                try:
                    st = e.stat()
                    is_dir = statmod.S_ISDIR(st.st_mode)
                    out.append((e.name, is_dir, 0 if is_dir else st.st_size, int(st.st_mtime)))
                except OSError:
                    continue
        except Exception as e:  # noqa: BLE001
            raise _friendly(e, self.sh, rel)
        return out

    def size(self, rel):
        try:
            if self.smb:
                import smbclient
                return smbclient.stat(self.full(rel), **self.auth).st_size
            return os.stat(self.full(rel)).st_size
        except Exception as e:  # noqa: BLE001
            raise _friendly(e, self.sh, rel)

    def open(self, rel):
        try:
            if self.smb:
                import smbclient
                return smbclient.open_file(self.full(rel), mode="rb", share_access="r", **self.auth)
            return open(self.full(rel), "rb")
        except Exception as e:  # noqa: BLE001
            raise _friendly(e, self.sh, rel)


LOGON, DENIED = 0xC000006D, 0xC0000022
NO_SHARE = 0xC00000CC
NOT_FOUND = {0xC0000225, 0xC0000034, 0xC000003A, 0xC000000F, 0xC0000033}


def _friendly(e, sh, rel=""):
    """A message for Setup instead of an SMB status code."""
    if isinstance(e, LibraryError):
        return e
    if sh["type"] != "folder":
        reset_connections()             # a failed login or a dropped connection isn't reused by the next try
    name, text = type(e).__name__, str(e)
    status = getattr(e, "ntstatus", None)
    where = location(sh) + (("\\" if sh["type"] != "folder" else "/") + rel if rel else "")
    if status == LOGON or name == "LogonFailure" or "STATUS_LOGON_FAILURE" in text:
        return LibraryError("The NAS didn't accept that username and password")
    if status == DENIED or name == "AccessDenied" or isinstance(e, PermissionError):
        return LibraryError(f"That account isn't allowed to read {where}")
    if status == NO_SHARE or name == "BadNetworkName" or "BAD_NETWORK_NAME" in text:
        return LibraryError(f"There's no share called \"{sh['share']}\" on {sh['server']}")
    if status in NOT_FOUND or isinstance(e, (FileNotFoundError, NotADirectoryError)):
        if sh["type"] != "folder" and not rel and not sh.get("path"):
            return LibraryError(f"There's no share called \"{sh['share']}\" on {sh['server']}")
        return LibraryError(f"Folder not found: {where}")
    low = text.lower()
    if isinstance(e, (ConnectionError, TimeoutError)) or any(
            w in low for w in ("failed to connect", "timed out", "name or service not known", "getaddrinfo",
                               "no route to host", "connection refused", "name resolution")):
        return LibraryError(f"No answer from {sh['server'] or where} (check the address, and that the NAS is on)")
    log.warning("Library error %s: %r", where, e)
    return LibraryError(f"Can't read {where}: {text or name}")


def reset_connections():
    """Forget open SMB sessions (after a login is changed), so the next read logs in again."""
    try:
        import smbclient
        smbclient.reset_connection_cache(fail_on_error=False)
    except Exception:  # noqa: BLE001
        pass


def check(sh, rel=""):
    """Can TunerBox read it? -> {ok, message, folders, videos}: the folders inside `rel` and how many
    videos are directly in it."""
    try:
        entries = FS(sh).listdir(rel)
    except LibraryError as e:
        return {"ok": False, "message": str(e), "folders": [], "videos": 0}
    folders = sorted((n for n, d, _, _ in entries if d and not SKIP_DIR.match(n)), key=str.lower)
    videos = sum(1 for n, d, _, _ in entries if not d and PurePosixPath(n).suffix.lower() in VIDEO_EXT)
    msg = f"Connected: {len(folders)} folder{'s' * (len(folders) != 1)}"
    if videos:
        msg += f", {videos} video{'s' * (videos != 1)}"
    return {"ok": True, "message": msg, "folders": folders, "videos": videos}


# ---------------------------------------------------------------- shares (Setup)
_FIELDS = ("name", "kind", "type", "server", "share", "path", "port", "username", "password", "enabled")


def shares(with_password=False):
    out = db.rows("SELECT * FROM lib_shares ORDER BY kind, name COLLATE NOCASE, id")
    for s in out:
        s["location"] = location(s)
        if not with_password:
            s["has_password"] = bool(s["password"])
            s["password"] = ""
        s["review"] = db.one("""SELECT count(*) n FROM vod_titles t LEFT JOIN lib_meta m ON m.title_id=t.id
                                WHERE t.source_id=? AND COALESCE(m.state,'') IN ('weak','none')""", (-s["id"],))["n"]
    return out


def share(sid):
    return db.one("SELECT * FROM lib_shares WHERE id=?", (sid,))


def clean(data, old=None):
    """Check what Setup sent for a library. A 'location' ('\\\\nas\\Media\\Movies' or a folder) fills
    type/server/share/path. A blank password keeps the saved one."""
    d = {**(old or {"type": "smb", "port": 445, "enabled": 1, "username": "", "password": "", "path": ""})}
    for k in _FIELDS:
        if k in data and data[k] is not None:
            d[k] = data[k]
    if data.get("location") is not None:
        server, sh, path = parse_location(data["location"])
        if not server and re.match(r"^(smb:)?[\\/]{2}", path.strip(), re.I):
            raise ValueError(r"Include the share name, like \\nas\Media\Movies")
        if server:
            if ":" in server:
                server, _, p = server.partition(":")
                d["port"] = int(p)
            d.update(type="smb", server=server, share=sh, path=path)
        else:
            d.update(type="folder", server="", share="", path=path)
    if old and not data.get("password"):
        d["password"] = old["password"]
    d["name"] = str(d.get("name") or "").strip()[:60]
    d["kind"] = d.get("kind") if d.get("kind") in KINDS else ""
    d["type"] = "folder" if d.get("type") == "folder" else "smb"
    d["server"], d["share"] = str(d.get("server") or "").strip().strip("\\/"), str(d.get("share") or "").strip().strip("\\/")
    d["path"] = str(d.get("path") or "").strip()
    if d["type"] == "smb":
        d["path"] = "/".join(x for x in re.split(r"[\\/]+", d["path"]) if x)
    try:
        d["port"] = int(d.get("port") or 445)
    except (TypeError, ValueError):
        d["port"] = 445
    d["enabled"] = 1 if d.get("enabled") in (1, True, "1", "true") else 0
    d["username"], d["password"] = str(d.get("username") or "").strip(), str(d.get("password") or "")
    if not d["kind"]:
        raise ValueError("Pick whether this folder has movies or TV shows")
    if d["type"] == "smb" and not (d["server"] and d["share"]):
        raise ValueError(r"Enter the network location, like \\nas\Media\Movies")
    if d["type"] == "folder" and not d["path"]:
        raise ValueError("Enter the folder")
    if not d["name"]:
        tail = (d["path"].replace("\\", "/").rstrip("/").split("/")[-1] if d["path"] else d["share"]) or "Library"
        d["name"] = tail
    return d


def add(data):
    d = clean(data)
    return db.run("""INSERT INTO lib_shares(name, kind, type, server, share, path, port, username, password, enabled)
                     VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  tuple(d[k] for k in _FIELDS))


def update(sid, data):
    """-> True when where it reads from or what it holds changed (it should be scanned again)."""
    old = share(sid)
    if not old:
        raise KeyError(sid)
    d = clean(data, old)
    db.run(f"UPDATE lib_shares SET {', '.join(k + '=?' for k in _FIELDS)} WHERE id=?", (*[d[k] for k in _FIELDS], sid))
    if (d["username"], d["password"]) != (old["username"], old["password"]):
        reset_connections()
    if d["name"] != old["name"]:
        db.run("UPDATE vod_titles SET category=? WHERE source_id=?", (d["name"], -sid))
    moved = any(d[k] != old[k] for k in ("kind", "type", "server", "share", "path", "port", "username", "password"))
    if moved and d["kind"] != old["kind"]:
        _drop_titles(sid)
    _vod_changed()
    return moved


def delete(sid):
    _drop_titles(sid)
    db.run("DELETE FROM lib_shares WHERE id=?", (sid,))
    _vod_changed()


def _drop_titles(sid):
    """A library's titles out of On Demand. Where you stopped and My List are kept (as for playlists)."""
    with db.conn() as c:
        c.execute("DELETE FROM lib_meta WHERE title_id IN (SELECT id FROM vod_titles WHERE source_id=?)", (-sid,))
        c.execute("DELETE FROM vod_episodes WHERE title_id IN (SELECT id FROM vod_titles WHERE source_id=?)", (-sid,))
        c.execute("DELETE FROM vod_titles WHERE source_id=?", (-sid,))


def _vod_changed():
    from . import vod
    vod._changed()


# ---------------------------------------------------------------- names
def _hash(text):
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]


def parse_name(raw):
    """'Dune.Part.Two.2024.2160p.WEB-DL.x265-GRP' or 'Dune Part Two (2024)' -> ('Dune Part Two', '2024')."""
    s = str(raw or "")
    if " " not in s.strip():
        s = re.sub(r"[._]+", " ", s)
    s = s.replace("_", " ")
    s = re.sub(r"\[(?!(?:19|20)\d\d\])[^\]]*\]|\{[^}]*\}", " ", s)     # [Group] and {tmdb-123} tags, not [1995]
    year, cut = "", None
    latest = time.localtime().tm_year + 1
    for m in YEAR_RX.finditer(s):
        if m.start() > 0 and int(m.group(1)) <= latest:
            year, cut = m.group(1), m.start()
    name = s[:cut] if cut is not None else s
    name = JUNK.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" -–.,([")
    return (name or str(raw).strip()), year


def parse_episode(filename, season_hint=None):
    """'Severance - S02E04 - Woe's Hollow.mkv' -> ('Severance', 2, 4, "Woe's Hollow"), else None.
    A file in a 'Season 2' folder may be just '04 - Title.mkv'."""
    stem = PurePosixPath(filename).stem
    for rx in EPISODE_RX:
        m = rx.match(stem)
        if m:
            show = m.group(1)
            title = m.group(4)
            if " " not in title.strip():
                title = re.sub(r"[._]+", " ", title)
            title = JUNK.sub("", title).strip(" -–.")
            return (parse_name(show)[0] if show.strip(" -._") else ""), int(m.group(2)), int(m.group(3)), title
    if season_hint is not None:
        m = BARE_EP.match(stem)
        if m and int(m.group(1)) > 0:
            return "", season_hint, int(m.group(1)), JUNK.sub("", m.group(2)).strip(" -–.")
    return None


def _walk(fs, rel="", depth=0, progress=None, found=None):
    """Every video file under `rel`: -> [(relative path, size, mtime)]."""
    found = [] if found is None else found
    for name, is_dir, size, mtime in fs.listdir(rel):
        path = f"{rel}/{name}" if rel else name
        if is_dir:
            if depth < WALK_DEPTH and not SKIP_DIR.match(name):
                _walk(fs, path, depth + 1, progress, found)
        elif PurePosixPath(name).suffix.lower() in VIDEO_EXT and not SKIP_FILE.search(name) and size != 0:
            found.append((path, size, mtime))
            if progress and len(found) % 50 == 0:
                progress(len(found))
    return found


def movies_from(files):
    """Group video files into movies. A file alone in its own folder is named after the folder
    ('Dune Part Two (2024)/movie.mkv'); otherwise after the file. Several parts or versions of one
    movie in one folder: the biggest file plays."""
    by_dir = {}
    for path, size, mtime in files:
        d = path.rsplit("/", 1)[0] if "/" in path else ""
        by_dir.setdefault(d, []).append((path, size, mtime))
    out = {}
    for d, group in by_dir.items():
        if d:
            folder = d.rsplit("/", 1)[-1]
            fname, fyear = parse_name(folder)
            names = {parse_name(PurePosixPath(p).stem) for p, _, _ in group}
            if len(group) == 1 or len(names) == 1 or fyear:
                path, size, mtime = max(group, key=lambda g: g[1])
                if not fyear:                       # the folder has no year: the file may
                    n2, y2 = parse_name(PurePosixPath(path).stem)
                    fyear = y2 if n2.lower() == fname.lower() else fyear
                out[d] = {"key": d, "name": fname, "year": fyear, "path": path, "size": size, "mtime": mtime}
                continue
        for path, size, mtime in group:
            name, year = parse_name(PurePosixPath(path).stem)
            out[path] = {"key": path, "name": name, "year": year, "path": path, "size": size, "mtime": mtime}
    return list(out.values())


def shows_from(files):
    """Group video files into shows and episodes. The first folder under the library is the show
    ('Severance/Season 02/Severance - S02E04.mkv'); a file right in the library is grouped by the show
    name in front of S01E02."""
    shows, skipped = {}, 0
    for path, size, mtime in files:
        parts = path.split("/")
        season_hint = None
        for p in parts[1:-1]:
            m = SEASON_DIR.match(p.strip())
            if m:
                season_hint = int(m.group(1))
        ep = parse_episode(parts[-1], season_hint)
        if not ep:
            skipped += 1
            continue
        file_show, season, number, title = ep
        if len(parts) > 1:
            key, (name, year) = parts[0], parse_name(parts[0])
        elif file_show:
            name, year = parse_name(file_show)
            key = name.lower()
        else:
            skipped += 1
            continue
        s = shows.setdefault(key, {"key": key, "name": name, "year": year, "episodes": {}})
        cur = s["episodes"].get((season, number))
        if not cur or size > cur["size"]:
            s["episodes"][(season, number)] = {"season": season, "episode": number, "name": title, "path": path,
                                               "size": size, "mtime": mtime}
    return list(shows.values()), skipped


# ---------------------------------------------------------------- scanning
def title_id(sid, kind, key):
    return f"L{sid}:{'m' if kind == 'movie' else 's'}{_hash(key.lower())}"


def episode_id(sid, show_key, season, number):
    return f"L{sid}:e{_hash(f'{show_key.lower()}|{season}|{number}')}"


def scan_share(sh, progress=None):
    """Read one library into On Demand. -> message. Titles already matched keep their details."""
    from . import vod
    sid, kind, now = sh["id"], sh["kind"], int(time.time())
    fs = FS(sh)
    try:
        files = _walk(fs, progress=(lambda n: progress(f"Reading {sh['name']}: {n} videos")) if progress else None)
    except LibraryError as e:
        db.run("UPDATE lib_shares SET status=? WHERE id=?", (f"error: {e}", sid))
        raise
    titles, episodes, skipped = [], [], 0
    if kind == "movie":
        for m in movies_from(files):
            titles.append({"id": title_id(sid, kind, m["key"]), "name": m["name"], "year": m["year"], "url": m["path"],
                           "ext": PurePosixPath(m["path"]).suffix.lstrip(".").lower(), "mtime": m["mtime"]})
    else:
        found, skipped = shows_from(files)
        for s in found:
            tid = title_id(sid, kind, s["key"])
            titles.append({"id": tid, "name": s["name"], "year": s["year"], "url": "", "ext": "",
                           "mtime": max(e["mtime"] for e in s["episodes"].values())})
            for e in s["episodes"].values():
                episodes.append({"id": episode_id(sid, s["key"], e["season"], e["episode"]), "title_id": tid,
                                 "season": e["season"], "episode": e["episode"], "name": e["name"], "url": e["path"],
                                 "ext": PurePosixPath(e["path"]).suffix.lstrip(".").lower()})
    rows = [{**t, "source_id": -sid, "kind": kind, "category": sh["name"], "search": vod.fold(t["name"]),
             "added": t["mtime"] or now, "seen_at": now} for t in titles]
    with db.conn() as c:
        c.execute("UPDATE vod_titles SET seen_at=0 WHERE source_id=?", (-sid,))      # what isn't found again goes
        # new titles get their names from the files; known ones keep what TMDB gave them
        c.executemany("""INSERT INTO vod_titles(id, source_id, kind, name, category, year, url, ext, added, seen_at, search, hidden)
                         VALUES (:id,:source_id,:kind,:name,:category,:year,:url,:ext,:added,:seen_at,:search,0)
                         ON CONFLICT(id) DO UPDATE SET url=excluded.url, ext=excluded.ext, category=excluded.category,
                           seen_at=excluded.seen_at, added=MAX(vod_titles.added, excluded.added),
                           duration=CASE WHEN vod_titles.url!=excluded.url THEN 0 ELSE vod_titles.duration END""", rows)
        c.execute("DELETE FROM vod_titles WHERE source_id=? AND seen_at=0", (-sid,))
        c.execute("UPDATE vod_titles SET hidden=1 WHERE source_id=? AND id IN (SELECT title_id FROM lib_meta WHERE state='ignored')", (-sid,))
        if kind == "series":
            c.executemany("""INSERT INTO vod_episodes(id, title_id, season, episode, name, url, ext)
                             VALUES (:id,:title_id,:season,:episode,:name,:url,:ext)
                             ON CONFLICT(id) DO UPDATE SET url=excluded.url, ext=excluded.ext,
                               duration=CASE WHEN vod_episodes.url!=excluded.url THEN 0 ELSE vod_episodes.duration END,
                               name=CASE WHEN vod_episodes.name='' THEN excluded.name ELSE vod_episodes.name END""", episodes)
            c.execute("CREATE TEMP TABLE IF NOT EXISTS _seen(id TEXT PRIMARY KEY)")
            c.execute("DELETE FROM _seen")
            c.executemany("INSERT OR IGNORE INTO _seen(id) VALUES (?)", [(e["id"],) for e in episodes])
            c.execute("""DELETE FROM vod_episodes WHERE title_id IN (SELECT id FROM vod_titles WHERE source_id=?)
                         AND id NOT IN (SELECT id FROM _seen)""", (-sid,))
        c.execute("DELETE FROM vod_episodes WHERE title_id NOT IN (SELECT id FROM vod_titles)")
        c.execute("DELETE FROM lib_meta WHERE title_id NOT IN (SELECT id FROM vod_titles)")
        n = len(titles)
        what = (f"{n} movie{'s' * (n != 1)}" if kind == "movie"
                else f"{n} show{'s' * (n != 1)}, {len(episodes)} episode{'s' * (len(episodes) != 1)}")
        if skipped:
            what += f" ({skipped} file{'s' * (skipped != 1)} without an episode number left out)"
        c.execute("UPDATE lib_shares SET status=?, scanned_at=?, count=? WHERE id=?", (f"ok: {what}", now, n, sid))
    _vod_changed()
    return what


def scan_all(progress=None, share_id=None, full_refresh=False):
    """Scan every enabled library (or one), then look up details for titles that need them. -> message."""
    progress = progress or (lambda *a, **k: None)
    todo = [s for s in db.rows("SELECT * FROM lib_shares WHERE enabled=1 ORDER BY id") if share_id in (None, s["id"])]
    done, errors = [], []
    for i, sh in enumerate(todo):
        progress(f"Reading {sh['name']}", i, len(todo))
        try:
            done.append(f"{sh['name']}: {scan_share(sh, progress)}")
        except LibraryError as e:
            errors.append(f"{sh['name']}: {e}")
            log.warning("Library %s: %s", sh["name"], e)
    db.set_meta("library_scanned", int(time.time()))
    looked = ""
    if settings()["tmdb_key"]:
        try:
            looked = fetch_details(progress, share_id, full_refresh)
        except LibraryError as e:
            errors.append(str(e))
    msg = "; ".join(done + errors) or "No libraries to scan"
    return msg + (f". {looked}" if looked else "")


def scan_due(now=None):
    hours = float(settings()["auto_scan_hours"] or 0)
    if hours <= 0 or not db.one("SELECT 1 FROM lib_shares WHERE enabled=1 LIMIT 1"):
        return False
    return (now or time.time()) - int(db.get_meta("library_scanned", 0) or 0) > hours * 3600


# ---------------------------------------------------------------- TMDB
def _tmdb(path, key=None, **params):
    key = key if key is not None else settings()["tmdb_key"]
    if not key:
        raise LibraryError("Add a TMDB API key in Setup › Local library to download details and posters")
    headers = {"Accept": "application/json"}
    if key.startswith("eyJ"):                       # a v4 "API Read Access Token"
        headers["Authorization"] = f"Bearer {key}"
    else:                                           # a v3 "API Key"
        params["api_key"] = key
    params.setdefault("language", settings()["language"] or "en-US")
    for attempt in range(4):
        try:
            r = httpx.get(TMDB + path, params=params, headers=headers, timeout=15)
        except httpx.HTTPError as e:
            if attempt == 3:
                raise LibraryError(f"Can't reach TMDB: {e}")
            time.sleep(1 + attempt)
            continue
        if r.status_code == 429:                    # too many requests: wait as told
            time.sleep(min(10, float(r.headers.get("Retry-After") or 2)))
            continue
        if r.status_code == 401:
            raise LibraryError("TMDB didn't accept the API key")
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise LibraryError(f"TMDB answered {r.status_code}")
        return r.json()
    raise LibraryError("TMDB is busy; try again in a minute")


def test_key(key):
    """-> {ok, message}"""
    try:
        _tmdb("/configuration", key=key.strip())
        return {"ok": True, "message": "TMDB key works"}
    except LibraryError as e:
        return {"ok": False, "message": str(e)}


def _year_of(r):
    return str(r.get("release_date") or r.get("first_air_date") or "")[:4]


def _score(name, year, r):
    """How well a TMDB result fits a name and year, 0-100."""
    from .vod import fold
    a = fold(name)
    best = max(fuzz.ratio(a, fold(r.get("title") or r.get("name") or "")),
               fuzz.ratio(a, fold(r.get("original_title") or r.get("original_name") or "")))
    ry = _year_of(r)
    if year and ry:
        diff = abs(int(year) - int(ry))
        best += 8 if diff == 0 else 3 if diff == 1 else -20
    elif year and not ry:
        best -= 10
    return max(0, min(100, round(best)))


def search_tmdb(kind, query, year=""):
    """Candidates for Fix match. -> [{id, name, year, overview, poster, score}]"""
    ep = "/search/movie" if kind == "movie" else "/search/tv"
    params = {"query": query, "include_adult": "false"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year
    d = _tmdb(ep, **params) or {}
    res = d.get("results") or []
    if not res and year:
        d = _tmdb(ep, query=query, include_adult="false") or {}
        res = d.get("results") or []
    out = [{"id": r["id"], "name": r.get("title") or r.get("name") or "", "year": _year_of(r),
            "overview": (r.get("overview") or "")[:300], "poster": IMG + "w185" + r["poster_path"] if r.get("poster_path") else "",
            "score": _score(query, year, r), "popularity": r.get("popularity") or 0} for r in res[:20]]
    return out


def _best(kind, name, year):
    cands = search_tmdb(kind, name, year)
    if not cands:
        return None, 0
    top = max(cands[:8], key=lambda c: (c["score"], c["popularity"]))
    return top["id"], top["score"]


def _apply(t, tmdb_id, score, state):
    """Download a title's details from TMDB and store them. -> True if TMDB had it."""
    kind = t["kind"]
    d = _tmdb(f"/{'movie' if kind == 'movie' else 'tv'}/{tmdb_id}", append_to_response="credits")
    if not d:
        return False
    cast = [c.get("name") for c in (d.get("credits") or {}).get("cast") or [] if c.get("name")][:8]
    if kind == "movie":
        crew = [c.get("name") for c in (d.get("credits") or {}).get("crew") or [] if c.get("job") == "Director"]
    else:
        crew = [c.get("name") for c in d.get("created_by") or [] if c.get("name")]
    name = d.get("title") or d.get("name") or t["name"]
    votes = d.get("vote_count") or 0
    from .vod import fold
    # the length isn't taken from TMDB: your copy may be a different cut, and the player's seek bar needs the
    # real one (measured from the file the first time it plays)
    db.run("""UPDATE vod_titles SET name=?, search=?, year=?, plot=?, genre=?, rating=?, actors=?, director=?, poster=?,
              info_at=? WHERE id=?""",
           (name, fold(name), _year_of(d) or t["year"], d.get("overview") or "",
            ", ".join(g["name"] for g in d.get("genres") or [] if g.get("name")),
            f"{d.get('vote_average') or 0:.1f}" if votes >= 5 else "", ", ".join(cast), ", ".join(crew),
            IMG + "w500" + d["poster_path"] if d.get("poster_path") else "", int(time.time()), t["id"]))
    if kind == "series":
        seasons = [r["season"] for r in db.rows("SELECT DISTINCT season FROM vod_episodes WHERE title_id=?", (t["id"],))]
        for n in seasons:
            s = _tmdb(f"/tv/{tmdb_id}/season/{n}") or {}
            for e in s.get("episodes") or []:
                db.run("""UPDATE vod_episodes SET name=CASE WHEN ?!='' THEN ? ELSE name END, plot=?, aired=?
                          WHERE title_id=? AND season=? AND episode=?""",
                       (e.get("name") or "", e.get("name") or "", e.get("overview") or "", e.get("air_date") or "",
                        t["id"], n, e.get("episode_number")))
    now = int(time.time())
    db.run("""INSERT INTO lib_meta(title_id, tmdb_id, score, state, query, backdrop, fetched_at, tried_at)
              VALUES (?,?,?,?,?,?,?,?)
              ON CONFLICT(title_id) DO UPDATE SET tmdb_id=excluded.tmdb_id, score=excluded.score, state=excluded.state,
                backdrop=excluded.backdrop, fetched_at=excluded.fetched_at, tried_at=excluded.tried_at,
                query=CASE WHEN excluded.query!='' THEN excluded.query ELSE lib_meta.query END""",
           (t["id"], tmdb_id, score, state, "", IMG + "w1280" + d["backdrop_path"] if d.get("backdrop_path") else "", now, now))
    return True


def _original(t):
    """The name and year from the files (TMDB's name replaces the title's once matched)."""
    m = db.one("SELECT query FROM lib_meta WHERE title_id=?", (t["id"],))
    if m and m["query"]:
        try:
            q = json.loads(m["query"])
            return q["name"], q["year"]
        except (ValueError, KeyError, TypeError):
            pass
    return t["name"], t["year"]


def lookup(t):
    """Find a title on TMDB and store its details. -> its state."""
    name, year = _original(t)
    query = json.dumps({"name": name, "year": year})
    tmdb_id, score = _best(t["kind"], name, year)
    now = int(time.time())
    if tmdb_id and _apply(t, tmdb_id, score, "ok" if score >= GOOD_MATCH else "weak"):
        db.run("UPDATE lib_meta SET query=? WHERE title_id=?", (query, t["id"]))
        return "ok" if score >= GOOD_MATCH else "weak"
    db.run("""INSERT INTO lib_meta(title_id, state, query, tried_at) VALUES (?, 'none', ?, ?)
              ON CONFLICT(title_id) DO UPDATE SET state=CASE WHEN lib_meta.state IN ('pinned','ignored') THEN lib_meta.state
                ELSE 'none' END, query=excluded.query, tried_at=excluded.tried_at""", (t["id"], query, now))
    return "none"


def fetch_details(progress=None, share_id=None, full_refresh=False):
    """Look up titles never looked up, misses after RETRY_DAYS, and refresh matched ones after REFRESH_DAYS. -> message."""
    progress = progress or (lambda *a, **k: None)
    now = int(time.time())
    w, a = ("AND t.source_id=?", [-share_id]) if share_id else ("", [])
    rows = db.rows(f"""SELECT t.*, COALESCE(m.state,'') state, COALESCE(m.tmdb_id,0) tmdb_id, COALESCE(m.score,0) score,
                              COALESCE(m.fetched_at,0) fetched_at, COALESCE(m.tried_at,0) tried_at
                       FROM vod_titles t JOIN lib_shares s ON -s.id=t.source_id AND s.enabled=1
                       LEFT JOIN lib_meta m ON m.title_id=t.id WHERE 1=1 {w}""", a)
    todo = []
    for r in rows:
        st = r["state"]
        if st == "ignored":
            continue
        if st == "":
            todo.append(r)
        elif st == "none" and (full_refresh or now - r["tried_at"] > RETRY_DAYS * 86400):
            todo.append(r)
        elif st in ("ok", "weak", "pinned") and (full_refresh or now - r["fetched_at"] > REFRESH_DAYS * 86400):
            todo.append(r)
    found = missed = 0
    with _meta_lock:
        for i, t in enumerate(todo):
            progress("Downloading details from TMDB", i, len(todo))
            try:
                if t["state"] in ("ok", "weak", "pinned") and t["tmdb_id"]:
                    ok = _apply(t, t["tmdb_id"], t["score"], t["state"])
                else:
                    ok = lookup(t) != "none"
            except LibraryError as e:
                if "API key" in str(e) or "reach" in str(e):
                    raise
                log.warning("TMDB %s: %s", t["name"], e)
                ok = False
            found += ok
            missed += not ok
    _vod_changed()
    if not todo:
        return ""
    return f"Details for {found} title{'s' * (found != 1)}" + (f", {missed} not found on TMDB" if missed else "")


# ---------------------------------------------------------------- Setup: review and fix matches
def review(share_id=None, flt="review", q="", limit=300):
    w, a = ["1=1"], []
    if share_id:
        w.append("t.source_id=?")
        a.append(-share_id)
    if flt == "review":
        w.append("COALESCE(m.state,'') IN ('weak','none')")
    elif flt == "ignored":
        w.append("m.state='ignored'")
    elif flt == "pending":
        w.append("COALESCE(m.state,'')=''")
    if q:
        from .vod import fold
        w.append("(t.search LIKE ? OR t.url LIKE ?)")
        a += [f"%{fold(q)}%", f"%{q}%"]
    rows = db.rows(f"""SELECT t.id, t.kind, t.name, t.year, t.url, t.poster, t.category, t.source_id,
                              COALESCE(m.state,'') state, COALESCE(m.score,0) score, COALESCE(m.tmdb_id,0) tmdb_id,
                              COALESCE(m.query,'') query,
                              (SELECT url FROM vod_episodes e WHERE e.title_id=t.id ORDER BY season, episode LIMIT 1) ep_url,
                              (SELECT count(*) FROM vod_episodes e WHERE e.title_id=t.id) eps
                       FROM vod_titles t JOIN lib_shares s ON -s.id=t.source_id
                       LEFT JOIN lib_meta m ON m.title_id=t.id WHERE {' AND '.join(w)}
                       ORDER BY CASE COALESCE(m.state,'') WHEN 'none' THEN 0 WHEN 'weak' THEN 1 WHEN '' THEN 2 ELSE 3 END,
                                t.name COLLATE NOCASE LIMIT ?""", (*a, limit))
    for r in rows:
        try:
            q0 = json.loads(r.pop("query") or "{}")
        except ValueError:
            q0 = {}
        r["file_name"], r["file_year"] = q0.get("name") or r["name"], q0.get("year") or r["year"]
        p = r.pop("url") or r.pop("ep_url") or ""
        r.pop("ep_url", None)
        r["file"] = p.rsplit("/", 1)[0] + "/" if r["kind"] == "series" and "/" in p else p
        r["share_id"] = -r.pop("source_id")
    return rows


def counts():
    r = db.one("""SELECT count(*) n, SUM(t.kind='movie') movies, SUM(t.kind='series') shows,
                         SUM(COALESCE(m.state,'') IN ('weak','none')) review
                  FROM vod_titles t JOIN lib_shares s ON -s.id=t.source_id AND s.enabled=1
                  LEFT JOIN lib_meta m ON m.title_id=t.id""")
    eps = db.one("""SELECT count(*) n FROM vod_episodes e JOIN vod_titles t ON t.id=e.title_id
                    JOIN lib_shares s ON -s.id=t.source_id AND s.enabled=1""")["n"]
    return {"titles": r["n"] or 0, "movies": r["movies"] or 0, "shows": r["shows"] or 0, "episodes": eps,
            "review": r["review"] or 0}


def _lib_title(tid):
    return db.one("SELECT t.* FROM vod_titles t JOIN lib_shares s ON -s.id=t.source_id WHERE t.id=?", (tid,))


def pin(tid, tmdb_id):
    """Fix match: use this TMDB title. Kept through rescans."""
    t = _lib_title(tid)
    if not t:
        raise KeyError(tid)
    name, year = _original(t)
    with _meta_lock:
        if not _apply(t, int(tmdb_id), 100, "pinned"):
            raise LibraryError("TMDB doesn't have that title any more")
    db.run("UPDATE lib_meta SET query=? WHERE title_id=?", (json.dumps({"name": name, "year": year}), tid))
    _vod_changed()
    return {"ok": True}


def unmatch(tid):
    """Back to the name from the file, without details (and don't look it up again by itself)."""
    t = _lib_title(tid)
    if not t:
        raise KeyError(tid)
    name, year = _original(t)
    from .vod import fold
    db.run("""UPDATE vod_titles SET name=?, search=?, year=?, plot='', genre='', rating='', actors='', director='', poster='',
              info_at=0 WHERE id=?""", (name, fold(name), year, tid))
    db.run("""INSERT INTO lib_meta(title_id, state, query, tried_at) VALUES (?, 'none', ?, ?)
              ON CONFLICT(title_id) DO UPDATE SET state='none', tmdb_id=0, score=0, backdrop='', fetched_at=0,
                tried_at=excluded.tried_at""", (tid, json.dumps({"name": name, "year": year}), int(time.time()) + 3650 * 86400))
    _vod_changed()
    return {"ok": True}


def ignore(tid, on=True):
    """Leave a title out of On Demand (a stray file, a home video…), or bring it back."""
    t = _lib_title(tid)
    if not t:
        raise KeyError(tid)
    if on:
        db.run("""INSERT INTO lib_meta(title_id, state) VALUES (?, 'ignored')
                  ON CONFLICT(title_id) DO UPDATE SET state='ignored'""", (tid,))
    else:
        m = db.one("SELECT tmdb_id FROM lib_meta WHERE title_id=?", (tid,))
        db.run("UPDATE lib_meta SET state=? WHERE title_id=?", ("pinned" if m and m["tmdb_id"] else "", tid))
    db.run("UPDATE vod_titles SET hidden=? WHERE id=?", (int(on), tid))
    _vod_changed()
    return {"ok": True}


def relookup(tid):
    t = _lib_title(tid)
    if not t:
        raise KeyError(tid)
    with _meta_lock:
        st = lookup(t)
    _vod_changed()
    return {"state": st}


# ---------------------------------------------------------------- playing
def is_library(source_id):
    return (source_id or 0) < 0


def file_of(item_id):
    """(share row, path inside it) of a movie or episode, or None."""
    e = db.one("SELECT title_id, url FROM vod_episodes WHERE id=?", (item_id,))
    t = db.one("SELECT source_id, url FROM vod_titles WHERE id=?", (e["title_id"] if e else item_id,))
    if not t or not is_library(t["source_id"]):
        return None
    sh = share(-t["source_id"])
    path = (e or t)["url"]
    return (sh, path) if sh and path else None


def play_url(item_id):
    from . import media
    return f"{media._base()}/api/library/file/{item_id}"


RANGE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 1024 * 1024


def open_range(item_id, range_header=""):
    """For /api/library/file: -> (status, headers, iterator of bytes)."""
    found = file_of(item_id)
    if not found:
        raise KeyError(item_id)
    sh, path = found
    fs = FS(sh)
    size = fs.size(path)
    start, end = 0, size - 1
    m = RANGE.match(range_header or "")
    status = 200
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                                   # the last N bytes
            start, end = max(0, size - int(m.group(2))), size - 1
        if start >= size:
            return 416, {"Content-Range": f"bytes */{size}"}, iter(())
        status = 206
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    f = fs.open(path)

    def body():
        try:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                data = f.read(min(CHUNK, left))
                if not data:
                    break
                left -= len(data)
                yield data
        finally:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass
    return status, headers, body()
