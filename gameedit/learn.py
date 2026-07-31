"""남의 편집본에서 편집 리듬을 **재서** 설정으로 옮긴다.

영상을 '보고 감을 잡는' 건 사람이 하는 일이고 이 프로그램은 못 한다.
대신 이미 편집된 영상에서 **셀 수 있는 것**은 정확히 셀 수 있다.

  · 컷이 분당 몇 번인가        → 얼마나 잘게 자르는 스타일인가
  · 컷 간격의 중앙값은 몇 초인가 → 한 호흡의 길이
  · 무음이 몇 %나 남아 있는가   → 죽은 시간을 얼마나 허용하는가

좋아하는 편집자의 완성본을 넣으면 그 수치를 뽑아 우리 설정값으로 바꿔 준다.
'저 사람 느낌' 을 추측이 아니라 측정으로 맞추는 방법이다.

한계는 분명히 해 둔다. 자막 밀도·밈 개수·줌 타이밍은 화면에 구워져 있어서
이 방법으로는 못 잰다. 컷 리듬과 죽은 시간만 옮겨 온다.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median

from .audio import analyze_audio
from .media import extract_audio, probe
from .scenes import detect_scenes


@dataclass
class StyleProfile:
    """편집본 한 편에서 잰 값."""

    source: str = ""
    duration: float = 0.0
    cut_count: int = 0
    cuts_per_minute: float = 0.0
    median_cut: float = 0.0      # 컷 간격 중앙값(초)
    p25_cut: float = 0.0         # 짧은 쪽 4분의 1 지점
    p75_cut: float = 0.0
    silence_ratio: float = 0.0   # 전체 중 무음이 차지하는 비율
    longest_silence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def measure_style(src: str | Path, cfg: dict | None = None, *, log=None) -> StyleProfile:
    """편집된 영상 한 편을 재서 StyleProfile 로 돌려준다."""
    cfg = cfg or {}
    say = log or (lambda _m: None)
    media = probe(src)
    profile = StyleProfile(source=str(src), duration=media.duration)
    if media.duration <= 0:
        profile.notes.append("영상 길이를 읽지 못했습니다")
        return profile

    say("컷을 세는 중…")
    scenes = detect_scenes(src, threshold=float(cfg.get("scene_threshold", 0.35) or 0.35),
                           scale_width=int(cfg.get("scene_scale_width", 320) or 320))
    times = sorted(s.t for s in scenes)
    profile.cut_count = len(times)
    profile.cuts_per_minute = round(len(times) / (media.duration / 60.0), 2)

    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    if gaps:
        profile.median_cut = round(median(gaps), 2)
        profile.p25_cut = round(_percentile(gaps, 0.25), 2)
        profile.p75_cut = round(_percentile(gaps, 0.75), 2)
    else:
        profile.notes.append("컷을 거의 못 찾았습니다 (정지 화면이거나 화면 변화가 적은 영상)")

    if media.has_audio:
        say("죽은 시간을 재는 중…")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "ref.wav"
            extract_audio(src, wav)
            audio = analyze_audio(wav, hop=0.1,
                                  silence_db=float(cfg.get("silence_db", -38.0)),
                                  min_silence=float(cfg.get("min_silence", 0.4)))
        quiet = sum(e - s for s, e in audio.silences)
        profile.silence_ratio = round(quiet / media.duration, 4)
        profile.longest_silence = round(max((e - s for s, e in audio.silences), default=0.0), 2)
    else:
        profile.notes.append("소리가 없어 죽은 시간은 재지 못했습니다")
    return profile


def style_to_config(profile: StyleProfile) -> dict:
    """잰 값 → 우리 설정값. 점 표기법 그대로 돌려준다."""
    out: dict[str, float | bool] = {}
    if profile.median_cut > 0:
        # 한 조각의 길이를 상대의 컷 간격에 맞춘다. 너무 극단으로는 가지 않게 묶는다.
        piece = max(0.4, min(4.0, profile.p25_cut or profile.median_cut))
        out["editing.dead_air_min_piece"] = round(piece, 2)
        out["highlight.min_clip"] = round(max(2.0, min(12.0, profile.median_cut * 1.5)), 2)
        out["highlight.max_clip"] = round(max(8.0, min(60.0, profile.p75_cut * 6)), 2)

    if profile.silence_ratio:
        # 무음을 거의 안 남기는 편집이면 우리도 더 짧은 무음부터 잘라낸다.
        if profile.silence_ratio < 0.05:
            out["editing.dead_air_min"] = 0.3
        elif profile.silence_ratio < 0.12:
            out["editing.dead_air_min"] = 0.45
        else:
            out["editing.dead_air_min"] = 0.8
        out["editing.dead_air_keep"] = 0.08 if profile.silence_ratio < 0.08 else 0.15

    if profile.cuts_per_minute:
        # 컷이 잦을수록 밈도 촘촘한 편집이라고 본다 (분당 컷의 1/4 정도)
        per_min = max(1.5, min(9.0, profile.cuts_per_minute / 4.0))
        out["memes.max_per_minute"] = round(per_min, 1)
        out["memes.cooldown"] = round(max(3.0, 30.0 / per_min), 1)
    return out


def describe(profile: StyleProfile) -> str:
    """사람이 읽을 요약."""
    lines = [
        f"길이           {profile.duration:.0f}초",
        f"컷 개수        {profile.cut_count}개  (분당 {profile.cuts_per_minute:.1f}회)",
    ]
    if profile.median_cut:
        lines.append(f"컷 간격        중앙값 {profile.median_cut:.1f}초 "
                     f"(짧은 쪽 {profile.p25_cut:.1f}초 · 긴 쪽 {profile.p75_cut:.1f}초)")
    if profile.silence_ratio:
        lines.append(f"죽은 시간      전체의 {profile.silence_ratio * 100:.1f}% "
                     f"(가장 긴 무음 {profile.longest_silence:.1f}초)")

    if profile.cuts_per_minute >= 20:
        verdict = "아주 촘촘한 편집입니다 (숏폼·하이텐션 계열)"
    elif profile.cuts_per_minute >= 10:
        verdict = "빠른 편집입니다 (일반적인 게임 하이라이트 속도)"
    elif profile.cuts_per_minute >= 4:
        verdict = "보통 속도입니다"
    else:
        verdict = "느긋한 편집입니다 (토크·풀영상 계열)"
    lines.append(f"→ {verdict}")
    for note in profile.notes:
        lines.append(f"⚠ {note}")
    return "\n".join(lines)


def merge_profiles(profiles: list[StyleProfile]) -> StyleProfile:
    """여러 편을 재서 평균을 낸다. 한 편만 보고 정하면 그 편의 특성에 휘둘린다."""
    usable = [p for p in profiles if p.duration > 0]
    if not usable:
        return StyleProfile()
    if len(usable) == 1:
        return usable[0]

    def avg(name: str) -> float:
        values = [getattr(p, name) for p in usable if getattr(p, name)]
        return round(sum(values) / len(values), 3) if values else 0.0

    merged = StyleProfile(
        source=f"{len(usable)}편 평균",
        duration=sum(p.duration for p in usable),
        cut_count=sum(p.cut_count for p in usable),
        cuts_per_minute=avg("cuts_per_minute"),
        median_cut=avg("median_cut"),
        p25_cut=avg("p25_cut"),
        p75_cut=avg("p75_cut"),
        silence_ratio=avg("silence_ratio"),
        longest_silence=max(p.longest_silence for p in usable),
    )
    return merged


def save_style(path: str | Path, settings: dict, profile: StyleProfile) -> Path:
    """설정 파일로 저장. -c 로 그대로 넘길 수 있는 형태."""
    nested: dict = {}
    for dotted, value in settings.items():
        node = nested
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    nested["_measured"] = profile.to_dict()

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
            path.write_text(yaml.safe_dump(nested, allow_unicode=True, sort_keys=False),
                            encoding="utf-8")
            return path
        except ImportError:
            path = path.with_suffix(".json")
    path.write_text(json.dumps(nested, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
