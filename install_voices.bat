@echo off
rem ============================================
rem  WinOCR TTS voice pack installer (zh-CN + en-US)
rem  Click YES on the UAC prompt when it appears.
rem  Log written to _voice_install.log
rem  Window auto-closes in 4s (no keypress needed).
rem ============================================
echo Installing WinOCR TTS voices (zh-CN + en-US)...
echo Please click YES on the UAC prompt.
echo.
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\install_tts_voices.py"
echo.
echo Finished. Check _voice_install.log
echo Window closes automatically...
timeout /t 4 >nul
