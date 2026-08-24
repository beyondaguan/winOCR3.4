@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   WinOCR 3.4 安装
echo ============================================================
echo.

rem ---- 选择 Python（关键）----
rem GUI 依赖 tkinter（Python 标准库自带），但 WorkBuddy / 嵌入式 / 精简版
rem Python 常缺 tkinter，用它建的 venv 没有界面，只能跑 console。
rem 所以这里优先用 py 启动器（官方安装默认带 tkinter），再回退 where python，
rem 且每步都用 `python -c "import tkinter"` 实测验证。
set "PYCMD="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import tkinter" >nul 2>nul && set "PYCMD=py -3"
)
if not defined PYCMD (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import tkinter" >nul 2>nul && set "PYCMD=python"
    )
)
if not defined PYCMD (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 python，请先安装官方 Python 3.10 - 3.14
        echo        下载: https://www.python.org/downloads/
        echo        安装时务必勾选 "Add Python to PATH" 和 "tcl/tk and IDLE"
        pause
        exit /b 1
    )
    echo [警告] PATH 上的 Python 不带 tkinter，图形界面将无法启动。
    echo        只能使用命令行模式（main.py console / ocr / doctor）。
    echo        请安装官方 Python 后重跑本脚本：https://www.python.org/downloads/
    set "PYCMD=python"
)

for /f "tokens=2" %%v in ('%PYCMD% --version 2^>^&1') do set PYVER=%%v
echo [1/4] Python %PYVER%

if not exist ".venv\Scripts\python.exe" (
    echo [2/4] 创建虚拟环境 .venv ...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
) else (
    echo [2/4] 虚拟环境已存在，跳过
)

set PY=.venv\Scripts\python.exe
for /f "tokens=2" %%v in ('%PY% -V') do set VENVVER=%%v
echo       虚拟环境 Python : %VENVVER%   （与上面的系统版本不同属正常）
%PY% -c "import tkinter" >nul 2>nul && echo       tkinter       : 可用，GUI 正常 || echo       tkinter       : 缺失，GUI 不可用（请用官方 Python 重装）


echo [3/4] 安装依赖（首次约 200MB，请耐心等待）...
%PY% -m pip install --upgrade pip -q
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [警告] 部分依赖安装失败。可先跑自检看缺哪一项：
    echo        .venv\Scripts\python.exe main.py doctor
    pause
)

echo [4/4] 自检...
%PY% main.py doctor

echo.
echo ============================================================
echo   安装完成
echo ------------------------------------------------------------
echo   启动图形界面 : 双击 run.bat
echo   自检         : .venv\Scripts\python.exe main.py doctor
echo   命令行识别   : .venv\Scripts\python.exe main.py ocr 图片.png -t zh-CN
echo ============================================================
pause
