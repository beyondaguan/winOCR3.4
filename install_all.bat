@echo off
chcp 936 >nul 2>&1
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   WinOCR 3.4 һ����װ
echo   ���� + OCRģ�� + ���߷���� + llama-cpp-python(����conda����)
echo ============================================================
echo.

rem ---- ��λ�� tkinter �� Python ----
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
    echo [����] δ�ҵ� Python���밲װ�ٷ� Python 3.10 - 3.14��
    echo        https://www.python.org/downloads/
    echo        ��װʱ��ѡ "Add Python to PATH" �� "tcl/tk and IDLE"
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('%PYCMD% --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)
if !PYMAJOR! lss 3 (
    echo [错误] Python 版本过低：%PYVER%（需要 3.10+）
    pause
    exit /b 1
)
if !PYMAJOR! equ 3 if !PYMINOR! lss 10 (
    echo [错误] Python 版本过低：%PYVER%（需要 3.10+）
    echo        RapidOCR 3.9 / onnxruntime 1.23 要求 Python 3.10+
    pause
    exit /b 1
)
echo [1/5] Python %PYVER%  ^(已满足 3.10+^)

rem ---- ���⻷�����Ѵ��ڵ� pip ��ʱ�ؽ���----
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m pip --version >nul 2>nul
    if errorlevel 1 (
        echo [��ʾ] �������⻷��ȱ�� pip�������ؽ�...
        rmdir /s /q .venv >nul 2>nul
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [2/5] �������⻷�� .venv ...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [����] �������⻷��ʧ��
        pause
        exit /b 1
    )
) else (
    echo [2/5] ���⻷���Ѵ��ڣ�����
)

set "PY=.venv\Scripts\python.exe"
for /f "tokens=2" %%v in ('%PY% -V 2^>nul') do set "VENVVER=%%v"
echo        ���⻷�� Python��%VENVVER%
%PY% -c "import tkinter" >nul 2>nul && echo        tkinter������ ^(GUI ģʽ^) || echo        tkinter��ȱʧ ^(��������ģʽ�����鰲װ�ٷ� Python^)

echo.
echo [3/5] ��װ pip ������Լ 200MB�������ĵȴ���...
set PYTHONUTF8=1
set "PIP_TIMEOUT=--default-timeout=60"

rem ---- pip ����װ������� ���� ����λ�ý�ɹ���� ��װ���ж� ----
echo        ���ڹ���ٷ����ڰ�...
%PY% -m pip install --upgrade pip -q %PIP_TIMEOUT%
if errorlevel 1 (
    echo [��ʾ] pip ����ʧ�ܣ�����λ�����...
)

echo        尝试 pip 镜像...
set "PIP_INSTALLED=0"

rem 国内镜像链：阿里云 → 清华 → 中科大
%PY% -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ %PIP_TIMEOUT%
if not errorlevel 1 set "PIP_INSTALLED=1"

if !PIP_INSTALLED! equ 0 (
    echo    ...阿里云失败����װ�ԥ����...
    %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple %PIP_TIMEOUT%
    if not errorlevel 1 set "PIP_INSTALLED=1"
)

if !PIP_INSTALLED! equ 0 (
    echo    ...清华失败����װ�ѧ����...
    %PY% -m pip install -r requirements.txt -i https://pypi.mirrors.ustc.edu.cn/simple/ %PIP_TIMEOUT%
    if not errorlevel 1 set "PIP_INSTALLED=1"
)

rem 海外/国内通用回退：PyPI 官方
if !PIP_INSTALLED! equ 0 (
    echo    ...国内镜像失败����װ�PyPI ����...
    %PY% -m pip install -r requirements.txt %PIP_TIMEOUT%
    if not errorlevel 1 set "PIP_INSTALLED=1"
)

if !PIP_INSTALLED! equ 0 (
    echo.
    echo [����] ������װʧ�ܣ�����л���л��չ������������ȥ �������
    echo        https://pypi.org/simple/ �������ȥ
    pause
    exit /b 1
)

echo.
echo [4/5] ����ģ�Ͳ���װ llama-cpp-python
echo        OCR Լ140MB + �����Լ160MB + LLM Լ1.5GB�����߹�����·
echo        �Ѿ�λ���ļ��Զ��������жϺ��������м�������
echo.

rem setup.bat ͨ������ WINOCR_INTERACTIVE=1 ���ñ��ű������ڴ�ѯ���Ƿ����� LLM
set "DL_ARGS="
if defined WINOCR_INTERACTIVE (
    set /p DL_LLM="�Ƿ����ر��� LLM ģ�ͣ�Qwen2.5 + Hunyuan-MT����Լ1.5GB�����س�=���أ����� n ����: "
    if /i "!DL_LLM!"=="n" set "DL_ARGS=ocr translate wheel"
)

%PY% tools\download_all_models.py !DL_ARGS!
if errorlevel 1 (
    echo.
    echo [��ʾ] ������Դ����ʧ�ܣ�OCR ���Ĺ����Կ�ʹ�á�
    echo        ����ָ����������б��ű���������������ɵ��ļ������ظ����ء�
)

echo.
echo [5/5] �Լ�...
echo ============================================================
%PY% main.py doctor

echo.
echo ============================================================
echo   ��װ���
echo ------------------------------------------------------------
echo   ����ͼ�ν��� : ˫�� run.bat
echo   �ٴ��Լ�     : .venv\Scripts\python.exe main.py doctor
echo   ������ʶ��   : .venv\Scripts\python.exe main.py ocr ͼƬ.png -t zh-CN
echo ============================================================

pause
