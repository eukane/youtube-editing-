"""분석 결과 → 편집 계획(EditPlan) 조립."""

from __future__ import annotations

import time
from pathlib import Path

from .aiplan import clips_from_decision, memes_from_decision
from .config import Config
from .cost import Ledger
from .editing import apply_editing, cold_open_length, editing_summary
from .glossary import load_glossary, plan_glossary_cues
from .highlights import build_clips
from .memes import load_packs, plan_memes
from .models import Analysis, EditPlan, plan_from_dict, load_json
from .subtitles import build_subtitle_cues, resolve_highlight_words


def _noop(_msg: str) -> None:
    pass


def _ask_ai(analysis: Analysis, config: Config, ledger, *, target_seconds: float,
            wishes: str, log):
    """AI 판단을 받아 온다. 무슨 일이 있어도 Decision 을 돌려준다.

    brain 을 여기서 import 하는 이유: AI 를 안 쓰는 사람이 대부분이고,
    안 쓸 때는 urllib 도 안 불러오는 게 맞다. 그리고 brain.py 에 문제가
    생겨도 편집 자체는 돌아가야 한다.
    """
    from .brain import Decision, decide

    ai_cfg = dict(config.section("ai"))
    if not ai_cfg.get("enabled"):
        return Decision(error="AI 편집이 꺼져 있습니다")
    return decide(analysis, ai_cfg, ledger, target_seconds=target_seconds,
                  wishes=wishes, log=log)


def build_plan(analysis: Analysis, config: Config, *, ledger: Ledger | None = None,
               wishes: str = "", log=_noop) -> EditPlan:
    plan = EditPlan(source=analysis.media.path, media=analysis.media)
    # 내역서는 **항상** 있어야 한다. 부르는 쪽이 안 넘겨줬다고 해서 쓴 돈이
    # 아무 데도 안 남으면, 사용자는 얼마가 나갔는지 알 방법이 없다.
    if ledger is None:
        ledger = Ledger(limit_krw=float(config.get("ai.limit_krw", 0.0) or 0.0))
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
        target -= reserve
    hl_cfg["target_duration"] = target

    # AI 가 켜져 있으면 먼저 물어본다. **실패해도 계속 간다** — 인터넷이
    # 끊기든 잔액이 떨어지든, 편집이 멈추는 것보다 규칙 기반으로 만드는 게
    # 낫다. decide() 는 예외를 내지 않고 이유를 담아 돌려준다.
    decision = _ask_ai(analysis, config, ledger, wishes=wishes,
                       target_seconds=target, log=log)

    selected = clips_from_decision(decision, analysis, hl_cfg) if decision.used else []
    if selected:
        log(f"  · AI 가 고른 구간 {len(selected)}개를 씁니다")
    else:
        if decision.used:
            # 불렀는데 쓸 구간이 안 나온 경우. 조용히 넘어가면 왜 AI 를 켰는데
            # 결과가 같은지 알 수가 없다.
            log("  · AI 가 쓸 만한 구간을 내지 못해 소리 기준으로 고릅니다")
        selected = build_clips(analysis, hl_cfg)

    plan.clips = apply_editing(selected, analysis, edit_cfg)
    plan.relayout()

    sub_cfg = dict(config.section("subtitles"))
    meme_cfg = dict(config.section("memes"))
    ai_memes = memes_from_decision(decision, plan, meme_cfg) if decision.used else []
    if ai_memes:
        # AI 밈이 있으면 규칙 기반 밈은 그 자리를 비켜 준다. 둘 다 넣으면
        # 화면이 글자로 덮인다.
        meme_cfg["max_total"] = max(0, int(meme_cfg.get("max_total", 40)) - len(ai_memes))
    if decision.emphasis_words:
        # AI 가 고른 핵심어를 자막 강조어에 얹는다 (설정값이 이긴다)
        sub_cfg["highlight_words"] = (list(sub_cfg.get("highlight_words") or [])
                                      + decision.emphasis_words)

    memes = load_packs(meme_cfg.get("packs", []), meme_cfg.get("pack_dirs", []),
                       asset_dirs=meme_cfg.get("asset_dirs", []))
    plan.memes = sorted(plan_memes(plan, analysis, memes, meme_cfg) + ai_memes,
                        key=lambda c: c.start)
    plan.subtitles = build_subtitle_cues(plan, analysis, sub_cfg)

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
        "highlight_words": resolve_highlight_words(sub_cfg, analysis.transcript),
        "glossary_terms": len([c for c in plan.subtitles if c.speaker == "glossary"]),
        "editing": editing_summary(plan.clips, edit_cfg),
        # AI 를 썼는지, 못 썼으면 왜인지. 화면에 그대로 보여 준다 — 켰는데
        # 결과가 예전과 같으면 사용자는 이유를 알 수 없다.
        "ai": {
            "used": decision.used,
            "error": decision.error,
            "model": str(config.get("ai.model", "")) if decision.used else "",
            "clip_count": len([c for c in plan.clips if c.reason == "ai"]),
            "meme_count": len(ai_memes),
            "title": decision.title,
            "reasons": [c.label for c in plan.clips if c.reason == "ai"][:20],
        },
    }
    if ledger.steps:
        plan.meta["cost"] = ledger.as_dict()
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
