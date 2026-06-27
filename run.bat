@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ===================== CONFIG =====================
set "HOST=0.0.0.0"
set "PORT=8000"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "DISPLAY_HOST=127.0.0.1"
set "DAEMON_HOST=127.0.0.1"
set "REQ_STAMP=.venv\.requirements.sha256"
set "NETWORK_IP=192.168.56.1"

REM ──────────────────────────────────────────────────────────────────────────────
title DB TESTLAB Server - %HOST%:%PORT%
cd /d "%~dp0"

REM ── Create .venv if missing ───────────────────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Virtual environment not found. Creating .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        echo         Make sure Python is installed and on PATH.
        pause & exit /b 1
    )
    echo [INFO] .venv created successfully.
)

REM ── Activate .venv ────────────────────────────────────────────────────────────
echo [INFO] Activating .venv...
call ".venv\Scripts\activate.bat"

REM ── Install / sync requirements.txt ──────────────────────────────────────────
if exist "requirements.txt" (
    set "CURRENT_REQ_HASH="
    set "SAVED_REQ_HASH="
    for /f "usebackq tokens=1" %%a in (`certutil -hashfile "requirements.txt" SHA256 ^| findstr /R "^[0-9A-F][0-9A-F]"`) do (
        set "CURRENT_REQ_HASH=%%a"
    )
    if exist "%REQ_STAMP%" (
        set /p SAVED_REQ_HASH=<"%REQ_STAMP%"
    )
    if /I not "!CURRENT_REQ_HASH!"=="!SAVED_REQ_HASH!" (
        echo [INFO] Installing requirements from requirements.txt...
        "%VENV_PYTHON%" -m pip install -r requirements.txt --quiet
        if errorlevel 1 (
            echo [ERROR] Failed to install requirements.
            pause & exit /b 1
        )
        >"%REQ_STAMP%" echo !CURRENT_REQ_HASH!
    ) else (
        echo [INFO] requirements.txt unchanged — skipping install.
    )
) else (
    echo [WARN] requirements.txt not found — skipping install.
)

REM ── Check django_extensions (needed for HTTPS) ─────────────────────────────
"%VENV_PYTHON%" -c "import django_extensions" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing django-extensions + SSL libraries...
    "%VENV_PYTHON%" -m pip install django-extensions Werkzeug pyOpenSSL --quiet
)

REM ── Kill any existing instance on port %PORT% ────────────────────────────────
echo [INFO] Checking for existing process on port %PORT%...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo [INFO] Killing PID %%a on port %PORT%...
    taskkill /PID %%a /F >nul 2>&1
)

REM ── Install keyboard (required for hotkey daemon) ────────────────────────────
echo [INFO] Ensuring 'keyboard' package is installed...
"%VENV_PYTHON%" -m pip install keyboard --quiet

REM ── Start hotkey daemon in a separate window ─────────────────────────────────
if exist "hotkey_daemon.py" (
    echo [INFO] Starting hotkey daemon ^(F1=Play, F2=Pause, F3=Resume, Esc=Stop^)...
    start "WebConX Hotkey Daemon" "%VENV_PYTHON%" hotkey_daemon.py --host %DAEMON_HOST% --port %PORT%
)

REM  Give each port its own session/CSRF cookie name so multiple instances
REM  running on the same host don't overwrite each other's browser cookies.
set "SESSION_COOKIE_NAME=sessionid_%PORT%"
set "CSRF_COOKIE_NAME=csrftoken_%PORT%"

REM ── Resolve first non-loopback IPv4 for network URL display ───────────────────
if not defined NETWORK_IP (
    for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue ^| Where-Object { $_.IPAddress -and $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254*' } ^| Select-Object -First 1 -ExpandProperty IPAddress"`) do (
        set "NETWORK_IP=%%i"
    )
    if not defined NETWORK_IP (
        for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /R /C:"IPv4 Address" /C:"IPv4-adres"') do (
            if not defined NETWORK_IP set "NETWORK_IP=%%i"
        )
    )
    if defined NETWORK_IP (
        for /f "tokens=* delims= " %%i in ("!NETWORK_IP!") do set "NETWORK_IP=%%i"
    )
)
if not defined NETWORK_IP set "NETWORK_IP=%DISPLAY_HOST%"

REM ── Django preparation steps ──────────────────────────────────────────────────
echo [INFO] Running migrations...
"%VENV_PYTHON%" manage.py migrate
if errorlevel 1 (
    echo [ERROR] Migration failed.
    pause & exit /b 1
)

echo [INFO] Running initial setup...
"%VENV_PYTHON%" -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','webapp.settings'); import django; django.setup(); from db_testcases.management.commands.setup_initial import Command; Command().handle()"
if errorlevel 1 (
    echo [ERROR] Initial setup failed.
    pause & exit /b 1
)

echo.
echo  ==================================================
echo   DB TESTLAB
echo   Local:   http://%DISPLAY_HOST%:%PORT%/
echo   Network: http://%NETWORK_IP%:%PORT%/
echo   Admin:   admin / admin
echo   Tester:  tester / tester123
echo   Viewer:  viewer / viewer123
echo  ==================================================
echo.

"%VENV_PYTHON%" manage.py runserver %HOST%:%PORT%

echo.
echo [INFO] Server stopped.
pause
endlocal
