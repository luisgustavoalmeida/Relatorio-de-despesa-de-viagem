@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo Falha ao criar .venv. Instale Python 3.11 ou superior e tente de novo.
        pause
        exit /b 1
    )
    echo Instalando dependencias...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Falha ao instalar dependencias.
        pause
        exit /b 1
    )
)

echo Iniciando o Gerador de Relatorios de Despesa de Viagem...
".venv\Scripts\python.exe" -m app.main
if errorlevel 1 (
    echo.
    echo O programa encerrou com erro.
    pause
)
