@echo off
:: Doma Sniper — PyInstaller Build Script
:: Run this once to produce DomaSniper.exe in the dist\ folder.
:: The resulting exe bundles Python + all dependencies — no install required.

title Doma Sniper Build
cd /d "%~dp0"
chcp 65001 >nul

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║        Doma Sniper — EXE Builder                    ║
echo  ║        github.com/sdmikecfc/trading-bot              ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: ── Step 1: Check Python ──────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed or not in PATH.
    echo  Download from https://python.org/downloads
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

:: ── Step 2: Install / upgrade build dependencies ──────────────────────────────
echo  [1/4] Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo  ERROR: pip install failed. See output above.
    pause
    exit /b 1
)

pip install pyinstaller --quiet
if errorlevel 1 (
    echo  ERROR: Could not install PyInstaller.
    pause
    exit /b 1
)

:: ── Step 3: Build the exe ─────────────────────────────────────────────────────
echo  [2/4] Building DomaSniper.exe (this takes ~60 seconds)...
pyinstaller ^
    --onefile ^
    --console ^
    --name DomaSniper ^
    --add-data "frog.txt;." ^
    --clean ^
    --noconfirm ^
    snipe.py

if errorlevel 1 (
    echo.
    echo  ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)

:: ── Step 4: Package the release zip ──────────────────────────────────────────
echo  [3/4] Packaging release zip...

:: Make release folder
if not exist "release" mkdir release

:: Copy the exe and the quick-start readme
copy /y "dist\DomaSniper.exe" "release\DomaSniper.exe" >nul
copy /y "release_README.txt"  "release\README.txt"     >nul 2>&1

:: Zip it up using PowerShell (built into Windows 10+)
powershell -NoProfile -Command ^
    "Compress-Archive -Path 'release\DomaSniper.exe','release\README.txt' -DestinationPath 'release\DomaSniper-win64.zip' -Force"

echo  [4/4] Done!
echo.
echo  ┌──────────────────────────────────────────────────────┐
echo  │  Output files:                                       │
echo  │    dist\DomaSniper.exe        ← the executable      │
echo  │    release\DomaSniper-win64.zip ← share this zip    │
echo  └──────────────────────────────────────────────────────┘
echo.
echo  Windows SmartScreen will warn on first run — that's normal for
echo  unsigned executables. Click "More info" then "Run anyway".
echo.
pause
