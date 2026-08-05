"""화면 보기(vision)를 붙이기 전에, **이 기기에서 될 일인지** 재 본다.

AI 가 영상을 보려면 1초에 한 장씩 작은 그림을 뽑아 보내야 한다. 1시간이면
3,600장이다. 값은 계산할 수 있지만 **폰이 견디는지는 계산으로 알 수 없다.**
추정하지 말고 실제로 30초치를 뽑아 보고, 거기서 1시간치를 환산한다.

여기서 재는 것은 셋이다.
  · 시간   — 그림을 뽑는 데 얼마나 걸리나 (Termux 가 죽던 그 문제와 같은 종류)
  · 용량   — 업로드해야 할 양 (와이파이 아니면 데이터가 나간다)
  · 저장공간 — 중간 파일이 들어갈 자리가 있나
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .media import ffmpeg, probe
from .system import available_memory_mb

# 실제로 뽑아 볼 길이. 짧으면 시작 비용에 묻히고, 길면 재는 데만 오래 걸린다.
SAMPLE_SECONDS = 30.0

# 화면 훑기용 크기. 작아도 "무슨 일이 벌어지는지" 는 보인다.
SCAN_WIDTH = 320
SCAN_HEIGHT = 180

# 이 정도를 넘으면 폰에서 1초 간격은 무리다 (뽑는 데만 원본 길이의 절반)
SLOW_RATIO = 0.5


@dataclass
class FrameReport:
    ok: bool = False
    seconds_per_hour: float = 0.0     # 1시간 영상의 그림을 뽑는 데 걸릴 시간(초)
    mb_per_hour: float = 0.0          # 그때 만들어질 그림의 총 용량
    frames_per_hour: int = 0
    kb_per_frame: float = 0.0
    sample_seconds: float = 0.0       # 실제로 재 본 길이
    sample_took: float = 0.0
    every: float = 1.0
    free_mb: float = 0.0
    error: str = ""

    @property
    def ratio(self) -> float:
        """원본 길이 대비 걸리는 시간. 0.5 면 1시간짜리에 30분."""
        return self.seconds_per_hour / 3600.0 if self.seconds_per_hour else 0.0

    def advice(self, *, every: float = 1.0) -> list[str]:
        """사람이 읽을 판정. **무엇을 해야 하는지**까지 적는다."""
        if not self.ok:
            return [f"재보지 못했습니다: {self.error}"]
        lines = [
            f"1시간 영상 기준 — 그림 {self.frames_per_hour:,}장 뽑는 데 "
            f"약 {self.seconds_per_hour / 60:.0f}분",
            f"만들어질 그림 용량 약 {self.mb_per_hour:.0f}MB "
            f"(장당 {self.kb_per_frame:.1f}KB)",
        ]
        if self.ratio >= SLOW_RATIO:
            lines.append(
                f"⚠ 원본 길이의 {self.ratio * 100:.0f}% 가 그림 뽑는 데만 듭니다. "
                f"{every:.0f}초 간격 대신 {every * 2:.0f}~{every * 5:.0f}초 간격을 권합니다.")
        else:
            lines.append(f"이 기기에서 {every:.0f}초 간격은 쓸 만합니다.")
        if self.mb_per_hour > 100:
            lines.append("⚠ 업로드 양이 큽니다. 와이파이에서 돌리세요.")
        return lines


def measure(video: str | Path, *, every: float = 1.0, work_dir: Path | None = None,
            sample_seconds: float = SAMPLE_SECONDS) -> FrameReport:
    """영상 가운데에서 짧게 뽑아 보고 1시간치를 환산한다.

    가운데를 쓰는 이유: 앞부분은 로고·로딩 화면이라 압축이 유난히 잘 돼서
    실제보다 빠르고 가볍게 나온다.
    """
    report = FrameReport(every=every, free_mb=available_memory_mb())
    try:
        info = probe(video)
    except Exception as err:
        report.error = str(err)[:160]
        return report
    if info.duration <= 0:
        report.error = "영상 길이를 읽지 못했습니다"
        return report

    span = min(sample_seconds, info.duration)
    start = max(0.0, (info.duration - span) / 2.0)
    report.sample_seconds = span

    owned = work_dir is None
    out_dir = Path(work_dir or tempfile.mkdtemp(prefix="gameedit-frames-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        began = time.time()
        # `-ss` 를 입력 앞에 둬야 앞부분을 통째로 디코딩하지 않는다.
        ffmpeg(["-ss", f"{start:.3f}", "-i", str(video), "-t", f"{span:.3f}",
                "-vf", f"fps=1/{max(0.1, every):.4f},scale={SCAN_WIDTH}:{SCAN_HEIGHT}",
                "-q:v", "6", str(out_dir / "f%05d.jpg")])
        report.sample_took = time.time() - began

        files = sorted(out_dir.glob("f*.jpg"))
        if not files:
            report.error = "그림이 한 장도 안 나왔습니다"
            return report
        total_bytes = sum(f.stat().st_size for f in files)
        report.kb_per_frame = total_bytes / len(files) / 1024.0
        report.frames_per_hour = int(3600.0 / max(0.1, every))
        report.mb_per_hour = report.kb_per_frame * report.frames_per_hour / 1024.0
        report.seconds_per_hour = report.sample_took / span * 3600.0
        report.ok = True
        return report
    except Exception as err:
        report.error = str(err)[:160]
        return report
    finally:
        if owned:
            shutil.rmtree(out_dir, ignore_errors=True)
