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

rem ---- actual launch (hidden console) ----
cd /d "D:\Private agent\frontend"
"C:\Users\zongxin\.workbuddy\binaries\node\versions\22.22.2\node.exe" scripts/start-dev.mjs >> "D:\Private agent\logs\desktop-launch.log" 2>&1
