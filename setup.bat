@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  Paper Reader - one-time setup
echo ============================================================
echo.

REM Find a usable Python launcher.
set "BOOT_PY="
where py >nul 2>nul && set "BOOT_PY=py -3"
if not defined BOOT_PY (
    where python >nul 2>nul && set "BOOT_PY=python"
)
if not defined BOOT_PY (
    echo ERROR: Python 3 was not found on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo Using Python: %BOOT_PY%
%BOOT_PY% --version
echo.

if exist ".venv\Scripts\python.exe" (
    echo Found existing .venv - reusing it.
) else (
    echo Creating virtual environment in .venv ...
    %BOOT_PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo Failed to create .venv.
        pause
        exit /b 1
    )
)
echo.

set "PY=.venv\Scripts\python.exe"

echo Upgrading pip ...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo pip upgrade failed.
    pause
    exit /b 1
)
echo.

echo Installing requirements ^(this can take a few minutes^) ...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency install failed.
    pause
    exit /b 1
)
echo.

if not exist "env.txt" (
    if exist "env_example.txt" (
        copy /Y "env_example.txt" "env.txt" >nul
        echo Created env.txt from env_example.txt.
    ) else (
        echo WARNING: env_example.txt not found - you'll need to create env.txt manually.
    )
) else (
    echo env.txt already exists - leaving it alone.
)
echo.

echo ============================================================
echo  Setup complete.
echo.
echo  NEXT STEPS:
echo    1. Open env.txt in Notepad and set OPENAI_API_KEY=sk-...
echo    2. Drag a PDF onto paper.bat   (or double-click to pick
echo       from already-processed papers under data\)
echo ============================================================
echo.
pause
