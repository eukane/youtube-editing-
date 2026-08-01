#!/usr/bin/env python3
"""쓸 수 있는 라이선스의 이미지를 받아서 밈 폴더에 넣는다.

    python tools/fetch_memes.py 폭발=explosion 웃음=laughing 놀람=surprised
    python tools/fetch_memes.py 불=fire --count 3

왼쪽이 **자막에 나오면 뜰 말**, 오른쪽이 **검색어**다. `=` 를 안 쓰면 같은
말로 검색한다. 검색은 영어가 결과가 훨씬 많다.

받은 파일은 밈 폴더에 그대로 떨어지고, 같은 폴더의 `출처.txt` 에 저작자와
라이선스가 기록된다. **CC BY 는 크레딧이 의무**라 이 파일 내용을 유튜브
설명란에 붙여 넣어야 한다.

라이선스는 아래 세 가지만 받는다. 나머지는 유튜브에 올리면 문제가 된다.

    CC0 / PDM  퍼블릭 도메인. 크레딧도 필요 없음
    CC BY      크레딧만 달면 상업적 사용 가능

    ✗ CC BY-NC  '비상업'. 유튜브 수익 창출이 상업적 사용이라 못 쓴다
    ✗ CC BY-ND  '변경 금지'. 영상에 얹어 편집하는 게 변경이라 못 쓴다
    ✗ CC BY-SA  '동일 조건'. 내 영상까지 같은 라이선스로 풀어야 할 수 있다

⚠ 여기서 받는 건 **일반 이미지**지 인터넷 밈이 아니다. 방송 캡처나 유행
짤은 거의 다 저작권이 있어서 CC 로 안 풀려 있다. 폭발·불꽃·물음표처럼
연출용 소재를 모으는 용도로 쓰면 된다.

출처: Openverse (https://openverse.org) — CC 라이선스 이미지 검색, 키 불필요.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gameedit.memes import DEFAULT_ASSET_DIRS  # noqa: E402

API = "https://api.openverse.org/v1/images/"
UA = {"User-Agent": "gameedit/1.0 (meme fetcher; https://github.com/eukane/youtube-editing-)"}

# 유튜브(수익 창출 포함)에 올려도 되는 것만. 이 목록은 넓히면 안 된다.
SAFE_LICENSES = ("cc0", "pdm", "by")

# ffmpeg 로 화면에 얹을 수 있는 형식만. svg 는 못 얹는다.
USABLE = {"png", "jpg", "jpeg", "gif", "webp"}

MAX_BYTES = 8 * 1024 * 1024
CREDIT_FILE = "출처.txt"
RECORD_FILE = "출처.json"


def target_dir(explicit: str = "") -> Path:
    """어디에 받을지. 지정 없으면 폰에서 보이는 밈 폴더."""
    if explicit:
        path = Path(explicit).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    for raw in DEFAULT_ASSET_DIRS:
        path = Path(raw).expanduser()
        if path.is_dir():
            return path
    path = Path(DEFAULT_ASSET_DIRS[-1]).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def search(term: str, count: int) -> list[dict]:
    query = urllib.parse.urlencode({
        "q": term,
        "license": ",".join(SAFE_LICENSES),
        "page_size": max(count * 4, 8),      # 형식·크기로 걸러낼 걸 감안해 넉넉히
    })
    req = urllib.request.Request(f"{API}?{query}", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res).get("results", [])


def usable(item: dict) -> bool:
    """라이선스와 크기만 여기서 본다.

    형식은 검사하지 않는다. Openverse 의 `filetype` 은 비어 있을 때가 훨씬
    많아서, 그걸로 거르면 멀쩡한 그림까지 전부 버린다. 실제 형식은 받아서
    첫 바이트를 보고 판단한다 (아래 sniff).
    """
    if str(item.get("license", "")).lower() not in SAFE_LICENSES:
        return False                          # API 를 믿지 말고 한 번 더 본다
    size = item.get("filesize") or 0
    return not size or size <= MAX_BYTES


def sniff(data: bytes) -> str | None:
    """받은 파일의 진짜 형식. 확장자나 API 값은 틀릴 수 있어서 내용을 본다.

    ffmpeg 로 화면에 얹을 수 있는 것만 통과시킨다. svg 는 못 얹기 때문에
    확장자만 보고 저장하면 편집할 때가 되어서야 실패한다.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def safe_name(trigger: str) -> str:
    """파일 이름이 곧 트리거다. `,` 와 `_` 는 트리거를 나누는 글자라 못 쓴다."""
    cleaned = re.sub(r"[\\/:*?\"<>|@,_]", "", trigger).strip()
    return cleaned or "meme"


def download(url: str) -> tuple[bytes, str]:
    """내려받아서 (내용, 확장자). 쓸 수 없는 형식이면 예외."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as res:
        data = res.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"파일이 너무 큽니다 ({len(data) // 1024 // 1024}MB)")
    if len(data) < 200:
        raise ValueError("빈 파일")
    kind = sniff(data)
    if kind is None:
        raise ValueError("화면에 얹을 수 없는 형식 (svg 등)")
    return data, kind


def record(folder: Path, entries: list[dict]) -> None:
    """출처를 남긴다. CC BY 는 크레딧이 의무라 이 파일이 곧 근거다."""
    if not entries:
        return
    path = folder / RECORD_FILE
    known: list[dict] = []
    if path.exists():
        try:
            known = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            known = []
    have = {e.get("file") for e in known}
    known.extend(e for e in entries if e["file"] not in have)
    path.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [
        "이 파일들은 크리에이티브 커먼즈 라이선스 이미지입니다.",
        "CC BY 표시가 있는 것은 아래 문구를 유튜브 설명란에 넣어야 합니다.",
        "(CC0 · PDM 은 넣지 않아도 됩니다)",
        "",
    ]
    for entry in known:
        mark = "크레딧 필요" if entry["license"] == "by" else "크레딧 불필요"
        lines.append(f"[{entry['file']}]  {entry['license'].upper()} — {mark}")
        lines.append(f"  {entry['attribution']}")
        lines.append(f"  {entry['source']}")
        lines.append("")
    (folder / CREDIT_FILE).write_text("\n".join(lines), encoding="utf-8")


def fetch(term: str, trigger: str, folder: Path, count: int) -> list[dict]:
    try:
        results = search(term, count)
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        print(f"  ✗ '{term}' 검색 실패: {err}", file=sys.stderr)
        return []

    candidates = [item for item in results if usable(item)]
    if not candidates:
        print(f"  ✗ '{term}' — 쓸 수 있는 라이선스의 그림을 못 찾았습니다")
        return []

    saved: list[dict] = []
    base = safe_name(trigger)
    for item in candidates:
        if len(saved) >= count:
            break
        try:
            data, ext = download(item["url"])
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError) as err:
            print(f"  · 건너뜀 ({err})", file=sys.stderr)
            continue          # 후보를 넉넉히 받아 왔으니 다음 걸로 계속한다

        # 파일 이름이 트리거라 같은 이름을 못 쓴다. `@` 뒤는 옵션 자리라
        # 트리거에 안 섞이므로 여기에 구분자를 붙인다.
        suffix = "" if not saved else f"@{chr(ord('a') + len(saved) - 1)}"
        dest = folder / f"{base}{suffix}.{ext}"
        try:
            dest.write_bytes(data)
        except OSError as err:
            print(f"  ✗ {dest.name} 저장 실패: {err}", file=sys.stderr)
            continue
        saved.append({
            "file": dest.name,
            "trigger": trigger,
            "license": str(item.get("license", "")).lower(),
            "attribution": str(item.get("attribution", "")).strip(),
            "source": str(item.get("foreign_landing_url") or item.get("url", "")),
        })
        print(f"  ✓ {dest.name}  ({len(data) // 1024}KB, {saved[-1]['license'].upper()})"
              f"  ← 자막에 '{trigger}' 나오면 뜸")
    if not saved:
        print(f"  ✗ '{term}' — 받을 수 있는 그림이 없었습니다")
    return saved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="CC 라이선스 이미지를 받아 밈 폴더에 넣습니다",
        epilog="예) python tools/fetch_memes.py 폭발=explosion 웃음=laughing")
    parser.add_argument("terms", nargs="+", metavar="트리거=검색어")
    parser.add_argument("--count", type=int, default=1, help="트리거마다 받을 개수 (기본 1)")
    parser.add_argument("--dir", default="", help="받을 폴더 (기본: 밈 폴더)")
    args = parser.parse_args(argv)

    folder = target_dir(args.dir)
    print(f"받는 곳: {folder}\n")

    entries: list[dict] = []
    for raw in args.terms:
        trigger, _, term = raw.partition("=")
        trigger, term = trigger.strip(), (term.strip() or trigger.strip())
        if not trigger:
            continue
        print(f"'{term}' 검색 중…")
        entries.extend(fetch(term, trigger, folder, max(1, args.count)))

    record(folder, entries)
    if not entries:
        print("\n받은 게 없습니다.")
        return 1

    need_credit = [e for e in entries if e["license"] == "by"]
    print(f"\n{len(entries)}개 받았습니다.")
    print(f"출처 기록: {folder / CREDIT_FILE}")
    if need_credit:
        print(f"⚠ 이 중 {len(need_credit)}개는 CC BY 라 유튜브 설명란에 크레딧이 필요합니다.")
        print(f"  {CREDIT_FILE} 내용을 복사해서 붙여 넣으세요.")
    else:
        print("전부 퍼블릭 도메인이라 크레딧 없이 써도 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
