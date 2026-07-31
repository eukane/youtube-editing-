"""밈 팩 로딩 + 밈 배치.

밈은 '언제 터뜨릴지' 가 전부라서, 아래 세 가지 신호로 자리를 잡는다.
  1. 대사 키워드   : "아 죽었다" → 사망 밈
  2. 오디오 피크   : 소리 지르는 순간 → 리액션 밈
  3. 편집 구조     : 클립 시작 라벨, 어색한 정적 구간

배치는 원본 타임라인에서 계산한 뒤 EditPlan 을 통해 결과물 타임라인으로 옮긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import load_data_file
from .models import Analysis, EditPlan, MemeCue


def _builtin_pack_dir() -> Path:
    """기본 밈팩 폴더 (소스 체크아웃 / 설치본 / 현재 폴더 순으로 탐색)."""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "assets" / "memepacks",  # 소스 체크아웃
        here / "assets" / "memepacks",         # 패키지에 포함된 경우
        Path.cwd() / "assets" / "memepacks",   # 작업 폴더
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


BUILTIN_PACK_DIR = _builtin_pack_dir()

PLACEMENTS = ("top", "center", "bottom", "left", "right", "fullscreen")


@dataclass
class MemeDef:
    id: str
    kind: str = "text"  # text | image | video | audio
    text: str = ""
    asset: str = ""
    sfx: str = ""
    triggers: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    placement: str = "top"
    style: str = "MemeTop"
    duration: float = 2.0
    weight: float = 1.0
    cooldown: float = 12.0
    scale: float = 0.28
    volume: float = 1.0
    sfx_volume: float = 0.8
    min_excitement: float = 0.0
    pack: str = ""
    base_dir: Path | None = None

    def resolved_asset(self) -> Path | None:
        if not self.asset:
            return None
        p = Path(self.asset)
        if not p.is_absolute() and self.base_dir:
            p = self.base_dir / p
        return p if p.exists() else None

    def resolved_sfx(self) -> Path | None:
        if not self.sfx:
            return None
        p = Path(self.sfx)
        if not p.is_absolute() and self.base_dir:
            p = self.base_dir / p
        return p if p.exists() else None

    def matches(self, text: str) -> str | None:
        lowered = (text or "").lower()
        for trigger in self.triggers:
            if trigger and trigger.lower() in lowered:
                return trigger
        return None


@dataclass
class MemePack:
    name: str
    directory: Path
    memes: list[MemeDef] = field(default_factory=list)
    description: str = ""


def load_pack(path: str | Path) -> MemePack:
    """폴더(pack.yaml 포함) 또는 pack.yaml 파일 경로를 받아 로드."""
    p = Path(path)
    if p.is_dir():
        for name in ("pack.yaml", "pack.yml", "pack.json"):
            candidate = p / name
            if candidate.exists():
                p = candidate
                break
        else:
            raise FileNotFoundError(f"밈팩 정의 파일이 없습니다: {path}/pack.yaml")
    data = load_data_file(p)
    base_dir = p.parent
    pack = MemePack(name=data.get("name", base_dir.name), directory=base_dir,
                    description=data.get("description", ""))
    for raw in data.get("memes", []) or []:
        meme = MemeDef(
            id=str(raw.get("id") or f"{pack.name}-{len(pack.memes)}"),
            kind=raw.get("kind", "text"),
            text=raw.get("text", ""),
            asset=raw.get("asset", ""),
            sfx=raw.get("sfx", ""),
            triggers=[str(t) for t in raw.get("triggers", []) or []],
            events=[str(e) for e in raw.get("events", []) or []],
            placement=raw.get("placement", "top"),
            style=raw.get("style", "MemeTop"),
            duration=float(raw.get("duration", 2.0)),
            weight=float(raw.get("weight", 1.0)),
            cooldown=float(raw.get("cooldown", 12.0)),
            scale=float(raw.get("scale", 0.28)),
            volume=float(raw.get("volume", 1.0)),
            sfx_volume=float(raw.get("sfx_volume", 0.8)),
            min_excitement=float(raw.get("min_excitement", 0.0)),
            pack=pack.name,
            base_dir=base_dir,
        )
        pack.memes.append(meme)
    return pack


def _as_list(value) -> list[str]:
    """설정에서 문자열 하나만 들어와도(`--set memes.packs=default`) 리스트로 취급."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(v) for v in value]


def load_packs(names, extra_dirs=None, *, builtin_dir: Path | None = None,
               asset_dirs=None) -> list[MemeDef]:
    names = _as_list(names)
    extra_dirs = _as_list(extra_dirs)
    builtin_dir = Path(builtin_dir or BUILTIN_PACK_DIR)
    memes: list[MemeDef] = []
    seen: set[str] = set()
    candidates: list[Path] = []
    for name in names:
        p = Path(name)
        candidates.append(p if p.exists() else builtin_dir / name)
    for extra in extra_dirs:
        candidates.append(Path(extra))
    for candidate in candidates:
        if not candidate.exists():
            continue
        pack = load_pack(candidate)
        for meme in pack.memes:
            key = f"{pack.name}:{meme.id}"
            if key in seen:
                continue
            seen.add(key)
            memes.append(meme)

    for asset_dir in _as_list(asset_dirs):
        for meme in scan_asset_dir(asset_dir):
            key = f"scan:{meme.id}"
            if key in seen:
                continue
            seen.add(key)
            memes.append(meme)
    return memes


# --------------------------------------------------------------------------
# 파일만 넣으면 되는 자동 스캔
# --------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTS = {".gif", ".mp4", ".webm", ".mov", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}

# 파일명에서 옵션을 읽는 접미사: 무야호@right@2.5s.png
_PLACEMENT_TOKENS = set(PLACEMENTS)


def parse_asset_filename(name: str) -> dict:
    """파일 이름 하나로 밈 정의를 만든다.

    규칙 (전부 선택):
      무야호.png                     → "무야호" 라고 말하면 뜸
      죽었,사망,뒤졌.png             → 쉼표로 트리거 여러 개
      개킹받네@right@2.5.png         → @위치, @노출시간(초)
      hype@_.png                     → 트리거 대신 이벤트(hype/silence/timeskip)
    """
    stem = Path(name).stem
    parts = [p.strip() for p in stem.split("@") if p.strip()]
    head = parts[0] if parts else stem
    options = parts[1:]

    triggers: list[str] = []
    events: list[str] = []
    for token in head.replace("_", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if token in ("hype", "peak", "silence", "timeskip"):
            events.append(token)
        else:
            triggers.append(token)

    placement = "top"
    duration = 2.0
    for option in options:
        low = option.lower()
        if low in _PLACEMENT_TOKENS:
            placement = low
            continue
        try:
            duration = float(low.rstrip("s"))
        except ValueError:
            continue
    return {"triggers": triggers, "events": events, "placement": placement,
            "duration": duration}


def scan_asset_dir(directory: str | Path, *, pack_name: str = "scan") -> list[MemeDef]:
    """폴더에 넣어 둔 이미지·움짤·효과음을 그대로 밈으로 만든다.

    같은 이름의 오디오 파일이 있으면 효과음으로 자동으로 짝지어 준다.
    (예: 무야호.png + 무야호.mp3 → 그림 뜨면서 소리도 같이)
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    files = sorted(p for p in directory.rglob("*") if p.is_file())
    audio_by_stem = {p.stem: p for p in files if p.suffix.lower() in AUDIO_EXTS}
    # 그림·영상과 이름이 같은 오디오는 그 밈의 효과음으로만 쓰고 단독 등록하지 않는다
    paired_audio = {p.stem for p in files
                    if p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS and p.stem in audio_by_stem}

    memes: list[MemeDef] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTS:
            kind = "image"
        elif suffix in VIDEO_EXTS:
            kind = "video"
        elif suffix in AUDIO_EXTS:
            if path.stem in paired_audio:
                continue
            kind = "audio"
        else:
            continue

        parsed = parse_asset_filename(path.name)
        if not parsed["triggers"] and not parsed["events"]:
            continue
        twin = audio_by_stem.get(path.stem) if kind != "audio" else None
        sfx = str(twin) if twin is not None else ""

        base_id = (parsed["triggers"] or parsed["events"])[0]
        meme_id = base_id
        suffix_n = 2
        while any(m.id == meme_id for m in memes):
            meme_id = f"{base_id}{suffix_n}"
            suffix_n += 1

        memes.append(MemeDef(
            id=meme_id,
            kind=kind,
            asset="" if kind == "audio" else str(path),
            sfx=sfx or (str(path) if kind == "audio" else ""),
            triggers=parsed["triggers"],
            events=parsed["events"],
            placement=parsed["placement"],
            duration=parsed["duration"],
            weight=1.6,  # 직접 넣은 밈을 기본 텍스트 밈보다 우선한다
            pack=pack_name,
            base_dir=directory,
        ))
    return memes


# --------------------------------------------------------------------------
# 배치
# --------------------------------------------------------------------------


def _keyword_time(segment, trigger: str) -> float:
    """세그먼트 안에서 키워드가 등장하는 대략적인 시각."""
    if segment.words:
        for word in segment.words:
            if trigger.lower() in word.text.lower():
                return word.start
        # 문자 위치 비율로 추정
    idx = segment.text.lower().find(trigger.lower())
    if idx < 0 or not segment.text:
        return segment.start
    ratio = idx / max(1, len(segment.text))
    return segment.start + ratio * max(0.0, segment.end - segment.start)


def _to_cue(meme: MemeDef, out_t: float, source_t: float, trigger: str) -> MemeCue:
    asset = meme.resolved_asset()
    sfx = meme.resolved_sfx()
    kind = meme.kind
    if kind in ("image", "video") and asset is None:
        kind = "text" if meme.text else "audio" if sfx else "skip"
    if kind == "audio" and sfx is None and asset is not None:
        sfx = asset
    return MemeCue(
        start=round(out_t, 3),
        duration=meme.duration,
        meme_id=meme.id,
        kind=kind,
        text=meme.text,
        asset=str(asset) if asset else "",
        style=meme.style,
        placement=meme.placement,
        volume=meme.volume,
        scale=meme.scale,
        sfx=str(sfx) if sfx else "",
        sfx_volume=meme.sfx_volume,
        trigger=trigger,
        source_start=round(source_t, 3),
        show_text=(kind == "text"),
        priority=meme.weight,
        cooldown=meme.cooldown,
    )


def _pick(candidates: list[MemeDef], usage: dict[str, int]) -> MemeDef:
    """가중치가 높고 덜 쓴 밈을 우선."""
    return max(candidates, key=lambda m: (m.weight - 0.25 * usage.get(m.id, 0), -usage.get(m.id, 0)))


def plan_memes(plan: EditPlan, analysis: Analysis, memes: list[MemeDef], cfg: dict) -> list[MemeCue]:
    if not cfg.get("enabled", True) or not plan.clips:
        return []

    cooldown = float(cfg.get("cooldown", 7.0))
    min_gap = float(cfg.get("min_gap", 1.5))
    max_per_minute = float(cfg.get("max_per_minute", 4.0))
    auto_reaction = bool(cfg.get("auto_reaction", True))
    auto_threshold = float(cfg.get("auto_reaction_threshold", 0.8))

    usage: dict[str, int] = {}
    candidates: list[MemeCue] = []

    # 1) 대사 키워드
    for seg in analysis.transcript.segments:
        matched = [(m, t) for m in memes for t in [m.matches(seg.text)] if t]
        if not matched:
            continue
        excitement = analysis.audio.mean_between(seg.start, seg.end)
        eligible = [m for m, _ in matched if excitement >= m.min_excitement]
        if not eligible:
            continue
        meme = _pick(eligible, usage)
        trigger = next(t for m, t in matched if m.id == meme.id)
        source_t = _keyword_time(seg, trigger)
        out_t = plan.map_time(source_t)
        if out_t is None:
            continue
        usage[meme.id] = usage.get(meme.id, 0) + 1
        candidates.append(_to_cue(meme, out_t + 0.1, source_t, trigger))

    # 2) 오디오 피크 → 리액션 밈
    if auto_reaction:
        hype = [m for m in memes if "hype" in m.events or "peak" in m.events]
        if hype:
            for t, value in analysis.audio.peaks:
                if value < auto_threshold:
                    continue
                out_t = plan.map_time(t)
                if out_t is None:
                    continue
                meme = _pick(hype, usage)
                usage[meme.id] = usage.get(meme.id, 0) + 1
                candidates.append(_to_cue(meme, out_t, t, "peak"))

    # 3) 클립 안에 통째로 남은 긴 정적 → 정적 밈
    silence_memes = [m for m in memes if "silence" in m.events]
    min_silence = float(cfg.get("silence_meme_min", 2.5))
    if silence_memes:
        for s_start, s_end in analysis.audio.silences:
            if s_end - s_start < min_silence:
                continue
            mapped = plan.map_range(s_start, s_end)
            # 편집본에 실제로 남은 정적이 그만큼 길어야 의미가 있다
            if not mapped or (mapped[1] - mapped[0]) < min_silence:
                continue
            meme = _pick(silence_memes, usage)
            usage[meme.id] = usage.get(meme.id, 0) + 1
            candidates.append(_to_cue(meme, mapped[0] + 0.4, s_start, "silence"))

    candidates.sort(key=lambda c: c.start)
    cues = _enforce_spacing(candidates, cooldown=cooldown, min_gap=min_gap,
                            max_per_minute=max_per_minute)

    # 3.5) 클립 사이 시간 점프 → "3분 후" 전환 카드
    cues.extend(_timeskip_cues(plan, memes, cfg))

    # 4) 클립 시작 라벨 (간격 규칙과 무관하게 항상 표시)
    if cfg.get("clip_intro_label", True):
        card_starts = [c.start for c in cues if c.trigger == "timeskip"]
        for clip in plan.clips:
            if not clip.label:
                continue
            # 전환 카드가 뜨는 클립은 라벨까지 겹치면 산만하다
            if any(abs(clip.out_start - t) < 1.0 for t in card_starts):
                continue
            cues.append(MemeCue(
                start=round(clip.out_start + 0.15, 3),
                duration=2.2,
                meme_id="clip_label",
                kind="text",
                text=clip.label,
                style="Label",
                placement="left",
                trigger="clip_start",
                source_start=clip.source_start,
            ))

    cues.sort(key=lambda c: c.start)
    return cues


def humanize_gap(seconds: float) -> str:
    """건너뛴 시간을 자막 카드용 문구로. (예: 185초 → '3분 후')"""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{int(round(seconds / 10.0)) * 10 or 10}초 후"
    if seconds < 3600:
        return f"{int(round(seconds / 60.0))}분 후"
    hours = seconds / 3600.0
    if hours < 2:
        return "1시간 후"
    return f"{int(hours)}시간 후"


def _timeskip_cues(plan: EditPlan, memes: list[MemeDef], cfg: dict) -> list[MemeCue]:
    """클립과 클립 사이에 원본 시간이 크게 건너뛰면 전환 카드를 끼운다.

    편집본만 보면 갑자기 상황이 바뀌어 있어서 시청자가 헷갈리는 걸 막아 주는,
    이 바닥에서 제일 흔한 자막 연출.
    """
    threshold = float(cfg.get("timeskip_min", 90.0))
    if threshold <= 0 or len(plan.clips) < 2:
        return []
    cards = [m for m in memes if "timeskip" in m.events]
    if not cards:
        return []

    out: list[MemeCue] = []
    usage: dict[str, int] = {}
    for prev, nxt in zip(plan.clips, plan.clips[1:]):
        gap = nxt.source_start - prev.source_end
        if gap < threshold:
            continue
        meme = _pick(cards, usage)
        usage[meme.id] = usage.get(meme.id, 0) + 1
        cue = _to_cue(meme, nxt.out_start, nxt.source_start, "timeskip")
        # 카드에 적힌 시간이 핵심이라, 배경 그림이 있어도 글자는 항상 얹는다
        cue.text = (meme.text or "{gap}").replace("{gap}", humanize_gap(gap))
        cue.show_text = True
        out.append(cue)
    return out


def _enforce_spacing(cues: list[MemeCue], *, cooldown: float, min_gap: float,
                     max_per_minute: float) -> list[MemeCue]:
    """같은 밈 반복(cooldown), 밈 도배(min_gap / 분당 개수)를 막는다.

    자리가 겹치면 우선순위(밈 weight)가 높은 쪽을 남긴다.
    대사에서 나온 밈이 자동 리액션 밈보다 대체로 정확하기 때문.
    """
    kept: list[MemeCue] = []
    last_by_id: dict[str, float] = {}

    def recalc_last() -> None:
        last_by_id.clear()
        for k in kept:
            last_by_id[k.meme_id] = max(last_by_id.get(k.meme_id, -1e9), k.start)

    def cue_cooldown(cue: MemeCue) -> float:
        # 팩에 적힌 밈별 쿨다운과 전역 설정 중 더 긴 쪽
        return max(cooldown, cue.cooldown)

    def cooldown_ok(pool: list[MemeCue], cue: MemeCue) -> bool:
        previous = [k.start for k in pool if k.meme_id == cue.meme_id]
        return not previous or (cue.start - max(previous)) >= cue_cooldown(cue)

    for cue in cues:
        if kept and cue.start - kept[-1].start < min_gap:
            if cue.priority > kept[-1].priority and cooldown_ok(kept[:-1], cue):
                kept[-1] = cue      # 더 좋은 밈으로 교체
                recalc_last()
            continue
        last = last_by_id.get(cue.meme_id)
        if last is not None and cue.start - last < cue_cooldown(cue):
            continue
        if max_per_minute > 0:
            window_start = cue.start - 60.0
            recent = sum(1 for k in kept if k.start >= window_start)
            if recent >= max_per_minute:
                continue
        kept.append(cue)
        last_by_id[cue.meme_id] = cue.start
    return kept


def missing_assets(memes: list[MemeDef]) -> list[str]:
    out: list[str] = []
    for meme in memes:
        if meme.asset and meme.resolved_asset() is None:
            out.append(f"{meme.id}: {meme.asset}")
        if meme.sfx and meme.resolved_sfx() is None:
            out.append(f"{meme.id}: {meme.sfx}")
    return out
