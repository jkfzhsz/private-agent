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
echo [1/7] Cleaning leftover build lock files...
if exist "%OUTPUT_DIR%\win-unpacked.tmp.lock" del /q "%OUTPUT_DIR%\win-unpacked.tmp.lock"
if exist "%OUTPUT_DIR%\win-unpacked.tmp"    rmdir /s /q "%OUTPUT_DIR%\win-unpacked.tmp" 2>nul

echo [2/7] Checking local rcedit (icon injection tool)...
if not exist "%RCEDIT_DIR%\rcedit-x64.exe" goto :missing_rcedit
set "ELECTRON_BUILDER_RCEDIT_PATH=%RCEDIT_DIR%"
echo   - using local rcedit: %RCEDIT_DIR%

echo [3/7] Building frontend (tsc main + vite)...
cd /d "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 goto :build_fail

echo [4/7] Building win-unpacked only (backend via extraResources)...
if exist "%OUTPUT_DIR%\win-unpacked" rmdir /s /q "%OUTPUT_DIR%\win-unpacked"
call npx electron-builder --win --dir --publish never
if errorlevel 1 goto :package_fail

REM verify backend was bundled
if not exist "%PKG_BACKEND%\private_agent" (
  echo   WARNING: resources\backend\private_agent missing; extraResources may have failed
)

echo [5/7] Bundling backend venv as zip (single-file install, faster NSIS)...
if exist "%BACKEND_VENV%\Scripts\python.exe" (
  REM 2026-08-12 v3: NSIS still hangs at 50% with 8976 files / 183MB venv.
  REM Root cause: NSIS decompresses each file individually + Defender scans
  REM every .pyd/.dll/.exe. Even after ML deps exclusion, ~9000 small files
  REM = 10+ min install freeze.
  REM Solution: pack venv into a single zip. NSIS installs 1 file (seconds),
  REM Electron main process extracts on first launch (~30-60s, one-time).
  REM
  REM Exclusion list (verified: backend code does NOT import any of these):
  REM   Heavy ML: torch/FlagEmbedding/transformers/scipy/pyarrow/sklearn/
  REM     sympy/modelscope/networkx/hf_xet/tokenizers/sentence_transformers
  REM   Data science (unused by backend): pandas/numpy/numpy.libs/PIL/lxml/
  REM     sentencepiece
  REM   Build/dev tools: pip/setuptools/pygments/huggingface_hub/peft/datasets
  REM   Non-runtime: __pycache__/include/share/test dirs + *.pyc/*.lib
  REM Saves ~125MB / ~4700 files -> venv.zip ~40MB / ~3500 files.
  echo   - robocopy to temp dir excluding non-runtime deps...
  robocopy "%BACKEND_VENV%" "%PKG_BACKEND%\.venv" /E /MT:16 /NFL /NDL /NJH /NJS /NC /NS /XD __pycache__ include share test torch torchgen functorch FlagEmbedding transformers sentence_transformers scipy scipy.libs sklearn sympy pyarrow pyarrow.libs modelscope modelscope_hub networkx hf_xet tokenizers pandas numpy numpy.libs PIL lxml sentencepiece pip setuptools pygments huggingface_hub peft datasets /XF *.pyc *.lib >nul
  if errorlevel 8 goto :venv_fail
  echo   - compressing venv to zip...
  pushd "%PKG_BACKEND%"
  "%BACKEND_VENV%\Scripts\python.exe" -c "import shutil; shutil.make_archive('venv', 'zip', '.venv')"
  popd
  if errorlevel 1 goto :venv_fail
  rmdir /s /q "%PKG_BACKEND%\.venv"
  echo   - venv.zip created
  echo   - Electron will auto-extract on first launch ~30-60s one-time
) else (
  echo   - SKIP: backend\.venv not found
)

echo [6/7] Packing NSIS installer (from win-unpacked, including venv.zip)...
REM 2026-08-09 fix: previously NSIS was built in [4/6] BEFORE the venv
REM copy, so the installer never contained .venv. Build unpacked first,
REM then prepackaged.
REM 2026-08-12: venv is now a single venv.zip (not 8976 loose files),
REM NSIS installs 1 file -> seconds instead of 10+ minutes.
call npx electron-builder --win nsis --prepackaged "%OUTPUT_DIR%\win-unpacked" --publish never
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
