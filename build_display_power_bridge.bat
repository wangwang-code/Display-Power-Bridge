@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_display_power_bridge.ps1" %*
if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)
echo.
echo Build succeeded.
endlocal
