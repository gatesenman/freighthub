@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 货代询价助手（调试控制台）
"%~dp0python\python.exe" run.py
pause
