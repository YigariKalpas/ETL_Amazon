@echo off
rem Project-portable Git (tools\git 2.56) with OpenSSL TLS backend.
rem The machine-wide E:\Git install is broken (no sh.exe, no git-remote-*),
rem and schannel TLS fails under the sandbox (SEC_E_NO_CREDENTIALS).
rem Usage: scripts\git_new.cmd <git args...>
setlocal
"%~dp0..\tools\git\cmd\git.exe" -c http.sslBackend=openssl %*
exit /b %ERRORLEVEL%
