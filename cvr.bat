@echo off
setlocal EnableDelayedExpansion

if /I "%~1"=="los-bundle" (
    set "LOCAL_APP_DIR=%LOCALAPPDATA%"
    if not defined LOCAL_APP_DIR (
        for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('LocalApplicationData')"`) do set "LOCAL_APP_DIR=%%I"
    )
    set "QGIS_PYTHON=!LOCAL_APP_DIR!\Programs\OSGeo4W\bin\python-qgis.bat"
    if not exist "!QGIS_PYTHON!" (
        echo QGIS Python launcher not found at "!QGIS_PYTHON!".
        echo Install QGIS Desktop via OSGeo4W or update this launcher path.
        exit /b 1
    )
    call "!QGIS_PYTHON!" "%~dp0coverage\scripts\coverage_los_qgis.py" %*
    exit /b %ERRORLEVEL%
)

python "%~dp0coverage\scripts\coverage_cli.py" %*
