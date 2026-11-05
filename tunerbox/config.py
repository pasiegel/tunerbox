"""Settings stored in data/settings.json (created with defaults on first run)."""
import json
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
    # Sports & PPV: country codes of playlist groups to show ("US", "US,CA,UK", or "*" for all)
    "sports_regions": "US",
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


def load():
    global _cache
    with _lock:
        if _cache is None:
            DATA.mkdir(parents=True, exist_ok=True)
            # first run of the installed app: start with the bundled lineup
            default_csv = BUNDLE / "data" / "channels.csv"
            if FROZEN and default_csv.exists() and not (DATA / "channels.csv").exists():
                shutil.copy(default_csv, DATA / "channels.csv")
            user = {}
            if SETTINGS_FILE.exists():
                user = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            _cache = _merge(DEFAULTS, user)
            SETTINGS_FILE.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
        return _cache


def get(key, default=None):
    return load().get(key, default)


def update(changes: dict):
    global _cache
    cur = load()
    with _lock:
        _cache = _merge(cur, changes)
        SETTINGS_FILE.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
        return _cache


def data_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else DATA / path
