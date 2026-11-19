@echo off
rem 货代询价助手 - 网页/服务器模式（Windows）。浏览器访问 http://本机IP:8765/ ，需要登录。
rem 用法：双击运行；或  start-server.bat 8080  （改端口）。 HTTPS：先 set FREIGHT_HUB_CERT=证书.pem 和 set FREIGHT_HUB_KEY=私钥.pem
chcp 65001 >nul
cd /d "%~dp0"
set FREIGHT_HUB_MODE=server
if not "%~1"=="" set FREIGHT_HUB_PORT=%~1
if "%FREIGHT_HUB_HOST%"=="" set FREIGHT_HUB_HOST=0.0.0.0
if "%FREIGHT_HUB_PORT%"=="" set FREIGHT_HUB_PORT=8765
rem 可选：首个管理员密码（不设则随机生成，写入 data\INITIAL_ADMIN_PASSWORD.txt）
rem set FREIGHT_HUB_ADMIN_PASSWORD=请改成你自己的强密码
if exist "%~dp0python\python.exe" (set PY="%~dp0python\python.exe") else (set PY=python)
echo 监听 %FREIGHT_HUB_HOST%:%FREIGHT_HUB_PORT% ，关闭此窗口即停止服务。
%PY% run.py --server
pause
