"""용어 사전 — 대사에 나온 이름에 작은 설명 자막을 붙인다.

"로토무가 나왔다" 라고 말하면 화면 위쪽에 작게 `로토무 · 전기/고스트` 를
띄우는 기능. 시청자가 모르는 이름이 나올 때 편집자가 넣어 주는 그 자막이다.

**편집 중에는 인터넷을 쓰지 않는다.** 미리 한 번 받아서 파일로 저장해 두고,
편집할 때는 그 파일만 읽는다. 이유는 세 가지다.

  · 폰이 오프라인일 수 있다
  · 편집 도중 수백 번 요청하면 느리고, 한 번 실패하면 편집이 통째로 멈춘다
  · 같은 영상을 다시 만들 때마다 결과가 달라지면 안 된다

사전 파일은 그냥 JSON 이라 직접 만들거나 고쳐도 된다.

    {"로토무": "전기 · 고스트", "우리 길드": "3년째 같이 하는 사람들"}
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Analysis, EditPlan, SubtitleCue

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "assets" / "glossary"


def load_glossary(paths=None) -> dict[str, str]:
    """사전 파일들을 합쳐서 {용어: 설명} 하나로."""
    out: dict[str, str] = {}
    candidates: list[Path] = []
    for raw in _as_list(paths):
        p = Path(raw).expanduser()
        candidates.append(p if p.exists() else BUILTIN_DIR / raw)
    for path in candidates:
        if path.is_dir():
            candidates.extend(sorted(path.glob("*.json")))
            continue
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(data, dict):
            for term, info in data.items():
                term = str(term).strip()
                if len(term) >= 2 and str(info).strip():
                    out.setdefault(term, str(info).strip())
    return out


def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def find_terms(text: str, glossary: dict[str, str]) -> list[str]:
    """한 줄에서 사전에 있는 말을 찾는다. 긴 것부터 (겹치면 긴 쪽만)."""
    found: list[str] = []
    for term in sorted(glossary, key=len, reverse=True):
        if term in text and not any(term in kept for kept in found):
            found.append(term)
    return found


def plan_glossary_cues(plan: EditPlan, analysis: Analysis, glossary: dict[str, str],
                       cfg: dict) -> list[SubtitleCue]:
    """대사에 나온 용어마다 작은 설명 자막을 만든다.

    같은 용어를 볼 때마다 띄우면 도배가 되므로, 한 번 설명한 용어는
    한동안(cooldown) 다시 띄우지 않는다.
    """
    if not glossary or not cfg.get("enabled", True):
        return []

    duration = float(cfg.get("duration", 2.4))
    cooldown = float(cfg.get("cooldown", 90.0))
    once = bool(cfg.get("once_per_term", False))
    limit = int(cfg.get("max_total", 40))
    template = str(cfg.get("format", "{term} · {info}"))

    last_shown: dict[str, float] = {}
    cues: list[SubtitleCue] = []

    for seg in analysis.transcript.segments:
        if len(cues) >= limit:
            break
        for term in find_terms(seg.text or "", glossary):
            if term in last_shown:
                if once or seg.start - last_shown[term] < cooldown:
                    continue
            starts = plan.map_all_times(seg.start)
            if not starts:
                continue
            last_shown[term] = seg.start
            out_start = starts[0]
            cues.append(SubtitleCue(
                start=round(out_start, 3),
                end=round(out_start + duration, 3),
                lines=[template.format(term=term, info=glossary[term])],
                style="Narr",
                speaker="glossary",
                source_start=round(seg.start, 3),
            ))
            break          # 한 문장에 하나만. 여러 개면 화면이 복잡해진다

    cues.sort(key=lambda c: c.start)
    return cues
