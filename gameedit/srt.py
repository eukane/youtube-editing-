"""SRT / WebVTT 읽기·쓰기."""

from __future__ import annotations

import re
from pathlib import Path

from .models import Segment, SubtitleCue, Transcript

_TIME_RE = re.compile(
    r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
)
_SHORT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_subtitle_file(path: str | Path) -> Transcript:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return parse_subtitle_text(text)


def parse_subtitle_text(text: str) -> Transcript:
    segments: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        time_idx = None
        start = end = 0.0
        for i, line in enumerate(lines):
            m = _TIME_RE.search(line)
            if m:
                start = _to_seconds(*m.group(1, 2, 3, 4))
                end = _to_seconds(*m.group(5, 6, 7, 8))
                time_idx = i
                break
            m2 = _SHORT_TIME_RE.search(line)
            if m2:
                start = _to_seconds("0", *m2.group(1, 2, 3))
                end = _to_seconds("0", *m2.group(4, 5, 6))
                time_idx = i
                break
        if time_idx is None:
            continue
        body = " ".join(lines[time_idx + 1:]).strip()
        body = re.sub(r"<[^>]+>", "", body)  # VTT 인라인 태그 제거
        if body:
            segments.append(Segment(start=start, end=end, text=body))
    segments.sort(key=lambda s: s.start)
    return Transcript(segments=segments)


def format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(cues: list[SubtitleCue], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
