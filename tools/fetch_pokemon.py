#!/usr/bin/env python3
"""포켓몬 이름·타입 사전을 만든다 (인터넷에서 한 번만).

    python tools/fetch_pokemon.py

결과: assets/glossary/pokemon.json  →  {"로토무": "전기 · 고스트", ...}

편집할 때는 이 파일만 읽는다. 편집 중에 인터넷을 쓰면 폰이 오프라인이거나
느릴 때 편집이 통째로 실패하기 때문에, 받아 두는 일과 쓰는 일을 분리했다.

출처: PokéAPI (https://pokeapi.co) — 공개 API, 키 없이 사용 가능.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "glossary" / "pokemon.json"
API = "https://pokeapi.co/api/v2"
UA = {"User-Agent": "gameedit/1.0 (glossary builder; https://github.com/eukane/youtube-editing-)"}


def get(url: str, *, retries: int = 3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.load(res)
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
            print(f"    · 재시도 {attempt + 2}/{retries} ({err})", file=sys.stderr)
    return None


def korean(entry: dict, key: str = "names") -> str:
    for name in entry.get(key, []):
        if name.get("language", {}).get("name") == "ko":
            return name.get("name", "")
    return ""


def fetch_types() -> dict[str, str]:
    """타입 영문 이름 → 한국어."""
    print("타입 이름 받는 중…")
    out: dict[str, str] = {}
    for item in get(f"{API}/type/?limit=100")["results"]:
        data = get(item["url"])
        ko = korean(data)
        if ko:
            out[data["name"]] = ko
    return out


def main() -> int:
    types = fetch_types()
    print(f"  타입 {len(types)}종")

    total = get(f"{API}/pokemon-species/?limit=1")["count"]
    print(f"포켓몬 {total}마리 받는 중… (몇 분 걸립니다)")

    glossary: dict[str, str] = {}
    skipped = 0
    for i in range(1, total + 1):
        try:
            species = get(f"{API}/pokemon-species/{i}/")
            name = korean(species)
            if not name or len(name) < 2:
                skipped += 1          # 한 글자 이름은 아무 데나 걸려서 제외
                continue
            mon = get(f"{API}/pokemon/{i}/")
            kinds = [types.get(t["type"]["name"], t["type"]["name"]) for t in mon["types"]]
            glossary[name] = " · ".join(kinds)
        except Exception as err:                      # 하나 실패해도 전체는 계속
            skipped += 1
            print(f"  ⚠ {i}번 건너뜀 ({err})", file=sys.stderr)
        if i % 100 == 0:
            print(f"  {i}/{total}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(glossary, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    print(f"\n저장: {OUT}  ({len(glossary)}개, 건너뜀 {skipped}개)")
    sample = list(glossary.items())[:5]
    for term, info in sample:
        print(f"  {term} → {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
