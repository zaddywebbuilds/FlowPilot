@echo off
setlocal
cd /d "%~dp0"
title FlowPilot

echo.
echo   FlowPilot
echo   =========
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo   Python is not installed, or is not on your PATH.
    echo.
    echo   1. Download Python from  https://www.python.org/downloads/
    echo   2. During install, TICK "Add python.exe to PATH"
    echo   3. Close this window and run this file again
    echo.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo   Your Python is too old. FlowPilot needs 3.10 or newer.
    echo   Get the latest from  https://www.python.org/downloads/
    echo.
    python --version
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo   First run, setting up. This takes about a minute.
    echo.
    echo   [1/3] Creating environment...
    python -m venv .venv || goto :fail
    echo   [2/3] Upgrading pip...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :fail
    echo   [3/3] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :fail
    echo.
    echo   Setup complete.
    echo.
)

echo   Opening http://127.0.0.1:8000
echo   Leave this window open while you use FlowPilot.
echo   Press Ctrl+C here to stop it.
echo.

start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
goto :eof

:fail
echo.
echo   Setup failed. Check your internet connection and try again.
echo   If it keeps failing, delete the ".venv" folder and re-run this file.
echo.
pause
exit /b 1
