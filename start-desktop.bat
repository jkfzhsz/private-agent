@echo off
title Private Agent Launcher
rem ============================================================================
rem  Private Agent - Desktop Launcher (minimal)
rem  All checks (node/deps/postgres/ports) live inside start-dev.mjs where
rem  they are reliable; this bat only invokes node. Pure ASCII.
rem ============================================================================
cd /d "%~dp0frontend"
echo.
echo  ================================================
echo   Private Agent - starting (see logs\desktop-launch.log)...
echo  ================================================
echo.
node scripts/start-dev.mjs
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo  ================================================
echo   Private Agent exited (code=%EXIT_CODE%)
echo  ================================================
echo.
pause
exit /b %EXIT_CODE%
