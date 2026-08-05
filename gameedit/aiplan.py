"""AI 의 판단(brain.Decision) → 실제 편집 재료(Clip · MemeCue · 강조어).

brain.py 는 "몇 초부터 몇 초까지가 왜 재밌는지" 만 말한다. 그걸 편집본으로
만드는 건 기존 코드(editing → subtitles → render)가 그대로 한다. 이 파일은
그 사이를 잇는 얇은 층이다.

**AI 가 낸 값을 그대로 믿지 않는다.** 시간이 영상 밖이거나, 구간이 겹치거나,
목표 길이를 두 배로 넘기는 답이 실제로 온다. 규칙 기반 경로(highlights.py)가
받는 것과 똑같은 검사를 여기서도 거치게 해서, AI 를 켰다고 갑자기 이상한
결과물이 나오는 일이 없게 한다.

그래서 이 파일의 함수들은 **어떤 입력에도 예외를 내지 않는다.** AI 가
쓰레기를 보내면 빈 목록이 나오고, 부르는 쪽은 규칙 기반으로 돌아간다.
"""

from __future__ import annotations

from .highlights import CATEGORY_KEYWORDS, fit_to_target, snap_clip_edges
from .models import Analysis, Clip, EditPlan, MemeCue

# AI 가 준 이유(why)를 클립 이름으로 쓸 때의 길이 제한. 검수 화면에 한 줄로
# 들어가야 한다.
LABEL_MAX = 42

# 밈 하나가 화면에 떠 있는 시간
MEME_SECONDS = 1.8


def _label_for(why: str) -> str:
    """AI 가 적은 이유 → 검수 화면에 쓸 짧은 이름.

    규칙 기반 경로는 "☠️ 사망각" 처럼 분류 이름을 붙인다. 같은 자리에
    AI 의 이유를 넣으면 왜 골랐는지가 그대로 보인다 — 이게 AI 를 쓰는 이유의
    절반이다. 다만 분류 그림문자는 앞에 붙여서 목록을 훑기 쉽게 둔다.
    """
    why = " ".join(str(why or "").split())
    if not why:
        return "🤖 AI 선택"
    for _key, name, words in CATEGORY_KEYWORDS:
        if any(word in why for word in words):
            icon = name.split(" ", 1)[0]
            return f"{icon} {why[:LABEL_MAX]}"
    return f"🤖 {why[:LABEL_MAX]}"


def clips_from_decision(decision, analysis: Analysis, cfg: dict) -> list[Clip]:
    """AI 가 고른 구간 → Clip 목록.

    규칙 기반과 같은 뒤처리를 거친다: 영상 밖 잘라내기 → 겹침 정리 →
    말 경계 스냅 → 목표 길이 맞추기. AI 는 초 단위로 대충 답하기 때문에
    스냅이 없으면 말이 중간에 끊긴 채로 시작한다.
    """
    duration = max(analysis.media.duration, analysis.audio.duration,
                   analysis.transcript.duration)
    if duration <= 0:
        return []

    min_clip = float(cfg.get("min_clip", 6.0))
    max_clip = float(cfg.get("max_clip", 45.0))

    raw: list[tuple[float, float, float, str]] = []
    for item in getattr(decision, "highlights", []) or []:
        try:
            start = max(0.0, float(item["start"]))
            end = min(duration, float(item["end"]))
            score = float(item.get("score", 0.5) or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < min_clip * 0.5:
            continue
        # 너무 긴 구간은 규칙 기반과 같은 기준으로 자른다. AI 는 가끔
        # "2분짜리가 통째로 재밌다" 고 답하는데 그건 편집이 아니다.
        if end - start > max_clip * 1.15:
            end = start + max_clip
        raw.append((start, end, max(0.0, min(1.0, score)), str(item.get("why", ""))))

    if not raw:
        return []

    # 겹치면 뒤엣것을 밀어낸다. 겹친 채로 두면 같은 장면이 두 번 나온다.
    raw.sort(key=lambda r: r[0])
    trimmed: list[tuple[float, float, float, str]] = []
    for start, end, score, why in raw:
        if trimmed and start < trimmed[-1][1]:
            start = trimmed[-1][1]
            if end - start < min_clip * 0.5:
                continue
        trimmed.append((start, end, score, why))

    word_bounds = [(w.start, w.end) for w in analysis.transcript.words()]
    snap = bool(cfg.get("snap_to_speech", True))
    snap_window = float(cfg.get("snap_window", 1.8))

    clips: list[Clip] = []
    for start, end, score, why in trimmed:
        if snap:
            start, end = snap_clip_edges(start, end, word_bounds, snap_window)
        start, end = max(0.0, start), min(duration, end)
        if end - start < min_clip * 0.5:
            continue
        clips.append(Clip(source_start=round(start, 3), source_end=round(end, 3),
                          score=round(score, 4), reason="ai", label=_label_for(why)))

    return fit_to_target(clips, cfg)


def memes_from_decision(decision, plan: EditPlan, cfg: dict) -> list[MemeCue]:
    """AI 가 정한 밈 문구 → 결과물 타임라인 위의 밈.

    AI 는 **원본** 시각으로 답한다. 잘려 나간 구간에 찍힌 밈은 결과물에
    자리가 없으므로 버린다 — 억지로 가까운 곳에 옮기면 엉뚱한 장면에
    붙는다. 그게 지금 규칙 기반에서 밈이 어색하던 이유다.
    """
    if not cfg.get("enabled", True):
        return []
    limit = int(cfg.get("max_total", 40) or 0)
    duration = MEME_SECONDS

    cues: list[MemeCue] = []
    for item in getattr(decision, "memes", []) or []:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        try:
            at = float(item["at"])
        except (KeyError, TypeError, ValueError):
            continue
        clip = next((c for c in plan.clips if c.contains_source(at)), None)
        if clip is None:
            continue                      # 잘려 나간 구간이다
        big = bool(item.get("big"))
        cues.append(MemeCue(
            start=round(clip.to_out(at), 3),
            duration=duration,
            meme_id="ai",
            kind="text",
            text=text[:24],
            style="MemeCenter" if big else "MemeTop",
            placement="center" if big else "top",
            trigger="ai",
            source_start=at,
            priority=2.0,                 # 자리 다툼에서 규칙 기반 밈을 이긴다
        ))

    cues.sort(key=lambda c: c.start)
    # 같은 자리에 몰리면 읽을 수가 없다. 최소 간격을 둔다.
    spaced: list[MemeCue] = []
    for cue in cues:
        if spaced and cue.start - spaced[-1].start < duration:
            continue
        spaced.append(cue)
    return spaced[:limit] if limit > 0 else spaced
