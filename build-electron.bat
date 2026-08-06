@echo off
setlocal EnableExtensions
REM ============================================================
REM Private Agent - Windows package script (goto structure)
REM Safer than if/else blocks: no parens, no ^-continuation,
REM which are the usual cause of double-click flash-close.
REM
REM This file is pure ASCII on purpose: cmd.exe parses .bat with
REM the ANSI code page (GBK on zh-CN); UTF-8 Chinese comments
REM garble into garbage commands (e.g. "not recognized...").
REM Keep ALL non-ASCII text OUT of this file.
REM
REM V1.5 package convergence (plan A): backend is bundled into
REM   resourcesPath/backend via electron-builder extraResources
REM   (source only; .venv/.env/outputs/logs/tests/egg-info excluded).
REM 2026-08-06 changes:
REM   [5/6] copy backend/.venv into the package for this machine
REM         (self-contained backend deps, ~76MB; venv is NOT portable,
REM         remove this step for distribution builds);
REM   version bumped to 0.3.0 in frontend/package.json.
REM Usage: double-click or run "build-electron.bat" in CMD
REM ============================================================

set "FRONTEND_DIR=%~dp0frontend"
set "RCEDIT_DIR=%FRONTEND_DIR%\build\rcedit"
set "OUTPUT_DIR=%FRONTEND_DIR%\release2"
set "BACKEND_VENV=%~dp0backend\.venv"
set "PKG_BACKEND=%OUTPUT_DIR%\win-unpacked\resources\backend"

echo.
echo [1/6] Cleaning leftover build lock files...
if exist "%OUTPUT_DIR%\win-unpacked.tmp.lock" del /q "%OUTPUT_DIR%\win-unpacked.tmp.lock"
if exist "%OUTPUT_DIR%\win-unpacked.tmp"    rmdir /s /q "%OUTPUT_DIR%\win-unpacked.tmp" 2>nul

echo [2/6] Checking local rcedit (icon injection tool)...
if not exist "%RCEDIT_DIR%\rcedit-x64.exe" goto :missing_rcedit
set "ELECTRON_BUILDER_RCEDIT_PATH=%RCEDIT_DIR%"
echo   - using local rcedit: %RCEDIT_DIR%

echo [3/6] Building frontend (tsc main + vite)...
cd /d "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 goto :build_fail

echo [4/6] Running electron-builder (win + nsis, backend via extraResources)...
if exist "%OUTPUT_DIR%\win-unpacked" rmdir /s /q "%OUTPUT_DIR%\win-unpacked"
call npx electron-builder --win
if errorlevel 1 goto :package_fail

REM verify backend was bundled
if not exist "%PKG_BACKEND%\private_agent" (
  echo   WARNING: resources\backend\private_agent missing; extraResources may have failed
)

echo [5/6] Bundling backend venv (this machine only, self-contained deps)...
if exist "%BACKEND_VENV%\Scripts\python.exe" (
  xcopy /E /I /Q /Y "%BACKEND_VENV%" "%PKG_BACKEND%\.venv\" >nul
  if errorlevel 1 goto :venv_fail
  echo   - venv bundled: %PKG_BACKEND%\.venv
) else (
  echo   - SKIP: backend\.venv not found (packaged app will probe system python)
)

echo [6/6] Done!
echo   Installer:   %OUTPUT_DIR%\Private Agent Setup 0.3.0.exe
echo   Portable:    %OUTPUT_DIR%\win-unpacked\Private Agent.exe
echo   Backend:     %PKG_BACKEND% (self-contained)
echo   Config:      first run needs %%APPDATA%%\Private Agent\backend.env
echo                or bundled backend\.env (backend.env takes priority)
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

:venv_fail
echo   ERROR: copying backend\.venv failed (disk space left?)
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
