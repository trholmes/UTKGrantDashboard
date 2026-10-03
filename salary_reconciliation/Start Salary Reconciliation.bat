@echo off
rem Double-clickable launcher for the salary reconciliation page on Windows.
rem Needs Python 3 (https://www.python.org/downloads/windows/), nothing else.
setlocal
title Salary Reconciliation
cd /d "%~dp0"

rem The "py" launcher comes with the python.org installer; plain "python"
rem covers other installs. A bare Windows has a "python" stub that only
rem opens the Microsoft Store, so each is checked by asking for its version.
py -3 --version >nul 2>nul
if not errorlevel 1 (
    py -3 server.py %*
    goto :finished
)
python --version >nul 2>nul
if not errorlevel 1 (
    python server.py %*
    goto :finished
)

echo.
echo   Python 3 was not found on this computer.
echo.
echo   Install it from  https://www.python.org/downloads/windows/
echo   (tick "Add python.exe to PATH" on the first screen of the installer),
echo   then double-click this file again.
echo.
echo   The download page is opening in your browser now.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:finished
rem Keep the window open if it stopped with an error, so the message can be
rem read (a clean Ctrl-C stop exits quietly).
if errorlevel 1 (
    echo.
    echo   Salary Reconciliation stopped with an error - see above.
    pause
)
