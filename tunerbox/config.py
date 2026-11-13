"""Settings stored in data/settings.json (created with defaults on first run)."""
import json
import re
import os
import shutil
import sys
import threading
from pathlib import Path

# Installed app (PyInstaller): read-only files live in the bundle, user data in the
# per-user app-data folder. From source: everything sits next to the code.
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
    if os.name == "nt":
        _user = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "TunerBox"
    elif sys.platform == "darwin":
        _user = Path.home() / "Library" / "Application Support" / "TunerBox"
    else:
        _user = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "TunerBox"
    DATA = Path(os.environ.get("TUNERBOX_DATA", _user))
else:
    ROOT = BUNDLE = Path(__file__).resolve().parent.parent
    DATA = Path(os.environ.get("TUNERBOX_DATA", ROOT / "data"))
WEB = BUNDLE / "web"
SETTINGS_FILE = DATA / "settings.json"


def bundled_ffmpeg():
    """ffmpeg shipped inside the installed app, if any."""
    exe = BUNDLE / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not exe.exists():
        return None
    if os.name != "nt" and not os.access(exe, os.X_OK):
        try:                                   # bundlers don't always keep the executable bit
            os.chmod(exe, 0o755)
        except OSError:
            pass
    return exe

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8642,
    # Your local cable lineup (Channel Number, Channel Name)
    "lineup_csv": "channels.csv",
    # The provider lineup that channels.csv was made for. If you pick a different provider in
    # Setup > Guide the CSV is ignored, so the two don't mix. "" = always use the CSV.
    "lineup_csv_lineup": "USA-MI21490-X",
    # Local guide from Gracenote (same data zap2it/TV Guide sites use)
    "gracenote": {
        "enabled": True,
        "postal_code": "49503",
        "country": "USA",
        "lineup_id": "USA-MI21490-X",      # Xfinity - Digital, Grand Rapids
        "headend_id": "MI21490",
        "device": "X",
        "hours": 48,
    },
    # Online XMLTV guides for channels that aren't in the local lineup.
    # Playlist x-tvg-url headers and Xtream xmltv.php are added automatically.
    "xmltv_sources": [
        "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
        "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    ],
    "epg_refresh_hours": 12,
    "source_refresh_hours": 24,
    # Full scan once a day at this hour (0-23, local time) instead of every source_refresh_hours (-1)
    "scan_hour": -1,
    # Between scans, re-read the playlists this often so event/PPV feeds get their event names and
    # listings during the day (0 = off). No stream checks; cheap when little has changed.
    "event_refresh_hours": 2,
    # Playlist channels that don't match the lineup get numbers starting here
    "extra_channel_start": 20000,
    "extras_enabled": True,
    # Fuzzy score (0-100) needed to auto-map a playlist channel to a lineup channel
    "match_threshold": 86,
    # Map a local station (e.g. your NBC affiliate) to a plain "NBC" feed if no local stream exists
    "network_fallback": True,
    # Hide lineup channels with no feed from the guide
    "hide_unmapped": True,
    # Check that streams actually play during a scan (Xtream sources are skipped
    # by default because providers limit simultaneous connections)
    "probe_streams": True,
    "probe_xtream": False,
    # Sports & Events: country codes of playlist groups to show ("US", "US,CA,UK", or "*" for all)
    "sports_regions": "US",
    # Leagues whose games are looked up on ESPN's scoreboard and matched to your channels, so they
    # show under Sports > On Now / Coming Up with scores (keys are in scoreboard.py; [] = off)
    "sports_leagues": ["nfl", "ncaaf", "nba", "ncaab", "mlb", "nhl"],
    # Also list games that none of your channels or event feeds carry
    "sports_show_unmatched": True,
    # What this box is called in Plex/Emby/Jellyfin, browser tabs and home-screen icons (two TunerBoxes
    # on one network can tell themselves apart)
    "box_name": "TunerBox",
    "probe_concurrency": 16,
    "probe_timeout": 10,
    # Playback: "copy" keeps the original video (fast), "h264" re-encodes (compatibility)
    "video_mode": "copy",
    "ffmpeg": "ffmpeg",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "recordings_dir": "recordings",
    "dvr_pad_before": 60,
    "dvr_pad_after": 180,
    # Extra stream cache in seconds (0 = fastest channel changes). Each TV screen can override it
    # in its Menu. Also keeps a read-ahead buffer on the server so a slow Wi-Fi viewer never stalls the provider.
    "stream_buffer": 0,
    # Turn a TV screen off after this many hours without a button press (0 = never), and show a
    # screensaver this many minutes after it's off (0 = never). Neither happens while a recording
    # is running or a phone remote is open.
    "sleep_enabled": True,
    "sleep_hours": 4,
    "screensaver_enabled": True,
    "screensaver_minutes": 10,
    # Local channels (folders of videos) get numbers from here up; their video is made at this height
    "media_channel_start": 2000,
    "media_resolution": "1080",
    # Favorite channel numbers, shared by every screen (star them from the guide, the menu or a phone)
    "favorites": [],
    # Phones paired as remotes: {id, name, hash, created, last_seen} (only token hashes are stored)
    "devices": [],
    # Only this PC and paired phones may change setup (watching stays open to the whole network)
    "require_pairing": False,
    # Remote-control buttons shared by every device: {"input id": "action"}.
    # Set from the TV screen: Menu > Remote control setup > Save as default.
    "keymap": {},
    # HDHomeRun emulation (Plex / Emby / Jellyfin). The device id is made once per install;
    # tuner_count 0 = automatic (your Xtream accounts' connection limits, else 4).
    "hdhr_device_id": "",
    "tuner_count": 0,
}

_lock = threading.Lock()
_cache = None


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_user():
    """The user's settings.json. A damaged file (edited by hand, say) is set aside as
    settings.json.broken and TunerBox starts with defaults instead of refusing to start."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    bad = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".broken")
    try:
        shutil.copy(SETTINGS_FILE, bad)
        print(f"TunerBox: settings.json is damaged; saved it as {bad.name} and started with defaults", file=sys.stderr)
    except OSError:
        pass
    return {}


def _write(data):
    """Write settings.json atomically: a crash mid-write never leaves a half-written file."""
    text = json.dumps(data, indent=2)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SETTINGS_FILE)


LEGACY_DEVICE_ID = "12345678"


def new_device_id():
    """A random 8-hex-digit HDHomeRun device id whose check digit is valid (what libhdhomerun and
    some apps verify)."""
    import secrets
    table = [0xA, 0x5, 0xF, 0x6, 0x7, 0xC, 0x1, 0xB, 0x9, 0x2, 0x8, 0xD, 0x4, 0x3, 0xE, 0x0]
    n = secrets.randbits(28) << 4
    chk = 0
    for i, shift in enumerate(range(28, 0, -4)):
        nib = (n >> shift) & 0xF
        chk ^= table[nib] if i % 2 == 0 else nib
    return f"{n | chk:08X}"


def valid_device_id(text):
    try:
        n = int(text, 16)
    except (TypeError, ValueError):
        return False
    table = [0xA, 0x5, 0xF, 0x6, 0x7, 0xC, 0x1, 0xB, 0x9, 0x2, 0x8, 0xD, 0x4, 0x3, 0xE, 0x0]
    chk = 0
    for i, shift in enumerate(range(28, -4, -4)):
        nib = (n >> shift) & 0xF
        chk ^= table[nib] if i % 2 == 0 else nib
    return chk == 0


def load():
    global _cache
    with _lock:
        if _cache is None:
            DATA.mkdir(parents=True, exist_ok=True)
            # first run of the installed app: start with the bundled lineup
            default_csv = BUNDLE / "data" / "channels.csv"
            if FROZEN and default_csv.exists() and not (DATA / "channels.csv").exists():
                shutil.copy(default_csv, DATA / "channels.csv")
            user = _read_user()
            _cache = _merge(DEFAULTS, user)
            if not _cache.get("hdhr_device_id"):
                # installs from before this setting already told Plex "12345678": changing it would
                # make Plex see a new tuner and lose its DVR setup
                _cache["hdhr_device_id"] = LEGACY_DEVICE_ID if user else new_device_id()
            _write(_cache)
        return _cache


def get(key, default=None):
    return load().get(key, default)


def update(changes: dict):
    global _cache
    load()
    with _lock:
        # merge into the current cache inside the lock, so two updates at once can't drop each other's changes
        _cache = _merge(_cache, changes)
        _write(_cache)
        return _cache


# Settings that name a program to run, or a place on disk to read or write: only the PC itself can
# change them, so another device on the network can't point TunerBox at its own program or folder.
PC_ONLY = {"ffmpeg", "recordings_dir", "lineup_csv", "host", "port", "require_pairing"}
# Managed through their own API calls, never through /api/settings
NOT_SETTABLE = {"devices", "hdhr_device_id"}


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def box_name():
    """This box's name (Setup › Settings › Other apps)."""
    return (get("box_name") or "").strip() or "TunerBox"


def _check_value(key, value, default):
    """`value` coerced to the type of `default`, or ValueError."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
    elif isinstance(default, (int, float)):
        if isinstance(value, str) and value.strip():
            try:
                value = float(value)
            except ValueError:
                pass
        if _is_num(value):
            return int(value) if isinstance(default, int) and float(value).is_integer() else value
    elif isinstance(default, str):
        if isinstance(value, str):
            return value.strip()
        if _is_num(value):
            return str(value)
    elif isinstance(default, list):
        if isinstance(value, list):
            return value
    elif isinstance(default, dict):
        if isinstance(value, dict):
            if key == "keymap":
                return {str(k): str(v) for k, v in value.items()}
            unknown = set(value) - set(default)
            if unknown:
                raise ValueError(f"{key}: unknown setting {sorted(unknown)[0]}")
            return {k: _check_value(f"{key}.{k}", v, default[k]) for k, v in value.items()}
    raise ValueError(f"{key}: expected {type(default).__name__}, got {type(value).__name__}")


def clean_changes(changes: dict, from_pc: bool) -> dict:
    """Check settings sent by a page before saving them. Unknown keys and wrong types are refused
    (ValueError); PC-only settings from another device are refused if they would change (PermissionError).
    Unchanged PC-only values are dropped, so a phone can still save the whole Settings form."""
    cur = load()
    out = {}
    for k, v in changes.items():
        if k in NOT_SETTABLE:
            continue
        if k not in DEFAULTS:
            raise ValueError(f"Unknown setting: {k}")
        v = _check_value(k, v, DEFAULTS[k])
        if k == "xmltv_sources":
            v = [str(u).strip() for u in v if str(u).strip()]
        elif k == "sports_leagues":
            v = sorted({str(x).strip().lower() for x in v if str(x).strip()})
        elif k == "box_name":
            v = re.sub(r"\s+", " ", v).strip()[:40] or "TunerBox"
        elif k == "favorites":
            v = sorted({int(n) for n in v if _is_num(n) or str(n).lstrip("-").isdigit()})
        if k in PC_ONLY and not from_pc:
            if v == cur.get(k):
                continue
            raise PermissionError(f"'{k}' can only be changed on the TunerBox PC")
        if k == "xmltv_sources" and not from_pc:
            old = set(cur.get(k) or [])
            if any(u not in old and not u.lower().startswith(("http://", "https://")) for u in v):
                raise PermissionError("Guide files on disk can only be added on the TunerBox PC")
        out[k] = v
    return out


def data_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else DATA / path
