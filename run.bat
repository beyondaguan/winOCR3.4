@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 虚拟环境路径（均相对本脚本，无任何硬编码绝对路径）
set "VENV_PY=.venv\Scripts\pythonw.exe"
set "VENV_PY_C=.venv\Scripts\python.exe"
set "CFG=.venv\pyvenv.cfg"

REM 虚拟环境存在但解释器起不来（基础 Python 被移动/重装导致 shim 指向失效）→ 重新绑定
if exist "%VENV_PY_C%" (
    "%VENV_PY_C%" -V >nul 2>nul || call :repair
) else if not exist "%VENV_PY%" (
    call :rebuild
)
REM python.exe 没问题但 pythonw.exe 的 shim 可能指向了已删除的安装（之前踩过这个坑），
REM 也单独探一下，触发 repair 让它从新 home 拷贝一份能用的 pythonw.exe。
if exist "%VENV_PY%" (
    "%VENV_PY%" -V >nul 2>nul || call :repair
)

REM 单实例程序：旧进程还活着时 start 只会激活旧窗口，改过的代码不会加载。
REM 所以启动前先清掉旧实例（无旧实例时 --quick 秒退，不拖慢启动）。
if exist "%VENV_PY_C%" (
    "%VENV_PY_C%" stop_winocr.py --quick
)

if exist "%VENV_PY%" (
    start "" "%VENV_PY%" main.py
    goto :eof
)
if exist "%VENV_PY_C%" (
    start "" "%VENV_PY_C%" main.py
    goto :eof
)

echo.
echo [错误] 未能启动 WinOCR，请先双击 setup.bat 完成安装。
echo.
pause
goto :eof

REM 重新把 venv 绑定到当前 PATH 上的 Python（保留 site-packages，不重装依赖）
:repair
echo [信息] 虚拟环境失效，正在重新绑定到当前 Python（保留已装依赖）...
set "BEST="
set "BESTV="
for /f "delims=" %%p in ('where python 2^>nul') do (
    for /f "tokens=2" %%v in ('"%%p" --version 2^>^&1') do set "VV=%%v"
    if not defined BEST (
        set "BEST=%%p"
        set "BESTV=!VV!"
    )
    if "!VV:~0,4!"=="3.12" (
        set "BEST=%%p"
        set "BESTV=!VV!"
        goto :bind
    )
)
if not defined BEST goto :rebuild
:bind
set "PEXE=!BEST!"
set "PV=!BESTV!"
for %%I in ("!PEXE!") do set "PH=%%~dpI"
set "PH=!PH:~0,-1!"
(
    echo home = !PH!
    echo include-system-site-packages = false
    echo version = !PV!
    echo executable = !PEXE!
    echo command = !PEXE! -m venv %~dp0.venv
) > "!CFG!"
echo        已绑定到 !PEXE! （依赖保留，无需重装）
REM 把新 home 下的 pythonw.exe 拷到 venv，避免 venv 自带的 shim 指向旧/已删除的安装
set "SRC_PYEXE=!PEXE!"
for %%I in ("!SRC_PYEXE!") do set "SRC_PH=%%~dpI"
set "SRC_PYW=!SRC_PH!pythonw.exe"
if exist "!SRC_PYW!" (
    copy /y "!SRC_PYW!" "%VENV_PY%" >nul && echo        已同步 pythonw.exe 到 venv
) else (
    REM 实在没 pythonw.exe 就用 python.exe 顶替（会弹一个控制台窗口，但能跑）
    copy /y "!SRC_PYEXE!" "%VENV_PY%" >nul
)
goto :eof

REM 彻底找不到可用 Python 时，基于当前 python 完整重建虚拟环境
:rebuild
echo [信息] 未找到可用虚拟环境，正在基于当前 Python 重建...
where python >nul 2>nul || (
    echo [错误] 未找到 python，请先安装 Python 3.10 - 3.14 并勾选 "Add Python to PATH"
    echo        下载: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo        系统 Python : %PYVER%
if exist ".venv" rmdir /s /q .venv
python -m venv .venv
if errorlevel 1 (
    echo [错误] 虚拟环境创建失败
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [警告] 部分依赖安装失败，可运行 .venv\Scripts\python.exe main.py doctor 自检
)
goto :eof
