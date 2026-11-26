@echo off
REM 工单5：以管理员权限运行 clean_pip_leftovers.ps1（清理 84 个空 pip-* 目录）。
REM 用法：右键本文件 -> 以管理员身份运行；或在命令行执行 clean_pip_leftovers.bat
setlocal
set "SCRIPT=%~dp0clean_pip_leftovers.ps1"
if not exist "%SCRIPT%" (
    echo [错误] 找不到脚本：%SCRIPT%
    pause
    exit /b 2
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%SCRIPT%' -Wait"
echo 已请求管理员权限执行；如未弹出 UAC 提示，请手动以管理员身份打开 PowerShell 运行：
echo   powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
pause
