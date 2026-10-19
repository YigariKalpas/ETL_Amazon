@echo off
rem Install versioned git hooks by pointing core.hooksPath at scripts\git-hooks.
rem Usage: scripts\install_git_hooks.cmd
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
git config core.hooksPath scripts/git-hooks
echo [ok] core.hooksPath = %CD%\scripts\git-hooks
git config --get core.hooksPath
exit /b 0
