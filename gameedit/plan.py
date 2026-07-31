"""분석 결과 → 편집 계획(EditPlan) 조립."""

from __future__ import annotations

import time
from pathlib import Path

from .config import Config
from .editing import apply_editing, editing_summary
from .highlights import build_clips
from .memes import load_packs, plan_memes
from .models import Analysis, EditPlan, plan_from_dict, load_json
from .subtitles import build_subtitle_cues, resolve_highlight_words


def build_plan(analysis: Analysis, config: Config) -> EditPlan:
    plan = EditPlan(source=analysis.media.path, media=analysis.media)
    edit_cfg = config.section("editing")
    # 1단계: 어디를 쓸지 고른다. 2단계: 그걸 편집본으로 만든다.
    selected = build_clips(analysis, config.section("highlight"))
    plan.clips = apply_editing(selected, analysis, edit_cfg)
    plan.relayout()

    meme_cfg = config.section("memes")
    memes = load_packs(meme_cfg.get("packs", []), meme_cfg.get("pack_dirs", []),
                       asset_dirs=meme_cfg.get("asset_dirs", []))
    plan.memes = plan_memes(plan, analysis, memes, meme_cfg)
    plan.subtitles = build_subtitle_cues(plan, analysis, config.section("subtitles"))

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
