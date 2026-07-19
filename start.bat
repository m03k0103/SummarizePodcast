@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT=%~dp0Source\download_and_summarize_podcast.py"
set "OUTPUT_DIR=%~dp0Output"
set "LOG_DIR=%~dp0log"

mkdir "%OUTPUT_DIR%" 2>nul
mkdir "%LOG_DIR%" 2>nul
for /f "tokens=1-3 delims=/-. " %%a in ('echo %date%') do set "DATE_STR=%%c%%a%%b"
set "LOG_FILE=%LOG_DIR%\run_%DATE_STR%.log"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "%SCRIPT%" %* 1>>"%LOG_FILE%" 2>&1
) else if exist ".venv311\Scripts\python.exe" (
  ".venv311\Scripts\python.exe" "%SCRIPT%" %* 1>>"%LOG_FILE%" 2>&1
) else (
  py -3 "%SCRIPT%" %* 1>>"%LOG_FILE%" 2>&1
)
