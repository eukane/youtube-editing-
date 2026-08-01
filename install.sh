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

# 설정 파일을 못 만지는 사람도 파일만 넣으면 밈이 늘어나게, 폰 파일 앱에서
# 바로 보이는 곳에 폴더를 미리 만들어 둔다.
MEME_DIR="$HOME/storage/shared/gameedit-memes"
if [ -d "$HOME/storage/shared" ]; then
    mkdir -p "$MEME_DIR" 2>/dev/null || true
fi
[ -d "$MEME_DIR" ] || MEME_DIR="$HOME/gameedit/memes"
mkdir -p "$MEME_DIR" 2>/dev/null || true
if [ -d "$MEME_DIR" ] && [ ! -f "$MEME_DIR/밈-넣는-법.txt" ]; then
    cat > "$MEME_DIR/밈-넣는-법.txt" <<'GUIDE'
여기에 이미지·움짤·효과음 파일을 넣기만 하면 편집할 때 자동으로 씁니다.
설정을 고칠 필요 없습니다.

파일 이름이 곧 "언제 뜰지" 입니다.

  무야호.png              → 영상에서 "무야호" 라고 말하면 뜸
  죽었,사망,뒤졌.png      → 쉼표로 여러 말에 반응
  hype.png                → 말과 상관없이 제일 신난 순간에 뜸
  silence.png             → 조용한 구간에 뜸
  개킹받네@right@2.5.png   → 오른쪽에 2.5초 동안

같은 이름의 소리 파일을 같이 넣으면 그림과 함께 재생됩니다.
  무야호.png + 무야호.mp3

쓸 수 있는 형식
  그림 : png jpg jpeg webp bmp
  움짤 : gif mp4 webm mov mkv
  소리 : mp3 wav m4a ogg flac aac

────────────────────────────────────────
인터넷에서 받아 오기

  밈받기 폭발=explosion 웃음=laughing

왼쪽이 자막에 나오면 뜰 말, 오른쪽이 검색어입니다 (영어가 결과가 많습니다).
유튜브에 올려도 되는 라이선스만 받아서 이 폴더에 넣고, 저작자를
'출처.txt' 에 적어 둡니다.

⚠ 받아지는 건 폭발·불꽃 같은 **연출용 그림**이지 인터넷 유행 짤이 아닙니다.
   방송 캡처나 유행 짤은 저작권이 있어서 자동으로 받아 올 수 없습니다.

⚠ 영상을 다 만들면 완성본 옆에 '크레딧.txt' 가 생길 수 있습니다.
   그 안의 글을 유튜브 설명란에 붙여 넣어야 합니다.
GUIDE
fi

# 게임마다 용어가 다르다. 사용자가 직접 채울 수 있는 자리를 만들어 둔다.
TERM_DIR="$HOME/storage/shared/gameedit-terms"
[ -d "$HOME/storage/shared" ] && mkdir -p "$TERM_DIR" 2>/dev/null || true
[ -d "$TERM_DIR" ] || TERM_DIR="$HOME/gameedit/terms"
mkdir -p "$TERM_DIR" 2>/dev/null || true
if [ -d "$TERM_DIR" ] && [ ! -f "$TERM_DIR/용어-넣는-법.txt" ]; then
    cat > "$TERM_DIR/용어-넣는-법.txt" <<'GUIDE'
여기에 .json 파일을 넣으면 편집할 때 자동으로 씁니다.
대사에 그 말이 나오면 화면 위쪽에 작게 설명이 뜹니다.

파일 예 (내용어.json) — 메모장으로 만들면 됩니다.

{
  "우리 길드": "3년째 같이 하는 사람들",
  "그 무기": "3만원 주고 산 것",
  "빨콩": "빨간 포션",
  "보스방": "3층 안쪽"
}

  · 파일 이름은 아무거나 (.json 으로 끝나기만 하면 됩니다)
  · 여러 개 넣어도 됩니다. 게임별로 나눠 두면 편합니다
  · 두 글자 이상만 인식합니다 (한 글자는 아무 데나 걸립니다)
  · 포켓몬 1023마리는 이미 들어 있습니다

⚠ 편집 중에는 인터넷을 쓰지 않습니다. 여기 있는 파일만 읽습니다.
GUIDE
fi

echo "[5/5] 실행 명령 등록"
BIN="$PREFIX/bin/편집기"
# 잠금 해제 명령은 termux-wake-unlock 이다. wake-lock 쪽에 해제 옵션을 붙여
# 부르면 사용법 안내가 stdout 으로 나와서, 편집기를 끌 때마다 엉뚱한 문구가
# 뜬다. 아래 실행 파일에는 정확한 명령만 넣는다.
cat > "$BIN" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
# 폰에서 편집기를 켜는 명령
cd "$TARGET" || exit 1
termux-wake-lock >/dev/null 2>&1 || true
trap 'termux-wake-unlock >/dev/null 2>&1 || true' EXIT
python -m gameedit serve --local --profile phone "\$@"
EOF
chmod +x "$BIN"
# 한글 입력 없이도 실행할 수 있게 ASCII 이름도 함께 만든다
ln -sf "$BIN" "$PREFIX/bin/edit"
ln -sf "$BIN" "$PREFIX/bin/gogo"

# 인터넷에서 밈 그림 받아오기: '밈받기 폭발=explosion'
FETCHER="$PREFIX/bin/밈받기"
cat > "$FETCHER" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
# 유튜브에 올려도 되는 라이선스의 그림만 받아 밈 폴더에 넣는다
cd "$TARGET" || exit 1
if [ \$# -eq 0 ]; then
    echo "이렇게 쓰세요:  밈받기 폭발=explosion 웃음=laughing"
    echo "  왼쪽 = 자막에 나오면 뜰 말 / 오른쪽 = 검색어(영어가 결과가 많음)"
    exit 1
fi
python tools/fetch_memes.py "\$@"
EOF
chmod +x "$FETCHER"
ln -sf "$FETCHER" "$PREFIX/bin/getmeme"

# 최신 버전 받아오기: 'update' 한 단어
UPDATER="$PREFIX/bin/update"
cat > "$UPDATER" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
# 편집기를 최신 버전으로
cd "$TARGET" || exit 1
echo "최신 버전을 받는 중…"
git pull --ff-only || { echo "❌ 받기 실패. 인터넷 연결을 확인해 주세요."; exit 1; }
pip install -e . >/dev/null 2>&1 || pip install -e .
echo "✅ 최신 버전입니다. 이제 edit 을 치면 됩니다."
EOF
chmod +x "$UPDATER"

echo
echo "=================================================="
echo "  ✅ 설치 끝!"
echo
echo "  이제 Termux 에서 아래 명령만 치면 됩니다."
echo
echo "      edit          (또는 편집기)"
echo
echo "  그러면 주소가 나옵니다. 크롬으로 그 주소를 여세요."
echo
echo "  나중에 최신 버전을 받으려면:  update"
echo "  밈 그림을 받으려면:           밈받기 폭발=explosion"
echo "=================================================="
echo
echo "지금 바로 켜보려면:  edit"
