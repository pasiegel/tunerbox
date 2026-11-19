"""The log file, for Setup › Log: what TunerBox is doing, followed live. Passwords, logins and keys are hidden
before a line is written (Formatter) and again when it's read (lines written before this existed)."""
import logging
import os
import re
import threading
import time

from . import config, db

LOG_NAME = "tunerbox.log"
TAIL_BYTES = 256 * 1024     # the first look reads this much of the end of the file
MAX_LINES = 500             # lines handed over in one go
# libraries that log every step at INFO (the SMB client: each file opened and closed): only their warnings are
# kept (__main__), and their routine lines already in the file aren't shown
CHATTY = ("smbprotocol", "smbclient", "spnego")
_ROUTINE = re.compile(r"^\S+ \S+ (%s)\S* (DEBUG|INFO) " % "|".join(CHATTY))

# key=value in an address or a message (…?username=me&password=secret, api_key=…)
_PARAM = re.compile(r"(?i)\b(password|passwd|pass|pwd|username|user|api_key|apikey|token|auth|key)=([^&\s\"']+)")
_SECRETS_TTL = 60
_cache = {"at": 0.0, "words": []}
_busy = threading.local()       # a lookup that logs something itself mustn't look up again


def _secrets():
    """Logins, passwords and keys TunerBox knows (playlists, NAS shares, TMDB): replaced wherever they show up."""
    if time.time() - _cache["at"] < _SECRETS_TTL or getattr(_busy, "on", False):
        return _cache["words"]
    _busy.on = True
    try:
        words = set()
        for r in db.rows("SELECT username, password FROM sources"):
            words.update((r["username"] or "", r["password"] or ""))
        for r in db.rows("SELECT username, password FROM lib_shares"):
            words.update((r["username"] or "", r["password"] or ""))
        try:
            from . import library
            words.add(library.settings().get("tmdb_key") or "")
        except Exception:  # noqa: BLE001
            pass
        # short ones ("tv", "1") would blank out half the log; longest first, so a password holding the
        # username is hidden whole
        _cache["words"] = sorted((w for w in words if len(w) >= 4), key=len, reverse=True)
    except Exception:  # noqa: BLE001  (the database isn't there yet at startup: keep what we had)
        pass
    finally:
        _cache["at"] = time.time()
        _busy.on = False
    return _cache["words"]


def forget():
    """A login was changed: look them up again before the next line."""
    _cache["at"] = 0.0


def redact(text):
    text = _PARAM.sub(lambda m: f"{m.group(1)}=***", text)
    for w in _secrets():
        if w in text:
            text = text.replace(w, "***")
    return text


class Formatter(logging.Formatter):
    """Lines go into the file with the secrets already hidden."""

    def format(self, record):
        return redact(super().format(record))


def _open_shared(path):
    """The log, opened to read. On Windows a file someone has open can't be renamed unless they allow it, and
    the log is renamed when it's started over (tunerbox.log -> tunerbox.log.1): opened the plain way, a look
    from Setup › Log at that moment would make the start-over fail (and lose that line). So there it's opened
    sharing read, write and delete, as the log handler expects."""
    if os.name != "nt":
        return open(path, "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    # GENERIC_READ; FILE_SHARE_READ | WRITE | DELETE; OPEN_EXISTING; FILE_ATTRIBUTE_NORMAL
    h = k32.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        k32.CloseHandle(h)
        raise
    return os.fdopen(fd, "rb")


def tail(after=-1, max_lines=MAX_LINES):
    """New lines since `after` (a position this returned before; -1: the last ones in the file).
    -> {pos, lines, restarted}: `restarted` when the file was started over (it's kept to ~5 MB) since."""
    path = config.DATA / LOG_NAME
    try:
        size = path.stat().st_size
    except OSError:
        return {"pos": 0, "lines": [], "restarted": False}
    restarted = 0 <= size < after
    start = max(0, size - TAIL_BYTES) if after < 0 or restarted else after
    first = after < 0 or restarted
    while True:
        try:
            with _open_shared(path) as f:
                f.seek(start)
                data = f.read(max(0, size - start))
        except OSError:                         # being started over this very moment: the next look gets it
            return {"pos": after, "lines": [], "restarted": False}
        end = data.rfind(b"\n")
        if end < 0:                             # nothing finished since: wait for the whole line
            return {"pos": start, "lines": [], "restarted": restarted}
        pos = start + end + 1
        lines = data[:end + 1].decode("utf-8", "replace").splitlines()
        if start and first:
            lines = lines[1:]                   # read from the middle of a line: drop that part
        lines = [x for x in lines if not _ROUTINE.match(x)]
        # the first look: if the end of the file is mostly the NAS client's routine lines, look further back
        if not first or len(lines) >= max_lines or start == 0:
            break
        start = max(0, size - (size - start) * 4)
    return {"pos": pos, "lines": [redact(x) for x in lines[-max_lines:]], "restarted": restarted}
