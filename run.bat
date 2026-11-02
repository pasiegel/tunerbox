@echo off
rem TunerBox launcher (Windows). Sets up Python on first run, then opens the app window.
rem   run.bat            app window
rem   run.bat --server   server only (TV browsers, Plex, VLC on other devices)
rem   run.bat --browser  use your web browser instead of the app window
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First run: setting up Python environment...
  python -m venv .venv || (echo Python 3.10+ is required & pause & exit /b 1)
)
fc /b requirements.txt .venv\requirements.txt >nul 2>nul || (
  echo Installing packages...
  .venv\Scripts\python -m pip install -q -r requirements.txt || (pause & exit /b 1)
  copy /y requirements.txt .venv\requirements.txt >nul
  .venv\Scripts\python -m tunerbox --shortcut
)
where ffmpeg >nul 2>nul || echo WARNING: ffmpeg not found on PATH - install it (winget install ffmpeg) or set its path in Setup ^> Settings.
if "%~1"=="" (
  start "" .venv\Scripts\pythonw.exe -m tunerbox
) else (
  .venv\Scripts\python -m tunerbox %*
)
