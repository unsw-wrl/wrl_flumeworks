@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo WRL FlumeWorks needs uv for this development checkout.
  echo Install uv, or use a packaged WaveFlume.exe release when available.
  pause
  exit /b 1
)

uv run --extra dev flumeworks %*
if errorlevel 1 pause

