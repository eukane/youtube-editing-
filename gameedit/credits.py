"""완성본에 실제로 쓰인 소재의 크레딧만 뽑아 준다.

`tools/fetch_memes.py` 로 받은 그림은 폴더에 `출처.json` 이 같이 쌓인다.
그런데 받아 둔 것 전부를 설명란에 적을 필요는 없다. **이번 영상에 실제로
나온 것만** 적으면 된다.

빠뜨리는 쪽이 위험하고 남기는 쪽은 그냥 지저분한 거라, 애매하면 넣는다.
그림 밈이 하나도 안 쓰였으면 아무것도 만들지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from .memes import default_asset_dirs
from .models import EditPlan

RECORD_FILE = "출처.json"

# 크레딧을 꼭 달아야 하는 라이선스. CC0·PDM 은 안 달아도 된다.
NEEDS_CREDIT = ("by", "by-sa", "by-nc", "by-nd", "by-nc-sa", "by-nc-nd")


def load_records(dirs=None) -> dict[str, dict]:
    """밈 폴더들의 `출처.json` 을 {파일이름: 기록} 하나로 합친다."""
    folders = [Path(d) for d in dirs] if dirs else default_asset_dirs()
    out: dict[str, dict] = {}
    for folder in folders:
        path = Path(folder).expanduser() / RECORD_FILE
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            name = str((entry or {}).get("file") or "").strip()
            if name:
                out.setdefault(name, entry)
    return out


def credits_for_plan(plan: EditPlan, dirs=None) -> list[dict]:
    """이번 편집에 실제로 쓰인 것 중 크레딧이 필요한 소재."""
    records = load_records(dirs)
    if not records:
        return []
    used: list[dict] = []
    seen: set[str] = set()
    for cue in plan.memes:
        for path in (cue.asset, cue.sfx):
            name = Path(str(path)).name
            entry = records.get(name)
            if not entry or name in seen:
                continue
            if str(entry.get("license", "")).lower() not in NEEDS_CREDIT:
                continue          # 퍼블릭 도메인은 적을 필요가 없다
            seen.add(name)
            used.append(entry)
    used.sort(key=lambda e: e.get("file", ""))
    return used


def credit_text(entries: list[dict]) -> str:
    """유튜브 설명란에 그대로 붙여 넣을 글."""
    if not entries:
        return ""
    lines = ["[사용한 이미지 출처]"]
    for entry in entries:
        attribution = str(entry.get("attribution") or "").strip()
        lines.append(attribution or f"{entry.get('file')} — {entry.get('source', '')}")
    return "\n".join(lines)


def write_credits(plan: EditPlan, out_dir: str | Path, dirs=None) -> Path | None:
    """완성본 옆에 `크레딧.txt` 를 남긴다. 쓸 게 없으면 만들지 않는다.

    영상 파일과 같은 폴더에 두는 게 중요하다. 폰에서 영상을 올릴 때 바로
    옆에 있어야 설명란에 붙여 넣는 걸 잊지 않는다.
    """
    entries = credits_for_plan(plan, dirs)
    if not entries:
        return None
    path = Path(out_dir) / "크레딧.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credit_text(entries) + "\n", encoding="utf-8")
    return path
