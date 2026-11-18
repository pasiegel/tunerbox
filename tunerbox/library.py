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
CREATE TABLE IF NOT EXISTS lib_files (   -- the file behind each movie and episode, as the last scan found it
    item_id TEXT PRIMARY KEY,        -- a movie's title id, or an episode id
    title_id TEXT NOT NULL,
    share_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    size INTEGER DEFAULT 0,          -- with the extension, how a renamed or moved file is recognised
    mtime INTEGER DEFAULT 0,
    probed_at INTEGER DEFAULT 0      -- its length was measured (or tried) then: see measure_lengths
);
CREATE INDEX IF NOT EXISTS ix_lib_files_title ON lib_files(title_id);
CREATE TABLE IF NOT EXISTS lib_dirs (     -- what each folder held when it was last read: see _Dirs
    share_id INTEGER NOT NULL,
    rel TEXT NOT NULL,
    mtime INTEGER DEFAULT 0,         -- the folder's modified time, from its parent's listing
    listed_at INTEGER DEFAULT 0,
    steady INTEGER DEFAULT 0,        -- read twice and found the same (or everything in it was old): safe to reuse
    entries TEXT DEFAULT '[]',       -- [[name, is_dir, size, mtime]]
    PRIMARY KEY (share_id, rel)
);
CREATE TABLE IF NOT EXISTS lib_missing ( -- titles a scan didn't find: hidden, and removed after remove_after_days
    title_id TEXT PRIMARY KEY,
    share_id INTEGER NOT NULL,
    since INTEGER NOT NULL
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
SMALL_FILE = 50 * 1024 * 1024   # a renamed file this small is only recognised with the same modified time too

# auto_scan_hours: only read once, to set up the "scan" task (libtasks.py) for people who had changed it
DEFAULT_SETTINGS = {"tmdb_key": "", "language": "en-US", "video_mode": "", "auto_scan_hours": 24, "remove_after_days": 30}
DETAIL_COLS = "name, search, year, plot, genre, rating, actors, director, poster, info_at"
_meta_lock = threading.Lock()


class LibraryError(Exception):
    """Something the person can fix: wrong password, no such share, folder not found…"""


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)
        c.executescript(VIEW)
    from . import libtasks
    libtasks.init()


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
        elif k == "remove_after_days":
            try:
                v = max(0, min(365, int(float(v))))
            except (TypeError, ValueError):
                raise ValueError("Remove missing videos after: a number of days")
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
                # An open share (Unraid "Public", Samba "guest ok"): log in as guest over NTLM. The password
                # mustn't be empty (an empty one makes the client look for a Kerberos ticket instead), and a
                # guest session has no signing key, so signing and the negotiate check can't be required.
                import smbclient
                self.auth.update(username="guest", password=self.auth["password"] or "guest",
                                 auth_protocol="ntlm", require_signing=False)
                smbclient.ClientConfig(require_secure_negotiate=False)

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
                    if is_dir and os.name == "nt":
                        # Windows answers scandir from the parent folder's own index, where a subfolder's time
                        # isn't updated when a file inside it changes: ask the subfolder itself, so a quick
                        # scan sees the new file
                        st = os.stat(e.path)
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
    name, text = type(e).__name__, str(e)
    status = getattr(e, "ntstatus", None)
    low = text.lower()
    if sh["type"] != "folder" and (status in (LOGON, DENIED) or isinstance(e, (ConnectionError, TimeoutError))
                                   or "connect" in low or "timed out" in low):
        reset_connections()             # a failed login or a dropped connection isn't reused by the next try
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
        # never send the saved password to a different server: a changed location needs it typed again
        moved = any(str(d.get(k) or "") != str(old.get(k) or "") for k in ("type", "server", "port"))
        if moved and old["password"]:
            d["password"] = ""
            if d.get("username"):
                raise ValueError("Enter the password again for the new server")
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
    if moved:
        db.run("DELETE FROM lib_dirs WHERE share_id=?", (sid,))
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
        c.execute("DELETE FROM lib_files WHERE share_id=?", (sid,))
        c.execute("DELETE FROM lib_missing WHERE share_id=?", (sid,))
        c.execute("DELETE FROM lib_dirs WHERE share_id=?", (sid,))
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


DIRS_MAX_AGE = 24 * 3600    # a folder's saved listing is used for this long at most, then it's read again
OLD_FILE = 24 * 3600        # a folder whose files are all older than this when read is taken as settled at once


class _Dirs:
    """What each folder of a library held when it was last read (lib_dirs), so a quick scan (the automatic
    one) needn't read them all again. A folder's modified time changes when something in it is added, removed
    or renamed, and its parent's listing tells us that time; so a folder with no subfolders to look in, whose
    time is the same as when it was read, still holds the same files. Only a folder found the same twice in a
    row (or whose files were all old) is trusted, so a file still being copied is read again until it's done,
    and every folder is read again at least once a day for the drives that don't keep folder times."""

    def __init__(self, sid, quick, now=None):
        self.sid, self.quick, self.now = sid, quick, int(now or time.time())
        self.saved = {r["rel"]: r for r in db.rows("SELECT * FROM lib_dirs WHERE share_id=?", (sid,))}
        self.seen = {}
        self.read = self.reused = 0

    @staticmethod
    def _descends(entries, depth):
        return any(is_dir and depth < WALK_DEPTH and not SKIP_DIR.match(name) for name, is_dir, _, _ in entries)

    def listdir(self, fs, rel, mtime, depth):
        old = self.saved.get(rel)
        if old is not None:
            try:
                old_entries = [tuple(e) for e in json.loads(old["entries"])]
            except (TypeError, ValueError):
                old = old_entries = None
        if (self.quick and old and mtime and old["mtime"] == mtime and old["steady"]
                and self.now - old["listed_at"] < DIRS_MAX_AGE and not self._descends(old_entries, depth)):
            self.reused += 1
            self.seen[rel] = (mtime, old["listed_at"], 1, old["entries"])
            return old_entries
        entries = [tuple(e) for e in fs.listdir(rel)]
        self.read += 1
        same = bool(old) and old["mtime"] == mtime and sorted(old_entries) == sorted(entries)
        settled = all(m and self.now - m > OLD_FILE for _, is_dir, _, m in entries if not is_dir)
        self.seen[rel] = (mtime or 0, self.now, 1 if (same or settled) else 0, json.dumps(entries))
        return entries

    def save(self):
        """Only what changed is written: a quick scan every half hour that reuses most folders writes next to nothing."""
        def same(rel, v):
            o = self.saved.get(rel)
            return o is not None and (o["mtime"], o["listed_at"], o["steady"], o["entries"]) == v
        changed = [(self.sid, rel, *v) for rel, v in self.seen.items() if not same(rel, v)]
        gone = [(self.sid, rel) for rel in self.saved if rel not in self.seen]
        if not changed and not gone:
            return
        with db.conn() as c:
            c.executemany("DELETE FROM lib_dirs WHERE share_id=? AND rel=?", gone)
            c.executemany("""INSERT INTO lib_dirs(share_id, rel, mtime, listed_at, steady, entries) VALUES (?,?,?,?,?,?)
                             ON CONFLICT(share_id, rel) DO UPDATE SET mtime=excluded.mtime, listed_at=excluded.listed_at,
                               steady=excluded.steady, entries=excluded.entries""", changed)


def _walk(fs, rel="", depth=0, progress=None, found=None, dirs=None, mtime=None):
    """Every video file under `rel`: -> [(relative path, size, mtime)]. `dirs`: a _Dirs to read through."""
    found = [] if found is None else found
    entries = dirs.listdir(fs, rel, mtime, depth) if dirs else fs.listdir(rel)
    for name, is_dir, size, mtime_ in entries:
        path = f"{rel}/{name}" if rel else name
        if is_dir:
            if depth < WALK_DEPTH and not SKIP_DIR.match(name):
                _walk(fs, path, depth + 1, progress, found, dirs, mtime_)
        elif PurePosixPath(name).suffix.lower() in VIDEO_EXT and not SKIP_FILE.search(name) and size != 0:
            found.append((path, size, mtime_))
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


def _n(n, word):
    return f"{n} {word}{'s' * (n != 1)}"


def scan_share(sh, progress=None, quick=False):
    """Read one library into On Demand. -> message. Titles already matched keep their details.

    A file that was renamed or moved is recognised (see _find_moves) and keeps where you stopped, its My List
    entry and its match. A title that's gone is hidden and listed in lib_missing; remove_missing() takes it
    out after remove_after_days, so a NAS that's switched off or a drive that isn't mounted loses nothing.

    `quick` (the automatic scan): folders that haven't changed aren't read again (see _Dirs), and when no file
    changed nothing is written. A title found after the library's first scan is "added" now (a copied file
    usually keeps its old date), and a show is too when it has a new episode: On Demand's New in My Library."""
    from . import vod
    sid, kind, now = sh["id"], sh["kind"], int(time.time())
    fs = FS(sh)
    dirs = _Dirs(sid, quick, now)
    try:
        files = _walk(fs, progress=(lambda n: progress(f"Reading {sh['name']}: {n} videos")) if progress else None, dirs=dirs)
    except LibraryError as e:
        db.run("UPDATE lib_shares SET status=? WHERE id=?", (f"error: {e}", sid))
        raise
    had = db.one("SELECT count(*) n FROM vod_titles WHERE source_id=?", (-sid,))["n"]
    if not files and had:
        e = LibraryError(f"Found no videos, so the {_n(had, 'title')} already here are kept "
                         "(is the drive connected, or the share empty?)")
        db.run("UPDATE lib_shares SET status=? WHERE id=?", (f"error: {e}", sid))
        raise e
    titles, episodes, skipped, on_disk = [], [], 0, []     # on_disk: (item id, title id, path, size, mtime)
    if kind == "movie":
        for m in movies_from(files):
            tid = title_id(sid, kind, m["key"])
            titles.append({"id": tid, "name": m["name"], "year": m["year"], "url": m["path"],
                           "ext": PurePosixPath(m["path"]).suffix.lstrip(".").lower(), "mtime": m["mtime"]})
            on_disk.append((tid, tid, m["path"], m["size"], m["mtime"]))
    else:
        found, skipped = shows_from(files)
        for s in found:
            tid = title_id(sid, kind, s["key"])
            titles.append({"id": tid, "name": s["name"], "year": s["year"], "url": "", "ext": "",
                           "mtime": max(e["mtime"] for e in s["episodes"].values())})
            for e in s["episodes"].values():
                eid = episode_id(sid, s["key"], e["season"], e["episode"])
                episodes.append({"id": eid, "title_id": tid, "season": e["season"], "episode": e["episode"],
                                 "name": e["name"], "url": e["path"], "ext": PurePosixPath(e["path"]).suffix.lstrip(".").lower()})
                on_disk.append((eid, tid, e["path"], e["size"], e["mtime"]))
    dirs.save()
    n = len(titles)
    what = _n(n, "movie") if kind == "movie" else f"{_n(n, 'show')}, {_n(len(episodes), 'episode')}"
    if skipped:
        what += f" ({_n(skipped, 'file')} without an episode number left out)"
    if quick and had and _unchanged(sid, on_disk, titles):
        db.run("UPDATE lib_shares SET status=?, scanned_at=? WHERE id=?", (f"ok: {what}; no changes", now, sid))
        return f"{what}; no changes"
    rows = [{**t, "source_id": -sid, "kind": kind, "category": sh["name"], "search": vod.fold(t["name"]),
             "added": t["mtime"] or now, "seen_at": now} for t in titles]
    with db.conn() as c:
        old = {r[0] for r in c.execute("SELECT id FROM vod_titles WHERE source_id=?", (-sid,))}
        if had:
            # after the first scan, "added" is when it turned up here: new titles, and shows with a new episode
            old_eps = {r[0] for r in c.execute("""SELECT e.id FROM vod_episodes e JOIN vod_titles t ON t.id=e.title_id
                                                  WHERE t.source_id=?""", (-sid,))} if kind == "series" else set()
            new_eps = {e["title_id"] for e in episodes if e["id"] not in old_eps}
            for r in rows:
                r["added"] = now if r["id"] not in old or r["id"] in new_eps else 0
        was_missing = {r[0] for r in c.execute("SELECT title_id FROM lib_missing WHERE share_id=?", (sid,))}
        new = {t["id"] for t in titles}
        moves = _find_moves(c, sid, old - new, new - old, on_disk)
        # new titles get their names from the files; known ones keep what TMDB gave them
        c.executemany("""INSERT INTO vod_titles(id, source_id, kind, name, category, year, url, ext, added, seen_at, search, hidden)
                         VALUES (:id,:source_id,:kind,:name,:category,:year,:url,:ext,:added,:seen_at,:search,0)
                         ON CONFLICT(id) DO UPDATE SET url=excluded.url, ext=excluded.ext, category=excluded.category,
                           seen_at=excluded.seen_at, added=MAX(vod_titles.added, excluded.added), hidden=0,
                           duration=CASE WHEN vod_titles.url!=excluded.url THEN 0 ELSE vod_titles.duration END""", rows)
        if kind == "series":
            c.executemany("""INSERT INTO vod_episodes(id, title_id, season, episode, name, url, ext)
                             VALUES (:id,:title_id,:season,:episode,:name,:url,:ext)
                             ON CONFLICT(id) DO UPDATE SET url=excluded.url, ext=excluded.ext,
                               duration=CASE WHEN vod_episodes.url!=excluded.url THEN 0 ELSE vod_episodes.duration END,
                               name=CASE WHEN vod_episodes.name='' THEN excluded.name ELSE vod_episodes.name END""", episodes)
        # the moved files' old rows are still there: their sizes are compared with the new ones in _carry
        c.executemany("""INSERT INTO lib_files(item_id, title_id, share_id, path, size, mtime) VALUES (?,?,?,?,?,?)
                         ON CONFLICT(item_id) DO UPDATE SET title_id=excluded.title_id, path=excluded.path,
                           size=excluded.size, mtime=excluded.mtime,
                           probed_at=CASE WHEN lib_files.size=excluded.size THEN lib_files.probed_at ELSE 0 END""",
                      [(i, t, sid, p, s, m) for i, t, p, s, m in on_disk])
        names = {t["id"]: t["name"] for t in titles}
        for o, n in moves.items():
            _carry(c, kind, o, n, names[n])
        gone = old - new - set(moves)
        c.executemany("INSERT OR IGNORE INTO lib_missing(title_id, share_id, since) VALUES (?,?,?)",
                      [(t, sid, now) for t in gone])
        c.executemany("DELETE FROM lib_missing WHERE title_id=?", [(t,) for t in new])
        c.execute("UPDATE vod_titles SET hidden=1 WHERE id IN (SELECT title_id FROM lib_missing WHERE share_id=?)", (sid,))
        c.execute("UPDATE vod_titles SET hidden=1 WHERE source_id=? AND id IN (SELECT title_id FROM lib_meta WHERE state='ignored')", (-sid,))
        if kind == "series":
            # an episode that's gone from a show that's still there goes now (its id comes from the show, season
            # and number, so where you stopped comes back with it); a show that's gone keeps its episodes
            c.execute("CREATE TEMP TABLE IF NOT EXISTS _seen(id TEXT PRIMARY KEY)")
            c.execute("DELETE FROM _seen")
            c.executemany("INSERT OR IGNORE INTO _seen(id) VALUES (?)", [(e["id"],) for e in episodes])
            c.execute("""DELETE FROM vod_episodes WHERE title_id IN (SELECT id FROM vod_titles WHERE source_id=?)
                         AND title_id NOT IN (SELECT title_id FROM lib_missing) AND id NOT IN (SELECT id FROM _seen)""", (-sid,))
        if not int(settings()["remove_after_days"]):
            _drop(c, [r[0] for r in c.execute("SELECT title_id FROM lib_missing WHERE share_id=?", (sid,))])
        c.execute("DELETE FROM vod_episodes WHERE title_id NOT IN (SELECT id FROM vod_titles)")
        c.execute("DELETE FROM lib_meta WHERE title_id NOT IN (SELECT id FROM vod_titles)")
        c.execute("""DELETE FROM lib_files WHERE share_id=? AND item_id NOT IN (SELECT id FROM vod_titles WHERE source_id=?)
                     AND item_id NOT IN (SELECT e.id FROM vod_episodes e JOIN vod_titles t ON t.id=e.title_id
                                         WHERE t.source_id=?)""", (sid, -sid, -sid))
        changes = []
        if had:
            added = len(new - old) - len(moves)
            changes += [f"{added} new"] if added else []
            changes += [f"{len(moves)} renamed or moved"] if moves else []
        missing = len(gone - was_missing)
        changes += [f"{missing} missing"] if missing else []
        if changes:
            what += f"; {', '.join(changes)}"
        c.execute("UPDATE lib_shares SET status=?, scanned_at=?, count=? WHERE id=?", (f"ok: {what}", now, n, sid))
    _vod_changed()
    return what


def _unchanged(sid, on_disk, titles):
    """The same files as last time, nothing missing and every title still there: a quick scan has nothing to do."""
    have = {(r["item_id"], r["path"], r["size"], r["mtime"]) for r in
            db.rows("SELECT item_id, path, size, mtime FROM lib_files WHERE share_id=?", (sid,))}
    if have != {(i, p, sz, m) for i, _, p, sz, m in on_disk}:
        return False
    if db.one("SELECT 1 FROM lib_missing WHERE share_id=? LIMIT 1", (sid,)):
        return False
    ids = {r["id"] for r in db.rows("SELECT id FROM vod_titles WHERE source_id=?", (-sid,))}
    return ids == {t["id"] for t in titles}


def _find_moves(c, sid, gone, fresh, on_disk):
    """Titles that are gone matched to new ones holding the same files: -> {old title id: new title id}.

    A file is known by its size and extension (two copies of a film are almost never the same size to the
    byte). A movie matches when its one file does; a show when at least half the episodes of the smaller of
    the two do (a show folder renamed while a new episode arrived still counts). Anything that could go two
    ways is left alone: it's then a new title and a missing one, as before."""
    if not gone or not fresh:
        return {}

    def key(path, size, mtime):
        if size <= 0:
            return None
        # small files (clips, test files) can share a size: those must have the same time too
        return (size, PurePosixPath(path).suffix.lower(), mtime if size < SMALL_FILE else None)
    old_files = {}
    for tid, path, size, mtime in c.execute("SELECT title_id, path, size, mtime FROM lib_files WHERE share_id=?", (sid,)):
        if tid in gone and key(path, size, mtime):
            old_files.setdefault(tid, set()).add(key(path, size, mtime))
    new_files, holders = {}, {}
    for _, tid, path, size, mtime in on_disk:
        if tid in fresh and key(path, size, mtime):
            new_files.setdefault(tid, set()).add(key(path, size, mtime))
            holders.setdefault(key(path, size, mtime), set()).add(tid)
    best = {}
    for o, keys in old_files.items():
        overlap = {}
        for k in keys:
            for n in holders.get(k, ()):
                overlap[n] = overlap.get(n, 0) + 1
        good = [(hits, n) for n, hits in overlap.items() if hits * 2 >= min(len(keys), len(new_files[n]))]
        good.sort(reverse=True)
        if good and (len(good) == 1 or good[0][0] > good[1][0]):
            best[o] = good[0][1]
    claimed = {}
    for o, n in best.items():
        claimed[n] = claimed.get(n, 0) + 1
    return {o: n for o, n in best.items() if claimed[n] == 1}


def _carry(c, kind, old, new, new_name):
    """A renamed or moved title: where you stopped, My List and when it was added go to its new id. Its match
    (and the details from TMDB) go too when its name is the same, or you picked or left it out yourself; a
    file renamed to fix a wrong match is looked up again."""
    from .vod import fold
    m = c.execute("SELECT state, query FROM lib_meta WHERE title_id=?", (old,)).fetchone()
    try:
        was = json.loads(m[1] or "{}").get("name") if m else None
    except ValueError:
        was = None
    was = was or c.execute("SELECT name FROM vod_titles WHERE id=?", (old,)).fetchone()[0]
    keep = bool(m) and (m[0] in ("pinned", "ignored") or fold(was) == fold(new_name))
    c.execute("UPDATE vod_titles SET added=(SELECT added FROM vod_titles WHERE id=?) WHERE id=?", (old, new))
    if keep:
        c.execute(f"UPDATE vod_titles SET ({DETAIL_COLS}) = (SELECT {DETAIL_COLS} FROM vod_titles WHERE id=?) WHERE id=?",
                  (old, new))
        c.execute("DELETE FROM lib_meta WHERE title_id=?", (new,))
        c.execute("UPDATE lib_meta SET title_id=? WHERE title_id=?", (new, old))
    if kind == "movie":
        items = [(old, new)]
    else:
        items = c.execute("""SELECT o.id, n.id FROM vod_episodes o JOIN vod_episodes n
                             ON n.title_id=? AND n.season=o.season AND n.episode=o.episode WHERE o.title_id=?""",
                          (new, old)).fetchall()
    table = "vod_titles" if kind == "movie" else "vod_episodes"
    for o, n in items:
        c.execute("UPDATE OR IGNORE vod_progress SET item_id=?, title_id=? WHERE item_id=?", (n, new, o))
        # the same file: its length is known already
        c.execute(f"""UPDATE {table} SET duration=(SELECT duration FROM {table} WHERE id=?) WHERE id=?
                      AND (SELECT size FROM lib_files WHERE item_id=?)=(SELECT size FROM lib_files WHERE item_id=?)""",
                  (o, n, o, n))
        if keep and kind == "series":
            c.execute("""UPDATE vod_episodes SET (name, plot, aired) = (SELECT name, plot, aired FROM vod_episodes WHERE id=?)
                         WHERE id=?""", (o, n))
    c.execute("UPDATE vod_progress SET title_id=? WHERE title_id=?", (new, old))
    c.execute("UPDATE OR IGNORE vod_list SET title_id=? WHERE title_id=?", (new, old))
    c.execute("UPDATE OR IGNORE vod_dismissed SET title_id=? WHERE title_id=?", (new, old))
    _drop(c, [old])


def _drop(c, title_ids):
    """Titles out of the library for good (their files are gone or moved)."""
    for i in range(0, len(title_ids), 500):
        part = title_ids[i:i + 500]
        q = ",".join("?" * len(part))
        for sql in ("DELETE FROM lib_meta WHERE title_id IN ({q})", "DELETE FROM lib_missing WHERE title_id IN ({q})",
                    "DELETE FROM lib_files WHERE title_id IN ({q})", "DELETE FROM vod_episodes WHERE title_id IN ({q})",
                    "DELETE FROM vod_titles WHERE id IN ({q})"):
            c.execute(sql.format(q=q), part)


def remove_missing(days=None, now=None):
    """Take out titles missing for longer than remove_after_days. -> message."""
    days = int(settings()["remove_after_days"]) if days is None else days
    cut = (now or time.time()) - days * 86400
    with db.conn() as c:
        ids = [r[0] for r in c.execute("""SELECT m.title_id FROM lib_missing m JOIN lib_shares s ON s.id=m.share_id
                                          WHERE s.enabled=1 AND m.since<=?""", (cut,))]
        _drop(c, ids)
        left = c.execute("SELECT count(*) FROM lib_missing").fetchone()[0]
    if ids:
        _vod_changed()
    msg = f"Removed {_n(len(ids), 'missing title')}" if ids else "Nothing to remove"
    return msg + (f"; {left} still missing, kept for now" if left else "")


LENGTH_BUDGET = 10 * 60     # seconds one run of measure_lengths may take (the next run carries on)


def measure_lengths(progress=None, budget=LENGTH_BUDGET, limit=1000):
    """Measure the length of videos that don't have one yet (newest first), so On Demand can show it before
    a title is first played. A file that can't be read is tried again after RETRY_DAYS. -> message."""
    from . import vod
    progress = progress or (lambda *a, **k: None)
    now = int(time.time())
    todo = db.rows("""SELECT f.item_id, f.title_id, t.id IS NOT NULL is_movie FROM lib_files f
                      JOIN lib_shares s ON s.id=f.share_id AND s.enabled=1
                      LEFT JOIN vod_titles t ON t.id=f.item_id LEFT JOIN vod_episodes e ON e.id=f.item_id
                      WHERE COALESCE(t.duration, e.duration, 1)=0 AND f.probed_at<?
                        AND f.title_id NOT IN (SELECT title_id FROM lib_missing)
                      ORDER BY f.mtime DESC LIMIT ?""", (now - RETRY_DAYS * 86400, limit))
    done = failed = 0
    start = time.time()
    for i, r in enumerate(todo):
        if time.time() - start > budget:
            break
        progress("Measuring video lengths", i, len(todo))
        it = vod.item(r["item_id"])
        secs = vod.probe_duration(it["url"]) if it else 0
        if secs:
            db.run(f"UPDATE {'vod_titles' if r['is_movie'] else 'vod_episodes'} SET duration=? WHERE id=?", (secs, r["item_id"]))
        db.run("UPDATE lib_files SET probed_at=? WHERE item_id=?", (now, r["item_id"]))
        done += bool(secs)
        failed += not secs
    if done:
        _vod_changed()
    if not todo:
        return "Every video's length is known"
    msg = f"Measured {_n(done, 'video')}" + (f", {failed} couldn't be read" if failed else "")
    left = len(todo) - done - failed
    return msg + (f"; {left} left for next time" if left else "")


TIDY_DAYS = 90      # where you stopped in a title that's gone is kept this long (it may come back)


def tidy(now=None):
    """Delete what's left of titles that are gone: where you stopped and My List entries (at once when their
    library was removed, else after TIDY_DAYS), stray rows, and cached subtitles. -> message."""
    from . import tracks
    now = now or time.time()
    cut = now - TIDY_DAYS * 86400
    lib = "GLOB 'L[0-9]*:*'"
    with db.conn() as c:
        shares_ = {r[0] for r in c.execute("SELECT id FROM lib_shares")}
        known = {r[0] for r in c.execute(f"SELECT id FROM vod_titles WHERE id {lib}")}
        known |= {r[0] for r in c.execute(f"SELECT id FROM vod_episodes WHERE id {lib}")}

        def stale(item, when):
            sid = item[1:item.index(":")]
            return item not in known and (not sid.isdigit() or int(sid) not in shares_ or when < cut)
        prog = [r[0] for r in c.execute(f"SELECT item_id, updated FROM vod_progress WHERE item_id {lib}") if stale(*r)]
        lst = [r[0] for r in c.execute(f"SELECT title_id, added FROM vod_list WHERE title_id {lib}") if stale(*r)]
        c.executemany("DELETE FROM vod_progress WHERE item_id=?", [(i,) for i in prog])
        c.executemany("DELETE FROM vod_list WHERE title_id=?", [(i,) for i in lst])
        rows = 0
        for sql in ("DELETE FROM lib_meta WHERE title_id NOT IN (SELECT id FROM vod_titles)",
                    "DELETE FROM lib_missing WHERE title_id NOT IN (SELECT id FROM vod_titles)",
                    "DELETE FROM lib_files WHERE item_id NOT IN (SELECT id FROM vod_titles) "
                    "AND item_id NOT IN (SELECT id FROM vod_episodes)"):
            rows += c.execute(sql).rowcount
    keys = {tracks._key(i) for i in known}
    subs = 0
    for f in tracks._cache_dir().glob("*.json"):
        if f.name.split("-", 1)[0] not in keys:
            try:
                f.unlink()
                subs += 1
            except OSError:
                pass
    parts = [_n(len(prog), "saved position")] if prog else []
    parts += [_n(len(lst), "My List entry").replace("entrys", "entries")] if lst else []
    parts += [_n(subs, "cached subtitle file")] if subs else []
    parts += [_n(rows, "stray row")] if rows else []
    return ("Removed " + ", ".join(parts)) if parts else "Nothing to clean up"


def scan_all(progress=None, share_id=None, full_refresh=False, only_new=False, quick=False):
    """Scan every enabled library (or one), then look up details for titles that need them (`only_new`: just
    titles never looked up; the "details" task does the rest). -> message."""
    progress = progress or (lambda *a, **k: None)
    todo = [s for s in db.rows("SELECT * FROM lib_shares WHERE enabled=1 ORDER BY id") if share_id in (None, s["id"])]
    done, errors = [], []
    for i, sh in enumerate(todo):
        progress(f"Reading {sh['name']}", i, len(todo))
        try:
            done.append(f"{sh['name']}: {scan_share(sh, progress, quick)}")
        except LibraryError as e:
            errors.append(f"{sh['name']}: {e}")
            log.warning("Library %s: %s", sh["name"], e)
    db.set_meta("library_scanned", int(time.time()))
    looked = ""
    if settings()["tmdb_key"]:
        try:
            looked = fetch_details(progress, share_id, full_refresh, only_new)
        except LibraryError as e:
            errors.append(str(e))
    msg = "; ".join(done + errors) or "No libraries to scan"
    return msg + (f". {looked}" if looked else "")


# ---------------------------------------------------------------- TMDB
def _tmdb(path, key=None, **params):
    key = key if key is not None else settings()["tmdb_key"]
    if not key:
        raise LibraryError("Add a TMDB API key in Setup › My Library to download details and posters")
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


def fetch_details(progress=None, share_id=None, full_refresh=False, only_new=False):
    """Look up titles never looked up, misses after RETRY_DAYS, and refresh matched ones after REFRESH_DAYS
    (`only_new`: just the first). Missing titles wait until they're back. -> message."""
    progress = progress or (lambda *a, **k: None)
    now = int(time.time())
    w, a = ("AND t.source_id=?", [-share_id]) if share_id else ("", [])
    rows = db.rows(f"""SELECT t.*, COALESCE(m.state,'') state, COALESCE(m.tmdb_id,0) tmdb_id, COALESCE(m.score,0) score,
                              COALESCE(m.fetched_at,0) fetched_at, COALESCE(m.tried_at,0) tried_at
                       FROM vod_titles t JOIN lib_shares s ON -s.id=t.source_id AND s.enabled=1
                       LEFT JOIN lib_meta m ON m.title_id=t.id
                       WHERE t.id NOT IN (SELECT title_id FROM lib_missing) {w}""", a)
    todo = []
    for r in rows:
        st = r["state"]
        if st == "ignored":
            continue
        if st == "":
            todo.append(r)
        elif only_new:
            continue
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
    elif flt == "missing":
        w.append("x.since IS NOT NULL")
    if q:
        from .vod import fold
        w.append("(t.search LIKE ? OR t.url LIKE ?)")
        a += [f"%{fold(q)}%", f"%{q}%"]
    rows = db.rows(f"""SELECT t.id, t.kind, t.name, t.year, t.url, t.poster, t.category, t.source_id,
                              COALESCE(m.state,'') state, COALESCE(m.score,0) score, COALESCE(m.tmdb_id,0) tmdb_id,
                              COALESCE(m.query,'') query, COALESCE(x.since,0) missing_since,
                              (SELECT url FROM vod_episodes e WHERE e.title_id=t.id ORDER BY season, episode LIMIT 1) ep_url,
                              (SELECT count(*) FROM vod_episodes e WHERE e.title_id=t.id) eps
                       FROM vod_titles t JOIN lib_shares s ON -s.id=t.source_id
                       LEFT JOIN lib_meta m ON m.title_id=t.id LEFT JOIN lib_missing x ON x.title_id=t.id
                       WHERE {' AND '.join(w)}
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
                         SUM(COALESCE(m.state,'') IN ('weak','none')) review, SUM(x.since IS NOT NULL) missing
                  FROM vod_titles t JOIN lib_shares s ON -s.id=t.source_id AND s.enabled=1
                  LEFT JOIN lib_meta m ON m.title_id=t.id LEFT JOIN lib_missing x ON x.title_id=t.id""")
    eps = db.one("""SELECT count(*) n FROM vod_episodes e JOIN vod_titles t ON t.id=e.title_id
                    JOIN lib_shares s ON -s.id=t.source_id AND s.enabled=1""")["n"]
    return {"titles": r["n"] or 0, "movies": r["movies"] or 0, "shows": r["shows"] or 0, "episodes": eps,
            "review": r["review"] or 0, "missing": r["missing"] or 0}


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
