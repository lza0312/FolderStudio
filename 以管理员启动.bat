@echo off
rem Folder Studio 启动脚本（管理员权限）
rem 需要创建「符号链接」时直接双击这个，会弹出 UAC 授权窗口。

setlocal
set "SCRIPT=%~dp0folder_tool.py"
set "PYW=D:\AI\AAWork\Tool\Python\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

rem 通过环境变量传参，避免 cmd 与 PowerShell 的引号互相打架
set FS_PYW=%PYW%
set FS_ARG="%SCRIPT%"
powershell -NoProfile -Command "Start-Process -FilePath $env:FS_PYW -ArgumentList $env:FS_ARG -Verb RunAs"
endlocal
