@echo off
setlocal
chcp 65001 >nul
title CUPK Course Assistant
cd /d "%~dp0"
if not exist "tools\windows_launcher.py" goto missing_files
if not exist "assistant\app.py" goto missing_files
set "PYTHONUTF8=1"
set "CUPK_PYTHON_COMMAND="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "CUPK_PYTHON_COMMAND=py -3"
if defined CUPK_PYTHON_COMMAND goto launch
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "CUPK_PYTHON_COMMAND=python"
if defined CUPK_PYTHON_COMMAND goto launch
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "CUPK_PYTHON_COMMAND=python3"
if defined CUPK_PYTHON_COMMAND goto launch
echo Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH", then run this file again.
set "CUPK_EXIT=1"
goto finish
:launch
%CUPK_PYTHON_COMMAND% -u "tools\windows_launcher.py" %*
set "CUPK_EXIT=%ERRORLEVEL%"
if "%CUPK_EXIT%"=="0" goto finish
echo Startup failed. See the error above and .state\startup.log.
goto finish
:missing_files
echo Project files are missing. Extract the WHOLE ZIP before running.
echo Keep this file beside the assistant and tools folders.
set "CUPK_EXIT=1"
:finish
echo.
echo Keep this window open while the service runs.
pause
exit /b %CUPK_EXIT%
