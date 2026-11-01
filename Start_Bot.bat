@echo off
setlocal

echo ============================================
echo   Clash of Clans Attack Bot - Starter
echo ============================================
echo.

cd /d "%~dp0"

REM --- Check Python is installed and on PATH ---
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on your PATH.
    echo Install it from https://www.python.org/downloads/
    echo and make sure to check "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

REM --- Check All_Tags.json is present ---
if not exist "All_Tags.json" (
    echo [ERROR] All_Tags.json not found in this folder.
    echo Make sure it sits next to coc_bot.py and this .bat file.
    echo.
    pause
    exit /b 1
)

REM --- Install/upgrade dependencies quietly ---
echo Checking dependencies...
python -m pip install --upgrade pip >nul 2>nul
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. See errors above.
    pause
    exit /b 1
)

echo.
echo Launching bot control panel...
echo.
python coc_bot.py

if errorlevel 1 (
    echo.
    echo [ERROR] The bot exited with an error. See above for details.
    pause
)

endlocal
