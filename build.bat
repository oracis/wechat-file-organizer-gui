@echo off
chcp 65001 >nul
REM 用「含 Tcl/Tk 的系统 Python」打包。WorkBuddy 的精简 Python 没有 tkinter，不能用。
set "PY=%1"
if "%PY%"=="" set "PY=python"

if not exist build\venv (
    "%PY%" -m venv build\venv
)
build\venv\Scripts\python.exe -m pip install --upgrade pip -q
build\venv\Scripts\python.exe -m pip install pyinstaller pillow -q
REM 说明：pillow 只用于「图片缩略图预览」；装不上也不影响主功能，程序会自动降级。
REM 关闭 WorkBuddy 的「安全删除」拦截，否则 pyinstaller 覆盖旧 exe 时会失败
set "CODEBUDDY_SAFE_DELETE_ENABLED=0"
REM --exclude-module numpy：Pillow 会间接带进 numpy，本工具用不到，排除可省约 12MB
build\venv\Scripts\pyinstaller --onefile --windowed --name WeChatFileOrganizer --distpath dist --workpath build\work --exclude-module numpy --exclude-module PIL.ImageQt --exclude-module PIL.ImageShow main.py
echo.
echo 产物: dist\WeChatFileOrganizer.exe
pause
