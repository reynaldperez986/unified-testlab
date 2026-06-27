@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "HOST=0.0.0.0"
set "PORT=8000"
set "APP_NAME=unified-testlab"
set "EXE_PATH=dist\%APP_NAME%\%APP_NAME%.exe"

title Unified TestLab - %HOST%:%PORT% (EXE)
color 0B
cd /d "%~dp0"

if not exist "%EXE_PATH%" (
    echo [ERROR] Executable not found at %EXE_PATH%
    echo [INFO] Please run build_exe.bat first to build the executable.
    pause
    exit /b 1
)

set "LAN_IP="
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /C:"IPv4"') do (
    for /f "tokens=*" %%j in ("%%i") do (
        if not defined LAN_IP set "LAN_IP=%%j"
    )
)
if not defined LAN_IP set "LAN_IP=127.0.0.1"

if not defined ALLOWED_HOSTS set "ALLOWED_HOSTS=127.0.0.1,localhost,%LAN_IP%"

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

netsh advfirewall firewall show rule name="Unified TestLab %PORT%" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="Unified TestLab %PORT%" dir=in action=allow protocol=TCP localport=%PORT% profile=private >nul 2>&1
)

echo.
echo  ==================================================
echo  Unified TestLab - Running from EXE
echo  ==================================================
echo  Host: %HOST%
echo  Port: %PORT%
echo  LAN IP: %LAN_IP%
echo  Allowed Hosts: %ALLOWED_HOSTS%
echo  Web Automation: http://127.0.0.1:%PORT%/
echo  API Lab:        http://127.0.0.1:%PORT%/api-lab/
echo  DB Lab:         http://127.0.0.1:%PORT%/db-lab/
echo  ==================================================
echo.

set "DJANGO_SETTINGS_MODULE=webapp.settings"

"%EXE_PATH%" runserver %HOST%:%PORT% --noreload

pause
