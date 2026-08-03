@echo off
title Private Agent Launcher
rem ============================================================================
rem  Private Agent - Desktop Launcher (Windows)
rem
rem  Usage:
rem    start-desktop.bat             normal launch (hardware acceleration)
rem    start-desktop.bat --no-gpu    disable GPU (remote desktop / VM / drivers)
rem
rem  Flow:
rem    1. Locate Node.js (PATH first, fallback to managed node)
rem    2. Verify frontend deps (electron/vite/typescript)
rem    3. Check PostgreSQL (5432) - auto-start script in Windows Startup folder
rem    4. Start Electron app via frontend/scripts/start-dev.mjs
rem       (Electron main auto-spawns the Python backend Sidecar + Vite;
rem        closing the app window shuts everything down)
rem
rem  NOTE: keep this file pure ASCII - UTF-8 chars break cmd parsing (GBK).
rem ============================================================================
setlocal
cd /d "%~dp0"

rem Step trace: every step appends to logs\launch-trace.log
if not exist "%~dp0logs" mkdir "%~dp0logs"
set "TRACE=%~dp0logs\launch-trace.log"
echo [%date% %time%] start-desktop.bat start (arg=%~1) >> "%TRACE%"

if /i "%~1"=="--no-gpu" set "PA_DISABLE_GPU=1"

echo.
echo  ================================================
echo   Private Agent - starting...
echo  ================================================
echo.

rem --- 1) Node.js ---
echo [%time%] step1: check node >> "%TRACE%"
set "NODE_CMD=node"
where node >nul 2>&1
if errorlevel 1 (
  set "NODE_CMD=C:\Users\zongxin\.workbuddy\binaries\node\versions\22.22.2\node.exe"
  if not exist "%NODE_CMD%" (
    echo [PA] ERROR: Node.js not found. Install Node 22+ or fix NODE_CMD path.
    pause
    exit /b 1
  )
)
echo [PA] Node: %NODE_CMD%
echo [%time%] node=%NODE_CMD% >> "%TRACE%"

rem --- 2) frontend deps ---
echo [%time%] step2: check frontend deps >> "%TRACE%"
if not exist "frontend\node_modules\electron\dist\electron.exe" (
  echo [PA] ERROR: frontend dependencies missing.
  echo       Run: cd frontend ^&^& npm install
  pause
  exit /b 1
)
echo [PA] frontend deps OK
echo [%time%] frontend deps OK >> "%TRACE%"

rem --- 3) PostgreSQL ---
echo [%time%] step3: check postgresql >> "%TRACE%"
netstat -ano | findstr /c:":5432 " >nul 2>&1
if errorlevel 1 (
  echo [PA] WARNING: PostgreSQL (port 5432) is not running.
  echo       Starting it now (postgres.exe) ...
  echo [%time%] postgres not running, starting... >> "%TRACE%"
  start "PrivateAgent-PG" "D:\PostgreSQL\16\bin\postgres.exe" -D "D:\PostgreSQL\16\data" -p 5432
  echo [%time%] postgres start issued >> "%TRACE%"
)

rem --- 4) leftover-port cleanup moved into start-dev.mjs ---

rem --- 5) Launch Electron (auto-spawns backend Sidecar + Vite) ---
echo [%time%] step4: run start-dev.mjs >> "%TRACE%"
echo.
echo [PA] Starting Private Agent desktop...
echo [PA] Close the app window to stop everything.
echo [PA] Logs: logs\desktop-launch.log
echo.
cd /d "%~dp0frontend"
"%NODE_CMD%" scripts/start-dev.mjs >> "%~dp0logs\desktop-launch.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo [%time%] start-dev.mjs exit code=%EXIT_CODE% >> "%TRACE%"

echo.
echo  ================================================
echo   Private Agent exited (code=%EXIT_CODE%)
echo   Logs: logs\desktop-launch.log
echo  ================================================
echo.
pause
exit /b %EXIT_CODE%
