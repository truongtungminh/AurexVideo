@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-AurexVideo-Runner.ps1"
if errorlevel 1 pause
endlocal
