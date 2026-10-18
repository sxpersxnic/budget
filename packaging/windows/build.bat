@echo off
rem Double-click to build the Windows installer (see packaging\README.md).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
set EXITCODE=%ERRORLEVEL%
echo.
pause
exit /b %EXITCODE%
