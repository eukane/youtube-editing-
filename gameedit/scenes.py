"""장면 전환 검출.

ffmpeg 의 scene 점수를 이용한다. 컷이 몰려 있는 구간은 대개
교전/이동/사망 리스폰처럼 '뭔가 벌어지는' 구간이라 하이라이트 점수에 반영한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from .media import ffmpeg_bin, run
from .models import SceneChange

_FRAME_RE = re.compile(r"pts_time:(\d+\.?\d*)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=(\d+\.?\d*)")


def detect_scenes(src: str | Path, *, threshold: float = 0.35,
                  scale_width: int = 320) -> list[SceneChange]:
    filters = []
    if scale_width and scale_width > 0:
        filters.append(f"scale={int(scale_width)}:-2")
    filters.append(f"select='gt(scene,{threshold})'")
    filters.append("metadata=print:file=-")

    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(src), "-an", "-vf", ",".join(filters), "-f", "null", "-",
    ]
    proc = run(cmd, check=False)
    return parse_scene_output(proc.stdout or "")


def parse_scene_output(text: str) -> list[SceneChange]:
    scenes: list[SceneChange] = []
    pending: float | None = None
    for line in text.splitlines():
        m = _FRAME_RE.search(line)
        if m:
            if pending is not None:
                scenes.append(SceneChange(t=pending, score=0.0))
            pending = float(m.group(1))
            continue
        s = _SCORE_RE.search(line)
        if s and pending is not None:
            scenes.append(SceneChange(t=pending, score=float(s.group(1))))
            pending = None
    if pending is not None:
        scenes.append(SceneChange(t=pending, score=0.0))
    return scenes


def scene_burst_curve(scenes: list[SceneChange], duration: float, *, hop: float = 1.0,
                      window: float = 6.0) -> list[float]:
    """초 단위 격자에 '최근 window 초 동안의 컷 밀도' 를 0~1 로 기록."""
    n = max(1, int(duration / hop) + 1)
    counts = [0.0] * n
    for scene in scenes:
        idx = int(scene.t / hop)
        if 0 <= idx < n:
            counts[idx] += 1.0
    half = max(1, int(window / hop / 2))
    out: list[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        density = sum(counts[lo:hi]) / max(1e-6, (hi - lo) * hop)
        out.append(min(1.0, density / 1.2))  # 초당 1.2컷이면 최대치
    return out
