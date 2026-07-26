@echo off
rem Scarica e installa ROCm, PyTorch e ProPainter nell'ambiente del programma.
title Video Watermark Remover - supporto GPU AMD
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"
"%~dp0python\python.exe" "%~dp0app\scripts\installa_gpu.py"
echo.
echo Premi un tasto per chiudere questa finestra.
pause >nul
