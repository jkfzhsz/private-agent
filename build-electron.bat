@echo off
setlocal EnableExtensions
REM ============================================================
REM Private Agent - Windows package script (goto structure)
REM Safer than if/else blocks: no parens, no ^-continuation,
REM which are the usual cause of double-click flash-close.
REM Fixes included:
REM   1. Remove leftover build lock (win-unpacked.tmp.lock)
REM   2. Local rcedit for icon injection (no GitHub download)
REM   3. Sync backend to D:\PA1.0\backend BEFORE packaging
REM      (packaged app probes D:\PA1.0\backend FIRST; stale copy
REM       there caused the "HTTP 405" old-backend bug)
REM Usage: double-click or run "build-electron.bat" in CMD
REM ============================================================

set "FRONTEND_DIR=%~dp0frontend"
set "RCEDIT_DIR=%FRONTEND_DIR%\build\rcedit"
set "OUTPUT_DIR=%FRONTEND_DIR%\release2"

echo.
echo [1/6] Cleaning leftover build lock files...
if exist "%OUTPUT_DIR%\win-unpacked.tmp.lock" del /q "%OUTPUT_DIR%\win-unpacked.tmp.lock"
if exist "%OUTPUT_DIR%\win-unpacked.tmp"    rmdir /s /q "%OUTPUT_DIR%\win-unpacked.tmp" 2>nul

echo [2/6] Checking local rcedit (icon injection tool)...
if not exist "%RCEDIT_DIR%\rcedit-x64.exe" goto :missing_rcedit
set "ELECTRON_BUILDER_RCEDIT_PATH=%RCEDIT_DIR%"
echo   - using local rcedit: %RCEDIT_DIR%

echo [3/6] Syncing backend to D:\PA1.0\backend (packaged app probes this first)...
if not exist "D:\PA1.0\backend" mkdir "D:\PA1.0\backend"
robocopy "D:\Private agent\backend" "D:\PA1.0\backend" /E /XD .venv __pycache__ outputs logs /XF .env *.pyc *.pyo /NFL /NDL /NJH /NJS /NP /R:1 /W:1
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 goto :robocopy_fail
echo   - backend synced (.venv/.env/outputs/logs excluded)

echo [4/6] Building frontend (tsc main + vite)...
cd /d "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 goto :build_fail

echo [5/6] Running electron-builder (win + nsis)...
if exist "%OUTPUT_DIR%\win-unpacked" rmdir /s /q "%OUTPUT_DIR%\win-unpacked"
call npx electron-builder --win
if errorlevel 1 goto :package_fail

echo [6/6] Done!
echo   Installer:   %OUTPUT_DIR%\Private Agent Setup *.exe
echo   Portable:    %OUTPUT_DIR%\win-unpacked\Private Agent.exe
echo   Verify: desktop shortcut / taskbar / Alt+Tab all show new PA icon
echo   Note: packaged app uses D:\PA1.0\backend (just synced) as backend
goto :end

:missing_rcedit
echo   ERROR: missing "%RCEDIT_DIR%\rcedit-x64.exe"
echo   Run scripts/regen_icons.py first, or restore frontend\build\rcedit\
goto :fail

:robocopy_fail
echo   WARNING: robocopy exit code %RC% (8+ = error), continuing anyway
echo   If D:\PA1.0\backend is incomplete, run the copy manually.
goto :end

:build_fail
echo   ERROR: frontend build failed (tsc or vite)
goto :fail

:package_fail
echo   ERROR: packaging failed, see messages above
goto :fail

:fail
echo.
echo ============================================================
echo  Build FAILED - see messages above.
echo ============================================================
pause
exit /b 1

:end
echo.
echo ============================================================
echo  Build finished. Check installer in release2 folder.
echo ============================================================
pause
endlocal
