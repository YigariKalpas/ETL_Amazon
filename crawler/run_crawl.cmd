@echo off
rem Convenience wrapper for the product page crawler.
rem Usage: crawler\run_crawl.cmd [ --limit N ] [ --delay SEC ] [ --dry-run ]
setlocal
"%~dp0..\.venv\Scripts\python.exe" "%~dp0crawl_product_pages.py" %*
exit /b %ERRORLEVEL%
