@echo off
chcp 936 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   WinOCR 3.4 一键安装
echo   依赖 + OCR模型 + 离线翻译包 + llama-cpp-python(国内conda镜像)
echo ============================================================
echo.

rem ---- 定位带 tkinter 的 Python ----
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
    echo [错误] 未找到 Python，请安装官方 Python 3.10 - 3.14：
    echo        https://www.python.org/downloads/
    echo        安装时勾选 "Add Python to PATH" 与 "tcl/tk and IDLE"
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('%PYCMD% --version 2^>^&1') do set "PYVER=%%v"
echo [1/5] Python %PYVER%

rem ---- 虚拟环境（已存在但 pip 损坏时重建）----
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m pip --version >nul 2>nul
    if errorlevel 1 (
        echo [提示] 已有虚拟环境缺少 pip，正在重建...
        rmdir /s /q .venv >nul 2>nul
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [2/5] 创建虚拟环境 .venv ...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
) else (
    echo [2/5] 虚拟环境已存在，跳过
)

set "PY=.venv\Scripts\python.exe"
for /f "tokens=2" %%v in ('%PY% -V 2^>nul') do set "VENVVER=%%v"
echo        虚拟环境 Python：%VENVVER%
%PY% -c "import tkinter" >nul 2>nul && echo        tkinter：可用 ^(GUI 模式^) || echo        tkinter：缺失 ^(仅命令行模式，建议安装官方 Python^)

echo.
echo [3/5] 安装 pip 依赖（约 200MB，请耐心等待）...
set PYTHONUTF8=1
%PY% -m pip install --upgrade pip -q -i https://mirrors.aliyun.com/pypi/simple/
%PY% -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
if errorlevel 1 (
    echo [提示] 阿里云镜像安装失败，尝试清华镜像...
    %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，请检查网络后重新运行本脚本
    pause
    exit /b 1
)

echo.
echo [4/5] 下载模型并安装 llama-cpp-python
echo        OCR 约140MB + 翻译包约160MB + LLM 约1.5GB，均走国内链路
echo        已就位的文件自动跳过，中断后重新运行即可续传
echo.

rem setup.bat 通过设置 WINOCR_INTERACTIVE=1 复用本脚本，并在此询问是否下载 LLM
set "DL_ARGS="
if defined WINOCR_INTERACTIVE (
    set /p DL_LLM="是否下载本地 LLM 模型（Qwen2.5 + Hunyuan-MT，共约1.5GB）？回车=下载，输入 n 跳过: "
    if /i "!DL_LLM!"=="n" set "DL_ARGS=ocr translate wheel"
)

%PY% tools\download_all_models.py !DL_ARGS!
if errorlevel 1 (
    echo.
    echo [提示] 部分资源下载失败，OCR 核心功能仍可使用。
    echo        网络恢复后重新运行本脚本即可续传，已完成的文件不会重复下载。
)

echo.
echo [5/5] 自检...
echo ============================================================
%PY% main.py doctor

echo.
echo ============================================================
echo   安装完成
echo ------------------------------------------------------------
echo   启动图形界面 : 双击 run.bat
echo   再次自检     : .venv\Scripts\python.exe main.py doctor
echo   命令行识别   : .venv\Scripts\python.exe main.py ocr 图片.png -t zh-CN
echo ============================================================

pause
