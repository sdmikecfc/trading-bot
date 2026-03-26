@echo off
:: Doma Protocol Sniper — Windows Launcher
:: Double-click this file to open the sniper bot.

title Doma Sniper Bot
cd /d "%~dp0"

:: Enable UTF-8 so box-drawing characters and emoji display correctly
chcp 65001 >nul

:: Widen the window so the frog art displays correctly
mode con cols=120 lines=40

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python is not installed or not in PATH.
    echo  Download it from https://python.org/downloads
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

:: Run the sniper
python snipe.py

:: Keep window open if there was an error
if errorlevel 1 (
    echo.
    echo  The bot exited with an error. See message above.
    pause
)
