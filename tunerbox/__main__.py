"""TunerBox launcher.

    python -m tunerbox              desktop app window (default)
    python -m tunerbox --server     server only (other devices / Plex use it)
    python -m tunerbox --browser    server + open the web browser
    python -m tunerbox --shortcut   add Desktop and Start Menu shortcuts (Windows)
"""
import argparse
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

from . import config

log = logging.getLogger("tunerbox")


def _quiet_console():
    # pythonw.exe has no console: sys.stdout/stderr are None and uvicorn would crash
    if sys.stdout is None or sys.stderr is None:
        config.DATA.mkdir(parents=True, exist_ok=True)
        sink = open(config.DATA / "console.log", "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink


def _already_running(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/api/jobs", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


def _wait_ready(port: int, server, timeout=20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if server.started:
            return True
        time.sleep(0.1)
    return False


def make_shortcut():
    if os.name != "nt":
        print("Shortcuts are Windows-only. On macOS/Linux run ./run.sh")
        return
    from .icon import write_ico
    if config.FROZEN:
        ico = sys.executable                        # the exe carries the icon
    else:
        ico = config.ROOT / "web" / "tunerbox.ico"
        write_ico(ico)
    if config.FROZEN:
        pyw, args = sys.executable, ""
    else:
        pyw = config.ROOT / ".venv" / "Scripts" / "pythonw.exe"
        if not pyw.exists():
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
        args = "-m tunerbox"
    ps = """
$s = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $l = $s.CreateShortcut((Join-Path $dir 'TunerBox.lnk'))
  $l.TargetPath = $env:TB_PYW
  $l.Arguments = $env:TB_ARGS
  $l.WorkingDirectory = $env:TB_ROOT
  $l.IconLocation = $env:TB_ICO
  $l.Description = 'TunerBox IPTV cable box'
  $l.Save()
}"""
    env = dict(os.environ, TB_PYW=str(pyw), TB_ARGS=args, TB_ROOT=str(config.ROOT), TB_ICO=str(ico))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True)
    print("Added TunerBox to the Desktop and Start Menu.")


class _NoClientResets(logging.Filter):
    """Windows asyncio logs a traceback whenever a viewer drops a connection; it's harmless."""
    def filter(self, record):
        return not (record.exc_info and isinstance(record.exc_info[1], (ConnectionResetError, BrokenPipeError)))


class _JsApi:
    """Called from the page as window.pywebview.api.*"""
    def __init__(self):
        self._window = None

    def toggle_fullscreen(self):
        self._window.toggle_fullscreen()

    def open_external(self, url):
        webbrowser.open(url)

    def pick_folder(self):
        """Folder picker for Local channels (network folders work too)."""
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
        got = self._window.create_file_dialog(kind)
        return got[0] if got else ""


def _kill_orphan_webviews(profile):
    """A crashed app can leave WebView2 processes holding its profile folder, and then the
    window never opens. Only called when no other TunerBox is running."""
    if os.name != "nt":
        return
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe'\" | "
          "Where-Object { $_.CommandLine -like ('*' + $env:TB_PROFILE + '*') } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=dict(os.environ, TB_PROFILE=str(profile)),
                       timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def run_window(url: str, server, thread):
    import webview
    api = _JsApi()
    win = webview.create_window("TunerBox", url, js_api=api, width=1280, height=760, min_size=(640, 400),
                                background_color="#000000", text_select=False)
    api._window = win
    try:
        webview.start(private_mode=False, storage_path=str(config.DATA / "webview"))
    except Exception as e:                      # no GUI toolkit (e.g. Linux without GTK/Qt)
        log.warning("App window unavailable (%s) - using the web browser", e)
        webbrowser.open(url)
        if thread is not None:
            thread.join()
        return
    # window closed: stop the server (this also stops live streams)
    if server is not None:
        server.should_exit = True
        thread.join(timeout=8)


def main():
    _quiet_console()
    ap = argparse.ArgumentParser(prog="tunerbox")
    ap.add_argument("--port", type=int)
    ap.add_argument("--host")
    ap.add_argument("--server", action="store_true", help="run the server only, no window")
    ap.add_argument("--browser", action="store_true", help="open the web browser instead of the app window")
    ap.add_argument("--window", action="store_true", help=argparse.SUPPRESS)   # old flag, now the default
    ap.add_argument("--shortcut", action="store_true", help="add Desktop/Start Menu shortcuts (Windows)")
    a = ap.parse_args()

    if a.shortcut:
        make_shortcut()
        return

    cfg = config.load()
    # every channel change logs a line or two: keep the log to about 20 MB on a box that's always on
    handlers = [logging.handlers.RotatingFileHandler(config.DATA / "tunerbox.log", maxBytes=5_000_000, backupCount=3,
                                                     encoding="utf-8", delay=True)]
    if sys.stdout and sys.stdout.isatty():
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").addFilter(_NoClientResets())
    host = a.host or cfg["host"]
    port = a.port or cfg["port"]
    url = f"http://127.0.0.1:{port}/"

    want_window = not (a.server or a.browser)
    if want_window:
        try:
            import webview  # noqa: F401
        except ImportError:
            print("pywebview isn't installed (pip install pywebview) - opening the browser instead")
            want_window, a.browser = False, True

    # a second launch just opens another window on the running server
    if _already_running(port):
        log.info("TunerBox already running on port %s", port)
        if want_window:
            run_window(url + "?on", None, None)
        elif a.browser:
            webbrowser.open(url)
        else:
            print(f"TunerBox is already running on port {port}")
        return

    try:
        lan = socket.gethostbyname(socket.gethostname())
        print(f"\n  TunerBox: {url}   (other devices: http://{lan}:{port}/)\n  Setup:    {url}setup\n")
    except OSError:
        pass

    from .api import app
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port,
                                           log_level="warning", log_config=None))
    if want_window:
        _kill_orphan_webviews(config.DATA / "webview")
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        if not _wait_ready(port, server):
            log.error("Server didn't start on port %s", port)
        run_window(url + "?on", server, t)
        return
    if a.browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    server.run()


if __name__ == "__main__":
    main()
