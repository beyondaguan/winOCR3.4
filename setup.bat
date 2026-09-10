@echo off
chcp 936 >nul
setlocal
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   WinOCR 3.4 安装
echo ============================================================
echo.

rem ---- 保存当前 pip 源配置 ----
set "PIP_ORIG_INDEX="
set "PIP_ORIG_EXTRA="
set "PIP_ORIG_TRUSTED="

for /f "usebackq delims=" %%a in (`pip config get global.index-url 2^>nul`) do set "PIP_ORIG_INDEX=%%a"
for /f "usebackq delims=" %%a in (`pip config get global.extra-index-url 2^>nul`) do set "PIP_ORIG_EXTRA=%%a"
for /f "usebackq delims=" %%a in (`pip config get global.trusted-host 2^>nul`) do set "PIP_ORIG_TRUSTED=%%a"

echo [0/6] 临时切换 pip 源到清华 + 阿里镜像...
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul
pip config set global.extra-index-url https://mirrors.aliyun.com/pypi/simple >nul 2>nul
pip config set global.trusted-host "pypi.tuna.tsinghua.edu.cn mirrors.aliyun.com" >nul 2>nul

for /f "usebackq delims=" %%a in (`pip config list 2^>nul`) do echo        %%a

echo.

rem ---- 选择 Python（优先带 tkinter 的）----
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
        echo        安装时勾选 "Add Python to PATH" 和 "tcl/tk and IDLE"
        pause
        goto :RESTORE_PIP
        exit /b 1
    )
    echo [警告] PATH 上的 Python 缺少 tkinter，图形界面将无法使用
    echo        只能使用命令行模式: main.py console / ocr / doctor
    echo        请安装官方 Python 以支持 GUI: https://www.python.org/downloads/
    set "PYCMD=python"
)

for /f "tokens=2" %%v in ('%PYCMD% --version 2^>^&1') do set PYVER=%%v
echo [1/6] Python %PYVER%

if not exist ".venv\Scripts\python.exe" (
    echo [2/6] 创建虚拟环境 .venv ...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        goto :RESTORE_PIP
        exit /b 1
    )
) else (
    echo [2/6] 虚拟环境已存在，跳过
)

set PY=.venv\Scripts\python.exe
for /f "tokens=2" %%v in ('%PY% -V') do set VENVVER=%%v
echo       虚拟环境 Python : %VENVVER%   (若与系统版本不同，请忽略)
%PY% -c "import tkinter" >nul 2>nul && echo       tkinter       : 可用（GUI 模式） || echo       tkinter       : 缺失（GUI 不可用，请用官方 Python 安装）


echo [3/6] 安装依赖（体积约 200MB，请耐心等待）...
%PY% -m pip install --upgrade pip -q
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。请检查网络或执行诊断：
    echo        .venv\Scripts\python.exe main.py doctor
    pause
)

echo [4/6] 下载 OCR 模型 tiny + medium（体积约 140MB，首次分发可跳过，模型可后续按需下载）...
%PY% tools\download_ocr_model.py tiny
%PY% tools\download_ocr_model.py medium

echo [5/6] 下载离线翻译语言包（含英译中，约 140MB；失败不中断，在线翻译自动兜底）...
%PY% tools\download_argos.py

echo.
echo [6/6] 可选：下载本地大模型（llama.cpp 翻译引擎需要）...
if not exist "models\llama" mkdir "models\llama"
echo.
echo   [1] Qwen2.5-0.5B-Instruct  (约 468MB，速度快，29种语言)
echo       模型：qwen2.5-0.5b-instruct-q4_k_m.gguf
echo.
echo   [2] HY-MT1.5-1.8B 翻译模型  (约 1.08GB，翻译质量好)
echo       模型：HY-MT1.5-1.8B-Q4_K_M.gguf
echo.
echo   [both] 两个都下载    [回车] 跳过
set /p MODEL_CHOICE="输入选择（1/2/both/回车跳过）："

if "%MODEL_CHOICE%"=="1" (
    echo.
    if not exist "models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf" (
        echo        正在下载 Qwen2.5-0.5B-Instruct...（约 468MB）
        curl -L --progress-bar -o "models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf" "https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf" 2>&1
        if errorlevel 1 (
            echo [警告] curl 下载失败，尝试 PowerShell...
            powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf' -OutFile 'models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf' -UseBasicParsing"
            if errorlevel 1 echo [警告] 下载失败，请手动下载后放到 models\llama\ 目录
        )
    ) else (
        echo        qwen2.5-0.5b-instruct-q4_k_m.gguf 已存在，跳过
    )
)

if "%MODEL_CHOICE%"=="2" (
    echo.
    if not exist "models\llama\HY-MT1.5-1.8B-Q4_K_M.gguf" (
        echo        正在下载 HY-MT1.5-1.8B...（约 1.08GB）
        curl -L --progress-bar -o "models\llama\HY-MT1.5-1.8B-Q4_K_M.gguf" "https://www.modelscope.cn/models/Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF/resolve/master/HY-MT1.5-1.8B-Q4_K_M.gguf" 2>&1
        if errorlevel 1 (
            echo [警告] curl 下载失败，尝试 PowerShell...
            powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.modelscope.cn/models/Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF/resolve/master/HY-MT1.5-1.8B-Q4_K_M.gguf' -OutFile 'models\llama\HY-MT1.5-1.8B-Q4_K_M.gguf' -UseBasicParsing"
            if errorlevel 1 echo [警告] 下载失败，请手动下载后放到 models\llama\ 目录
        )
    ) else (
        echo        HY-MT1.5-1.8B-Q4_K_M.gguf 已存在，跳过
    )
)

if /i "%MODEL_CHOICE%"=="both" (
    echo.
    if not exist "models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf" (
        echo        正在下载 Qwen2.5-0.5B-Instruct...（约 468MB）
        curl -L --progress-bar -o "models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf" "https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf" 2>&1
        if errorlevel 1 (
            echo [警告] curl 下载失败，尝试 PowerShell...
            powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf' -OutFile 'models\llama\qwen2.5-0.5b-instruct-q4_k_m.gguf' -UseBasicParsing"
            if errorlevel 1 echo [警告] 下载失败，请手动下载后放到 models\llama\ 目录
        )
    ) else (
        echo        qwen2.5-0.5b-instruct-q4_k_m.gguf 已存在，跳过
    )
    if not exist "models\llama\HY-MT1.5-1.8B-Q4_K_M.gguf" (
        echo        正在下载 HY-MT1.5-1.8B...（约 1.08GB）
        curl -L --progress-bar -o "models\llama\HY-MT1.5-1.8B-Q4_K_M.gguf" "https://www.modelscope.cn/models/Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF/resolve/master/HY-MT1.5-1.8B-Q4_K_M.gguf" 2>&1
        if errorlevel 1 (
            echo [警告] curl 下载失败，尝试 PowerShell...
            powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.modelscope.cn/models/Tencent-Hunyuan/HY-MT1.5-1.8B-GGUF/resolve/master/HY-MT1.5-1.8B-Q4_K_M.gguf' -OutFile 'models\llama\HY-MT1.5-1.8B-Q4_K_M.gguf' -UseBasicParsing"
            if errorlevel 1 echo [警告] 下载失败，请手动下载后放到 models\llama\ 目录
        )
    ) else (
        echo        HY-MT1.5-1.8B-Q4_K_M.gguf 已存在，跳过
    )
)

echo.

echo [7/7] 自检...
%PY% main.py doctor

echo.
echo ============================================================
echo   安装完成
echo ------------------------------------------------------------
echo   启动图形界面 : 双击 run.bat
echo   自检         : .venv\Scripts\python.exe main.py doctor
echo   命令行识别   : .venv\Scripts\python.exe main.py ocr 图片.png -t zh-CN
echo ============================================================

:RESTORE_PIP
echo.
echo [恢复] 还原 pip 源配置...
if defined PIP_ORIG_INDEX (
    pip config set global.index-url "!PIP_ORIG_INDEX!" >nul 2>nul
) else (
    pip config unset global.index-url >nul 2>nul
)

if defined PIP_ORIG_EXTRA (
    pip config set global.extra-index-url "!PIP_ORIG_EXTRA!" >nul 2>nul
) else (
    pip config unset global.extra-index-url >nul 2>nul
)

if defined PIP_ORIG_TRUSTED (
    pip config set global.trusted-host "!PIP_ORIG_TRUSTED!" >nul 2>nul
) else (
    pip config unset global.trusted-host >nul 2>nul
)

echo        pip 源已恢复
echo.

pause
