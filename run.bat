@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo Creating local Python environment...
    where python >nul 2>nul
    if not errorlevel 1 (
        python -m venv "%VENV%"
    ) else (
        py -3 -m venv "%VENV%"
    )
    if not exist "%PY%" (
        echo Failed to create the local Python environment.
        echo Install Python 3.10+ and enable "Add Python to PATH", then try again.
        pause
        exit /b 1
    )
)

"%PY%" -c "import playwright" >nul 2>nul
if errorlevel 1 (
    echo Installing playwright into the local environment...
    "%PY%" -m pip install playwright -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo Failed to install playwright. Check your network and try again.
        pause
        exit /b 1
    )
)

"%PY%" main.py %*
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" echo Exit code: %EXITCODE%
pause
