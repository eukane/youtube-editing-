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

# 설정 파일을 덮어쓸지 되묻지 않게 한다. 물어보면 폰에서는 그대로 멈춰 버린다.
export DEBIAN_FRONTEND=noninteractive
APT_OPTS=(-y -o Dpkg::Options::=--force-confnew -o Dpkg::Options::=--force-confdef)

# apt-get 이 없는 환경도 있어서 pkg 로 물러설 수 있게 감싼다.
apt_do() {
    if command -v apt-get >/dev/null 2>&1; then
        apt-get "$1" "${APT_OPTS[@]}" "${@:2}"
    else
        pkg "$1" -y "${@:2}"
    fi
}

apt_do update >/dev/null 2>&1 || true

# 오래 안 쓴 Termux 는 기반 패키지가 낡아서, 새 패키지를 넣으려고 하면
# dpkg 가 오류로 끝난다. 먼저 밀린 것부터 올려 두면 대부분 해결된다.
echo "      기본 패키지 정리 중… (처음이면 몇 분 걸립니다)"
apt_do upgrade >/dev/null 2>&1 || true
repair_apt() {
    command -v dpkg >/dev/null 2>&1 && { dpkg --configure -a >/dev/null 2>&1 || true; }
    command -v apt-get >/dev/null 2>&1 &&
        { apt-get --fix-broken install "${APT_OPTS[@]}" >/dev/null 2>&1 || true; }
    return 0
}
repair_apt

install_pkg() {
    local name="$1" optional="${2:-}"
    if apt_do install "$name" >/dev/null 2>&1; then
        return 0
    fi
    echo "      ↻ $name 다시 시도"
    repair_apt
    if apt_do install "$name" >/dev/null 2>&1; then
        return 0
    fi
    if [ -n "$optional" ]; then
        echo "      ⚠ $name 은(는) 건너뜁니다 (없어도 편집은 됩니다)"
        return 0
    fi
    echo
    echo "❌ '$name' 설치에 실패했습니다. 아래는 실제 오류 내용입니다."
    echo "--------------------------------------------------"
    apt_do install "$name" 2>&1 | tail -25
    echo "--------------------------------------------------"
    echo "이 화면을 그대로 캡처해서 물어보시면 원인을 알 수 있습니다."
    exit 1
}

install_pkg python
install_pkg ffmpeg
install_pkg git
install_pkg termux-api optional      # 없어도 됨 (알림·배터리 연동용)

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
if ! pip install -e . >/dev/null 2>&1; then
    echo "      ↻ 다시 시도"
    python -m ensurepip --upgrade >/dev/null 2>&1 || true
    if ! pip install -e . >/dev/null 2>&1; then
        echo
        echo "❌ 편집기 설치에 실패했습니다. 아래는 실제 오류 내용입니다."
        echo "--------------------------------------------------"
        pip install -e . 2>&1 | tail -25
        echo "--------------------------------------------------"
        exit 1
    fi
fi

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
