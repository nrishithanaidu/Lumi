@echo off
chcp 65001 >nul
:: ─────────────────────────────────────────────────────
::  Lumi — AI Document Intelligence
::  Startup script for Windows
::  Usage: double-click start.bat
:: ─────────────────────────────────────────────────────

echo.
echo ╔══════════════════════════════════════════════╗
echo ║   Lumi — AI Document Intelligence            ║
echo ║   AWS Textract + Amazon Bedrock Nova Lite    ║
echo ╚══════════════════════════════════════════════╝
echo.

SET DIR=%~dp0
SET VENV=%DIR%.venv
SET LUMI_PATH=%DIR%lumi_project

:: Create virtualenv if not exists
IF NOT EXIST "%VENV%" (
    echo Creating virtual environment...
    python -m venv "%VENV%"
)

:: Activate
call "%VENV%\Scripts\activate.bat"

:: Install dependencies
echo Installing dependencies...
pip install -r "%DIR%requirements.txt" -q

:: Set PYTHONPATH
SET PYTHONPATH=%LUMI_PATH%;%PYTHONPATH%

:: Copy .env if exists
IF EXIST "%LUMI_PATH%\.env" (
    IF NOT EXIST "%DIR%.env" (
        copy "%LUMI_PATH%\.env" "%DIR%.env"
    )
)

echo Starting Lumi server...
echo Open http://localhost:5000 in your browser
echo.

cd "%DIR%"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python server.py

pause
