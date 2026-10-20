@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_desktop_app.ps1"
if errorlevel 1 (
  echo.
  echo BookBot 启动失败，请查看上方提示。
  pause
)
