@echo off
setlocal

echo ============================================
echo   Build standalone CoC_Bot.exe (PyInstaller)
echo ============================================
echo.
echo This must be run on Windows (an exe built elsewhere won't run
echo on Windows). It packages coc_bot.py + your Python environment
echo into a single CoC_Bot.exe in the "dist" folder.
echo.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH. Install it from python.org first.
    pause
    exit /b 1
)

echo Installing build dependencies...
python -m pip install --upgrade pip >nul 2>nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo Building CoC_Bot.exe ...
python -m PyInstaller --onefile --noconsole --name CoC_Bot coc_bot.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See errors above.
    echo.
    echo If the error mentions "Unable to find" a file, and this project
    echo folder is inside OneDrive, that file is likely a cloud-only
    echo placeholder that hasn't actually downloaded to disk yet.
    echo Right-click the project folder in File Explorer -^> "Always keep
    echo on this device", or better: move this whole folder somewhere
    echo outside OneDrive (e.g. C:\CoCBot) and run this script from there.
    pause
    exit /b 1
)

echo.
echo Copying All_Tags.json next to the exe...
copy /Y "All_Tags.json" "dist\All_Tags.json" >nul

echo.
echo Done! Your bot is in the "dist" folder:
echo   dist\CoC_Bot.exe
echo   dist\All_Tags.json
echo.
echo Keep BOTH files together - the exe reads All_Tags.json from its own
echo folder at runtime, so you can move the whole "dist" folder anywhere,
echo and you can re-tag/edit All_Tags.json later without rebuilding.
echo.
pause
endlocal
