"""자막 만드는 데 시간이 얼마나 걸릴지, 이 기기에서 직접 재 본다.

폰마다 성능이 열 배씩 차이나서 "1시간이면 20분쯤" 같은 추정은 의미가 없다.
짧은 조각 두 개를 **실제로 돌려 보고** 이 기기의 속도를 잰 다음, 그 값으로
영상 전체 시간을 계산한다.

조각을 두 개 쓰는 이유가 있다. 모델을 메모리에 올리는 시간은 오디오 길이와
상관없이 매번 똑같이 든다. 한 번만 재면 그 고정 비용까지 길이에 비례한다고
착각해서 긴 영상의 예상 시간을 크게 부풀린다.

    짧은 조각(6초)  걸린 시간 = 모델 올리기 + 6초 × 속도
    긴 조각(45초)   걸린 시간 = 모델 올리기 + 45초 × 속도
    두 식을 빼면                45-6초       ×  속도   ← 속도만 남는다

메모리도 같이 본다. 램이 모자라면 느린 게 아니라 그냥 죽기 때문에, 돌리기
전에 미리 알려 줘야 한다.
"""

from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .media import extract_audio, probe
from .transcribe import find_whisper_model, resolve_backend, transcribe

Logger = Callable[[str], None]

SHORT_SAMPLE = 6.0
LONG_SAMPLE = 45.0

# 모델을 올리는 데 실제로 필요한 램 (whisper.cpp 기준, 대략).
# 모델 파일이 있으면 파일 크기로 다시 계산하므로 이건 참고값이다.
MODEL_RAM_MB = {
    "tiny": 400.0,
    "base": 550.0,
    "small": 1100.0,
    "medium": 2700.0,
    "large": 4400.0,
}

VERDICT_NOTE = {
    "ok": "메모리 넉넉합니다.",
    "tight": "메모리가 빠듯합니다. 다른 앱을 모두 닫고 돌리세요.",
    "impossible": "이 기기 메모리로는 이 모델을 못 돌립니다. 더 작은 모델을 쓰세요.",
    "unknown": "메모리를 확인할 수 없어 그냥 진행합니다.",
}


def _noop(_msg: str) -> None:
    pass


@dataclass
class SpeedReport:
    """이 기기에서 잰 자막 속도."""

    backend: str = "none"
    model: str = ""
    ok: bool = False
    error: str = ""

    load_seconds: float = 0.0            # 모델 올리는 데 걸리는 고정 시간
    seconds_per_minute: float = 0.0      # 오디오 1분당 걸리는 초
    measured: list[tuple[float, float]] = field(default_factory=list)  # (조각길이, 걸린시간)

    sample_text: str = ""                # 인식된 대사 (정확도를 눈으로 확인하라고)
    sample_lines: int = 0

    memory_available_mb: float = 0.0
    memory_needed_mb: float = 0.0
    memory_verdict: str = "unknown"      # ok | tight | impossible | unknown

    source_duration: float = 0.0

    def predict(self, duration: float) -> float:
        """길이 duration(초) 짜리 영상의 자막에 걸릴 시간(초)."""
        return self.load_seconds + max(0.0, float(duration)) / 60.0 * self.seconds_per_minute

    @property
    def memory_note(self) -> str:
        return VERDICT_NOTE.get(self.memory_verdict, "")

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "ok": self.ok,
            "error": self.error,
            "load_seconds": round(self.load_seconds, 1),
            "seconds_per_minute": round(self.seconds_per_minute, 1),
            "sample_text": self.sample_text,
            "sample_lines": self.sample_lines,
            "memory_available_mb": round(self.memory_available_mb),
            "memory_needed_mb": round(self.memory_needed_mb),
            "memory_verdict": self.memory_verdict,
            "memory_note": self.memory_note,
            "source_duration": round(self.source_duration, 1),
            "predicted_seconds": round(self.predict(self.source_duration), 1),
            "predicted_hour_seconds": round(self.predict(3600.0), 1),
            "summary": self.summary(),
        }

    def summary(self) -> str:
        """사람이 읽을 한 문단."""
        if not self.ok:
            return self.error or "속도를 재지 못했습니다."
        lines = [f"오디오 1분당 약 {self.seconds_per_minute:.0f}초 걸립니다."]
        if self.source_duration > 0:
            lines.append(f"이 영상({human_time(self.source_duration)})이면 "
                         f"약 {human_time(self.predict(self.source_duration))}.")
        lines.append(f"1시간짜리면 약 {human_time(self.predict(3600.0))}.")
        if self.memory_verdict != "ok":
            lines.append(self.memory_note)
        return " ".join(lines)


def human_time(seconds: float) -> str:
    """초 → '3분', '1시간 12분'. 예상 시간은 초 단위까지 볼 이유가 없다."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f}초"
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"{minutes}분"
    return f"{minutes // 60}시간 {minutes % 60}분" if minutes % 60 else f"{minutes // 60}시간"


# ---------------------------------------------------------------- 메모리

def available_memory_mb() -> float:
    """지금 실제로 더 쓸 수 있는 램(MB). 못 알아내면 0.

    `MemTotal` 이 아니라 `MemAvailable` 을 본다. 안드로이드는 총 4GB 라도
    시스템과 다른 앱이 이미 절반 넘게 쓰고 있어서, 총량으로 판단하면 죽는다.
    """
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    m = re.search(r"^MemAvailable:\s+(\d+)\s*kB", text, re.MULTILINE)
    if not m:
        return 0.0
    return int(m.group(1)) / 1024.0


def model_memory_mb(model_name: str, model_path: str | None = None) -> float:
    """모델을 올리는 데 필요한 램(MB).

    파일이 있으면 파일 크기로 계산한다. whisper.cpp 는 가중치를 통째로 올린
    뒤 계산용 버퍼를 더 쓰기 때문에 파일 크기보다 조금 더 든다.
    """
    if model_path:
        try:
            size_mb = Path(model_path).stat().st_size / (1024 * 1024)
            return size_mb * 1.35 + 180.0
        except OSError:
            pass
    name = (model_name or "").lower()
    for key, need in MODEL_RAM_MB.items():
        if key in name:
            return need
    return MODEL_RAM_MB["base"]


def memory_verdict(needed_mb: float, available_mb: float) -> str:
    if available_mb <= 0:
        return "unknown"
    if needed_mb <= available_mb * 0.6:
        return "ok"
    if needed_mb <= available_mb * 0.95:
        return "tight"
    return "impossible"


# ---------------------------------------------------------------- 속도 측정

def _sample_windows(duration: float) -> list[tuple[float, float]]:
    """어디를 잘라서 재 볼지. (시작, 길이) 두 개.

    영상 가운데에서 뽑는다. 앞뒤는 인트로·엔딩이라 말이 없을 때가 많고, 말이
    없으면 인식기가 일찍 끝나 버려서 속도가 실제보다 빠르게 나온다.
    """
    duration = max(0.0, float(duration))
    if duration <= 0:
        return [(0.0, SHORT_SAMPLE), (0.0, LONG_SAMPLE)]

    long_len = min(LONG_SAMPLE, max(6.0, duration * 0.5))
    short_len = min(SHORT_SAMPLE, max(1.0, long_len * 0.15))
    # 긴 조각을 가운데에 놓고, 짧은 조각은 그 바로 앞에서 뽑는다
    long_start = max(0.0, duration * 0.5 - long_len / 2)
    short_start = max(0.0, long_start - short_len - 1.0)
    if short_start + short_len > duration:
        short_start = 0.0
    return [(short_start, short_len), (long_start, long_len)]


def _solve(measured: list[tuple[float, float]]) -> tuple[float, float]:
    """(조각길이, 걸린시간) 들 → (모델 올리는 시간, 오디오 1분당 초).

    두 점을 잇는 직선의 기울기와 절편이다. 측정이 흔들려 기울기가 음수로
    나오면(짧은 조각이 더 오래 걸린 경우) 직선을 못 믿으므로, 긴 조각
    하나만으로 단순 비례 계산한다.
    """
    if not measured:
        return 0.0, 0.0
    if len(measured) == 1:
        length, elapsed = measured[0]
        return 0.0, (elapsed / length * 60.0 if length > 0 else 0.0)

    (a_len, a_time), (b_len, b_time) = sorted(measured)
    span = b_len - a_len
    if span <= 0.5:
        return 0.0, (b_time / b_len * 60.0 if b_len > 0 else 0.0)
    rate = (b_time - a_time) / span            # 오디오 1초당 초
    if rate <= 0:
        return 0.0, (b_time / b_len * 60.0 if b_len > 0 else 0.0)
    load = max(0.0, b_time - rate * b_len)
    return load, rate * 60.0


def measure(source: str | Path, config, *, log: Logger = _noop) -> SpeedReport:
    """영상에서 짧은 조각 두 개를 잘라 실제로 자막을 만들어 보고 속도를 잰다."""
    options = dict(config.section("transcribe"))
    backend = resolve_backend(str(options.get("backend", "auto")),
                              str(options.get("external", "")), options)
    report = SpeedReport(backend=backend, model=str(options.get("model") or ""))

    try:
        report.source_duration = float(probe(source).duration or 0.0)
    except Exception:                                   # 길이를 몰라도 측정은 된다
        report.source_duration = 0.0

    if backend == "external":
        report.ok = True
        report.error = ""
        report.sample_text = "밖에서 만든 자막 파일을 쓰므로 폰에서 인식하지 않습니다."
        return report
    if backend == "none":
        report.error = ("자막을 만들 프로그램이 없습니다. Termux 에서 "
                        "`bash install-subtitles.sh` 를 먼저 실행해 주세요.")
        return report

    model_path = find_whisper_model(str(options.get("whisper_cpp_model", "")),
                                    str(options.get("model", "")))
    if model_path:
        report.model = Path(model_path).name
    report.memory_available_mb = available_memory_mb()
    report.memory_needed_mb = model_memory_mb(str(options.get("model", "")), model_path)
    report.memory_verdict = memory_verdict(report.memory_needed_mb, report.memory_available_mb)

    if report.memory_verdict == "impossible":
        report.error = (
            f"'{report.model or options.get('model')}' 모델은 약 "
            f"{report.memory_needed_mb:.0f}MB 가 필요한데 지금 쓸 수 있는 메모리는 "
            f"{report.memory_available_mb:.0f}MB 뿐입니다. 돌리면 기기가 멈춥니다. "
            "더 작은 모델(base 또는 tiny)을 받아 주세요.")
        return report

    windows = _sample_windows(report.source_duration)
    with tempfile.TemporaryDirectory(prefix="gameedit-speed-") as tmp:
        for index, (start, length) in enumerate(windows):
            wav = Path(tmp) / f"sample{index}.wav"
            try:
                extract_audio(source, wav, start=start, duration=length)
            except Exception as err:
                report.error = f"소리를 뽑아내지 못했습니다: {err}"
                return report
            if not wav.exists() or wav.stat().st_size < 1024:
                report.error = "이 영상에서 소리를 찾지 못했습니다."
                return report

            log(f"  · {length:.0f}초 조각 측정 중…")
            began = time.monotonic()
            try:
                transcript = transcribe(wav, options)
            except Exception as err:
                report.error = f"자막을 만들지 못했습니다: {err}"
                return report
            elapsed = time.monotonic() - began
            report.measured.append((length, elapsed))

            if index == len(windows) - 1:               # 긴 조각의 결과를 보여 준다
                text = " ".join(s.text for s in transcript.segments).strip()
                report.sample_text = text[:200]
                report.sample_lines = len(transcript.segments)

    report.load_seconds, report.seconds_per_minute = _solve(report.measured)
    report.ok = report.seconds_per_minute > 0
    if not report.ok:
        report.error = "속도가 0 으로 나왔습니다. 이 조각에 말이 없었을 수 있습니다."
    return report
