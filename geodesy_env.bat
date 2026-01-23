@echo off
setlocal

set "start_dir=C:\Users\David\Documents\Local_Python\geodesy"

REM Navigate to the start directory
cd /d "%start_dir%"

REM Activate the virtual environment
echo Activating geodenv...
call "%start_dir%\geodenv\Scripts\activate.bat"

REM Install packages from requirements.txt if it exists
if exist "%start_dir%\requirements.txt" (
    echo Loading required python modules listed in requirements.txt...
    pip install -r "%start_dir%\requirements.txt"
) else (
    if exist "%start_dir%\geodenv\requirements.txt" (
        echo Loading even more python modules, even more specific to the geodenv environment...
        pip install -r "%start_dir%\venv\requirements.txt"
    )
)

echo Activated geodenv.

REM Set credential variables
if exist "%start_dir%\geodenv\dotenv.txt" (
    echo Setting credential specific to the geodenv virtual environment.
    for /f "tokens=1,2 delims==" %%a in (%start_dir%\geodenv\dotenv.txt) do (
        set %%a=%%b
        )
	) 
)

REM Keep the command prompt open
cmd.exe /k
