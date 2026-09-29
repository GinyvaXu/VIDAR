@echo off
rem 双击启动 VIDAR 图形界面
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [错误] 未找到虚拟环境，请先在本目录执行：
    echo     uv sync --extra asr --extra gui
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m vidar.gui.app
