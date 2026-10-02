@echo off
setlocal
set "BASE=%USERPROFILE%\Documents\TrafficLift-Local-Video"
set "PYTHON=%BASE%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Could not find the existing TrafficLift LTX Python environment:
  echo %PYTHON%
  echo Install or repair the LTX setup before starting this bridge.
  pause
  exit /b 1
)
"%PYTHON%" "%~dp0trafficlift_video_bridge.py"
echo.
echo TrafficLift local video bridge stopped.
pause
