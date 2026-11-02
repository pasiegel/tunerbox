#!/usr/bin/env sh
# TunerBox launcher (macOS / Linux). Sets up Python on first run, then opens the app window.
#   ./run.sh            app window (falls back to the browser if no GUI toolkit)
#   ./run.sh --server   server only
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Python environment..."
  python3 -m venv .venv || exit 1
fi
if ! cmp -s requirements.txt .venv/requirements.txt; then
  .venv/bin/python -m pip install -q -r requirements.txt && cp requirements.txt .venv/requirements.txt || exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || echo "WARNING: ffmpeg not found (brew install ffmpeg / apt install ffmpeg)"
exec .venv/bin/python -m tunerbox "$@"
