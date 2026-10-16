@echo off
rem Wrapper for pg.ps1 (bypasses PowerShell ExecutionPolicy).
rem Usage: scripts\pg.cmd [ status ^| serve ^| start ^| stop ^| psql ^| reinit ]
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0pg.ps1" %*
exit /b %ERRORLEVEL%
