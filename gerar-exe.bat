@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo  Compilando RelatorioDespesaViagem.exe
echo ========================================
echo.

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" packaging\build.py
if errorlevel 1 (
    echo.
    echo FALHA na compilacao.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Concluido.
echo  Pasta: dist\RelatorioDespesaViagem\
echo  ZIP:   dist\RelatorioDespesaViagem-Windows.zip
echo ========================================
echo.
pause
exit /b 0
