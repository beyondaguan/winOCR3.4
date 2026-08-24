@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

REM 彻底关闭 WinOCR（改配置/升级后需重启时用）
REM 逻辑在 stop_winocr.py：只结束命令行含 main.py 的 python/pythonw 进程，
REM 先优雅退出，超时强杀，不误伤其它 Python 程序。
REM 用法：双击本文件；强杀请用 .venv\Scripts\python.exe stop_winocr.py --force

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" stop_winocr.py
) else (
    python stop_winocr.py
)
if errorlevel 1 (
    echo.
    echo 关闭未完全成功，可用管理员身份运行: python stop_winocr.py --force
)
echo.
pause
