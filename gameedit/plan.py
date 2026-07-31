"""분석 결과 → 편집 계획(EditPlan) 조립."""

from __future__ import annotations

import time
from pathlib import Path

from .config import Config
from .highlights import build_clips
from .memes import load_packs, plan_memes
from .models import Analysis, EditPlan, plan_from_dict, load_json
from .subtitles import build_subtitle_cues


def build_plan(analysis: Analysis, config: Config) -> EditPlan:
    plan = EditPlan(source=analysis.media.path, media=analysis.media)
    plan.clips = build_clips(analysis, config.section("highlight"))
    plan.relayout()

    meme_cfg = config.section("memes")
    memes = load_packs(meme_cfg.get("packs", []), meme_cfg.get("pack_dirs", []))
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
    }
    return plan


def load_plan(path: str | Path) -> EditPlan:
    plan = plan_from_dict(load_json(path))
    plan.relayout()
    return plan
