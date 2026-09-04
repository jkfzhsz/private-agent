@echo off
rem ============================================================================
rem  Private Agent - silent launcher (no console window, no pause).
rem  Self-hides via PowerShell restart (double-click the bat = hidden window).
rem  Fallback: start-desktop.vbs (wscript Run ... ,0) achieves the same hiding.
rem  All output goes to D:\Private agent\logs\desktop-launch.log.
rem ============================================================================

rem ---- self-hide: restart this bat with a hidden console window ----
if not defined PA_HIDDEN (
  set "PA_HIDDEN=1"
  powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c \"\"%~f0\"\"' -WindowStyle Hidden"
  exit /b
)

rem ---- resolve node.exe dynamically (2026-09-04) ----
rem Original code hardcoded versions\22.22.2\node.exe. After a WorkBuddy
rem upgrade the folder was renamed to 22.22.2-2, so the hardcoded path went
rem stale and double-click silently failed (log only showed "the system
rem cannot find the path specified."). Resolve in two tiers so the script
rem no longer depends on a concrete version folder name:
rem   1) node from PATH (WorkBuddy managed version preferred)
rem   2) scan versions\* for any existing node.exe (auto-adapts to
rem      variant suffixes like -2 / -3)
rem If both fail, write to the log and exit instead of failing silently.
set "NODE_EXE="
for /f "delims=" %%i in ('where node 2^>nul') do (
  if not defined NODE_EXE set "NODE_EXE=%%i"
)
if not defined NODE_EXE (
  for /d %%d in ("C:\Users\zongxin\.workbuddy\binaries\node\versions\*") do (
    if exist "%%d\node.exe" if not defined NODE_EXE set "NODE_EXE=%%d\node.exe"
  )
)
if not defined NODE_EXE (
  echo [%date% %time%] ERROR: node.exe not found, launch aborted.>> "D:\Private agent\logs\desktop-launch.log"
  exit /b 1
)

rem ---- actual launch (hidden console) ----
cd /d "D:\Private agent\frontend"
"%NODE_EXE%" scripts/start-dev.mjs >> "D:\Private agent\logs\desktop-launch.log" 2>&1
