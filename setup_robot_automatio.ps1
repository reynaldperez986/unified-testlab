<#
   Robot Framework + Selenium Installer (Python 3.14 version)
   Author: Copilot
#>

Write-Host "=== Robot Framework Automation Installer (Python 3.14) ===" -ForegroundColor Cyan

# -------------------------------
# 1. Install Python 3.14.0 (if missing)
# -------------------------------
Write-Host "`nChecking Python version..." -ForegroundColor Yellow

$python = (Get-Command python -ErrorAction SilentlyContinue)

$requiredMajor = 3
$requiredMinor = 14

$installPython = $false

if ($python) {
    $versionText = python --version
    Write-Host "Python detected: $versionText" -ForegroundColor Green
    $ver = python - << 'EOF'
import sys
print(sys.version_info.major, sys.version_info.minor)
EOF

    $parts = $ver.Trim().Split(" ")

    if ($parts[0] -ne $requiredMajor -or $parts[1] -ne $requiredMinor) {
        Write-Host "Detected Python is NOT 3.14. Installing Python 3.14..." -ForegroundColor Yellow
        $installPython = $true
    }
} else {
    Write-Host "Python not found. Installing Python 3.14..." -ForegroundColor Cyan
    $installPython = $true
}

if ($installPython -eq $true) {
    $pythonInstaller = "$env:TEMP\python314.exe"
    Write-Host "Downloading Python 3.14 installer..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe" -OutFile $pythonInstaller

    Write-Host "Installing Python 3.14..." -ForegroundColor Cyan
    Start-Process $pythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait
}

# Reload PATH
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine")

# -------------------------------
# 2. Install Python packages
# -------------------------------
Write-Host "`nInstalling Robot Framework + Selenium dependencies..." -ForegroundColor Yellow

pip install -U pip
pip install robotframework
pip install robotframework-seleniumlibrary
pip install selenium==4.9.1
pip install webdriver-manager
pip install requests

Write-Host "`n✅ Python packages installed." -ForegroundColor Green

# -------------------------------
# 3. Detect Chrome version
# -------------------------------
Write-Host "`nDetecting Google Chrome version..." -ForegroundColor Yellow

$chromePaths = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe"
)

$chromeExe = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-Not $chromeExe) {
    Write-Host "❌ Chrome not found. Install Chrome first." -ForegroundColor Red
    exit
}

$chromeVersion = (Get-Item $chromeExe).VersionInfo.ProductVersion
$majorVersion = $chromeVersion.Split(".")[0]

Write-Host "Chrome version detected: $chromeVersion" -ForegroundColor Green

# -------------------------------
# 4. Download ChromeDriver
# -------------------------------
Write-Host "`nDownloading ChromeDriver for version $majorVersion..." -ForegroundColor Yellow

$driverUrl = "https://storage.googleapis.com/chrome-for-testing-public/$majorVersion.0.0/win64/chromedriver-win64.zip"
$driverZip = "$env:TEMP\chromedriver.zip"

Invoke-WebRequest -Uri $driverUrl -OutFile $driverZip -ErrorAction Stop

Write-Host "✅ ChromeDriver downloaded." -ForegroundColor Green

# -------------------------------
# 5. Extract to C:\web__automation\drivers
# -------------------------------
$driverFolder = "C:\web__automation\drivers"

if (-Not (Test-Path $driverFolder)) {
    New-Item -Path $driverFolder -ItemType Directory | Out-Null
}

Expand-Archive -Path $driverZip -DestinationPath $driverFolder -Force

# Move chromedriver.exe to top of folder
$exePath = (Get-ChildItem "$driverFolder" -Recurse -Filter "chromedriver.exe" | Select-Object -First 1).FullName
Copy-Item $exePath "$driverFolder\chromedriver.exe" -Force

Write-Host "✅ ChromeDriver extracted to $driverFolder" -ForegroundColor Green

# -------------------------------
# 6. Run the Robot test
# -------------------------------
Write-Host "`nRunning Robot Framework test..." -ForegroundColor Cyan

cd C:\web__automation
python -m robot _test.robot

Write-Host "`n✅ Installation complete. Test executed successfully." -ForegroundColor Green