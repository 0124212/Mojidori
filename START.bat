@echo off
REM =====================================================================
REM  Mojidori — double-click to run. Opens the web UI in your browser.
REM  First run sets up a private Python environment (.venv) next to this
REM  file and installs what's missing. Just wait.
REM =====================================================================
cd /d "%~dp0"
set KMP_DUPLICATE_LIB_OK=TRUE
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" goto :run

REM --- find a Python to build the environment with (3.12 preferred) ---
set "PY="
where py >nul 2>&1 && (py -3.12 -c "" >nul 2>&1 && set "PY=py -3.12" || set "PY=py -3")
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo.
    echo  Python not found. Installing it via winget...
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    echo.
    echo  Python installed. Please DOUBLE-CLICK this file again to continue.
    pause
    exit /b 0
)

echo  Creating the app's Python environment (.venv)...
%PY% -m venv .venv
if errorlevel 1 (
    echo  Could not create .venv - see the error above.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul

:run
REM --- ffmpeg (decodes every audio/video format) ---
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  FFmpeg not found. Installing via winget, one-time...
    winget install -e --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
    echo  If the app says FFmpeg is missing, close this window and double-click START.bat again.
)

".venv\Scripts\python.exe" start.py %*
if errorlevel 1 (
    echo.
    echo  Something went wrong - the error is above.
    pause
)
