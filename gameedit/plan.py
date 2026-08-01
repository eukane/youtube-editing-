"""분석 결과 → 편집 계획(EditPlan) 조립."""

from __future__ import annotations

import time
from pathlib import Path

from .config import Config
from .editing import apply_editing, cold_open_length, editing_summary
from .glossary import load_glossary, plan_glossary_cues
from .highlights import build_clips
from .memes import load_packs, plan_memes
from .models import Analysis, EditPlan, plan_from_dict, load_json
from .subtitles import build_subtitle_cues, resolve_highlight_words


def build_plan(analysis: Analysis, config: Config) -> EditPlan:
    plan = EditPlan(source=analysis.media.path, media=analysis.media)
    edit_cfg = config.section("editing")
    # 1단계: 어디를 쓸지 고른다. 2단계: 그걸 편집본으로 만든다.
    #
    # 도입부는 본편 장면을 한 번 더 보여 주는 것이라 완성본이 그만큼 길어진다.
    # 고르는 단계에서 미리 자리를 비워 두지 않으면 화면에서 고른 길이를
    # 매번 초과한다 (실측 +5.0초, 30초 목표에서는 +17%).
    hl_cfg = dict(config.section("highlight"))
    reserve = cold_open_length(edit_cfg)
    target = float(hl_cfg.get("target_duration", 480.0))
    if reserve > 0 and target > reserve * 2:
        hl_cfg["target_duration"] = target - reserve
    selected = build_clips(analysis, hl_cfg)
    plan.clips = apply_editing(selected, analysis, edit_cfg)
    plan.relayout()

    meme_cfg = config.section("memes")
    memes = load_packs(meme_cfg.get("packs", []), meme_cfg.get("pack_dirs", []),
                       asset_dirs=meme_cfg.get("asset_dirs", []))
    plan.memes = plan_memes(plan, analysis, memes, meme_cfg)
    plan.subtitles = build_subtitle_cues(plan, analysis, config.section("subtitles"))

    # 용어 설명 자막 (포켓몬 타입 등). 미리 받아 둔 사전 파일에서만 읽는다.
    gloss_cfg = config.section("glossary")
    glossary = load_glossary(gloss_cfg.get("files", [])) if gloss_cfg.get("enabled") else {}
    if glossary:
        plan.subtitles.extend(plan_glossary_cues(plan, analysis, glossary, gloss_cfg))
        plan.subtitles.sort(key=lambda c: c.start)

    source_duration = analysis.media.duration or 1.0
    plan.meta = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_duration": round(source_duration, 2),
        "output_duration": round(plan.duration, 2),
        "compression": round(plan.duration / source_duration, 4),
        "clip_count": len(plan.clips),
        "meme_count": len(plan.memes),
        "subtitle_count": len(plan.subtitles),
        "meme_packs": list(meme_cfg.get("packs", [])),
        "language": analysis.transcript.language,
        "fallback": bool(plan.clips) and all(c.reason == "fallback" for c in plan.clips),
        "selected_count": len(selected),
        # 자동으로 고른 강조 단어. 렌더 단계에서 색을 칠할 때 쓰고,
        # plan.json 에 남으니 사람이 보고 고칠 수도 있다.
        "highlight_words": resolve_highlight_words(config.section("subtitles"),
                                                   analysis.transcript),
        "glossary_terms": len([c for c in plan.subtitles if c.speaker == "glossary"]),
        "editing": editing_summary(plan.clips, edit_cfg),
    }
    return plan


def load_plan(path: str | Path, *, log=None) -> EditPlan:
    """저장된 편집 계획을 읽는다. 사람이 고친 파일이라 항상 검사한다."""
    plan = plan_from_dict(load_json(path))
    problems = plan.sanitize()
    if problems and log:
        log(f"⚠ plan.json 에서 이상한 값 {len(problems)}건을 고쳤습니다:")
        for problem in problems[:8]:
            log(f"   · {problem}")
        if len(problems) > 8:
            log(f"   · … 외 {len(problems) - 8}건")
    plan.remap_cues()
    return plan
