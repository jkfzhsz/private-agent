@echo off
setlocal EnableExtensions
REM ============================================================
REM Private Agent - Windows package script (goto structure)
REM Safer than if/else blocks: no parens, no ^-continuation,
REM which are the usual cause of double-click flash-close.
REM Fixes included:
REM   1. Remove leftover build lock (win-unpacked.tmp.lock)
REM   2. Local rcedit for icon injection (no GitHub download)
REM   3. V1.5 项-6 打包收敛(方案 A): backend 由 electron-builder
REM      extraResources 内置进 resourcesPath/backend(自包含, 排除
REM      .venv/.env/outputs/logs/tests); 不再同步 D:\PA1.0\backend
REM      (双目录同步是历史 "HTTP 405" 旧后端事故根源, 已删除)
REM Usage: double-click or run "build-electron.bat" in CMD
REM ============================================================

set "FRONTEND_DIR=%~dp0frontend"
set "RCEDIT_DIR=%FRONTEND_DIR%\build\rcedit"
set "OUTPUT_DIR=%FRONTEND_DIR%\release2"

echo.
echo [1/5] Cleaning leftover build lock files...
if exist "%OUTPUT_DIR%\win-unpacked.tmp.lock" del /q "%OUTPUT_DIR%\win-unpacked.tmp.lock"
if exist "%OUTPUT_DIR%\win-unpacked.tmp"    rmdir /s /q "%OUTPUT_DIR%\win-unpacked.tmp" 2>nul

echo [2/5] Checking local rcedit (icon injection tool)...
if not exist "%RCEDIT_DIR%\rcedit-x64.exe" goto :missing_rcedit
set "ELECTRON_BUILDER_RCEDIT_PATH=%RCEDIT_DIR%"
echo   - using local rcedit: %RCEDIT_DIR%

echo [3/5] Building frontend (tsc main + vite)...
cd /d "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 goto :build_fail

echo [4/5] Running electron-builder (win + nsis, backend via extraResources)...
if exist "%OUTPUT_DIR%\win-unpacked" rmdir /s /q "%OUTPUT_DIR%\win-unpacked"
call npx electron-builder --win
if errorlevel 1 goto :package_fail

echo [5/5] Done!
echo   Installer:   %OUTPUT_DIR%\Private Agent Setup *.exe
echo   Portable:    %OUTPUT_DIR%\win-unpacked\Private Agent.exe
echo   Verify: desktop shortcut / taskbar / Alt+Tab all show new PA icon
echo   Note: backend bundled in resources\backend (self-contained, no external dir)
echo   Config: first run needs %APPDATA%\Private Agent\backend.env or bundled backend\.env
goto :end

:missing_rcedit
echo   ERROR: missing "%RCEDIT_DIR%\rcedit-x64.exe"
echo   Run scripts/regen_icons.py first, or restore frontend\build\rcedit\
goto :fail

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
