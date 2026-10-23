@echo off
rem Supervisor loop for the product page crawler (Python implementation).
rem Usage: scripts\crawl_forever.cmd [ --delay 3 ] [ --batch 200 ] [ --max-hours 6 ]
rem Stop : create data\crawler\STOP
setlocal
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\crawler\crawl_supervisor.py" %*
exit /b %ERRORLEVEL%
