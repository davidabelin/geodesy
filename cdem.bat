@echo off
pushd "%~dp0"
"%~dp0geodenv\Scripts\python.exe" -m altex %*
set "altex_exit_code=%errorlevel%"
popd
exit /b %altex_exit_code%
