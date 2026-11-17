"""Audio tracks and subtitles of On Demand titles from your own libraries (library.py).

* What a file has is read with ffmpeg itself (no ffprobe needed) and remembered while TunerBox runs.
* Subtitles: the text ones inside the file (SRT, ASS, WebVTT, MP4 text) and subtitle files next to the
  video (`Movie.srt`, `Movie.en.srt`, `Movie.en.forced.srt`, or in a `Subs` folder). Each is turned into
  a list of timed lines once and cached in the data folder (never next to your files); the TV screen
  draws them over the picture, so they stay in step when you skip.
* Picture subtitles (PGS from Blu-ray, VobSub from DVD) are listed but can't be shown yet: they'd have to
  be drawn into the video while it's re-encoded.
* Playlist titles (Xtream, M3U) aren't read: that would take a second provider connection while the
  title plays, and most accounts have only one.
"""
import hashlib
import json
import logging
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config
from .streaming import NO_WINDOW, ffmpeg_bin

log = logging.getLogger("tunerbox.tracks")

TEXT = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer"}
SIDE = (".srt", ".ass", ".ssa", ".vtt")
SUB_DIRS = ("subs", "subtitles", "sub")
LANG = {
    "en": "English", "eng": "English", "es": "Spanish", "spa": "Spanish", "fr": "French", "fre": "French",
    "fra": "French", "de": "German", "ger": "German", "deu": "German", "it": "Italian", "ita": "Italian",
    "pt": "Portuguese", "por": "Portuguese", "nl": "Dutch", "dut": "Dutch", "nld": "Dutch", "sv": "Swedish",
    "swe": "Swedish", "da": "Danish", "dan": "Danish", "no": "Norwegian", "nor": "Norwegian", "fi": "Finnish",
    "fin": "Finnish", "pl": "Polish", "pol": "Polish", "ru": "Russian", "rus": "Russian", "ja": "Japanese",
    "jpn": "Japanese", "ko": "Korean", "kor": "Korean", "zh": "Chinese", "chi": "Chinese", "zho": "Chinese",
    "ar": "Arabic", "ara": "Arabic", "he": "Hebrew", "heb": "Hebrew", "el": "Greek", "gre": "Greek",
    "ell": "Greek", "tr": "Turkish", "tur": "Turkish", "hi": "Hindi", "hin": "Hindi", "cs": "Czech",
    "cze": "Czech", "ces": "Czech", "hu": "Hungarian", "hun": "Hungarian", "ro": "Romanian", "rum": "Romanian",
}
STREAM = re.compile(r"^\s*Stream #0:(\d+)(?:\[[^\]]*\])?(?:\((\w+)\))?: (Video|Audio|Subtitle): (\w+)(.*)$")
TITLE = re.compile(r"^\s+title\s*:\s*(.+)$")
CUE = re.compile(r"(?:(\d+):)?(\d\d):(\d\d)[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d\d):(\d\d)[.,](\d{3})")
TAG = re.compile(r"<(?!/?i>)[^>]*>|\{\\[^}]*\}")

_info = {}                      # item id -> (read at, info)
_lock = threading.Lock()
_extract_lock = threading.Lock()


def lang_name(code):
    return LANG.get((code or "").lower(), "") if code and code.lower() not in ("und", "unk") else ""


def _label(lang, title, extra=(), fallback="Unknown"):
    """'English · Commentary', 'English (forced)', the title alone, or `fallback` when it says nothing."""
    name = lang_name(lang)
    parts = [p for p in (name, (title or "").strip()) if p]
    if len(parts) == 2 and parts[1].lower().startswith(parts[0].lower()):
        parts = parts[1:]
    label = " · ".join(parts) or fallback
    return label + "".join(f" ({x})" for x in extra if x)


def _cache_dir():
    d = Path(config.DATA) / "cache" / "subs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(item_id):
    return hashlib.sha1(item_id.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- what a file has
def parse_streams(text):
    """ffmpeg's description of an input -> [{index, kind, codec, lang, title, default, forced, layout}]."""
    out, cur = [], None
    for line in text.splitlines():
        m = STREAM.match(line)
        if m:
            rest = m.group(5)
            layout = next((w for w in ("7.1", "5.1(side)", "5.1", "stereo", "mono") if f" {w}" in rest), "")
            cur = {"index": int(m.group(1)), "kind": m.group(3).lower(), "codec": m.group(4).lower(),
                   "lang": m.group(2) or "", "title": "", "default": "(default)" in rest,
                   "forced": "(forced)" in rest, "layout": layout.replace("(side)", "")}
            out.append(cur)
            continue
        if cur is not None:
            t = TITLE.match(line)
            if t and not cur["title"]:
                cur["title"] = t.group(1).strip()
            elif line.lstrip().startswith(("Stream #", "Input #", "Output #")):
                cur = None
    return out


def _probe(url):
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", url], capture_output=True, timeout=40,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_streams(r.stderr.decode("utf-8", "ignore"))


def _sidecars(item_id):
    """Subtitle files next to a library video: [(path inside the library, label, lang, forced)]."""
    from . import library
    found = library.file_of(item_id)
    if not found:
        return []
    sh, path = found
    folder, _, name = path.rpartition("/")
    stem = name.rsplit(".", 1)[0].lower()
    fs = library.FS(sh)
    out = []

    def consider(rel, fname, any_name):
        low = fname.lower()
        if not low.endswith(SIDE):
            return
        base = low.rsplit(".", 1)[0]
        if base == stem:
            tags = []
        elif base.startswith(stem + "."):
            tags = base[len(stem) + 1:].split(".")
        elif any_name:              # a Subs folder: 'English.srt', '2_English.srt', 'Movie.eng.srt'
            tags = re.split(r"[._\s-]+", base)
        else:
            return
        lang = next((t for t in tags if t in LANG), "")
        extra = [x for x in ("forced", "sdh", "cc") if x in tags]
        title = "" if lang or not any_name else fname.rsplit(".", 1)[0]
        out.append((rel, _label(lang, title, [x.upper() if x != "forced" else x for x in extra], "Subtitle file"), lang,
                    "forced" in tags))

    try:
        entries = fs.listdir(folder)
    except Exception:  # noqa: BLE001
        return []
    for n, is_dir, _, _ in entries:
        if not is_dir:
            consider(f"{folder}/{n}" if folder else n, n, False)
        elif n.lower() in SUB_DIRS:
            sub = f"{folder}/{n}" if folder else n
            try:
                for n2, d2, _, _ in fs.listdir(sub):
                    if not d2:
                        consider(f"{sub}/{n2}", n2, True)
            except Exception:  # noqa: BLE001
                pass
    return sorted(out, key=lambda x: (x[3], x[1].lower()))


def info(item_id, url):
    """{audio: [{n, label, default}], subs: [{id, label, lang, forced, default, picture}]} for a library title."""
    with _lock:
        hit = _info.get(item_id)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    streams = _probe(url)
    audio, subs = [], []
    for s in (s for s in streams if s["kind"] == "audio"):
        audio.append({"n": len(audio), "label": _label(s["lang"], s["title"], [s["layout"]] if s["layout"] else [],
                                                       f"Track {len(audio) + 1}"),
                      "default": s["default"]})
    k = 0
    for s in (s for s in streams if s["kind"] == "subtitle"):
        picture = s["codec"] not in TEXT
        subs.append({"id": f"s{k}", "label": _label(s["lang"], s["title"], ["forced"] if s["forced"] else [],
                                                    f"Subtitles {k + 1}"),
                     "lang": s["lang"], "forced": s["forced"], "default": s["default"], "picture": picture})
        k += 1
    for i, (rel, label, lang, forced) in enumerate(_sidecars(item_id)):
        subs.append({"id": f"f{i}", "label": label + " · file", "lang": lang, "forced": forced, "default": False,
                     "picture": False, "path": rel})
    out = {"audio": audio, "subs": subs}
    with _lock:
        _info[item_id] = (time.time(), out)
    return out


def public(data):
    """What the TV screen gets (no paths inside the library)."""
    return {"audio": data["audio"], "subs": [{k: v for k, v in s.items() if k != "path"} for s in data["subs"]]}


# ---------------------------------------------------------------- subtitles -> timed lines
def parse_vtt(text):
    """WebVTT / SRT text -> [[start, end, text]] (seconds; only <i> kept as formatting)."""
    cues, lines, cur = [], text.replace("\r", "").split("\n"), None
    for line in lines + [""]:
        m = CUE.search(line)
        if m:
            g = [int(x or 0) for x in m.groups()]
            cur = [g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, []]
            continue
        if cur is None:
            continue
        if line.strip():
            cur[2].append(TAG.sub("", line).replace("\\N", "\n").strip())
        else:
            body = "\n".join(t for t in cur[2] if t)
            if body and cur[1] > cur[0]:
                cues.append([round(cur[0], 3), round(cur[1], 3), body])
            cur = None
    cues.sort(key=lambda c: c[0])
    return cues


def _to_vtt(cmd_in, timeout):
    """Run ffmpeg with input options `cmd_in` -> WebVTT text (or '')."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", *cmd_in, "-f", "webvtt", "pipe:1"],
                           capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.decode("utf-8", "ignore")


def _embedded(item_id, url, data):
    """All the text subtitles inside the file, in one read of it (a film is read once, however many there are)."""
    cache = _cache_dir()
    todo = [s for s in data["subs"] if s["id"].startswith("s") and not s["picture"]
            and not (cache / f"{_key(item_id)}-{s['id']}.json").exists()]
    if not todo:
        return
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", url]
        for s in todo:
            cmd += ["-map", f"0:s:{s['id'][1:]}", "-f", "webvtt", str(Path(tmp) / f"{s['id']}.vtt")]
        try:
            subprocess.run(cmd, capture_output=True, timeout=900, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("Subtitles for %s: %s", item_id, e)
            return
        for s in todo:
            f = Path(tmp) / f"{s['id']}.vtt"
            if f.exists():
                cues = parse_vtt(f.read_text(encoding="utf-8", errors="ignore"))
                (cache / f"{_key(item_id)}-{s['id']}.json").write_text(json.dumps(cues), encoding="utf-8")


def _sidecar(item_id, sub):
    from . import library
    sh, _ = library.file_of(item_id)
    with library.FS(sh).open(sub["path"]) as f:
        raw = f.read(20 * 1024 * 1024)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    ext = sub["path"].rsplit(".", 1)[-1].lower()
    if ext in ("srt", "vtt"):
        return parse_vtt(text)
    with tempfile.TemporaryDirectory() as tmp:          # ASS/SSA: ffmpeg turns it into WebVTT
        p = Path(tmp) / f"sub.{ext}"
        p.write_text(text, encoding="utf-8")
        return parse_vtt(_to_vtt(["-i", str(p)], 60))


def cues(item_id, url, sub_id):
    """[[start, end, text]] for subtitle `sub_id` of a library title (read once, then from the cache)."""
    data = info(item_id, url)
    sub = next((s for s in data["subs"] if s["id"] == sub_id), None)
    if not sub:
        raise KeyError(sub_id)
    if sub["picture"]:
        raise ValueError("Picture subtitles (PGS, VobSub) can't be shown yet")
    f = _cache_dir() / f"{_key(item_id)}-{sub_id}.json"
    if not f.exists():
        if sub_id.startswith("f"):
            f.write_text(json.dumps(_sidecar(item_id, sub)), encoding="utf-8")
        else:
            with _extract_lock:                 # one film read at a time
                _embedded(item_id, url, data)
    if not f.exists():
        raise ValueError("Couldn't read those subtitles")
    return json.loads(f.read_text(encoding="utf-8"))
