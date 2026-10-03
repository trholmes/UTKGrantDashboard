@echo off
rem TEMPORARY: drag the three reports onto this file. It writes
rem diagnostic.txt (counts and account codes only - no names or amounts)
rem next to it. Read that file, then send it back.
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>nul
if not errorlevel 1 (
    py -3 diagnose.py %*
) else (
    python diagnose.py %*
)
pause
