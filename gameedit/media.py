"""ffmpeg / ffprobe 래퍼."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from .models import MediaInfo


class FFmpegError(RuntimeError):
    pass


def _from_imageio(name: str) -> str | None:
    """imageio-ffmpeg 가 설치돼 있으면 그 바이너리를 재사용."""
    if name != "ffmpeg":
        return None
    try:  # pragma: no cover - 설치 여부에 따라 다름
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def find_binary(name: str) -> str | None:
    env = os.environ.get(f"GAMEEDIT_{name.upper()}")
    if env and (Path(env).exists() or shutil.which(env)):
        return env
    found = shutil.which(name)
    if found:
        return found
    return _from_imageio(name)


def ffmpeg_bin() -> str:
    binary = find_binary("ffmpeg")
    if not binary:
        raise FFmpegError(
            "ffmpeg 를 찾을 수 없습니다. 설치 후 PATH 에 등록하거나 GAMEEDIT_FFMPEG 환경변수로 경로를 지정하세요."
        )
    return binary


def ffprobe_bin() -> str | None:
    return find_binary("ffprobe")


def run(cmd: Sequence[str], *, capture: bool = True, check: bool = True,
        stderr_tail: int = 40) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        errors="replace",
    )
    if check and proc.returncode != 0:
        tail = ""
        if proc.stderr:
            tail = "\n".join(proc.stderr.strip().splitlines()[-stderr_tail:])
        raise FFmpegError(f"명령 실패 ({proc.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return proc


def ffmpeg(args: Sequence[str], *, quiet: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    base = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y"]
    if quiet:
        base += ["-loglevel", "error"]
    return run(base + list(args), check=check)


# --------------------------------------------------------------------------
# 미디어 정보
# --------------------------------------------------------------------------

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")
_VIDEO_RE = re.compile(r"Stream #\d+:\d+.*Video:.*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*Audio:")
_ROTATION_RE = re.compile(r"displaymatrix:\s*rotation of\s*(-?\d+(?:\.\d+)?)\s*degrees")


def _rotation_swaps_dimensions(rotation: float) -> bool:
    """90·270도 회전이면 가로세로가 뒤바뀐다."""
    return round(abs(float(rotation))) % 180 == 90


def _parse_rate(value: str) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe(path: str | Path) -> MediaInfo:
    """ffprobe 로 미디어 정보를 읽고, 없으면 ffmpeg 출력 파싱으로 대체."""
    path = str(path)
    if not Path(path).exists():
        raise FileNotFoundError(f"영상 파일이 없습니다: {path}")

    probe_bin = ffprobe_bin()
    if probe_bin:
        proc = run([
            probe_bin, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ])
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(data.get("format", {}).get("duration") or 0.0)
        info = MediaInfo(path=path, duration=duration, has_audio=audio is not None)
        if video:
            info.width = int(video.get("width") or 0)
            info.height = int(video.get("height") or 0)
            # 폰으로 찍은 세로 영상은 회전 정보만 붙어 있고 저장은 가로로 돼 있다.
            # ffmpeg 이 디코딩할 때 알아서 돌려주므로 실제 프레임 크기로 바꿔 둔다.
            rotation = 0.0
            for side in video.get("side_data_list") or []:
                if "rotation" in side:
                    rotation = float(side.get("rotation") or 0)
                    break
            else:
                rotation = float((video.get("tags") or {}).get("rotate") or 0)
            info.rotation = rotation
            if _rotation_swaps_dimensions(rotation):
                info.width, info.height = info.height, info.width
            fps = _parse_rate(video.get("avg_frame_rate") or "") or _parse_rate(
                video.get("r_frame_rate") or ""
            )
            info.fps = fps or 30.0
            if not duration:
                info.duration = float(video.get("duration") or 0.0)
        return info

    return _probe_with_ffmpeg(path)


def _probe_with_ffmpeg(path: str) -> MediaInfo:
    proc = run([ffmpeg_bin(), "-hide_banner", "-i", path], check=False)
    text = (proc.stderr or "") + (proc.stdout or "")
    info = MediaInfo(path=path)

    m = _DURATION_RE.search(text)
    if m:
        h, mnt, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info.duration = h * 3600 + mnt * 60 + sec

    vm = _VIDEO_RE.search(text)
    if vm:
        info.width, info.height = int(vm.group(1)), int(vm.group(2))
        line = text[vm.start(): text.find("\n", vm.start())]
        fm = _FPS_RE.search(line)
        info.fps = float(fm.group(1)) if fm else 30.0
        rm = _ROTATION_RE.search(text)
        if rm:
            info.rotation = float(rm.group(1))
            if _rotation_swaps_dimensions(info.rotation):
                info.width, info.height = info.height, info.width

    info.has_audio = bool(_AUDIO_RE.search(text))
    if info.duration <= 0:
        raise FFmpegError(f"영상 길이를 알 수 없습니다: {path}")
    return info


# --------------------------------------------------------------------------
# 추출 유틸
# --------------------------------------------------------------------------


def extract_audio(src: str | Path, dst: str | Path, *, sample_rate: int = 16000) -> Path:
    """분석용 모노 wav 추출."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-acodec", "pcm_s16le", str(dst)])
    return dst


def extract_thumbnail(src: str | Path, t: float, dst: str | Path, *, width: int = 320) -> Path | None:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg(["-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
                "-vf", f"scale={width}:-2", str(dst)])
    except FFmpegError:
        return None
    return dst if dst.exists() else None


def format_timecode(seconds: float, *, ms: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if ms:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    if h:
        return f"{h:d}:{m:02d}:{int(s):02d}"
    return f"{m:d}:{int(s):02d}"
