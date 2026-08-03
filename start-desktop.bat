@echo off
title Private Agent Launcher
rem ============================================================================
rem  Private Agent - Desktop Launcher (Windows)
rem
rem  Usage:
rem    start-desktop.bat             normal launch (hardware acceleration)
rem    start-desktop.bat --no-gpu    disable GPU (remote desktop / VMs / bad drivers)
rem
rem  Flow:
rem    1. Locate Node.js (PATH first, fallback to managed node)
rem    2. Verify frontend deps (electron/vite/typescript)
rem    3. Check PostgreSQL (5432) - auto-start script installed in Startup folder
rem    4. Check backend port 8765 (warn if already in use)
rem    5. Start Electron desktop app via frontend/scripts/start-dev.mjs
rem       (Electron main process auto-spawns the Python backend Sidecar
rem        + Vite dev server; closing the app window shuts everything down)
rem ============================================================================
setlocal
cd /d "%~dp0"

rem 步骤追踪: 每步追加到 logs\launch-trace.log, 秒退也能定位执行到哪
if not exist "%~dp0logs" mkdir "%~dp0logs"
set "TRACE=%~dp0logs\launch-trace.log"
echo [%date% %time%] start-desktop.bat 启动 (arg=%~1) >> "%TRACE%"

if /i "%~1"=="--no-gpu" set "PA_DISABLE_GPU=1"

echo.
echo  ================================================
echo   Private Agent - starting...
echo  ================================================
echo.

rem --- 1) Node.js ---
echo [%time%] 步骤1: 检查 Node >> "%TRACE%"
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
echo [%time%] Node=%NODE_CMD% >> "%TRACE%"

rem --- 2) frontend deps ---
echo [%time%] 步骤2: 检查 frontend 依赖 >> "%TRACE%"
if not exist "frontend\node_modules\electron\dist\electron.exe" (
  echo [PA] ERROR: frontend dependencies missing.
  echo       Run: cd frontend ^&^& npm install
  pause
  exit /b 1
)
echo [PA] frontend deps OK
echo [%time%] frontend deps OK >> "%TRACE%"

rem --- 3) PostgreSQL ---
echo [%time%] 步骤3: 检查 PostgreSQL >> "%TRACE%"
netstat -ano | findstr /c:":5432 " >nul 2>&1
if errorlevel 1 (
  echo [PA] WARNING: PostgreSQL (port 5432) is not running.
  echo       Auto-start script is in your Windows Startup folder,
  echo       or start it manually:
  echo         D:\PostgreSQL\16\bin\postgres.exe -D D:\PostgreSQL\16\data -p 5432
  echo       Continuing in 6 seconds...
  timeout /t 6 /nobreak >nul
)

rem --- 4) 残留端口进程清理已移入 start-dev.mjs(启动前自动执行) ---
rem        (node 内用 netstat/taskkill 直接清理, 比 bat 嵌套 for 更可靠)

rem --- 5) Launch Electron (auto-spawns backend Sidecar + Vite) ---
echo [%time%] 步骤4: 启动 start-dev.mjs >> "%TRACE%"
echo.
echo [PA] Starting Private Agent desktop...
echo [PA] Close the app window to stop everything.
echo [PA] Logs: logs\desktop-launch.log
echo.
cd /d "%~dp0frontend"
"%NODE_CMD%" scripts/start-dev.mjs >> "%~dp0logs\desktop-launch.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo [%time%] start-dev.mjs 退出 code=%EXIT_CODE% >> "%TRACE%"

echo.
echo  ================================================
echo   Private Agent exited (code=%EXIT_CODE%)
echo   Logs: logs\desktop-launch.log
echo  ================================================
echo.
pause
exit /b %EXIT_CODE%
