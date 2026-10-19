@echo off
rem Wrapper for healthcheck.ps1 (bypasses PowerShell ExecutionPolicy).
rem Usage: scripts\healthcheck.cmd [ -SkipPan ]
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0healthcheck.ps1" %*
exit /b %ERRORLEVEL%
