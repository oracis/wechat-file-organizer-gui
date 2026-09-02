@echo off
chcp 65001 >nul
REM 用「含 Tcl/Tk 的系统 Python」打包。WorkBuddy 的精简 Python 没有 tkinter，不能用。
set "PY=%1"
if "%PY%"=="" set "PY=python"

if not exist build\venv (
    "%PY%" -m venv build\venv
)
build\venv\Scripts\python.exe -m pip install --upgrade pip -q
build\venv\Scripts\python.exe -m pip install pyinstaller -q
build\venv\Scripts\pyinstaller --onefile --windowed --name WeChatFileOrganizer --distpath dist --workpath build\work main.py
echo.
echo 产物: dist\WeChatFileOrganizer.exe
pause
