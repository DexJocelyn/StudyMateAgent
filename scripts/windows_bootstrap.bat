@echo off
setlocal
cd /d "%~dp0.."
set "_STUDYMATE_ARGS=%*"

rem Probe interpreters before using them; py may refer to a removed installation.
call :try_python ".venv\Scripts\python.exe"
if not errorlevel 1 goto run
call :try_python py -3.12
if not errorlevel 1 goto run
call :try_python py -3.13
if not errorlevel 1 goto run
call :try_python py -3.11
if not errorlevel 1 goto run
call :try_python py -3
if not errorlevel 1 goto run
call :try_python python
if not errorlevel 1 goto run
call :try_python python3
if not errorlevel 1 goto run

rem Official installers commonly use these locations even when PATH is missing.
for %%V in (312 313 311 314) do (
    call :try_python "%LocalAppData%\Programs\Python\Python%%V\python.exe"
    if not errorlevel 1 goto run
    call :try_python "%ProgramFiles%\Python%%V\python.exe"
    if not errorlevel 1 goto run
)

echo No working Python 3.11+ was found. This step does not use the network or VPN.
echo Check the installed interpreter paths below:
where py 2>nul
where python 2>nul
py -0p 2>nul
echo Repair Python or add its actual installation directory to PATH, then retry.
exit /b 2

:try_python
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "_STUDYMATE_PYTHON=%*"
exit /b 0

:run
%_STUDYMATE_PYTHON% -c "import sys; print('Using Python:', sys.version.split()[0], sys.executable)"
%_STUDYMATE_PYTHON% scripts\windows_start.py %_STUDYMATE_ARGS%
exit /b %errorlevel%
