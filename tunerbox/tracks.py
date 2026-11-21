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
import xml.etree.ElementTree as ET
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


SIZE = re.compile(r"\b(\d{3,5})x(\d{3,5})\b")
FPS = re.compile(r"([\d.]+) fps")
KBPS = re.compile(r"Duration:.*?bitrate:\s*(\d+) kb/s")
PICTURE = {"mjpeg", "png", "bmp", "gif"}       # cover art, not the film


def av_summary(text):
    """ffmpeg's description of an input -> what Pick a version shows about it:
    {w, h, vcodec, bits, hdr, fps, acodec, layout, kbps} ({} when it has no picture).
    Only the input part counts: what's after "Output #" is what TunerBox makes of it."""
    head = text.split("Output #", 1)[0].split("Stream mapping:", 1)[0]
    video = audio = None
    for line in head.splitlines():
        m = STREAM.match(line)
        if not m:
            continue
        kind, codec, rest = m.group(3), m.group(4).lower(), m.group(5)
        if kind == "Video" and video is None and codec not in PICTURE and "attached pic" not in rest:
            video = (codec, rest)
        elif kind == "Audio" and audio is None:          # the first one: what plays unless another is picked
            audio = (codec, rest)
    if not video:
        return {}
    codec, rest = video
    size, fps, kbps = SIZE.search(rest), FPS.search(rest), KBPS.search(head)
    hdr = ("Dolby Vision" if "DOVI configuration" in head else "HDR10" if "smpte2084" in rest
           else "HLG" if "arib-std-b67" in rest else "")
    out = {"w": int(size.group(1)) if size else 0, "h": int(size.group(2)) if size else 0, "vcodec": codec,
           "bits": 12 if "p12" in rest else 10 if "p10" in rest else 8, "hdr": hdr,
           "fps": float(fps.group(1)) if fps else 0, "kbps": int(kbps.group(1)) if kbps else 0}
    if audio:
        layout = next((w for w in ("7.1", "5.1(side)", "5.1", "stereo", "mono") if f" {w}" in audio[1]), "")
        out.update(acodec=audio[0], layout=layout.replace("(side)", ""))
    return out


_LAYOUTS = {8: "7.1", 7: "6.1", 6: "5.1", 2: "stereo", 1: "mono"}


def _layout(text, channels=0):
    """"5.1(side)" / "stereo" / 6 channels -> "5.1"."""
    text = str(text or "").lower()
    found = next((w for w in ("7.1", "6.1", "5.1", "stereo", "mono") if w in text), "")
    return found or _LAYOUTS.get(channels, "")


def xtream_summary(info):
    """An Xtream panel's own reading of a movie (get_vod_info's info.video / info.audio, ffprobe's fields)
    -> the same {w, h, vcodec, bits, hdr, fps, acodec, layout} as av_summary, or {} when it has none."""
    v = info.get("video") if isinstance(info.get("video"), dict) else {}
    a = info.get("audio") if isinstance(info.get("audio"), dict) else {}
    try:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    except (TypeError, ValueError):
        return {}
    if not (w and h):
        return {}
    pix, trc = str(v.get("pix_fmt") or ""), str(v.get("color_transfer") or "")
    side = str(v.get("side_data_list") or "").lower()
    hdr = ("Dolby Vision" if "dovi" in side or "dolby vision" in side else "HDR10" if trc == "smpte2084"
           else "HLG" if trc == "arib-std-b67" else "")
    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = str(v.get(key) or "").partition("/")
        try:
            fps = round(float(num) / float(den or 1), 3)
        except (ValueError, ZeroDivisionError):
            continue
        if fps:
            break
    out = {"w": w, "h": h, "vcodec": str(v.get("codec_name") or "").lower(),
           "bits": 12 if "p12" in pix else 10 if "p10" in pix else 8, "hdr": hdr, "fps": fps}
    if a.get("codec_name"):
        try:
            ch = int(a.get("channels") or 0)
        except (TypeError, ValueError):
            ch = 0
        out.update(acodec=str(a["codec_name"]).lower(), layout=_layout(a.get("channel_layout"), ch))
    return out


# what a release name says ("Heat.1995.2160p.UHD.BluRay.DV.HDR.x265.TrueHD.Atmos.7.1-GRP"). Separators are dots,
# spaces, dashes, underscores or brackets; (?<![a-z0-9]) / (?![a-z0-9]) stand in for word edges between them
_E, _F = r"(?<![a-z0-9])", r"(?![a-z0-9])"
_N_RES = [(re.compile(_E + r"(2160p|4k|uhd)" + _F), 3840, 2160), (re.compile(_E + r"1080[pi]" + _F), 1920, 1080),
          (re.compile(_E + r"720p" + _F), 1280, 720), (re.compile(_E + r"(576p|480p|dvdrip|dvd)" + _F), 720, 480)]
_N_VIDEO = [(re.compile(_E + r"([xh]\.?265|hevc)" + _F), "hevc"), (re.compile(_E + r"([xh]\.?264|avc)" + _F), "h264"),
            (re.compile(_E + r"av1" + _F), "av1"), (re.compile(_E + r"(xvid|divx)" + _F), "mpeg4")]
_N_HDR = [(re.compile(_E + r"(dolby[ ._-]?vision|dovi|dv)" + _F), "Dolby Vision"),
          (re.compile(_E + r"hdr10(\+|plus)"), "HDR10+"), (re.compile(_E + r"hdr(10)?" + _F), "HDR10"),
          (re.compile(_E + r"hlg" + _F), "HLG")]
# audio: the codec can run into the channels ("DDP5.1", "DTS-HD.MA.7.1", "AAC2.0")
_N_AUDIO = [(re.compile(_E + r"truehd"), "truehd"), (re.compile(_E + r"(ddp|dd\+|e-?ac-?3)"), "eac3"),
            (re.compile(_E + r"dts"), "dts"), (re.compile(_E + r"(ac-?3|dd)(?=[ ._-]?\d|[^a-z0-9]|$)"), "ac3"),
            (re.compile(_E + r"aac"), "aac"), (re.compile(_E + r"flac" + _F), "flac"),
            (re.compile(_E + r"opus" + _F), "opus")]
_N_CH = re.compile(r"(?<!\d)(?<!\d\.)([1-7])[ .]([01])(?!\d)")


def name_summary(name):
    """What a file or release name says about the picture and sound, as av_summary's fields (those it can tell),
    or {} when it says nothing about the picture. A guess until the file itself is read."""
    s = str(name or "").lower()
    out = {}
    for rx, w, h in _N_RES:
        if rx.search(s):
            out.update(w=w, h=h)
            break
    for rx, codec in _N_VIDEO:
        if rx.search(s):
            out["vcodec"] = codec
            break
    hdr = [label for rx, label in _N_HDR if rx.search(s)]
    if hdr:
        out["hdr"] = hdr[0]
    if not out:
        return {}
    for rx, codec in _N_AUDIO:
        if rx.search(s):
            out["acodec"] = codec
            break
    ch = _N_CH.search(s)
    if ch and "acodec" in out:
        n = int(ch.group(1)) + int(ch.group(2))
        out["layout"] = _LAYOUTS.get(n, f"{ch.group(1)}.{ch.group(2)}")
    return out


# An NFO file next to a video (Kodi, Jellyfin, Emby, Plex add-ons, Radarr and Sonarr write them): its
# <fileinfo><streamdetails> is what the program that wrote it read from the file, so the file needn't be read again.
# Codec names vary by writer: mapped to ffmpeg's, as av_summary gives them
_NFO_INFO = re.compile(rb"<fileinfo\b.*?</fileinfo\s*>", re.S | re.I)
_NFO_VCODEC = {"avc": "h264", "avc1": "h264", "x264": "h264", "h.264": "h264", "h265": "hevc", "x265": "hevc",
               "h.265": "hevc", "hvc1": "hevc", "hev1": "hevc", "xvid": "mpeg4", "divx": "mpeg4", "dx50": "mpeg4",
               "mpeg2": "mpeg2video", "mpeg-2": "mpeg2video", "vc-1": "vc1", "wvc1": "vc1"}
_NFO_ACODEC = {"dca": "dts", "dtshd_ma": "dts", "dtshd_hra": "dts", "dts-hd": "dts", "dts-hd ma": "dts", "dtsx": "dts",
               "ac-3": "ac3", "e-ac-3": "eac3", "ec-3": "eac3", "ddp": "eac3", "dd+": "eac3", "mlp": "truehd",
               "mpeg1audio": "mp3", "mp3float": "mp3"}
_NFO_HDR = {"dolbyvision": "Dolby Vision", "dolby vision": "Dolby Vision", "dovi": "Dolby Vision", "hdr10": "HDR10",
            "hdr10+": "HDR10+", "hdr10plus": "HDR10+", "hlg": "HLG"}


def _num(el, tag, kind=int):
    try:
        return kind(float((el.findtext(tag) or "").strip())) if el is not None else 0
    except ValueError:
        return 0


def nfo_summary(data):
    """An NFO file's bytes -> (length in seconds, the same fields as av_summary plus kbps when it gives one), or
    (0, {}) when it doesn't say how long the video is and what its picture is: then the video itself is read.
    Only the <fileinfo> part is parsed (an NFO may have a link after the XML, and nothing else is needed)."""
    m = _NFO_INFO.search(data or b"")
    if not m:
        return 0, {}
    try:
        info = ET.fromstring(m.group(0))
    except ET.ParseError:
        return 0, {}
    sd = info.find("streamdetails")
    video = sd.find("video") if sd is not None else None
    w, h = _num(video, "width"), _num(video, "height")
    secs = _num(video, "durationinseconds") or next(
        (n for el in (sd.findall("*") if sd is not None else []) if (n := _num(el, "durationinseconds"))), 0)
    if not (w and h and secs > 0):
        return 0, {}
    codec = (video.findtext("codec") or "").strip().lower()
    hdr = _NFO_HDR.get((video.findtext("hdrtype") or "").strip().lower(), "")
    out = {"w": w, "h": h, "vcodec": _NFO_VCODEC.get(codec, codec), "bits": 10 if hdr else 8, "hdr": hdr,
           "fps": round(_num(video, "framerate", float), 3)}
    audio = sd.find("audio")
    if audio is not None and (ac := (audio.findtext("codec") or "").strip().lower()):
        out.update(acodec=_NFO_ACODEC.get(ac, ac), layout=_layout("", _num(audio, "channels")))
    return secs, out


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
