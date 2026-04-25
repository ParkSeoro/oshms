@echo off
chcp 65001 >nul 2>nul
title OSHMS - 주식 자동 매매 시스템 (데스크톱)

echo ========================================
echo   OSHMS 주식 자동 매매 시스템
echo   데스크톱 GUI 모드
echo ========================================
echo.

cd /d "%~dp0"

:: Python 찾기 (python → py → python3 순으로 시도)
set PYTHON=
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    :: Microsoft Store 가짜 python 여부 확인
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
    echo   3. 설치 후 이 파일을 다시 실행하세요.
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
echo 필요한 라이브러리를 확인합니다...
%PYTHON% -m pip install -r requirements.txt -q
if %ERRORLEVEL% neq 0 (
    echo [경고] 일부 라이브러리 설치에 실패했습니다. 계속 시도합니다...
    echo.
)

echo.
echo 데스크톱 앱을 시작합니다...
echo.
%PYTHON% main.py gui

if %ERRORLEVEL% neq 0 (
    echo.
    echo ========================================
    echo   [오류] 프로그램 실행 중 오류가 발생했습니다.
    echo   위의 오류 메시지를 확인해 주세요.
    echo ========================================
)
echo.
pause
