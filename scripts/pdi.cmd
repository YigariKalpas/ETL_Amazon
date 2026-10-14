@echo off
rem ============================================================
rem Project-local PDI/Kettle launcher.
rem Injects a project-private JDK 11 and KETTLE_HOME, so the
rem machine-wide Java 25 is left untouched. Nothing is written
rem to system environment variables.
rem Usage:
rem   scripts\pdi.cmd status                        show environment
rem   scripts\pdi.cmd spoon                         launch Spoon client
rem   scripts\pdi.cmd pan <trans.ktr> [options]     run a transformation
rem   scripts\pdi.cmd kitchen <job.kjb> [options]   run a job
rem ============================================================
setlocal EnableExtensions
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"

set "JAVA_HOME=%PROJECT_ROOT%\tools\jdk-11"
set "PENTAHO_JAVA_HOME=%JAVA_HOME%"
set "PENTAHO_DI_HOME=%PROJECT_ROOT%\tools\pdi-ce-9.4.0.0-343\data-integration"
set "KETTLE_HOME=%PROJECT_ROOT%\etl\kettle"
set "PENTAHO_DI_JAVA_OPTIONS=-Xmx4g -Dfile.encoding=UTF-8"
set "PATH=%JAVA_HOME%\bin;%PATH%"

set "TOOL=%~1"
if "%TOOL%"=="" set "TOOL=status"

set "ARGS="
shift
:collect
if "%~1"=="" goto :dispatch
set "ARGS=%ARGS% %1"
shift
goto :collect

:dispatch
if /I "%TOOL%"=="status"  goto :status
if /I "%TOOL%"=="spoon"   goto :spoon
if /I "%TOOL%"=="pan"     goto :pan
if /I "%TOOL%"=="kitchen" goto :kitchen
echo [ERROR] unknown tool: %TOOL%
echo Usage: pdi.cmd status ^| spoon ^| pan ^| kitchen [args...]
exit /b 2

:status
echo JAVA_HOME                 = %JAVA_HOME%
echo PENTAHO_JAVA_HOME         = %PENTAHO_JAVA_HOME%
echo PENTAHO_DI_HOME           = %PENTAHO_DI_HOME%
echo KETTLE_HOME               = %KETTLE_HOME%
echo PENTAHO_DI_JAVA_OPTIONS   = %PENTAHO_DI_JAVA_OPTIONS%
echo.
echo --- java -version ---
if exist "%JAVA_HOME%\bin\java.exe" (
  "%JAVA_HOME%\bin\java.exe" -version
) else (
  echo   [MISSING] %JAVA_HOME%\bin\java.exe
  echo   run first: scripts\setup_pdi.cmd
)
echo.
echo --- key files ---
if exist "%PENTAHO_DI_HOME%\Spoon.bat"   (echo   Spoon.bat   OK) else (echo   Spoon.bat   MISSING)
if exist "%PENTAHO_DI_HOME%\Pan.bat"     (echo   Pan.bat     OK) else (echo   Pan.bat     MISSING)
if exist "%PENTAHO_DI_HOME%\Kitchen.bat" (echo   Kitchen.bat OK) else (echo   Kitchen.bat MISSING)
exit /b 0

:spoon
if not exist "%PENTAHO_DI_HOME%\Spoon.bat" goto :missing
call "%PENTAHO_DI_HOME%\Spoon.bat" %ARGS%
exit /b %ERRORLEVEL%

:pan
if not exist "%PENTAHO_DI_HOME%\Pan.bat" goto :missing
call "%PENTAHO_DI_HOME%\Pan.bat" %ARGS%
exit /b %ERRORLEVEL%

:kitchen
if not exist "%PENTAHO_DI_HOME%\Kitchen.bat" goto :missing
call "%PENTAHO_DI_HOME%\Kitchen.bat" %ARGS%
exit /b %ERRORLEVEL%

:missing
echo [ERROR] PDI not found. Run first: scripts\setup_pdi.cmd
exit /b 1
