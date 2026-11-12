@echo off
rem Folder Studio 启动脚本（普通权限）
rem 外显名 / 图标 / 目录联接 / 硬链接 / 快捷方式 都能用；
rem 只有「符号链接」需要管理员，可在程序右上角点「以管理员重启」。

setlocal
set "SCRIPT=%~dp0folder_tool.py"
set "PYW=D:\AI\AAWork\Tool\Python\pythonw.exe"
if exist "%PYW%" goto RUN
set "PYW=pythonw.exe"

:RUN
start "" "%PYW%" "%SCRIPT%"
endlocal
