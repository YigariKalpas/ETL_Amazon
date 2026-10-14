@echo off
rem Wrapper for setup_pdi.ps1 (bypasses PowerShell ExecutionPolicy).
rem Usage: scripts\setup_pdi.cmd [ -Force ]
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_pdi.ps1" %*
exit /b %ERRORLEVEL%
