@echo off
rem ============================================================================
rem  Private Agent - silent launcher (no console window, no pause).
rem  Called by start-desktop.vbs via wscript with window hidden.
rem  All output goes to D:\Private agent\logs\desktop-launch.log.
rem ============================================================================
cd /d "D:\Private agent\frontend"
"C:\Users\zongxin\.workbuddy\binaries\node\versions\22.22.2\node.exe" scripts/start-dev.mjs >> "D:\Private agent\logs\desktop-launch.log" 2>&1
