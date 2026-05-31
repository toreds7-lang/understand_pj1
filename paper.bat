@echo off
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: Python venv not found at "%~dp0%PY%"
    echo Double-click setup.bat first to install dependencies.
    pause
    exit /b 1
)

if not exist "env.txt" (
    echo ERROR: env.txt not found.
    echo Copy env_example.txt to env.txt and fill in your OPENAI_API_KEY.
    pause
    exit /b 1
)

if "%~1"=="" goto serve

REM Drag-and-drop: process the PDF first, then serve it.
"%PY%" run.py "%~1"
if errorlevel 1 (
    echo.
    echo run.py failed ^(exit %errorlevel%^).
    pause
    exit /b %errorlevel%
)

REM Derive paper_id the same way run.py does: stem.lower().replace(" ", "_")
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[IO.Path]::GetFileNameWithoutExtension('%~1').ToLower().Replace(' ','_')"`) do set "PID=%%I"

"%PY%" serve.py %PID%
if errorlevel 1 pause
exit /b %errorlevel%

:serve
"%PY%" serve.py
if errorlevel 1 pause
exit /b %errorlevel%
