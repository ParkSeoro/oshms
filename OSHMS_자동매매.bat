@echo off
chcp 65001 >nul 2>nul
title OSHMS - 자동 매매 실행

echo ========================================
echo   OSHMS 주식 자동 매매 시스템
echo   자동매매 CLI 모드
echo ========================================
echo.

cd /d "%~dp0"

:: Python 찾기
set PYTHON=
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python --version >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set PYTHON=python
    )
)
if "%PYTHON%"=="" (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set PYTHON=py
    )
)
if "%PYTHON%"=="" (
    where python3 >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set PYTHON=python3
    )
)
if "%PYTHON%"=="" (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    echo   1. https://python.org 에서 Python 3.10 이상을 설치하세요.
    echo   2. 설치 시 "Add python.exe to PATH" 를 반드시 체크하세요!
    echo.
    pause
    exit /b 1
)

echo [OK] Python 확인: %PYTHON%
%PYTHON% --version
echo.

:: 가상환경이 있으면 활성화
if exist "venv\Scripts\activate.bat" (
    echo [OK] 가상환경 활성화
    call venv\Scripts\activate.bat
)

:: 필요 라이브러리 설치
%PYTHON% -m pip install -r requirements.txt -q
if %ERRORLEVEL% neq 0 (
    echo [경고] 일부 라이브러리 설치에 실패했습니다.
    echo.
)

echo 전략: expert (전문가 모드)
echo 분석 주기: 10초
echo.
echo 종료하려면 Ctrl+C를 누르세요.
echo.

%PYTHON% main.py trade --strategy expert --interval 10

echo.
pause
