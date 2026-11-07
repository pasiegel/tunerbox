# Publishing TunerBox and making releases

GitHub Actions builds the desktop app for you. No secrets or signing keys are needed.

| Workflow | When | What |
|---|---|---|
| **Checks** (`.github/workflows/ci.yml`) | every push and pull request | Python compiles, the server starts and answers, JavaScript syntax, version number. About a minute. |
| **Build & release** (`.github/workflows/release.yml`) | pushing a tag like `v1.4.1`, or **Actions › Build & release › Run workflow** | Windows installer + portable zip, Mac `.dmg` (Apple Silicon), Linux `.tar.gz`, each with ffmpeg included. With a tag, it publishes them as a GitHub Release. |
| **Docker image** (`.github/workflows/docker.yml`) | the same `v*.*.*` tag, or **Run workflow** | Multi-arch (amd64 + arm64) server image pushed to `ghcr.io/<you>/tunerbox` as `1.4.1`, `1.4` and `latest` (`edge` for manual runs). |

## One-time setup

### 1. Create the GitHub repository and push this folder
Make an empty repository on GitHub (no README or .gitignore, this folder has them), then in this `repo` folder:

```powershell
git init -b main
git add .
git update-index --add --chmod=+x run.sh
git commit -m "TunerBox 1.4.0"
git remote add origin https://github.com/<you>/tunerbox.git
git push -u origin main
```

### 2. Let the workflow publish releases
**Settings › Actions › General › Workflow permissions**: choose **Read and write permissions**. The workflow asks for it itself, but some accounts default to read-only.

## Making a release

1. Commit your changes and push.
2. Tag the version and push the tag:
   ```powershell
   git tag v1.4.1
   git push origin v1.4.1
   ```
3. Watch it under **Actions**. In about 10–15 minutes the release appears under **Releases** with the downloads and install notes.

The tag sets the version: the workflow runs `python packaging/set_version.py 1.4.1` before building. To keep the file in the repo matching, run the same command yourself before tagging and commit the result.

A test build without a release: **Actions › Build & release › Run workflow**. The files are under the run's **Artifacts**.

## What each build uses (all handled by the workflow)

| Build | Runner | Notes |
|---|---|---|
| Windows | `windows-latest` | PyInstaller, Inno Setup (Chocolatey), ffmpeg "essentials" from gyan.dev. Not code-signed, so SmartScreen warns once. |
| macOS | `macos-14` (Apple Silicon) | PyInstaller `.app` in a `.dmg`, static ffmpeg from ffmpeg.martin-riedl.de (if that download fails, the app uses a Homebrew ffmpeg instead). Not notarized: right-click › Open the first time. Intel Macs can run from source (`./run.sh`). |
| Linux | `ubuntu-22.04` | PyInstaller folder in a `.tar.gz`, static ffmpeg from johnvansickle.com. |

## Day-to-day
The original TunerBox folder stays the workspace (it has the Python environment and local build tools). After changing code there, copy the source over and commit:

```powershell
cd H:\PycharmProjects\TunerBox
.venv\Scripts\python packaging\export_repo.py      # updates repo\ (keeps .github, RELEASING.md, .gitignore)
cd repo
git add -A
git commit -m "what changed"
git push
```

To run from source: `run.bat` (Windows) or `./run.sh` (Mac/Linux). To build locally: `python packaging/build.py`.

Not included on purpose: `.venv`, `build/`, `dist/`, the Android and iPad apps (they need private signing keys), and `data/` apart from the default lineup (your settings, database and logs stay on your PC).
