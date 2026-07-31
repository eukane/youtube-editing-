"""하이라이트 구간 선정.

원본 전체를 1초 격자 점수판으로 만든 뒤(오디오 흥분도 + 대사 키워드 + 컷 밀도 +
말 밀도 + 웃음), 점수가 높은 봉우리부터 클립을 잘라낸다.
컷 지점은 말이 중간에 끊기지 않도록 단어 경계로 스냅한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .audio import moving_average, quantile
from .models import Analysis, Clip, merge_ranges
from .scenes import scene_burst_curve

GRID_HOP = 1.0

# 클립 제목에 쓰는 카테고리 (앞에서부터 먼저 매칭)
CATEGORY_KEYWORDS: list[tuple[str, str, list[str]]] = [
    ("death", "☠️ 사망각", ["죽었", "죽네", "죽음", "사망", "터졌", "당했", "털렸", "망했", "망함", "지네"]),
    ("win", "🏆 클리어", ["이겼", "클리어", "성공", "1등", "1위", "뚫었", "잡았", "생존", "살았"]),
    ("laugh", "😂 웃음벨", ["ㅋㅋ", "ㅎㅎ", "웃겨", "하하", "미친", "왜저래"]),
    ("shock", "😱 리액션", ["헐", "우와", "와씨", "말도 안", "설마", "실화", "소름", "지렸", "대박"]),
    ("fail", "🤦 실수", ["실패", "아 진짜", "아니 왜", "왜이래", "버그", "렉"]),
]


@dataclass
class ScoreGrid:
    hop: float
    values: list[float]
    audio: list[float]
    keyword: list[float]
    scene: list[float]
    speech: list[float]

    @property
    def duration(self) -> float:
        return len(self.values) * self.hop

    def mean(self, start: float, end: float) -> float:
        i0 = max(0, int(start / self.hop))
        i1 = min(len(self.values), max(i0 + 1, int(end / self.hop)))
        window = self.values[i0:i1]
        return sum(window) / len(window) if window else 0.0


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    top = max(values)
    if top <= 0:
        return [0.0] * len(values)
    return [v / top for v in values]


def build_score_grid(analysis: Analysis, cfg: dict) -> ScoreGrid:
    duration = max(analysis.media.duration, analysis.audio.duration, analysis.transcript.duration)
    n = max(1, int(duration / GRID_HOP) + 1)
    weights = cfg.get("weights", {})

    audio = [analysis.audio.mean_between(i * GRID_HOP, (i + 1) * GRID_HOP) for i in range(n)]

    keywords = [k for k in (cfg.get("keywords") or []) if k]
    laugh_tokens = [k for k in (cfg.get("laughter_tokens") or []) if k]
    keyword_hits = [0.0] * n
    speech = [0.0] * n

    for seg in analysis.transcript.segments:
        text = seg.text
        lowered = text.lower()
        hits = sum(1 for kw in keywords if kw.lower() in lowered)
        laughs = sum(lowered.count(tok.lower()) for tok in laugh_tokens)
        # 감탄 부호도 텐션 신호
        excl = len(re.findall(r"[!?]{1,}", text))
        i0 = max(0, int(seg.start / GRID_HOP))
        i1 = min(n, max(i0 + 1, int(seg.end / GRID_HOP) + 1))
        if hits or laughs or excl:
            value = min(3.0, hits + 1.2 * min(laughs, 3) + 0.4 * min(excl, 3))
            for i in range(i0, i1):
                keyword_hits[i] += value
            # 키워드 직후 리액션까지 커버
            for i in range(i1, min(n, i1 + 3)):
                keyword_hits[i] += value * 0.4
        for i in range(i0, i1):
            speech[i] += 1.0

    scene = scene_burst_curve(analysis.scenes, duration, hop=GRID_HOP)
    if len(scene) < n:
        scene += [0.0] * (n - len(scene))

    keyword_norm = _normalize(keyword_hits)
    speech_norm = [min(1.0, v) for v in speech]

    values: list[float] = []
    for i in range(n):
        score = (
            weights.get("audio", 1.0) * audio[i]
            + weights.get("keyword", 1.3) * keyword_norm[i]
            + weights.get("scene", 0.6) * scene[i]
            + weights.get("speech", 0.35) * speech_norm[i]
        )
        values.append(score)

    # 웃음은 별도 가중 (전사에 웃음 표기가 있을 때만 의미)
    laugh_weight = weights.get("laughter", 1.0)
    if laugh_tokens and laugh_weight:
        for seg in analysis.transcript.segments:
            if any(tok in seg.text for tok in laugh_tokens):
                i0 = max(0, int(seg.start / GRID_HOP))
                i1 = min(n, max(i0 + 1, int(seg.end / GRID_HOP) + 1))
                for i in range(i0, i1):
                    values[i] += laugh_weight * 0.5

    values = moving_average(values, 3)

    for entry in cfg.get("boost_ranges") or []:
        if len(entry) < 2:
            continue
        start, end = float(entry[0]), float(entry[1])
        weight = float(entry[2]) if len(entry) > 2 else 1.0
        for i in range(max(0, int(start)), min(n, int(end) + 1)):
            values[i] += weight

    for entry in cfg.get("exclude_ranges") or []:
        if len(entry) < 2:
            continue
        start, end = float(entry[0]), float(entry[1])
        for i in range(max(0, int(start)), min(n, int(end) + 1)):
            values[i] = -1.0

    return ScoreGrid(hop=GRID_HOP, values=values, audio=audio, keyword=keyword_norm,
                     scene=scene, speech=speech_norm)


# --------------------------------------------------------------------------
# 구간 선택
# --------------------------------------------------------------------------


def _grow_window(values: list[float], seed: int, hop: float, *, floor: float,
                 min_len: float, max_len: float) -> tuple[float, float]:
    left = right = seed
    n = len(values)
    max_steps = int(max_len / hop)
    while (right - left) < max_steps:
        can_left = left > 0 and values[left - 1] >= floor
        can_right = right < n - 1 and values[right + 1] >= floor
        if not can_left and not can_right:
            break
        if can_right and (not can_left or values[right + 1] >= values[left - 1]):
            right += 1
        else:
            left -= 1
    start, end = left * hop, (right + 1) * hop
    if end - start < min_len:  # 너무 짧으면 앞뒤로 균등 확장
        need = min_len - (end - start)
        start = max(0.0, start - need / 2)
        end = min(n * hop, start + min_len)
    return start, end


def select_ranges(grid: ScoreGrid, cfg: dict) -> list[tuple[float, float, float]]:
    """(start, end, score) 목록을 점수 높은 순으로 고른다."""
    values = list(grid.values)
    hop = grid.hop
    target = float(cfg.get("target_duration", 480.0))
    min_clip = float(cfg.get("min_clip", 6.0))
    max_clip = float(cfg.get("max_clip", 45.0))
    max_clips = int(cfg.get("max_clips", 40))

    positive = [v for v in values if v > 0]
    if not positive:
        return []
    floor = max(quantile(positive, 0.55), 0.15)

    chosen: list[tuple[float, float, float]] = []
    total = 0.0
    while total < target and len(chosen) < max_clips:
        seed = max(range(len(values)), key=lambda i: values[i])
        if values[seed] < floor:
            break
        # 봉우리 높이의 절반까지만 확장한다. 전역 하한만 쓰면 잔잔한 구간까지
        # 계속 먹어들어가 클립이 전부 max_clip 길이로 늘어난다.
        grow_floor = max(floor * 0.9, values[seed] * 0.45)
        start, end = _grow_window(values, seed, hop, floor=grow_floor,
                                  min_len=min_clip, max_len=max_clip)
        score = grid.mean(start, end)
        chosen.append((start, end, score))
        total += end - start
        # 선택된 구간과 주변을 후보에서 제거
        i0 = max(0, int(start / hop) - 1)
        i1 = min(len(values), int(end / hop) + 2)
        for i in range(i0, i1):
            values[i] = -1.0
    return chosen


def _snap_to_words(start: float, end: float, word_bounds: list[tuple[float, float]],
                   window: float) -> tuple[float, float]:
    """말이 잘리지 않도록 컷 지점을 단어 경계로 밀어준다."""
    if not word_bounds:
        return start, end
    # 시작점이 어떤 단어의 중간이라면 그 단어의 시작으로 당긴다
    for w_start, w_end in word_bounds:
        if w_start < start < w_end and (start - w_start) <= window:
            start = w_start
            break
    for w_start, w_end in word_bounds:
        if w_start < end < w_end and (w_end - end) <= window:
            end = w_end
    return start, end


def cut_internal_silence(ranges: list[tuple[float, float]],
                         silences: list[tuple[float, float]],
                         *, min_silence: float, keep: float = 0.35,
                         min_piece: float = 2.0) -> list[tuple[float, float]]:
    """클립 안에 길게 남은 정적을 잘라내 여러 조각으로 나눈다.

    (합쳐진 하이라이트 사이에 10초씩 아무 말 없는 구간이 남으면 편집본이 늘어진다)
    """
    if min_silence <= 0 or not silences:
        return list(ranges)
    long_silences = [(s, e) for s, e in silences if e - s >= min_silence]
    if not long_silences:
        return list(ranges)

    out: list[tuple[float, float]] = []
    for start, end in ranges:
        pieces = [(start, end)]
        for s_start, s_end in long_silences:
            next_pieces: list[tuple[float, float]] = []
            for p_start, p_end in pieces:
                if s_end <= p_start or s_start >= p_end:
                    next_pieces.append((p_start, p_end))
                    continue
                left = (p_start, min(p_end, s_start + keep))
                right = (max(p_start, s_end - keep), p_end)
                if left[1] - left[0] >= min_piece:
                    next_pieces.append(left)
                if right[1] - right[0] >= min_piece:
                    next_pieces.append(right)
            pieces = next_pieces
        out.extend(pieces)
    out.sort()
    return out


def _trim_silence_edges(start: float, end: float, silences: list[tuple[float, float]],
                        *, keep: float = 0.35) -> tuple[float, float]:
    for s_start, s_end in silences:
        if s_start <= start < s_end and s_end < end:  # 앞쪽 정적
            start = min(end - 1.0, s_end - keep)
        if s_start < end <= s_end and s_start > start:  # 뒤쪽 정적
            end = max(start + 1.0, s_start + keep)
    return start, end


# 내용과 무관한 자동 번호. 목록에서 고를 때는 쓸모 있지만 완성본 화면에
# 얹으면 편집본이 아니라 검수용 영상처럼 보인다.
GENERIC_LABEL_PREFIXES = ("🔥 하이라이트", "🎬 구간", "🎬 전체")


def is_generic_label(label: str) -> bool:
    return (label or "").startswith(GENERIC_LABEL_PREFIXES)


def label_for(text: str, index: int, categories=None) -> tuple[str, str]:
    lowered = (text or "").lower()
    for key, label, words in (categories or CATEGORY_KEYWORDS):
        if any(w.lower() in lowered for w in words):
            return key, label
    return "highlight", f"🔥 하이라이트 {index}"


def fallback_clips(duration: float, cfg: dict) -> list[Clip]:
    """점수로 고를 게 하나도 없을 때 균등 간격으로라도 잘라 준다.

    소리가 없거나(마이크 없이 녹화), 전부 조용하거나, 영상이 min_clip 보다 짧으면
    점수판이 통째로 0이 된다. 그렇다고 결과물을 아예 못 만들어 주는 것보다는
    고르게 잘라 주고 사실을 알려주는 편이 낫다.
    """
    if duration <= 0:
        return []
    min_clip = float(cfg.get("min_clip", 6.0))
    max_clip = float(cfg.get("max_clip", 45.0))
    target = min(float(cfg.get("target_duration", 480.0)), duration)

    # 통째로 쓰는 게 자연스러운 경우 (짧은 영상, 목표 길이가 원본과 비슷한 경우)
    if duration <= max(min_clip, 12.0) or target >= duration * 0.9:
        return [Clip(source_start=0.0, source_end=round(duration, 3), reason="fallback",
                     label="🎬 전체")]

    clip_len = min(max(target / 6.0, min_clip), max_clip, duration)
    count = max(1, min(int(round(target / clip_len)), int(duration // clip_len)))
    step = duration / count

    clips: list[Clip] = []
    for i in range(count):
        center = step * (i + 0.5)
        start = min(max(0.0, center - clip_len / 2), duration - clip_len)
        clips.append(Clip(source_start=round(start, 3),
                          source_end=round(start + clip_len, 3),
                          reason="fallback", label=f"🎬 구간 {i + 1}"))
    return clips


def build_clips(analysis: Analysis, cfg: dict) -> list[Clip]:
    grid = build_score_grid(analysis, cfg)
    ranges = select_ranges(grid, cfg)
    # 격자는 항상 한 칸 이상이라 grid.duration 을 그대로 쓰면 길이 0 인 영상도 1초가 된다
    duration = max(analysis.media.duration, analysis.audio.duration,
                   analysis.transcript.duration)
    if duration <= 0:
        return []

    pad_before = float(cfg.get("pad_before", 1.5))
    pad_after = float(cfg.get("pad_after", 1.2))
    merge_gap = float(cfg.get("merge_gap", 3.0))
    min_clip = float(cfg.get("min_clip", 6.0))
    max_clip = float(cfg.get("max_clip", 45.0))
    target = float(cfg.get("target_duration", 480.0))

    padded: list[tuple[float, float]] = []
    for start, end, _score in ranges:
        padded.append((max(0.0, start - pad_before), min(duration, end + pad_after)))

    forced: list[tuple[float, float]] = []
    for entry in cfg.get("must_include_ranges") or []:
        if len(entry) >= 2:
            forced.append((float(entry[0]), float(entry[1])))
    padded.extend(forced)

    merged = merge_ranges(padded, gap=merge_gap)
    merged = cut_internal_silence(
        merged, analysis.audio.silences,
        min_silence=float(cfg.get("cut_internal_silence", 2.5)),
        min_piece=max(2.0, min_clip * 0.6),
    )

    # 병합으로 너무 길어진 구간은 max_clip 기준으로 분할
    limited: list[tuple[float, float]] = []
    for start, end in merged:
        span = end - start
        if span <= max_clip * 1.15:
            limited.append((start, end))
            continue
        pieces = max(2, int(span // max_clip) + 1)
        step = span / pieces
        for i in range(pieces):
            limited.append((start + i * step, start + (i + 1) * step))

    word_bounds = [(w.start, w.end) for w in analysis.transcript.words()]
    silences = analysis.audio.silences
    snap = bool(cfg.get("snap_to_speech", True))
    snap_window = float(cfg.get("snap_window", 1.8))
    drop_tail = bool(cfg.get("drop_silence_tail", True))

    clips: list[Clip] = []
    for start, end in limited:
        if drop_tail:
            start, end = _trim_silence_edges(start, end, silences)
        if snap:
            start, end = _snap_to_words(start, end, word_bounds, snap_window)
        start = max(0.0, start)
        end = min(duration, end)
        if end - start < min_clip * 0.6:
            continue
        score = grid.mean(start, end)
        pinned = any(f_end > start and f_start < end for f_start, f_end in forced)
        clips.append(Clip(source_start=round(start, 3), source_end=round(end, 3),
                          score=round(score, 4), reason="manual" if pinned else "auto"))

    # 목표 길이 초과 시 점수 낮은 클립부터 제거 (수동 지정 구간은 항상 유지)
    clips.sort(key=lambda c: (c.reason == "manual", c.score), reverse=True)
    kept: list[Clip] = []
    total = 0.0
    for clip in clips:
        if total >= target and kept and clip.reason != "manual":
            continue
        kept.append(clip)
        total += clip.duration
    kept.sort(key=lambda c: c.source_start)

    if not kept:
        return fallback_clips(duration, cfg)

    peaks = [t for t, _ in analysis.audio.peaks]
    for i, clip in enumerate(kept, start=1):
        text = analysis.transcript.text_between(clip.source_start, clip.source_end)
        key, label = label_for(text, i)
        clip.label = label
        if clip.reason != "manual":
            clip.reason = key
        if any(clip.contains_source(t) for t in peaks):
            clip.effects.append("punch")
    return kept
