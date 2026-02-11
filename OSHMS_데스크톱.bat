@echo off
chcp 65001 >nul
title OSHMS - 주식 자동 매매 시스템

echo ========================================
echo   OSHMS 주식 자동 매매 시스템
echo   데스크톱 GUI 모드
echo ========================================
echo.

cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo python.org 에서 Python을 설치해주세요.
    echo 설치 시 "Add python.exe to PATH" 를 반드시 체크하세요!
    echo.
    pause
    exit /b 1
)

echo Python 확인 완료.
echo 필요한 라이브러리를 확인합니다...
pip install -r requirements.txt -q 2>nul

echo.
echo 프로그램을 시작합니다...
echo.
python main.py gui

if %ERRORLEVEL% neq 0 (
    echo.
    echo [오류] 프로그램 실행 중 오류가 발생했습니다.
    echo.
    pause
)
