@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"
set "APP_NAME=unified-testlab"

title Unified TestLab - Build EXE
color 0E

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

echo [INFO] Installing dependencies...
"%VENV_PIP%" install -r requirements.txt
"%VENV_PIP%" install pyinstaller

echo [INFO] Applying migrations and setup...
"%VENV_PYTHON%" manage.py migrate --noinput
"%VENV_PYTHON%" manage.py setup_initial

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo [INFO] Building executable...
"%VENV_PYTHON%" -m PyInstaller --noconfirm --clean --onedir --name "%APP_NAME%" --collect-all django --collect-all recorder --collect-all api_testcases --collect-all db_testcases --collect-all psycopg2 --collect-all psycopg --collect-all openpyxl --collect-all oracledb --collect-all cryptography --add-data "templates_api;templates_api" --add-data "templates_db;templates_db" --add-data "static;static" --add-data "static_api;static_api" --add-data "static_db;static_db" manage.py
if errorlevel 1 (
    echo [ERROR] EXE build failed.
    pause
    exit /b 1
)

echo [SUCCESS] Build complete: dist\%APP_NAME%
pause
exit /b 0
