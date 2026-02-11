@echo off
chcp 65001 >nul
title OSHMS - 웹 서버 (모바일/PC 브라우저)

echo ========================================
echo   OSHMS 주식 자동 매매 시스템
echo   웹 서버 모드 (모바일/PC 브라우저)
echo ========================================
echo.

cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo python.org 에서 Python을 설치해주세요.
    echo.
    pause
    exit /b 1
)

echo Python 확인 완료.
pip install -r requirements.txt -q 2>nul

echo.

:: PC의 IP 주소 표시
echo ── 접속 방법 ──────────────────────────
echo   PC: http://localhost:5000
echo.
echo   모바일(같은 Wi-Fi): 아래 IP로 접속
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R "IPv4"') do (
    set IP=%%a
    call set IP=%%IP: =%%
    echo   http://%%IP:5000
)
echo ────────────────────────────────────────
echo.
echo 종료하려면 이 창을 닫으세요.
echo.

python main.py web

if %ERRORLEVEL% neq 0 (
    echo.
    echo [오류] 서버 실행 중 오류가 발생했습니다.
    echo.
    pause
)
