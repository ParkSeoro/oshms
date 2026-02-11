@echo off
chcp 65001 >nul
title OSHMS - 자동 매매 실행

echo ========================================
echo   OSHMS 주식 자동 매매 시스템
echo   자동매매 CLI 모드
echo ========================================
echo.

cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    pause
    exit /b 1
)

pip install -r requirements.txt -q 2>nul

echo 전략: expert (전문가 모드)
echo 분석 주기: 10초
echo.
echo 종료하려면 Ctrl+C 를 누르세요.
echo.

python main.py trade --strategy expert --interval 10

pause
