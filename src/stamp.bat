@echo off
setlocal
rem Align child Python output with the Chinese Windows console code page 936.
rem Some setups inject PYTHONUTF8=1 / PYTHONIOENCODING=utf-8, which makes
rem Chinese output show as mojibake in a cp936 console window.
set "PYTHONUTF8=0"
set "PYTHONIOENCODING=cp936"
rem PDF Stamp Tool - lightweight launcher, ASCII only, paths handled at runtime
set "D=%~dp0"
set "PY="

for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%LOCALAPPDATA%\Programs\Python\Python310\python.exe") do if not defined PY if exist %%P set "PY=%%~fP"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python not found. Install Python 3.9+ with tkinter, then run again.
  pause
  exit /b 1
)

if "%~1"=="" goto GUI

rem Drag-and-drop mode: stamp the dropped PDFs with default settings, no window
"%PY%" "%D%stamp_gui.py" %*
echo.
pause
exit /b 0

:GUI
rem Window mode: use pythonw so no console window is left behind
set "PYW=%PY:python.exe=pythonw.exe%"
if exist "%PYW%" (
  start "" "%PYW%" "%D%stamp_gui.py"
) else (
  start "" "%PY%" "%D%stamp_gui.py"
)
exit /b 0
