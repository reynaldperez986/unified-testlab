@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

title Unified TestLab - Setup
color 0B

if not exist "%VENV_PYTHON%" (
    echo [INFO] Creating virtual environment...
    py -3 -m venv "%VENV_DIR%" 2>nul || python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

echo [INFO] Upgrading pip...
"%VENV_PYTHON%" -m pip install --upgrade pip

echo [INFO] Installing dependencies...
"%VENV_PIP%" install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo [INFO] Creating PostgreSQL database if not exists...
"%VENV_PYTHON%" -c "import psycopg2; conn = psycopg2.connect(host='localhost', port=5432, dbname='postgres', user='postgres', password='password'); conn.autocommit = True; cur = conn.cursor(); cur.execute(\"SELECT 1 FROM pg_database WHERE datname = 'automation_db'\"); r = cur.fetchone(); cur.execute('CREATE DATABASE \"automation_db\"') if not r else None; print('[INFO] Database created.' if not r else '[INFO] Database already exists.'); conn.close()"
if errorlevel 1 (
    echo [WARNING] Could not auto-create database. Make sure PostgreSQL is running and credentials are correct.
    echo [WARNING] You can create it manually: CREATE DATABASE automation_db;
)

echo [INFO] Running migrations...
"%VENV_PYTHON%" manage.py makemigrations --noinput
"%VENV_PYTHON%" manage.py migrate --noinput
if errorlevel 1 (
    echo [ERROR] Migration failed.
    pause
    exit /b 1
)

echo [INFO] Running initial setup...
"%VENV_PYTHON%" manage.py setup_initial

echo.
echo [SUCCESS] Setup complete.
pause
exit /b 0
