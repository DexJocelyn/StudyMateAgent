@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "%~dp0scripts\windows_bootstrap.bat"
if errorlevel 1 (
    echo 启动未成功。请检查上面的Python路径或依赖错误。
    pause
)
