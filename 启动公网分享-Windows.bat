@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "%~dp0scripts\windows_bootstrap.bat" --share
if errorlevel 1 (
    echo 分享未成功。请根据上面的具体错误排查；Python未启动时与VPN无关。
    pause
)
