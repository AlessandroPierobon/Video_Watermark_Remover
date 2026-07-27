@echo off
rem Installa PyTorch (CPU) per il motore LaMa. Non usa iopaint.
title Video Watermark Remover - supporto LaMa
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"
"%~dp0python\python.exe" "%~dp0app\scripts\installa_lama.py"
echo.
echo Premi un tasto per chiudere questa finestra.
pause >nul
