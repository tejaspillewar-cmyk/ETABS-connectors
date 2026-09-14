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
echo  Writing plugin.config...
set "CONFIG_PATH=%ROOT%\etabs_plugin\plugin.config"
(
    echo # Paths used by EtabsGuiPlugin.dll. Edit these if the project or the
    echo # python environment moves. Lines starting with # are ignored.
    echo PYTHONW=%VENV%\Scripts\pythonw.exe
    echo SCRIPT=%ROOT%\etabs_gui.pyw
) > "%CONFIG_PATH%"
echo  Wrote: %CONFIG_PATH%

echo.
echo  Registering Plugin as a COM component for the current user...
set "FX_PATH=C:\Windows\Microsoft.NET\Framework64\v4.0.30319"
set "DLL_PATH=%ROOT%\etabs_plugin\EtabsLiveConnector.dll"
set "REG_PATH=%ROOT%\plugin_temp.reg"

if exist "%FX_PATH%\regasm.exe" (
    "%FX_PATH%\regasm.exe" /regfile:"%REG_PATH%" "%DLL_PATH%" /codebase >nul 2>&1
    if exist "%REG_PATH%" (
        powershell -Command "(Get-Content '%REG_PATH%') -replace 'HKEY_CLASSES_ROOT', 'HKEY_CURRENT_USER\Software\Classes' | Set-Content '%REG_PATH%'"
        reg import "%REG_PATH%" >nul 2>&1
        del "%REG_PATH%"
        echo  COM registration successful.
    ) else (
        echo  Warning: Failed to generate COM registry file.
    )
) else (
    echo  Warning: .NET Framework regasm.exe not found. COM registration skipped.
)

echo.
echo  Configuring ETABS Menu...
"%VENV%\Scripts\python.exe" "%ROOT%\etabs_plugin\register_etabs.py"

echo.
echo  ============================================================
echo   Setup complete!
echo  ============================================================
echo.
echo  The plugin "Live Connector" has been automatically added to ETABS!
echo.
echo  To use it:
echo    1. Open ETABS
echo    2. Go to the Tools menu.
echo    3. Click "Live Connector" at the bottom of the list.
echo.
echo  You do NOT need to add it manually in Add/Show Plugins.
echo.
pause
