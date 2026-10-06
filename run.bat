@echo off
setlocal enabledelayedexpansion
chcp 65001 > nul
title Telegram Cleaner

echo ===============================================================================
echo                           Telegram Cleaner Launcher
echo ===============================================================================
echo.

:: 1. Check Python installation
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not found in system PATH.
    echo Please install Python 3.10 or newer from https://www.python.org/downloads/
    echo During installation, be sure to check "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

:: Show detected Python version
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [INFO] Detected Python: %PYTHON_VERSION%

:: Switch working directory to script location
cd /d "%~dp0"

:: 2. Check and install dependencies
echo [INFO] Checking Python dependencies...
python -c "import telethon, fastapi, uvicorn, pydantic, aiosqlite, dotenv" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Missing required packages. Installing from requirements.txt...
    python -m pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo.
        echo [ERROR] Failed to install dependencies from requirements.txt.
        echo Please ensure you have internet access and proper permissions.
        echo.
        pause
        exit /b 1
    )
    echo [INFO] Dependencies installed successfully.
) else (
    echo [INFO] Core dependencies are satisfied.
)
echo.

:: 3. Run application
echo [INFO] Starting Telegram Cleaner server...
echo.
python main.py %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Application stopped unexpectedly with error code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)

endlocal
