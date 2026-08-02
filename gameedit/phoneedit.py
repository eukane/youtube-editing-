"""편집 계획 ↔ 폰 화면 사이의 번역.

폰에서는 클립을 체크 해제해 빼거나 자막 오타를 고칠 수 있다. 그 결과를
편집 계획에 반영할 때 **뒤에 있던 밈·자막의 위치도 같이 당겨야** 한다.
안 그러면 클립 하나를 뺐을 때 그 뒤의 모든 자막이 어긋난다.

화면에 보낼 때는 필요한 것만 추려서 보낸다. 편집 계획 전체를 그대로
보내면 폰에서 쓰지도 않는 값 때문에 목록이 무거워진다.
"""

from __future__ import annotations

from pathlib import Path

from .media import format_timecode
from .models import EditPlan

# --------------------------------------------------------------------------
# 편집 계획 ↔ 폰 화면
# --------------------------------------------------------------------------


def plan_for_phone(plan: EditPlan) -> dict:
    clips = []
    for i, clip in enumerate(plan.clips):
        clips.append({
            "index": i,
            "label": clip.label,
            "start": round(clip.source_start, 2),
            "end": round(clip.source_end, 2),
            "start_text": format_timecode(clip.source_start),
            "duration": round(clip.duration, 1),
            "score": round(clip.score, 2),
            "out_start_text": format_timecode(clip.out_start),
        })
    subtitles = [{
        "index": i,
        "start_text": format_timecode(sub.start),
        "text": sub.text,
        "style": sub.style,
    } for i, sub in enumerate(plan.subtitles)]
    memes = [{
        "index": i,
        "start_text": format_timecode(cue.start),
        "label": cue.text or Path(cue.asset).stem or cue.meme_id,
        "meme_id": cue.meme_id,
    } for i, cue in enumerate(plan.memes)]
    return {"clips": clips, "subtitles": subtitles, "memes": memes,
            "duration_text": format_timecode(plan.duration)}


def _as_index(value) -> int | None:
    """폰에서 온 값은 문자열일 수도, 쓰레기일 수도 있다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def apply_phone_edits(plan: EditPlan, edits: dict) -> EditPlan:
    """폰에서 보낸 수정 사항을 편집 계획에 반영.

    잘못된 값이 섞여 와도 서버가 죽지 않도록 조용히 무시한다.
    """
    raw_removed = edits.get("removed_clips") or []
    if isinstance(raw_removed, (str, bytes)) or not hasattr(raw_removed, "__iter__"):
        raw_removed = []
    removed = {i for i in (_as_index(v) for v in raw_removed) if i is not None}
    if removed:
        plan.clips = [c for i, c in enumerate(plan.clips) if i not in removed]

    subtitle_edits = edits.get("subtitle_edits") or {}
    if not isinstance(subtitle_edits, dict):
        subtitle_edits = {}
    for raw_index, text in subtitle_edits.items():
        index = _as_index(raw_index)
        if index is None or text is None or not (0 <= index < len(plan.subtitles)):
            continue
        lines = [ln for ln in str(text).split("\n") if ln.strip()]
        plan.subtitles[index].lines = lines or [str(text)]

    if edits.get("drop_memes"):
        plan.memes = [c for c in plan.memes if c.meme_id == "clip_label"]

    plan.remap_cues()
    return plan


