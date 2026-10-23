@echo off
rem Crawl dashboard: status distribution, snapshot size, progress, ETA.
setlocal
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\crawler\crawl_stats.py" %*
exit /b %ERRORLEVEL%
