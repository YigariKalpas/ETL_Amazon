@echo off
rem Wrapper for git_guard.ps1 (bypasses PowerShell ExecutionPolicy).
rem Usage: scripts\git_guard.cmd [ -MaxMB 50 ]
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0git_guard.ps1" %*
exit /b %ERRORLEVEL%
