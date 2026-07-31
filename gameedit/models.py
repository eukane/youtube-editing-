"""편집 파이프라인 전체에서 오가는 데이터 구조.

모든 시간 값의 단위는 '초(float)'.
- ``source_*`` 로 시작하는 시간은 원본 영상 기준 타임라인
- ``out_*`` / 자막·밈 큐의 시간은 편집 결과물 기준 타임라인
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence


# --------------------------------------------------------------------------
# 전사(transcript)
# --------------------------------------------------------------------------


@dataclass
class Word:
    start: float
    end: float
    text: str
    confidence: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str = "main"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Transcript:
    language: str = "ko"
    segments: list[Segment] = field(default_factory=list)

    def words(self) -> list[Word]:
        out: list[Word] = []
        for seg in self.segments:
            if seg.words:
                out.extend(seg.words)
            else:
                # 단어 타임스탬프가 없는 백엔드는 세그먼트를 통째로 한 단어처럼 취급
                out.append(Word(seg.start, seg.end, seg.text))
        return out

    def text_between(self, start: float, end: float) -> str:
        chunks = [s.text for s in self.segments if s.end > start and s.start < end]
        return " ".join(c.strip() for c in chunks if c.strip())

    @property
    def duration(self) -> float:
        return max((s.end for s in self.segments), default=0.0)


# --------------------------------------------------------------------------
# 분석 결과
# --------------------------------------------------------------------------


@dataclass
class AudioAnalysis:
    """일정 간격(hop)으로 샘플링한 오디오 특징."""

    hop: float = 0.05
    rms_db: list[float] = field(default_factory=list)
    excitement: list[float] = field(default_factory=list)  # 0..1
    silences: list[tuple[float, float]] = field(default_factory=list)
    peaks: list[tuple[float, float]] = field(default_factory=list)  # (시각, 점수)

    def at(self, t: float) -> float:
        """시각 t 의 흥분도(0..1)."""
        if not self.excitement:
            return 0.0
        idx = int(t / self.hop)
        idx = min(max(idx, 0), len(self.excitement) - 1)
        return self.excitement[idx]

    def mean_between(self, start: float, end: float) -> float:
        if not self.excitement or end <= start:
            return 0.0
        i0 = max(0, int(start / self.hop))
        i1 = min(len(self.excitement), max(i0 + 1, int(end / self.hop)))
        window = self.excitement[i0:i1]
        return sum(window) / len(window) if window else 0.0

    @property
    def duration(self) -> float:
        return len(self.excitement) * self.hop


@dataclass
class SceneChange:
    t: float
    score: float = 0.0


@dataclass
class MediaInfo:
    path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    has_audio: bool = True

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class Analysis:
    media: MediaInfo = field(default_factory=MediaInfo)
    audio: AudioAnalysis = field(default_factory=AudioAnalysis)
    scenes: list[SceneChange] = field(default_factory=list)
    transcript: Transcript = field(default_factory=Transcript)

    def scene_density(self, start: float, end: float) -> float:
        """구간 내 초당 장면 전환 횟수."""
        if end <= start:
            return 0.0
        n = sum(1 for s in self.scenes if start <= s.t < end)
        return n / (end - start)


# --------------------------------------------------------------------------
# 편집 계획
# --------------------------------------------------------------------------


@dataclass
class Clip:
    """최종본에 살릴 원본 구간 한 덩어리."""

    source_start: float
    source_end: float
    score: float = 0.0
    reason: str = ""
    label: str = ""
    effects: list[str] = field(default_factory=list)  # punch / fadein / fadeout ...
    out_start: float = 0.0  # 결과물 타임라인상의 시작 시각 (plan 빌드 시 계산)

    @property
    def duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def out_end(self) -> float:
        return self.out_start + self.duration

    def contains_source(self, t: float) -> bool:
        return self.source_start <= t < self.source_end

    def to_out(self, t: float) -> float:
        return self.out_start + (t - self.source_start)


@dataclass
class MemeCue:
    """결과물 타임라인 위에 얹히는 밈 한 개."""

    start: float
    duration: float
    meme_id: str
    kind: str = "text"  # text | image | video | audio
    text: str = ""
    asset: str = ""
    style: str = "MemeTop"
    placement: str = "top"
    volume: float = 1.0
    scale: float = 0.28  # 화면 너비 대비 이미지/영상 밈 크기
    sfx: str = ""
    sfx_volume: float = 0.9
    trigger: str = ""
    source_start: float = 0.0
    show_text: bool = True  # 자막 레이어에 text 를 그릴지 (그림 밈 위에 글자를 얹을 때 사용)
    priority: float = 1.0  # 자리 다툼이 날 때 높은 쪽이 살아남는다
    cooldown: float = 0.0  # 이 밈이 다시 나오기까지 최소 간격(초)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class SubtitleCue:
    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    style: str = "Main"
    speaker: str = "main"
    source_start: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class EditPlan:
    source: str = ""
    media: MediaInfo = field(default_factory=MediaInfo)
    clips: list[Clip] = field(default_factory=list)
    memes: list[MemeCue] = field(default_factory=list)
    subtitles: list[SubtitleCue] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def relayout(self) -> None:
        """클립 순서/길이가 바뀐 뒤 out_start 를 다시 계산."""
        t = 0.0
        for clip in self.clips:
            clip.out_start = t
            t += clip.duration

    def map_time(self, source_t: float) -> float | None:
        """원본 시각 → 결과물 시각. 잘려나간 구간이면 None."""
        for clip in self.clips:
            if clip.contains_source(source_t):
                return clip.to_out(source_t)
        return None

    def map_range(self, start: float, end: float) -> tuple[float, float] | None:
        """원본 구간 → 결과물 구간. 한 클립 안에 들어가는 부분만 살린다."""
        for clip in self.clips:
            if end <= clip.source_start or start >= clip.source_end:
                continue
            s = max(start, clip.source_start)
            e = min(end, clip.source_end)
            if e - s <= 0.01:
                continue
            return clip.to_out(s), clip.to_out(e)
        return None


# --------------------------------------------------------------------------
# 직렬화 (dataclass ↔ JSON)
# --------------------------------------------------------------------------


def _tuples_to_lists(obj: Any) -> Any:
    if isinstance(obj, tuple):
        return [_tuples_to_lists(o) for o in obj]
    if isinstance(obj, list):
        return [_tuples_to_lists(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _tuples_to_lists(v) for k, v in obj.items()}
    return obj


def to_dict(obj: Any) -> dict:
    return _tuples_to_lists(asdict(obj))


def _build(cls, data: Any):
    if data is None:
        return cls()
    return cls(**data)


def analysis_from_dict(data: dict) -> Analysis:
    audio_raw = data.get("audio") or {}
    audio = AudioAnalysis(
        hop=audio_raw.get("hop", 0.05),
        rms_db=list(audio_raw.get("rms_db") or []),
        excitement=list(audio_raw.get("excitement") or []),
        silences=[tuple(s) for s in audio_raw.get("silences") or []],
        peaks=[tuple(p) for p in audio_raw.get("peaks") or []],
    )
    tr_raw = data.get("transcript") or {}
    segments = []
    for seg in tr_raw.get("segments") or []:
        words = [Word(**w) for w in seg.get("words") or []]
        segments.append(
            Segment(
                start=seg["start"],
                end=seg["end"],
                text=seg.get("text", ""),
                words=words,
                speaker=seg.get("speaker", "main"),
            )
        )
    return Analysis(
        media=_build(MediaInfo, data.get("media")),
        audio=audio,
        scenes=[SceneChange(**s) for s in data.get("scenes") or []],
        transcript=Transcript(language=tr_raw.get("language", "ko"), segments=segments),
    )


def plan_from_dict(data: dict) -> EditPlan:
    plan = EditPlan(
        source=data.get("source", ""),
        media=_build(MediaInfo, data.get("media")),
        clips=[Clip(**c) for c in data.get("clips") or []],
        memes=[MemeCue(**m) for m in data.get("memes") or []],
        subtitles=[SubtitleCue(**s) for s in data.get("subtitles") or []],
        meta=data.get("meta") or {},
    )
    return plan


def save_json(obj: Any, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = obj if isinstance(obj, dict) else to_dict(obj)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def total_duration(items: Iterable[Clip]) -> float:
    return sum(c.duration for c in items)


def merge_ranges(ranges: Sequence[tuple[float, float]], gap: float = 0.0) -> list[tuple[float, float]]:
    """겹치거나 gap 이내로 붙어 있는 구간들을 병합."""
    if not ranges:
        return []
    ordered = sorted((float(s), float(e)) for s, e in ranges if e > s)
    merged: list[tuple[float, float]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start - last_end <= gap:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
