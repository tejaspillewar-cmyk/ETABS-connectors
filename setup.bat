@echo off
setlocal EnableDelayedExpansion
title ETABS Live Connector - Setup
echo.
echo  ============================================================
echo   ETABS Live Connector -- First-Time Setup
echo  ============================================================
echo.

:: -- Find Python ----------------------------------------------------
set "PYTHON="
where pythonw.exe >nul 2>&1 && for /f "delims=" %%P in ('where pythonw.exe') do if not defined PYTHON set "PYTHON=%%P"
if not defined PYTHON (
    where python.exe >nul 2>&1 && for /f "delims=" %%P in ('where python.exe') do if not defined PYTHON set "PYTHON=%%P"
)
if not defined PYTHON (
    echo  ERROR: Python is not installed or not on your PATH.
    echo.
    echo  Install Python from https://www.python.org/downloads/
    echo  and tick "Add Python to PATH" during installation.
    echo  No admin rights are needed -- choose "Install for this user only".
    echo.
    pause
    exit /b 1
)

echo  Found Python: %PYTHON%

:: -- Get the folder this .bat lives in --------------------------------
set "ROOT=%~dp0"
:: Remove trailing backslash
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

:: -- Create virtual environment ----------------------------------------
set "VENV=%ROOT%\.venv"
if exist "%VENV%\Scripts\python.exe" (
    echo  Virtual environment already exists at .venv
) else (
    echo  Creating virtual environment...
    "%PYTHON%" -m venv "%VENV%"
    if errorlevel 1 (
        echo  ERROR: Failed to create the virtual environment.
        echo  Make sure Python's "venv" module is available.
        pause
        exit /b 1
    )
    echo  Created: %VENV%
)

:: -- Install dependencies -----------------------------------------------
echo  Installing dependencies...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
"%VENV%\Scripts\pip.exe" install -r "%ROOT%\requirements.txt"
if errorlevel 1 (
    echo.
    echo  ERROR: pip install failed. Check the output above.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   Setup complete!
echo  ============================================================
echo.
echo  Python environment ready. One manual step left to add the ETABS
echo  menu shortcut (this cannot be automated -- see README.md section 7
echo  for why):
echo.
echo    1. Open ETABS.
echo    2. Tools -^> Add/Show Plugins -^> Add.
echo    3. Browse to: %ROOT%\PyLauncherPlugin\PyLauncherPlugin.dll
echo    4. Click "Live Connector" (or whatever you named it) in the
echo       Tools menu whenever you want to use the tool.
echo.
echo  You can also just run: pythonw etabs_gui.pyw
echo.
pause
