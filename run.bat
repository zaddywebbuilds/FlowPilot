@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv || goto :fail
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :fail
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :fail
    echo Downloading Chromium for Playwright...
    ".venv\Scripts\python.exe" -m playwright install chromium || goto :fail
)

echo.
echo   FlowPilot is starting at http://127.0.0.1:8000
echo   Press Ctrl+C to stop.
echo.

start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
goto :eof

:fail
echo.
echo Setup failed. Make sure Python 3.10+ is installed and on your PATH.
pause
