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

echo [4/7] Building win-unpacked only (backend via extraResources)...
if exist "%OUTPUT_DIR%\win-unpacked" rmdir /s /q "%OUTPUT_DIR%\win-unpacked"
call npx electron-builder --win --dir
if errorlevel 1 goto :package_fail

REM verify backend was bundled
if not exist "%PKG_BACKEND%\private_agent" (
  echo   WARNING: resources\backend\private_agent missing; extraResources may have failed
)

echo [5/7] Bundling backend venv (this machine only, self-contained deps)...
if exist "%BACKEND_VENV%\Scripts\python.exe" (
  REM 2026-08-09: venv grew to ~1.1GB (torch/FlagEmbedding). xcopy
  REM chokes with "Insufficient memory" on low-RAM machines -> use
  REM robocopy multi-thread (/MT:16).
  REM 2026-08-09 17:00: slow install (30min) = venv had 50002 files
  REM (mostly non-runtime junk) in the installer -> NSIS unpacks tens of
  REM thousands of files + Defender scans each one.
  REM Exclude by DIR NAME (absolute-path /XD proven NOT to work):
  REM   __pycache__/include/share dirs + *.pyc/*.lib files
  REM (include=C++ headers, lib=static libs, share=build data; NOT needed
  REM  at runtime. tests dir must NOT be excluded - transformers lazy
  REM  AutoModel import breaks. Verified 0.86GB / 22282 files, imports OK.)
  robocopy "%BACKEND_VENV%" "%PKG_BACKEND%\.venv" /E /MT:16 /NFL /NDL /NJH /NJS /NC /NS /XD __pycache__ include share /XF *.pyc *.lib >nul
  if errorlevel 8 goto :venv_fail
  echo   - venv bundled: %PKG_BACKEND%\.venv
) else (
  echo   - SKIP: backend\.venv not found (packaged app will probe system python)
)

echo [6/7] Packing NSIS installer (from win-unpacked, NOW including .venv)...
REM 2026-08-09 fix: previously NSIS was built in [4/6] BEFORE the venv
REM copy, so the installer never contained .venv (installed app fell back
REM to system python without deps). Build unpacked first, then prepackaged.
call npx electron-builder --win nsis --prepackaged "%OUTPUT_DIR%\win-unpacked"
if errorlevel 1 goto :package_fail

echo [7/7] Done!
echo   Installer:   %OUTPUT_DIR%\Private Agent Setup 0.5.0.exe
echo   Portable:    %OUTPUT_DIR%\win-unpacked\Private Agent.exe
echo   Backend:     %PKG_BACKEND% (self-contained + .venv)
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
echo   ERROR: copying backend\.venv failed (low memory or disk space)
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
