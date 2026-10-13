@echo off
rem Project Python 3 environment bootstrap wrapper.
rem Purpose: bypass PowerShell ExecutionPolicy for .ps1 and provide a single entry point.
rem Usage:   scripts\bootstrap_env.cmd [ -Recreate ]
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap_env.ps1" %*
exit /b %ERRORLEVEL%
