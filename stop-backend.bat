@echo off
title Private Agent - Stop Backend
rem ============================================================================
rem  Private Agent - Stop leftover backend/sidecar processes
rem
rem  Kills processes listening on 8765 (backend) and 5173 (vite dev).
rem  Use this if a previous instance crashed and left the port occupied,
rem  causing "Backend port 8765 is already in use" when launching.
rem ============================================================================
setlocal

echo [PA] Scanning for processes on ports 8765 / 5173 ...

for %%P in (8765 5173) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr /c:":%%P " ^| findstr "LISTENING"') do (
    echo [PA] Killing PID %%a (port %%P) ...
    taskkill /PID %%a /F >nul 2>&1
  )
)

echo.
echo [PA] Done. Ports 8765 / 5173 should be free now.
echo.
pause
exit /b 0
