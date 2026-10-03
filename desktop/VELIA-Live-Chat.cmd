@echo off
setlocal
if not exist "%~dp0VELIA Desktop Preview.exe" (
  echo Extract this update into the VELIA Desktop folder, next to the executable.
  pause
  exit /b 1
)
if not defined LOCALAPPDATA (
  echo Windows LOCALAPPDATA is unavailable.
  pause
  exit /b 1
)
set "VELIA_GATEWAY_URL=https://velia-desktop-gateway-deepalpha-bot-pr-577.up.railway.app/desktop-api/v1"
set "VELIA_DESKTOP_HOME=%LOCALAPPDATA%\VELIA\DesktopGatewayPreview"
start "" /d "%~dp0" "%~dp0VELIA Desktop Preview.exe"
