@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local_video\setup_free_studio.ps1"
if errorlevel 1 (
  echo.
  echo Setup stopped. Read the message above.
  pause
)
