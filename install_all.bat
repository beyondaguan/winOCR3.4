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
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)
if !PYMAJOR! lss 3 (
    echo [错误] Python 版本过低，%PYVER%（需要 3.10+）
    pause
    exit /b 1
)
if !PYMAJOR! equ 3 if !PYMINOR! lss 10 (
    echo [错误] Python 版本过低，%PYVER%（需要 3.10+）
    echo        RapidOCR 3.9 / onnxruntime 1.23 要求 Python 3.10+
    pause
    exit /b 1
)
echo [1/5] Python %PYVER%  (已满足 3.10+)

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
set "PIP_TIMEOUT=--timeout=60"

rem ---- pip 依赖安装：依次尝试多个国内镜像，全部失败再走官方 ----
echo        升级 pip 中...
%PY% -m pip install --upgrade pip -q %PIP_TIMEOUT%
if errorlevel 1 (
    echo [提示] pip 升级失败，继续尝试安装依赖...
)

echo        尝试 pip 镜像...
set "PIP_INSTALLED=0"

rem 国内镜像顺序：阿里云 -> 清华 -> 中科大
%PY% -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ %PIP_TIMEOUT%
if not errorlevel 1 set "PIP_INSTALLED=1"

if !PIP_INSTALLED! equ 0 (
    echo    ...阿里云失败，尝试清华镜像...
    %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple %PIP_TIMEOUT%
    if not errorlevel 1 set "PIP_INSTALLED=1"
)

if !PIP_INSTALLED! equ 0 (
    echo    ...清华失败，尝试中科大镜像...
    %PY% -m pip install -r requirements.txt -i https://pypi.mirrors.ustc.edu.cn/simple/ %PIP_TIMEOUT%
    if not errorlevel 1 set "PIP_INSTALLED=1"
)

rem 海外/国内通用兜底：PyPI 官方
if !PIP_INSTALLED! equ 0 (
    echo    ...国内镜像失败，尝试 PyPI 官方源...
    %PY% -m pip install -r requirements.txt %PIP_TIMEOUT%
    if not errorlevel 1 set "PIP_INSTALLED=1"
)

if !PIP_INSTALLED! equ 0 (
    echo.
    echo [错误] 依赖安装失败，请检查网络后重新运行本脚本
    echo        https://pypi.org/simple/ 可能无法访问
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

rem ---- [4b] official llama.cpp multi-variant CPU DLLs (AVX speed-up) ----
echo.
echo [4b/5] llama.cpp AVX speed-up package ^(official upstream, ~17MB^)
echo        Auto-selects the native CPU kernel at runtime; non-fatal on failure.
%PY% tools\fix_llama_avx.py
if errorlevel 1 (
    echo [WARN] AVX package not applied - local LLM keeps the slower
    echo        baseline kernel. OCR / translate / UI are unaffected.
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
