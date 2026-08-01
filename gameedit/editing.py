"""고른 구간을 '편집'으로 바꾸는 단계.

highlights.py 가 **어디를 쓸지** 고른다면, 여기서는 **어떻게 붙일지**를 정한다.
장면을 이어 붙이기만 하면 검수용 영상이지 편집본이 아니다. 이 장르(게임 실황
하이라이트)의 편집본이 갖는 성질을 규칙으로 옮겼다.

  1. 죽은 시간이 없다
     말과 말 사이가 0.5초만 벌어져도 잘라 붙인다(점프컷). 실황 편집에서
     가장 크게 체감되는 차이가 이것이다.

  2. 첫 3초에 제일 센 장면이 온다
     시청자는 앞부분에서 이탈한다. 그래서 클라이맥스를 맨 앞에 한 번 보여
     주고(콜드오픈) 본편으로 들어간다.

  3. 지루하지만 필요한 구간은 자르지 않고 빨리 감는다
     이동·정비 구간을 통째로 자르면 맥락이 끊긴다. 2~4배속으로 넘긴다.

  4. 끝은 가장 센 장면 근처에서 맺는다
     조용한 구간에서 끝나면 다 보고도 심심하게 남는다.

전부 config 의 editing 섹션으로 끌 수 있다.
"""

from __future__ import annotations

from .models import Analysis, Clip

# 새로 만든 조각이 물려받으면 안 되는 표시
_ONE_SHOT_EFFECTS = {"coldopen"}


def _overlapping_silences(silences, start: float, end: float, min_len: float):
    out = []
    for s, e in silences:
        s2, e2 = max(s, start), min(e, end)
        if e2 - s2 >= min_len:
            out.append((s2, e2))
    out.sort()
    return out


def split_dead_air(clip: Clip, silences, *, min_silence: float, keep: float,
                   min_piece: float) -> list[Clip]:
    """클립 하나를 무음 기준으로 잘라 여러 조각으로 만든다 (점프컷).

    잘라낸 자리에 keep 초씩 남겨서 말꼬리가 뭉개지지 않게 한다.
    """
    gaps = _overlapping_silences(silences, clip.source_start, clip.source_end, min_silence)
    if not gaps:
        return [clip]

    pieces: list[tuple[float, float]] = []
    cursor = clip.source_start
    for g_start, g_end in gaps:
        piece_end = min(g_start + keep, clip.source_end)
        if piece_end - cursor >= min_piece:
            pieces.append((cursor, piece_end))
        cursor = max(cursor, g_end - keep)
    if clip.source_end - cursor >= min_piece:
        pieces.append((cursor, clip.source_end))

    if not pieces:
        return [clip]          # 전부 무음이면 손대지 않는다

    out: list[Clip] = []
    for i, (start, end) in enumerate(pieces):
        effects = [e for e in clip.effects if e not in _ONE_SHOT_EFFECTS]
        out.append(Clip(source_start=round(start, 3), source_end=round(end, 3),
                        score=clip.score, reason=clip.reason,
                        label=clip.label if i == 0 else "",
                        effects=effects, speed=clip.speed))
    return out


def remove_dead_air(clips: list[Clip], silences, cfg: dict) -> list[Clip]:
    """전체 클립에 점프컷을 적용한다.

    조각이 너무 많아지면 렌더 필터그래프가 감당을 못 하므로(폰에서 특히),
    기준을 완화해 가며 조각 수를 max_pieces 이하로 맞춘다.
    """
    if not cfg.get("dead_air", True) or not silences:
        return list(clips)

    min_silence = float(cfg.get("dead_air_min", 0.5))
    keep = float(cfg.get("dead_air_keep", 0.12))
    min_piece = float(cfg.get("dead_air_min_piece", 0.8))
    max_pieces = int(cfg.get("max_pieces", 400))
    if min_silence <= 0:
        return list(clips)

    candidates: list[tuple[float, float, float]] = []
    for clip in clips:
        for start, end in _overlapping_silences(silences, clip.source_start,
                                                clip.source_end, min_silence):
            candidates.append((end - start, start, end))
    if not candidates:
        return list(clips)

    # 상한에 걸리면 기준을 올려 아무것도 안 자르는 게 아니라, **가장 긴 정적부터**
    # 잘라낸다. 제일 늘어지는 자리가 먼저 사라져야 편집본다워진다.
    budget = max_pieces - len(clips)
    if budget <= 0:
        return list(clips)
    gaps = sorted((s, e) for _, s, e in sorted(candidates, reverse=True)[:budget])

    out: list[Clip] = []
    for clip in clips:
        out.extend(split_dead_air(clip, gaps, min_silence=min_silence,
                                  keep=keep, min_piece=min_piece))
    return out


def enforce_cut_length(clips: list[Clip], target: float) -> list[Clip]:
    """평균 컷 길이를 목표에 맞춘다.

    점프컷은 무음이 나올 때마다 자르기 때문에, 숨을 자주 쉬는 사람이면
    0.5초짜리 조각이 수십 개 나온다. '평균 컷 2.2초' 같은 스타일 규격을
    맞추려면 너무 잘게 나뉜 조각은 **도로 붙여야** 한다.

    붙이는 순서는 '사이가 가장 좁은 곳' 부터다. 원본에서 거의 안 떨어진
    두 조각을 먼저 합쳐야 티가 안 난다.
    """
    if target <= 0 or len(clips) < 2:
        return clips

    while True:
        durations = [c.source_duration for c in clips]
        if not durations or sum(durations) / len(durations) >= target * 0.9:
            break
        # 이어 붙일 수 있는 이웃 중 사이가 가장 좁은 쌍을 찾는다
        best, best_gap = -1, None
        for i in range(len(clips) - 1):
            a, b = clips[i], clips[i + 1]
            if a.reason == "bridge" or b.reason == "bridge":
                continue
            if a.speed != b.speed:
                continue
            gap = b.source_start - a.source_end
            if gap < 0:
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = i, gap
        if best < 0:
            break
        a, b = clips[best], clips[best + 1]
        merged = Clip(source_start=a.source_start, source_end=b.source_end,
                      score=max(a.score, b.score), reason=a.reason or b.reason,
                      label=a.label or b.label, speed=a.speed, zoom=a.zoom or b.zoom,
                      effects=sorted(set(a.effects) | set(b.effects)))
        clips = [*clips[:best], merged, *clips[best + 2:]]
    return clips


def _has_speech(analysis: Analysis, start: float, end: float) -> bool:
    for word in analysis.transcript.words():
        if word.end > start and word.start < end:
            return True
    if not analysis.transcript.words():
        for seg in analysis.transcript.segments:
            if seg.end > start and seg.start < end:
                return True
    return False


def apply_speed_ramps(clips: list[Clip], analysis: Analysis, cfg: dict) -> list[Clip]:
    """말이 없고 잔잔한 긴 구간은 자르지 말고 빨리 감는다.

    통째로 잘라내면 '어떻게 저기로 갔지?' 가 되고, 그대로 두면 늘어진다.
    """
    if not cfg.get("speed_ramp", True):
        return clips

    speed = float(cfg.get("ramp_speed", 2.0))
    min_duration = float(cfg.get("ramp_min_duration", 2.5))
    max_score = float(cfg.get("ramp_max_score", 0.35))
    if speed <= 1.0:
        return clips

    for clip in clips:
        if clip.source_duration < min_duration or clip.reason == "manual":
            continue
        if clip.score > max_score:
            continue
        if _has_speech(analysis, clip.source_start, clip.source_end):
            continue
        clip.speed = speed
        if "speedup" not in clip.effects:
            clip.effects.append("speedup")
    return clips


def bridge_gaps(clips: list[Clip], cfg: dict) -> list[Clip]:
    """가까이 붙은 두 하이라이트 사이는 잘라내지 말고 **빨리 감아서 잇는다**.

    20초 떨어진 두 장면을 하드컷으로 붙이면 시청자는 '어? 언제 저기 갔지' 가
    된다. 그 20초를 8배속 2.5초로 흘려 보내면 한 판을 계속 보고 있는 느낌이
    유지된다. 짜깁기와 편집을 가르는 지점이 여기다.
    """
    if not cfg.get("bridge_gaps", True) or len(clips) < 2:
        return clips

    speed = float(cfg.get("bridge_speed", 8.0))
    lo = float(cfg.get("bridge_min", 1.5))
    hi = float(cfg.get("bridge_max", 25.0))
    if speed <= 1.0 or hi <= lo:
        return clips

    out: list[Clip] = []
    for clip in clips:
        if out:
            gap_start, gap_end = out[-1].source_end, clip.source_start
            if lo <= gap_end - gap_start <= hi:
                out.append(Clip(source_start=round(gap_start, 3),
                                source_end=round(gap_end, 3),
                                reason="bridge", speed=speed,
                                effects=["speedup", "bridge"]))
        out.append(clip)
    return out


def apply_zooms(clips: list[Clip], cfg: dict) -> list[Clip]:
    """리액션 줌인을 **정해진 빈도로** 건다.

    지금까지는 오디오 피크마다 무조건 걸어서, 시끄러운 영상이면 계속 확대된
    채로 흘러갔다. 편집자들은 분당 몇 번 쓸지를 정해 놓고 센 순간에만 쓴다.
    점수가 높을수록 더 크게 당긴다.
    """
    if not cfg.get("zoom", True):
        for clip in clips:
            clip.effects[:] = [e for e in clip.effects if e != "punch"]
        return clips

    lo = float(cfg.get("zoom_min", 1.12))
    hi = max(lo, float(cfg.get("zoom_max", 1.35)))
    per_minute = float(cfg.get("zoom_per_minute", 0.0))
    if per_minute <= 0:                      # 빈도를 안 정했으면 예전처럼 둔다
        for clip in clips:
            if "punch" in clip.effects and not clip.zoom:
                clip.zoom = lo
        return clips

    out_minutes = sum(c.duration for c in clips) / 60.0
    budget = max(1, round(per_minute * out_minutes))

    ranked = sorted((c for c in clips if c.reason != "bridge"),
                    key=lambda c: c.score, reverse=True)
    chosen = ranked[:budget]
    top = max((c.score for c in chosen), default=0.0)
    bottom = min((c.score for c in chosen), default=0.0)

    for clip in clips:
        clip.effects[:] = [e for e in clip.effects if e != "punch"]
        clip.zoom = 0.0
    for clip in chosen:
        # 제일 센 클립이 hi, 나머지는 점수에 비례해 lo~hi 사이
        ratio = 1.0 if top <= bottom else (clip.score - bottom) / (top - bottom)
        clip.zoom = round(lo + (hi - lo) * ratio, 3)
        clip.effects.append("punch")
    return clips


def cold_open_length(cfg: dict) -> float:
    """도입부가 완성본에 더할 길이(초).

    도입부는 본편에서 **한 번 더** 보여 주는 것이라 그만큼 완성본이 길어진다.
    이 값을 목표 길이에서 미리 빼 두지 않으면, 화면에서 "3분" 을 고른 사람이
    3분 + 도입부 길이짜리를 받게 된다.
    """
    if not cfg.get("cold_open", True):
        return 0.0
    pieces = max(1, int(cfg.get("cold_open_pieces", 1)))
    total = float(cfg.get("cold_open_seconds", 0.0) or 0.0)
    if total > 0:
        return total
    return max(0.0, float(cfg.get("cold_open_max", 5.0))) * pieces


def pick_cold_open(clips: list[Clip], cfg: dict, *, source_duration: float = 0.0) -> list[Clip]:
    """맨 앞에 붙일 도입부(하이라이트 선공개)를 만든다.

    한 장면만 5초 보여 주는 것과, 자극적인 장면 네 개를 30초에 걸쳐 몰아
    보여 주는 것은 완전히 다른 도입부다. 스타일마다 길이와 개수를 정한다.

    도입부는 **원본에서 새로 잘라낸다.** 본편은 점프컷으로 잘게 쪼개져 있어서
    그 조각을 그대로 쓰면 요청한 길이가 절대 안 나온다. 그리고 서로 멀리
    떨어진 장면만 고른다. 안 그러면 한 장면의 앞뒤 조각 다섯 개가 뽑혀
    같은 화면을 다섯 번 보여 주게 된다.
    """
    if not cfg.get("cold_open", True) or len(clips) < 2:
        return []

    pieces = max(1, int(cfg.get("cold_open_pieces", 1)))
    total = float(cfg.get("cold_open_seconds", 0.0))
    per_piece = total / pieces if total > 0 else float(cfg.get("cold_open_max", 5.0))
    if per_piece <= 0:
        return []

    limit = source_duration or max((c.source_end for c in clips), default=0.0)
    ranked = [c for c in sorted(clips, key=lambda c: c.score, reverse=True)
              if c.score > 0 and c.reason != "bridge"]

    hook: list[Clip] = []
    used: list[float] = []
    apart = max(per_piece, 10.0)          # 서로 이만큼은 떨어진 장면끼리
    for clip in ranked:
        if len(hook) >= pieces:
            break
        if any(abs(clip.source_start - t) < apart for t in used):
            continue
        end = min(limit, clip.source_start + per_piece) if limit else \
            clip.source_start + per_piece
        if end - clip.source_start < 0.8:
            continue
        used.append(clip.source_start)
        hook.append(Clip(source_start=clip.source_start, source_end=round(end, 3),
                         score=clip.score, reason="coldopen", label="",
                         effects=["coldopen", "punch"], zoom=clip.zoom))
    return hook


def trim_flat_tail(clips: list[Clip], cfg: dict) -> list[Clip]:
    """마지막이 밋밋한 클립이면 떼어낸다. 끝맛은 세게."""
    if not cfg.get("end_on_peak", True) or len(clips) < 3:
        return clips
    scores = [c.score for c in clips if c.score > 0]
    if not scores:
        return clips
    weak = sum(scores) / len(scores) * 0.5
    out = list(clips)
    while len(out) > 2 and out[-1].score < weak and out[-1].reason not in ("manual",):
        out.pop()
    return out


def apply_editing(clips: list[Clip], analysis: Analysis, cfg: dict) -> list[Clip]:
    """선택된 구간 목록 → 실제 편집 순서의 클립 목록."""
    if not clips or not cfg.get("enabled", True):
        return clips

    clips = trim_flat_tail(clips, cfg)
    clips.sort(key=lambda c: c.source_start)

    # 이어붙이기가 먼저다. 점프컷 뒤에 하면 방금 낸 컷을 도로 메워 버린다.
    clips = bridge_gaps(clips, cfg)

    keep_whole = [c for c in clips if c.reason == "bridge"]
    cuttable = [c for c in clips if c.reason != "bridge"]
    cut = remove_dead_air(cuttable, analysis.audio.silences, cfg)
    cut = enforce_cut_length(cut, float(cfg.get("target_cut_length", 0.0)))
    cut = apply_speed_ramps(cut, analysis, cfg)
    clips = sorted([*cut, *keep_whole], key=lambda c: c.source_start)
    clips = apply_zooms(clips, cfg)

    hook = pick_cold_open(clips, cfg, source_duration=analysis.media.duration)
    return [*hook, *clips]


def editing_summary(clips: list[Clip], cfg: dict) -> dict:
    """무엇을 했는지 사람이 볼 수 있게."""
    return {
        "cold_open": any("coldopen" in c.effects for c in clips),
        "cold_open_pieces": sum(1 for c in clips if c.reason == "coldopen"),
        "bridges": sum(1 for c in clips if c.reason == "bridge"),
        "jump_cuts": max(0, len(clips) - 1),
        "sped_up": sum(1 for c in clips if c.speed > 1.0),
        "punch_ins": sum(1 for c in clips if "punch" in c.effects),
    }
