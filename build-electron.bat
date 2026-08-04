@echo off
setlocal
REM ============================================================
REM Private Agent - Windows package script (with new icon)
REM Fixes included:
REM   1. Remove leftover build lock (win-unpacked.tmp.lock)
REM      which makes electron-builder hang in packaging phase
REM   2. Use local rcedit (icon injection tool) to avoid
REM      downloading from GitHub which can hang on slow network
REM   3. package.json build config already includes build/icon.ico
REM Usage: double-click or run "build-electron.bat" in CMD
REM ============================================================

set "FRONTEND_DIR=%~dp0frontend"
set "RCEDIT_DIR=%FRONTEND_DIR%\build\rcedit"
set "OUTPUT_DIR=%FRONTEND_DIR%\release2"

echo [1/5] Cleaning leftover build lock files...
if exist "%OUTPUT_DIR%\win-unpacked.tmp.lock" (
    del /q "%OUTPUT_DIR%\win-unpacked.tmp.lock"
    echo   - removed leftover lock
)
if exist "%OUTPUT_DIR%\win-unpacked.tmp" (
    rmdir /s /q "%OUTPUT_DIR%\win-unpacked.tmp" 2>nul
    echo   - removed unfinished temp output dir
)

echo [2/5] Checking local rcedit (icon injection tool)...
if not exist "%RCEDIT_DIR%\rcedit-x64.exe" (
    echo   ERROR: missing "%RCEDIT_DIR%\rcedit-x64.exe"
    echo   Run scripts/regen_icons.py first, or restore frontend/build/rcedit/
    pause
    exit /b 1
)
set "ELECTRON_BUILDER_RCEDIT_PATH=%RCEDIT_DIR%"
echo   - using local rcedit: %RCEDIT_DIR%

echo [3/5] Building frontend (tsc main + vite)...
cd /d "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 (
    echo   ERROR: frontend build failed
    pause
    exit /b 1
)

echo [4/5] Running electron-builder (win + nsis)...
if exist "%OUTPUT_DIR%\win-unpacked" (
    rmdir /s /q "%OUTPUT_DIR%\win-unpacked" 2>nul
    echo   - removed old win-unpacked (avoid stale icon cache)
)
call npx electron-builder --win
if errorlevel 1 (
    echo   ERROR: packaging failed, see messages above
    pause
    exit /b 1
)

echo [5/5] Done!
echo   Installer:   %OUTPUT_DIR%\Private Agent Setup *.exe
echo   Portable:    %OUTPUT_DIR%\win-unpacked\Private Agent.exe
echo   Verify: desktop shortcut / taskbar / Alt+Tab all show new PA icon
pause
endlocal
