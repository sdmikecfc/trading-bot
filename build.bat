@echo off
:: Doma Sniper -- PyInstaller Build Script
:: Run this once to produce DomaSniper.exe in the dist\ folder.

title Doma Sniper Build
cd /d "%~dp0"
chcp 65001 >nul

echo.
echo  Doma Sniper -- EXE Builder
echo  github.com/sdmikecfc/trading-bot
echo.

:: Step 1: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed or not in PATH.
    echo  Download from https://python.org/downloads
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

:: Step 2: Install dependencies
echo  [1/4] Installing dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo  ERROR: pip install failed. See output above.
    pause
    exit /b 1
)

pip install pyinstaller -q
if errorlevel 1 (
    echo  ERROR: Could not install PyInstaller.
    pause
    exit /b 1
)

:: Step 3: Build the exe
echo  [2/4] Building DomaSniper.exe (this takes about 60 seconds)...
pyinstaller --onefile --console --name DomaSniper --add-data "frog.txt;." --collect-data eth_account --clean --noconfirm snipe.py
if errorlevel 1 (
    echo.
    echo  ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)

:: Step 4: Package the release zip
echo  [3/4] Packaging release zip...
if not exist "release" mkdir release
copy /y "dist\DomaSniper.exe" "release\DomaSniper.exe" >nul
copy /y "release_README.txt" "release\README.txt" >nul
powershell -NoProfile -Command "Compress-Archive -Path 'release\DomaSniper.exe','release\README.txt' -DestinationPath 'release\DomaSniper-win64.zip' -Force"

echo  [4/4] Done!
echo.
echo  Output files:
echo    dist\DomaSniper.exe           -- the executable
echo    release\DomaSniper-win64.zip  -- share this zip
echo.
echo  Windows SmartScreen will warn on first run -- that is normal for
echo  unsigned executables. Click "More info" then "Run anyway".
echo.
pause
