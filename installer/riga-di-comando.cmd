@echo off
rem Apre un prompt gia' agganciato al Python del programma.
title Video Watermark Remover - riga di comando
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"
set "PATH=%~dp0python;%~dp0python\Scripts;%PATH%"
cd /d "%~dp0app"
echo.
echo   Video Watermark Remover - riga di comando
echo   -----------------------------------------
echo.
echo   Esempio:  python dewatermark.py --input video.mp4 --output pulito.mp4
echo   Opzioni:  python dewatermark.py --help
echo.
cmd /k
