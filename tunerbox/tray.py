"""Notification-area (system tray) icon for server-only mode on Windows: open TunerBox, open Setup,
or exit without hunting for the process. Needs pystray + Pillow; without them (or on another OS)
run() returns False and the server just runs as before."""
import logging
import os
import subprocess
import sys
import threading
import webbrowser

from . import config

log = logging.getLogger("tunerbox.tray")


def available() -> bool:
    if os.name != "nt":
        return False
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _image():
    from PIL import Image
    from .icon import _rgba
    size = 64
    return Image.frombytes("RGBA", (size, size), _rgba(size))


def _open_window():
    """Start the app normally: it finds this server running and just opens a window on it."""
    if config.FROZEN:
        cmd = [sys.executable]
    else:
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        cmd = [pyw if os.path.exists(pyw) else sys.executable, "-m", "tunerbox"]
    try:
        subprocess.Popen(cmd, cwd=str(config.ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        log.warning("Couldn't open the TunerBox window (%s)", e)


def run(url: str, server, thread) -> bool:
    """Show the tray icon until Exit is picked (or the server stops). Blocks; False if no tray."""
    if not available():
        return False
    import pystray

    def exit_app(icon, _item=None):
        log.info("Exit from the tray icon")
        server.should_exit = True
        icon.visible = False
        icon.stop()

    port = url.rstrip("/").rsplit(":", 1)[-1]
    menu = pystray.Menu(
        pystray.MenuItem("Open TunerBox", lambda: _open_window(), default=True),
        pystray.MenuItem("Open in web browser", lambda: webbrowser.open(url)),
        pystray.MenuItem("Setup", lambda: webbrowser.open(url + "setup")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit TunerBox", exit_app),
    )
    icon = pystray.Icon("TunerBox", _image(), f"{config.box_name()} server (port {port})", menu)

    def watch():
        # the server stopped by itself (port in use, crash): take the icon away too
        thread.join()
        icon.stop()
    threading.Thread(target=watch, daemon=True).start()

    log.info("Server only: TunerBox icon in the notification area (right-click > Exit TunerBox to stop)")
    try:
        icon.run()
    except Exception as e:  # noqa: BLE001  (no notification area, e.g. a service session)
        log.warning("Tray icon unavailable (%s)", e)
        return False
    server.should_exit = True
    thread.join(timeout=8)
    return True
