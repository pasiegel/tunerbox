"""The TV page's script, /static/tv.js, joined from the files in web/tv/ (in name order).

All the parts share one scope, as if they were still one file: they are wrapped in a single
`(() => { "use strict"; ... })();`. So a part can use anything another part defines, but code that runs
straight away (not inside a function) can only use what earlier parts define. No build step: the server
joins them on request and keeps the result until a part changes.
"""
import threading

from . import config

PARTS = config.WEB / "tv"
HEADER = '/* TunerBox set-top box UI (joined from web/tv/*.js by tunerbox/bundle.py) */\n(() => {\n"use strict";\n'
FOOTER = "})();\n"

_lock = threading.Lock()
_cache = {"key": None, "text": ""}


def parts():
    return sorted(PARTS.glob("*.js"))


def version():
    """Newest change time of any part (for the ?v= stamp on the page)."""
    return max((int(p.stat().st_mtime) for p in parts()), default=0)


def tv_js():
    files = parts()
    key = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    with _lock:
        if _cache["key"] != key:
            body = "".join(_ending(p.read_text(encoding="utf-8")) for p in files)
            _cache.update(key=key, text=HEADER + body + FOOTER)
        return _cache["text"]


def _ending(text):
    return text if text.endswith("\n") else text + "\n"


if __name__ == "__main__":      # python -m tunerbox.bundle > tv.js   (CI syntax check)
    import sys
    sys.stdout.write(tv_js())
