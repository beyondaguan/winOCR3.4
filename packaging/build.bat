@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."
echo ============================================================
echo  WinOCR 打包脚本（PyInstaller onedir + 数据外置）
echo ============================================================

echo [1/4] 构建 WinOCR.exe（PyInstaller onedir）...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\winocr.spec
if errorlevel 1 (
    echo [错误] PyInstaller 构建失败
    pause
    exit /b 1
)

echo [2/4] 复制 OCR 模型（v6_tiny + v6_medium + cls，与 exe 同级）...
if not exist "dist\WinOCR\models" mkdir "dist\WinOCR\models"
if exist "models\v6_tiny"   xcopy /e /i /y "models\v6_tiny"   "dist\WinOCR\models\v6_tiny"   >nul
if exist "models\v6_medium" xcopy /e /i /y "models\v6_medium" "dist\WinOCR\models\v6_medium" >nul
if exist "models\ch_ppocr_mobile_v2.0_cls_infer.onnx" copy /y "models\ch_ppocr_mobile_v2.0_cls_infer.onnx" "dist\WinOCR\models\" >nul

echo [3/4] 复制插件 / 便携开关 / vendor 说明 ...
if not exist "dist\WinOCR\plugins" mkdir "dist\WinOCR\plugins"
xcopy /e /i /y "plugins" "dist\WinOCR\plugins" >nul
if not exist "dist\WinOCR\config.toml" type nul > "dist\WinOCR\config.toml"
if not exist "dist\WinOCR\vendor" mkdir "dist\WinOCR\vendor"
echo 离线翻译语言包放入 argos_packages\ 子目录即自动生效（无需重打包）> "dist\WinOCR\vendor\argos_packages_README.txt"
REM 单实例由 exe 内部 CreateMutexW/FindWindowW 处理，无需外部 stop 脚本。

echo [4/4] 完成：dist\WinOCR\
dir /s /a-d "dist\WinOCR" 2>nul | find "个文件" || dir /s /a-d "dist\WinOCR" 2>nul | find "File(s)"
endlocal
