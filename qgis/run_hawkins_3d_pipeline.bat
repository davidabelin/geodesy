@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "QGIS_PYTHON=%LOCALAPPDATA%\Programs\OSGeo4W\bin\python-qgis-ltr.bat"

if not exist "%QGIS_PYTHON%" (
    echo QGIS Python launcher not found at "%QGIS_PYTHON%".
    echo Install QGIS Desktop via OSGeo4W or update this launcher path.
    exit /b 1
)

call "%QGIS_PYTHON%" "%SCRIPT_DIR%hawkins_3d_pipeline.py" %*
