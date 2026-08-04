@echo off
REM ============================================================
REM Private Agent - Windows 打包脚本(带新图标, 2026-08-04)
REM 修复项:
REM   1. 清理上次中断遗留的打包锁(win-unpacked.tmp.lock 会导致
REM      electron-builder 在 packaging 阶段无限等待 -> 卡死)
REM   2. 本地 rcedit(图标注入工具)路径, 避免从 GitHub 下载卡住
REM   3. package.json build 配置已含 build/icon.ico(多尺寸透明图标)
REM 用法: 双击运行或 CMD 执行 build-electron.bat
REM ============================================================
setlocal

set FRONTEND_DIR=%~dp0frontend
set RCEDIT_DIR=%FRONTEND_DIR%\build\rcedit
set OUTPUT_DIR=%FRONTEND_DIR%\release2

echo [1/4] 清理上次打包的锁残留(win-unpacked.tmp.lock)
if exist "%OUTPUT_DIR%\win-unpacked.tmp.lock" (
    del /q "%OUTPUT_DIR%\win-unpacked.tmp.lock"
    echo   - 已删除残留锁文件
)
if exist "%OUTPUT_DIR%\win-unpacked.tmp" (
    rmdir /s /q "%OUTPUT_DIR%\win-unpacked.tmp" 2>nul
    echo   - 已清理未完成的临时输出目录
)

echo [2/4] 检查本地 rcedit(图标注入工具)
if not exist "%RCEDIT_DIR%\rcedit-x64.exe" (
    echo   ERROR: 缺少 %RCEDIT_DIR%\rcedit-x64.exe, 请确认图标预处理已执行
    exit /b 1
)
set "ELECTRON_BUILDER_RCEDIT_PATH=%RCEDIT_DIR%"
echo   - 使用本地 rcedit: %RCEDIT_DIR%

echo [3/4] 执行 electron-builder 打包(win + nsis)
cd /d "%FRONTEND_DIR%"
call npx electron-builder --win
if errorlevel 1 (
    echo   ERROR: 打包失败, 请查看上方错误信息
    exit /b 1
)

echo [4/4] 打包完成!
echo   安装器:   %OUTPUT_DIR%\Private Agent Setup *.exe
echo   免安装版: %OUTPUT_DIR%\win-unpacked\Private Agent.exe
echo   验证: 桌面快捷方式/任务栏/Alt+Tab 均为新 PA 图标(无白底)

endlocal
