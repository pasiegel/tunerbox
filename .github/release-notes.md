## Downloads

| Computer | File | Notes |
|---|---|---|
| Windows 10/11 | `TunerBox-Setup-….exe` | Installs to `C:\Program Files\TunerBox` (asks for admin; pick **Install for me only** to install without admin). Replaces a per-user install from 1.4.2 or older; your settings and recordings stay. Windows SmartScreen may warn because the app isn't code-signed: **More info › Run anyway**. `…-windows-portable.zip` runs without installing. |
| Mac (Apple Silicon) | `TunerBox-…-macos-arm64.dmg` | Drag TunerBox to Applications. The app isn't notarized: the first time, right-click it › **Open**, or run `xattr -dr com.apple.quarantine /Applications/TunerBox.app`. |
| Linux x64 | `TunerBox-…-linux-x64.tar.gz` | Unpack and run `TunerBox/TunerBox` (`--browser` uses your web browser, `--server` runs headless). |
| Docker (x64, ARM64) | `ghcr.io/pasiegel/tunerbox` | Server only. `docker run -d -p 8642:8642 -v tunerbox-data:/data -e TZ=America/New_York ghcr.io/pasiegel/tunerbox:latest` (set `TZ` to your time zone), or use `compose.yaml` from the repo. |

TunerBox runs on one computer (playlists, guide, recordings). Phones, tablets and TV browsers on the same network open `http://<that-computer>:8642/` to watch; on an iPad use Safari › Share › **Add to Home Screen**.
