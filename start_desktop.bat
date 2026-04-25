@echo off
:: English-named launcher for systems with Korean filename issues
:: 한국어 파일명이 작동하지 않는 시스템용 영문 런처
call "%~dp0OSHMS_데스크톱.bat"
if %ERRORLEVEL% neq 0 (
    chcp 65001 >nul 2>nul
    cd /d "%~dp0"
    echo Fallback: launching directly...
    python main.py gui
    if %ERRORLEVEL% neq 0 (
        py main.py gui
    )
    pause
)
