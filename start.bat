@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto checkenv
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
    if not errorlevel 1 (
        py -3 -m venv .venv
        goto checkenv
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
    if not errorlevel 1 (
        python -m venv .venv
        goto checkenv
    )
)
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo Enable "Add Python to PATH", then run start.bat again.
pause
exit /b 1
:checkenv
if not exist ".venv\Scripts\python.exe" (
    echo Could not create Python environment. Check Python installation and folder permissions.
    pause
    exit /b 1
)
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies. Internet access is needed on the first run.
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Installation failed. Check Internet access and try again.
        pause
        exit /b 1
    )
    copy /y requirements.txt .venv\installed-requirements.txt >nul
)
".venv\Scripts\python.exe" app.py %*
if errorlevel 1 pause
