@echo off
echo ========================================
echo    Interview Bot Application Startup
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ and try again
    pause
    exit /b 1
)

REM Check if requirements are installed
echo Checking dependencies...
pip show flask >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
)

echo.
echo Starting application...
echo.

REM Start the application using the robust startup script
python run_app.py

REM If the application exits with an error
if errorlevel 1 (
    echo.
    echo ERROR: Application failed to start
    echo Check the logs for more details
    pause
    exit /b 1
)

pause
