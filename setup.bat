@echo off
chcp 936 >nul 2>&1
cd /d "%~dp0"

rem ============================================================
rem  WinOCR 3.4 分步安装入口
rem  与 install_all.bat 共用同一套安装逻辑（单一实现，避免漂移），
rem  区别仅在于：本入口会在下载前询问是否下载 LLM 模型。
rem ============================================================

set "WINOCR_INTERACTIVE=1"
call "%~dp0install_all.bat"
exit /b %errorlevel%
