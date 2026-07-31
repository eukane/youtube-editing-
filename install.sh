#!/data/data/com.termux/files/usr/bin/bash
#
# 안드로이드 폰 단독 설치 스크립트 (Termux 전용)
#
#   Termux 를 열고 아래 한 줄만 붙여넣으면 됩니다.
#     curl -sL <이 파일 주소> | bash
#   또는 저장소를 받은 폴더에서
#     bash termux설치.sh
#
set -e

echo "=================================================="
echo "  게임 하이라이트 편집기 - 폰 설치"
echo "=================================================="
echo

# TERMUX_ROOT 는 테스트에서 가짜 환경을 가리키게 하기 위한 것. 평소엔 건드릴 일 없다.
TERMUX_ROOT="${TERMUX_ROOT:-/data/data/com.termux}"
if [ -z "$PREFIX" ] || [ ! -d "$TERMUX_ROOT" ]; then
    echo "❌ 이 스크립트는 Termux 앱 안에서 실행해야 합니다."
    echo "   F-Droid 에서 Termux 를 설치한 뒤 다시 시도해 주세요."
    echo "   (플레이스토어 버전은 오래돼서 안 됩니다)"
    exit 1
fi

echo "[1/5] 폰 저장소 접근 권한"
if [ ! -d "$HOME/storage" ]; then
    echo "      화면에 뜨는 권한 요청에서 '허용' 을 눌러 주세요."
    termux-setup-storage || true
    sleep 2
fi

echo "[2/5] 필요한 프로그램 설치 (몇 분 걸립니다)"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python ffmpeg git termux-api >/dev/null

echo "[3/5] 한글 폰트"
FONT_DIR="$HOME/.termux/fonts"
mkdir -p "$FONT_DIR"
if [ ! -f "$FONT_DIR/NotoSansKR.ttf" ]; then
    pkg install -y fontconfig-utils >/dev/null 2>&1 || true
fi

echo "[4/5] 편집기 설치"
TARGET="$HOME/gameedit"
if [ -d "$TARGET/.git" ]; then
    git -C "$TARGET" pull --ff-only >/dev/null 2>&1 || true
elif [ -f "./pyproject.toml" ]; then
    TARGET="$(pwd)"
else
    echo "      저장소를 받는 중…"
    git clone --depth 1 "${GAMEEDIT_REPO:-https://github.com/eukane/youtube-editing-.git}" "$TARGET"
fi
cd "$TARGET"
pip install -e . >/dev/null

echo "[5/5] 실행 명령 등록"
BIN="$PREFIX/bin/편집기"
cat > "$BIN" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
# 폰에서 편집기를 켜는 명령
cd "$TARGET" || exit 1
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-lock -u 2>/dev/null || termux-wake-unlock 2>/dev/null || true' EXIT
python -m gameedit serve --local --profile phone "\$@"
EOF
chmod +x "$BIN"
# 한글 입력 없이도 실행할 수 있게 ASCII 이름도 함께 만든다
ln -sf "$BIN" "$PREFIX/bin/edit"
ln -sf "$BIN" "$PREFIX/bin/gogo"

echo
echo "=================================================="
echo "  ✅ 설치 끝!"
echo
echo "  이제 Termux 에서 아래 명령만 치면 됩니다."
echo
echo "      edit          (또는 편집기)"
echo
echo "  그러면 주소가 나옵니다. 크롬으로 그 주소를 여세요."
echo "=================================================="
echo
echo "지금 바로 켜보려면:  edit"
