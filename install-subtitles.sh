#!/data/data/com.termux/files/usr/bin/bash
#
# 폰에서 대사 자막을 만들려면 필요한 음성 인식 설치 (Termux 전용)
#
# 안드로이드에서는 faster-whisper(파이토치)가 돌아가지 않습니다.
# 대신 C++ 로 만든 whisper.cpp 를 빌드해서 씁니다.
#
#   bash ~/gameedit/install-subtitles.sh          # 기본(base) 모델
#   bash ~/gameedit/install-subtitles.sh small    # 더 정확하지만 느리고 무거움
#
set -e

MODEL_SIZE="${1:-base}"
MODEL_DIR="$HOME/whisper-models"
SRC_DIR="$HOME/whisper.cpp"

echo "=================================================="
echo "  폰에서 자막 만들기 - 음성 인식 설치"
echo "  모델: $MODEL_SIZE"
echo "=================================================="
echo

# TERMUX_ROOT 는 테스트에서 가짜 환경을 가리키게 하기 위한 것. 평소엔 건드릴 일 없다.
TERMUX_ROOT="${TERMUX_ROOT:-/data/data/com.termux}"
if [ -z "$PREFIX" ] || [ ! -d "$TERMUX_ROOT" ]; then
    echo "❌ Termux 앱 안에서 실행해 주세요."
    exit 1
fi

echo "[1/4] 빌드 도구 설치"
pkg install -y git cmake clang make wget >/dev/null

echo "[2/4] whisper.cpp 내려받기"
if [ -d "$SRC_DIR/.git" ]; then
    git -C "$SRC_DIR" pull --ff-only >/dev/null 2>&1 || true
else
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$SRC_DIR"
fi

echo "[3/4] 빌드 (폰 성능에 따라 5~15분 걸립니다. 끄지 마세요)"
cd "$SRC_DIR"
cmake -B build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_EXAMPLES=ON >/dev/null
cmake --build build -j"$(nproc)" >/dev/null

BINARY=""
for candidate in build/bin/whisper-cli build/bin/main build/bin/whisper; do
    [ -x "$SRC_DIR/$candidate" ] && BINARY="$SRC_DIR/$candidate" && break
done
if [ -z "$BINARY" ]; then
    echo "❌ 빌드는 끝났는데 실행 파일을 못 찾았습니다."
    echo "   $SRC_DIR/build/bin 안을 확인해 주세요."
    exit 1
fi
ln -sf "$BINARY" "$PREFIX/bin/whisper-cli"

echo "[4/4] 한국어 모델 내려받기 ($MODEL_SIZE)"
mkdir -p "$MODEL_DIR"
MODEL_FILE="$MODEL_DIR/ggml-$MODEL_SIZE.bin"
if [ -f "$MODEL_FILE" ]; then
    echo "      이미 있습니다: $MODEL_FILE"
else
    URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL_SIZE.bin"
    wget -O "$MODEL_FILE.part" "$URL"
    mv "$MODEL_FILE.part" "$MODEL_FILE"
fi

echo
echo "=================================================="
echo "  ✅ 자막 준비 끝!"
echo
echo "  이제 '편집기' 로 만들면 대사 자막이 같이 들어갑니다."
echo
echo "  주의: 음성 인식은 폰에서 제일 오래 걸리는 단계입니다."
echo "        1시간짜리 영상이면 30분 이상 걸릴 수 있습니다."
echo "        급하면 편집기 화면에서 '대사 자막' 스위치를 끄세요."
echo "=================================================="
