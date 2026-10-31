@echo off
chcp 65001 >nul
title 微信 AI 桥接机器人 v3
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "PYTHONPATH=%~dp0src"

echo ============================================
echo   微信 AI 桥接机器人 v3
echo   运行前确认：小号微信已登录、窗口未锁屏
echo   Ctrl+C 停止
echo ============================================

"%VENV%\Scripts\python.exe" -m wechat_agent.supervisor %*

echo.
echo 程序已退出。
pause
