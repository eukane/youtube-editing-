@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title 폰으로 편집하기 - 서버
cd /d "%~dp0"

echo ==================================================
echo   폰으로 편집하기
echo ==================================================
echo.

set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [X] 파이썬이 설치돼 있지 않습니다.
    echo     https://www.python.org/downloads/ 에서 설치해 주세요.
    echo     설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요.
    pause
    exit /b 1
)

%PY% -c "import gameedit" >nul 2>&1
if errorlevel 1 (
    echo 처음 실행이라 필요한 것을 설치합니다. 잠시 기다려 주세요...
    %PY% -m pip install -e .
    if errorlevel 1 (
        echo [X] 설치에 실패했습니다. docs 폴더의 "처음-실행하기.md" 를 참고해 주세요.
        pause
        exit /b 1
    )
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [X] ffmpeg 가 없습니다. PowerShell 에서 아래를 실행한 뒤 다시 시도해 주세요.
    echo.
    echo         winget install Gyan.FFmpeg
    echo.
    pause
    exit /b 1
)

echo 서버를 켭니다.
echo 윈도우 방화벽 창이 뜨면 "액세스 허용" 을 눌러 주세요. ^(안 누르면 폰에서 접속이 안 됩니다^)
echo.
%PY% -m gameedit serve --watch "%USERPROFILE%\Videos"
pause
