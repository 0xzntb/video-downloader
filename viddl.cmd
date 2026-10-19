@echo off
rem Viddl launcher for Windows (R10).
rem
rem install.ps1 copies this file into %LOCALAPPDATA%\viddl\bin, where it starts
rem the private environment next door. Run from a checkout, it uses the
rem checkout's virtual environment or the Python on PATH, so viddl.cmd works
rem before installing too.
setlocal EnableExtensions
set "HERE=%~dp0"

set "VIDDL_EXE=%HERE%..\venv\Scripts\viddl.exe"
if exist "%VIDDL_EXE%" goto run_exe
set "VIDDL_EXE=%HERE%venv\Scripts\viddl.exe"
if exist "%VIDDL_EXE%" goto run_exe
set "VIDDL_EXE=%HERE%.venv\Scripts\viddl.exe"
if exist "%VIDDL_EXE%" goto run_exe

where py >nul 2>nul
if not errorlevel 1 goto run_py
where python >nul 2>nul
if not errorlevel 1 goto run_python

echo Viddl is not installed for this user. Run install.ps1 first. 1>&2
exit /b 9009

:run_exe
"%VIDDL_EXE%" %*
exit /b %ERRORLEVEL%

:run_py
py -3 -m viddl %*
exit /b %ERRORLEVEL%

:run_python
python -m viddl %*
exit /b %ERRORLEVEL%
