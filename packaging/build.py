r"""Build the desktop app.

    .venv\Scripts\python packaging\build.py          Windows: dist\TunerBox + installer
    .venv/bin/python packaging/build.py             macOS: dist/TunerBox.app   Linux: dist/TunerBox

Bundles ffmpeg from the PATH (or --ffmpeg PATH). PyInstaller can't cross-compile, so
build each OS on that OS.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tunerbox import __version__  # noqa: E402
from tunerbox.icon import write_ico, write_png  # noqa: E402

WIN = os.name == "nt"
MAC = sys.platform == "darwin"


def find_ffmpeg(arg):
    if arg:
        return Path(arg)
    from tunerbox import streaming
    p = streaming.ffmpeg_bin()          # looks through Chocolatey/scoop shims
    return Path(shutil.which(p) or p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", help="ffmpeg executable to bundle")
    ap.add_argument("--no-ffmpeg", action="store_true")
    ap.add_argument("--no-installer", action="store_true")
    a = ap.parse_args()

    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    ico = write_ico(build / "tunerbox.ico")
    sep = ";" if WIN else ":"
    data = [(ROOT / "web", "web")]
    if (ROOT / "data" / "channels.csv").exists():      # optional starting lineup
        data.append((ROOT / "data" / "channels.csv", "data"))
    if not a.no_ffmpeg:
        ff = find_ffmpeg(a.ffmpeg)
        if not ff.exists():
            sys.exit(f"ffmpeg not found ({ff}); pass --ffmpeg PATH or --no-ffmpeg")
        print("Bundling", ff)
        data.append((ff, "ffmpeg"))
    icon = ico
    if MAC:
        icon = write_png(build / "tunerbox.png", 512)   # PyInstaller converts it (needs Pillow)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", "TunerBox", "--icon", str(icon),
           "--distpath", str(ROOT / "dist"), "--workpath", str(build / "pyi"), "--specpath", str(build),
           "--collect-submodules", "uvicorn", "--collect-all", "webview",
           "--hidden-import", "tunerbox.api", "--collect-submodules", "qrcode", "--paths", str(ROOT)]
    for src, dst in data:
        cmd += ["--add-data", f"{src}{sep}{dst}"]
    if WIN:
        cmd += ["--version-file", str(write_version_file(build))]
    cmd.append(str(ROOT / "packaging" / "app.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)

    if WIN and not a.no_installer:
        iscc = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        if not Path(iscc).exists():
            print(r"Inno Setup not found - skipping the installer (dist\TunerBox is ready to zip).")
            return
        subprocess.run([iscc, f"/DAppVersion={__version__}", f"/DRoot={ROOT}",
                        str(ROOT / "packaging" / "tunerbox.iss")], check=True)
        print("Installer:", ROOT / "dist" / f"TunerBox-Setup-{__version__}.exe")


def write_version_file(build):
    v = tuple(int(x) for x in __version__.split(".")) + (0,)
    f = build / "version.txt"
    f.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'TunerBox'),
    StringStruct('FileDescription', 'TunerBox IPTV cable box'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('ProductName', 'TunerBox'),
    StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")
    return f


if __name__ == "__main__":
    main()
