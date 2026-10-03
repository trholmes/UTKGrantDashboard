@echo off
rem TEMPORARY: double-click to describe the reports in the data folder
rem (or drag the three reports onto this file). It writes
rem diagnostic.txt (counts and account codes only - no names or amounts)
rem next to it. Read that file, then send it back.
setlocal
title Salary Reconciliation diagnostic
cd /d "%~dp0"

rem The runs stay outside ( ) blocks: a dragged path such as
rem C:\Reports\DetailBalances(2).xlsx arrives unquoted, and its ")" would
rem end the block.
py -3 --version >nul 2>nul
if errorlevel 1 goto :try_python
py -3 diagnose.py %*
goto :finished

:try_python
python --version >nul 2>nul
if errorlevel 1 goto :no_python
python diagnose.py %*
goto :finished

:no_python
echo.
echo   Python 3 was not found on this computer.
echo   Install it from  https://www.python.org/downloads/windows/
echo   (tick "Add python.exe to PATH"), then try again.

:finished
pause
