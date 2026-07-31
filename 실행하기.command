#!/usr/bin/env bash
# 맥·리눅스용 실행 파일. 더블클릭하면 됩니다.
# (맥에서 "열 수 없음" 이 뜨면 파일 우클릭 → 열기)

cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "❌ 파이썬이 설치돼 있지 않습니다."
    echo "   https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행해 주세요."
    read -r -p "엔터를 누르면 닫힙니다..." _
    exit 1
fi

echo "=================================================="
echo "  게임 하이라이트 자동 편집기"
echo "=================================================="
echo

# 처음 실행이면 설치
if ! "$PY" -c "import gameedit" >/dev/null 2>&1; then
    echo "처음 실행이라 필요한 것을 설치합니다. 잠시 기다려 주세요…"
    "$PY" -m pip install -e . || {
        echo "❌ 설치에 실패했습니다. docs/처음-실행하기.md 를 참고해 주세요."
        read -r -p "엔터를 누르면 닫힙니다..." _
        exit 1
    }
    echo
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "❌ ffmpeg 가 없습니다. 아래 명령으로 설치한 뒤 다시 실행해 주세요."
    echo "     맥   : brew install ffmpeg"
    echo "     리눅스: sudo apt install ffmpeg"
    read -r -p "엔터를 누르면 닫힙니다..." _
    exit 1
fi

if ! "$PY" -c "import faster_whisper" >/dev/null 2>&1 \
   && ! "$PY" -c "import whisper" >/dev/null 2>&1; then
    echo "ℹ️  음성 인식이 설치돼 있지 않아 이번에는 자막 없이 편집합니다."
    echo "   대사 자막을 넣으려면 한 번만 아래를 실행하세요 (용량이 큽니다)."
    echo "       $PY -m pip install faster-whisper"
    echo
fi

echo "편집할 영상 파일을 이 창에 끌어다 놓고 엔터를 누르세요."
read -r -p "영상 파일: " VIDEO
VIDEO="${VIDEO%\"}"
VIDEO="${VIDEO#\"}"
VIDEO="${VIDEO%\'}"
VIDEO="${VIDEO#\'}"
VIDEO="$(echo "$VIDEO" | sed 's/[[:space:]]*$//')"

if [ ! -f "$VIDEO" ]; then
    echo "❌ 파일을 찾을 수 없습니다: $VIDEO"
    read -r -p "엔터를 누르면 닫힙니다..." _
    exit 1
fi

echo
echo "완성본을 몇 분짜리로 만들까요? (그냥 엔터 = 10분)"
read -r -p "길이(분): " LENGTH
[ -z "$LENGTH" ] && LENGTH=10

echo
echo "편집을 시작합니다. 영상 길이에 따라 오래 걸릴 수 있습니다…"
echo
"$PY" -m gameedit auto "$VIDEO" -t "${LENGTH}m"
STATUS=$?

echo
if [ $STATUS -eq 0 ]; then
    echo "✅ 완성됐습니다. out 폴더의 final.mp4 를 확인하세요."
    echo "   어디를 왜 잘랐는지는 work 폴더의 plan.html 을 열어 보면 됩니다."
    if command -v open >/dev/null 2>&1; then
        open out 2>/dev/null
    fi
else
    echo "❌ 편집 중 문제가 생겼습니다. 위에 나온 메시지를 확인해 주세요."
    echo "   docs/처음-실행하기.md 의 '자주 나는 오류' 항목이 도움이 됩니다."
fi

read -r -p "엔터를 누르면 닫힙니다..." _
