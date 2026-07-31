#!/usr/bin/env bash
# 맥·리눅스: 더블클릭하면 폰에서 접속할 주소가 나옵니다.
cd "$(dirname "$0")" || exit 1

PY=""
for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 && { PY="$candidate"; break; }
done
if [ -z "$PY" ]; then
    echo "❌ 파이썬이 설치돼 있지 않습니다. https://www.python.org/downloads/"
    read -r -p "엔터를 누르면 닫힙니다..." _
    exit 1
fi

if ! "$PY" -c "import gameedit" >/dev/null 2>&1; then
    echo "처음 실행이라 필요한 것을 설치합니다…"
    "$PY" -m pip install -e . || { read -r -p "엔터…" _; exit 1; }
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "❌ ffmpeg 가 없습니다.  맥: brew install ffmpeg"
    read -r -p "엔터를 누르면 닫힙니다..." _
    exit 1
fi

"$PY" -m gameedit serve --watch "$HOME/Movies" --watch "$HOME/Videos"
read -r -p "엔터를 누르면 닫힙니다..." _
