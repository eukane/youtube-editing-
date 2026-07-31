@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
title 게임 하이라이트 자동 편집기
cd /d "%~dp0"

echo ==================================================
echo   게임 하이라이트 자동 편집기
echo ==================================================
echo.

rem ---- 파이썬 확인 ----
rem py 런처를 먼저 본다. python.exe 는 미설치 상태에서도 스토어를 여는
rem 가짜 실행 파일이 잡히는 경우가 있다.
set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [X] 파이썬이 설치돼 있지 않습니다.
    echo     https://www.python.org/downloads/ 에서 설치해 주세요.
    echo     설치할 때 첫 화면 맨 아래 "Add python.exe to PATH" 를 꼭 체크하세요.
    echo.
    pause
    exit /b 1
)

rem ---- 처음 실행이면 설치 ----
%PY% -c "import gameedit" >nul 2>&1
if errorlevel 1 (
    echo 처음 실행이라 필요한 것을 설치합니다. 잠시 기다려 주세요...
    echo.
    %PY% -m pip install -e .
    if errorlevel 1 (
        echo.
        echo [X] 설치에 실패했습니다. docs 폴더의 "처음-실행하기.md" 를 참고해 주세요.
        pause
        exit /b 1
    )
    echo.
)

rem ---- ffmpeg 확인 ----
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [X] ffmpeg 가 설치돼 있지 않습니다.
    echo     PowerShell 을 열고 아래를 실행한 뒤, 이 창을 닫았다가 다시 실행해 주세요.
    echo.
    echo         winget install Gyan.FFmpeg
    echo.
    pause
    exit /b 1
)

rem ---- 음성 인식 설치 여부 ----
%PY% -c "import faster_whisper" >nul 2>&1
if errorlevel 1 (
    %PY% -c "import whisper" >nul 2>&1
    if errorlevel 1 (
        echo [알림] 음성 인식이 설치돼 있지 않아 이번에는 자막 없이 편집합니다.
        echo        대사 자막을 넣으려면 한 번만 아래를 실행하세요. ^(용량이 큽니다^)
        echo.
        echo            %PY% -m pip install faster-whisper
        echo.
    )
)

rem ---- 영상 파일 입력 ----
echo 편집할 영상 파일을 이 창에 끌어다 놓고 엔터를 누르세요.
echo.
set /p "VIDEO=영상 파일: "
set VIDEO=!VIDEO:"=!
if not exist "!VIDEO!" (
    echo.
    echo [X] 파일을 찾을 수 없습니다: !VIDEO!
    pause
    exit /b 1
)

rem ---- 완성본 길이 ----
echo.
echo 완성본을 몇 분짜리로 만들까요?  ^(그냥 엔터 = 10분^)
set /p "LENGTH=길이(분): "
if "!LENGTH!"=="" set "LENGTH=10"

echo.
echo 편집을 시작합니다. 영상 길이에 따라 오래 걸릴 수 있습니다...
echo.
%PY% -m gameedit auto "!VIDEO!" -t !LENGTH!m

if errorlevel 1 (
    echo.
    echo [X] 편집 중 문제가 생겼습니다. 위에 나온 메시지를 확인해 주세요.
    echo     docs 폴더의 "처음-실행하기.md" 에 있는 "자주 나는 오류" 항목이 도움이 됩니다.
    pause
    exit /b 1
)

echo.
echo [완료] out 폴더의 final.mp4 를 확인하세요.
echo        어디를 왜 잘랐는지는 work 폴더의 plan.html 을 열어 보면 됩니다.
echo.
if exist "out" start "" "out"
pause
