"""Audio tracks and subtitles of library titles: reading what a file has, subtitle files next to it,
turning subtitles into timed lines, and asking ffmpeg for another audio track."""
import shutil
import subprocess

import pytest

from tunerbox import hls, streaming, tracks, vod

FFMPEG_I = """Input #0, matroska,webm, from 'http://127.0.0.1:8646/api/library/file/L3:x':
  Duration: 02:41:50.57, start: 0.000000, bitrate: 2126 kb/s
  Stream #0:0: Video: hevc (Main 10), yuv420p10le(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 23.98 fps (default)
    Metadata:
      title           : The.Good.35mm.LPP-TechnicolorReference
  Stream #0:1(eng): Audio: aac (LC), 48000 Hz, stereo, fltp (default)
    Metadata:
      title           : Theatrical Mono [2021 Kino UHD]
  Stream #0:2(eng): Audio: aac (LC), 48000 Hz, mono, fltp
    Metadata:
      title           : Commentary
  Stream #0:3(eng): Subtitle: hdmv_pgs_subtitle (pgssub), 1920x1080
    Metadata:
      title           : PGS
  Stream #0:4(eng): Subtitle: subrip (srt) (default) (forced)
    Metadata:
      title           : OCR -SRT-
  Stream #0:5(spa): Audio: ac3, 48000 Hz, 5.1(side), fltp, 640 kb/s
  Stream #0:6: Subtitle: ass (ssa)
At least one output file must be specified
"""

SRT = """1
00:00:00,500 --> 00:00:01,500
Hello <i>there</i>

2
00:00:01,600 --> 00:00:02,900
<font color="red">Second</font> line
with two rows

3
01:02:03,004 --> 01:02:04,000
{\\an8}Up top
"""


def test_parse_streams():
    s = tracks.parse_streams(FFMPEG_I)
    assert [x["kind"] for x in s] == ["video", "audio", "audio", "subtitle", "subtitle", "audio", "subtitle"]
    a1 = s[1]
    assert (a1["lang"], a1["title"], a1["default"], a1["layout"]) == ("eng", "Theatrical Mono [2021 Kino UHD]", True, "stereo")
    assert s[2]["title"] == "Commentary" and s[2]["layout"] == "mono" and not s[2]["default"]
    assert s[3]["codec"] == "hdmv_pgs_subtitle" and s[3]["title"] == "PGS"
    assert s[4]["codec"] == "subrip" and s[4]["forced"] and s[4]["default"]
    assert s[5]["layout"] == "5.1" and s[5]["title"] == ""
    assert s[6]["lang"] == "" and s[6]["title"] == ""          # no metadata: the next stream's isn't borrowed


def test_info_labels_and_kinds(monkeypatch):
    monkeypatch.setattr(tracks, "_probe", lambda url: tracks.parse_streams(FFMPEG_I))
    monkeypatch.setattr(tracks, "_sidecars", lambda item_id: [("Movie/Movie.en.srt", "English", "en", False)])
    d = tracks.info("L3:x", "http://x")
    assert [a["label"] for a in d["audio"]] == ["English · Theatrical Mono [2021 Kino UHD] (stereo)",
                                                "English · Commentary (mono)", "Spanish (5.1)"]
    assert [a["n"] for a in d["audio"]] == [0, 1, 2]
    subs = {s["id"]: s for s in d["subs"]}
    assert subs["s0"]["picture"] and subs["s0"]["label"] == "English · PGS"
    assert not subs["s1"]["picture"] and subs["s1"]["forced"] and subs["s1"]["label"] == "English · OCR -SRT- (forced)"
    assert not subs["s2"]["picture"] and subs["s2"]["label"] == "Subtitles 3"
    assert subs["f0"]["label"] == "English · file" and subs["f0"]["path"] == "Movie/Movie.en.srt"
    assert "path" not in tracks.public(d)["subs"][-1]           # the TV screen isn't told where files are


def test_parse_vtt_srt():
    cues = tracks.parse_vtt(SRT)
    assert cues[0] == [0.5, 1.5, "Hello <i>there</i>"]                 # <i> kept, other tags dropped
    assert cues[1] == [1.6, 2.9, "Second line\nwith two rows"]
    assert cues[2] == [3723.004, 3724.0, "Up top"]                      # ASS override tags dropped
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.500 align:start\nShort form\n"
    assert tracks.parse_vtt(vtt) == [[1.0, 2.5, "Short form"]]


def test_sidecar_files(tmp_path, monkeypatch):
    movie = tmp_path / "The Movie (1966)"
    (movie / "Subs").mkdir(parents=True)
    for n in ("The Movie (1966).mkv", "The Movie (1966).srt", "The Movie (1966).en.forced.srt",
              "The Movie (1966).spa.sdh.ass", "Other Film.srt", "notes.txt"):
        (movie / n).write_text("x", encoding="utf-8")
    (movie / "Subs" / "2_French.srt").write_text("x", encoding="utf-8")
    sh = {"type": "folder", "path": str(tmp_path)}
    from tunerbox import library
    monkeypatch.setattr(library, "file_of", lambda item_id: (sh, "The Movie (1966)/The Movie (1966).mkv"))
    found = {rel: (label, lang, forced) for rel, label, lang, forced in tracks._sidecars("L9:x")}
    assert found["The Movie (1966)/The Movie (1966).srt"] == ("Subtitle file", "", False)
    assert found["The Movie (1966)/The Movie (1966).en.forced.srt"] == ("English (forced)", "en", True)
    assert found["The Movie (1966)/The Movie (1966).spa.sdh.ass"] == ("Spanish (SDH)", "spa", False)
    assert found["The Movie (1966)/Subs/2_French.srt"][0] == "2_French"          # 'french' isn't a code: its name
    assert not any("Other Film" in k or "notes" in k for k in found)


def test_stream_commands_pick_the_audio_track():
    cmd = vod.stream_cmd("http://127.0.0.1:1/api/library/file/L3:x", 10, None, None, 1)
    assert cmd[cmd.index("0:v:0?") + 2] == "0:a:1?"
    assert "0:a:0?" in vod.stream_cmd("http://127.0.0.1:1/f", 0)
    h = hls._cmd("http://127.0.0.1:1/f", "copy", 0, ["-re"], 2)
    assert "0:a:2?" in h and "0:v:0?" in h
    assert "-map" not in hls._cmd("http://127.0.0.1:1/f", "copy", 0, ["-re"])     # unchanged without a choice


@pytest.mark.skipif(not shutil.which(streaming.ffmpeg_bin()) and not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_embedded_subtitles_end_to_end(tmp_path, monkeypatch):
    """A real MKV with two audio tracks and an SRT inside: read its tracks, then its subtitles (cached)."""
    srt = tmp_path / "t.srt"
    srt.write_text(SRT.split("\n\n3")[0] + "\n", encoding="utf-8")
    mkv = tmp_path / "m.mkv"
    r = subprocess.run([streaming.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
                        "-f", "lavfi", "-i", "sine=duration=3", "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
                        "-i", str(srt), "-map", "0", "-map", "1", "-map", "2", "-map", "3",
                        "-c:v", "mpeg4", "-c:a", "aac", "-c:s", "srt",
                        "-metadata:s:a:1", "title=Commentary", "-metadata:s:s:0", "language=eng", str(mkv)],
                       capture_output=True)
    if r.returncode:
        pytest.skip("this ffmpeg can't make the sample")
    monkeypatch.setattr(tracks, "_sidecars", lambda item_id: [])
    d = tracks.info("L1:e2e", str(mkv))
    assert [a["label"] for a in d["audio"]] == ["Track 1 (mono)", "Commentary (mono)"]
    assert [s["id"] for s in d["subs"]] == ["s0"] and d["subs"][0]["label"] == "English"
    cues = tracks.cues("L1:e2e", str(mkv), "s0")
    assert cues[0][2] == "Hello <i>there</i>" and abs(cues[0][0] - 0.5) < 0.01
    assert len(cues) == 2
    mkv.unlink()                                       # second time: from the cache, the file isn't needed
    assert tracks.cues("L1:e2e", str(mkv), "s0") == cues
    with pytest.raises(KeyError):
        tracks.cues("L1:e2e", str(mkv), "s9")
