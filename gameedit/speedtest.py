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

import array
import re
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .media import extract_audio, probe
from .system import available_memory_mb
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
    # 받아 둔 모델 전부와, 왜 이걸 골랐는지. 조용히 작은 걸 쓰면 사용자는
    # 더 좋은 모델을 받아 놓고도 아무 일이 안 일어난 줄 안다.
    models_found: list[str] = field(default_factory=list)
    model_note: str = ""

    source_duration: float = 0.0
    no_speech: bool = False              # 인식 결과가 '말이 없어서 지어낸 것' 같은지

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
            "no_speech": self.no_speech,
            "memory_available_mb": round(self.memory_available_mb),
            "memory_needed_mb": round(self.memory_needed_mb),
            "memory_verdict": self.memory_verdict,
            "models_found": self.models_found,
            "model_note": self.model_note,
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

PROBE_POINTS = 8
PROBE_SECONDS = 2.5

# 이 정도도 안 되면 사실상 무음이다 (16bit 기준 RMS)
QUIET_RMS = 200.0


def _rms(wav_path: Path) -> float:
    """wav 조각의 소리 크기. 말이 있는지 없는지 가늠하는 용도."""
    try:
        with wave.open(str(wav_path)) as handle:
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        return 0.0
    if len(frames) < 4:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frames[:len(frames) - len(frames) % 2])
    if not samples:
        return 0.0
    total = sum(float(s) * s for s in samples)
    return (total / len(samples)) ** 0.5


def find_talking_point(source, duration: float, *, log: Logger = _noop) -> float:
    """말이 있을 만한 지점을 찾는다.

    조용한 구간을 재면 두 가지가 다 틀어진다. 인식기가 할 일이 없어 일찍
    끝나서 **속도가 실제보다 몇 배 빠르게** 나오고, 결과로 나오는 글자는
    인식기가 지어낸 헛소리라 사용자가 "모델이 고장났나" 로 오해한다.
    실제로 `[몇일이 없음] [몇일이 없음]` 이 나오고 1시간에 5분이라는
    말도 안 되는 값이 찍혔다.

    영상 곳곳에서 짧게 소리 크기를 재고 제일 시끄러운 곳을 고른다.
    (말인지 게임 소리인지는 구분 못 하지만, 무음보다는 훨씬 낫다)
    """
    duration = max(0.0, float(duration))
    if duration <= PROBE_SECONDS * 2:
        return 0.0

    best_start, best_rms = duration * 0.5, -1.0
    with tempfile.TemporaryDirectory(prefix="gameedit-probe-") as tmp:
        for i in range(PROBE_POINTS):
            # 앞뒤 10% 는 인트로·엔딩이라 건너뛴다
            start = duration * (0.1 + 0.8 * i / max(1, PROBE_POINTS - 1))
            start = min(start, max(0.0, duration - PROBE_SECONDS))
            probe_wav = Path(tmp) / f"p{i}.wav"
            try:
                extract_audio(source, probe_wav, start=start, duration=PROBE_SECONDS)
            except Exception:
                continue
            level = _rms(probe_wav)
            if level > best_rms:
                best_start, best_rms = start, level

    if best_rms < QUIET_RMS:
        log("  · 영상 전체가 조용합니다. 속도가 실제보다 빠르게 나올 수 있습니다.")
    return best_start


def _sample_windows(duration: float, center: float = -1.0) -> list[tuple[float, float]]:
    """어디를 잘라서 재 볼지. (시작, 길이) 두 개.

    center 는 말이 있을 것 같은 지점. 안 주면 영상 가운데를 쓴다.
    """
    duration = max(0.0, float(duration))
    if duration <= 0:
        return [(0.0, SHORT_SAMPLE), (0.0, LONG_SAMPLE)]

    long_len = min(LONG_SAMPLE, max(6.0, duration * 0.5))
    short_len = min(SHORT_SAMPLE, max(1.0, long_len * 0.15))
    if center < 0:
        center = duration * 0.5
    long_start = min(max(0.0, center), max(0.0, duration - long_len))
    short_start = max(0.0, long_start - short_len - 1.0)
    if short_start + short_len > duration:
        short_start = 0.0
    return [(short_start, short_len), (long_start, long_len)]


def looks_like_no_speech(text: str) -> bool:
    """인식 결과가 '말이 없어서 지어낸 것' 처럼 보이는지.

    whisper 는 무음 구간에서 `[음악]` 같은 태그나 같은 문구 반복을 뱉는다.
    그걸 그대로 보여 주면 사용자는 모델이 고장난 줄 안다.
    """
    text = (text or "").strip()
    if not text:
        return True
    # 대괄호·괄호 태그를 걷어내고 남는 게 거의 없으면 말이 아니다
    stripped = re.sub(r"[\[\(][^\]\)]*[\]\)]", "", text).strip()
    if len(stripped) < max(4, len(text) * 0.3):
        return True
    # 같은 조각이 계속 반복되면 (환각의 전형적인 모습)
    chunks = [c for c in re.split(r"[\s.,!?]+", text) if c]
    return len(chunks) >= 3 and len(set(chunks)) <= max(1, len(chunks) // 3)


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


MODEL_QUALITY = ("tiny", "base", "small", "medium", "large")


def describe_model_choice(chosen: str | None) -> tuple[list[str], str]:
    """받아 둔 모델 목록과, 왜 이걸 골랐는지 한 줄 설명.

    더 좋은 모델을 받아 놓고도 프로그램이 계속 작은 걸 쓰면 사용자는 아무
    일도 안 일어난 줄 안다. 실제로 그래서 두 번을 헛돌았다.
    """
    from .transcribe import installed_models, model_fits

    found = installed_models()
    names = [f"{p.name} ({p.stat().st_size // 1024 // 1024}MB)" for p in found]
    if not found:
        return names, "받아 둔 모델이 없습니다."
    if not chosen:
        return names, "쓸 수 있는 모델을 못 찾았습니다."

    picked = Path(chosen)
    bigger = [p for p in found if p.stat().st_size > picked.stat().st_size]
    if not bigger:
        rank = next((i for i, q in enumerate(MODEL_QUALITY) if q in picked.name.lower()), -1)
        if 0 <= rank < len(MODEL_QUALITY) - 1:
            nxt = MODEL_QUALITY[rank + 1]
            return names, (f"받아 둔 것 중 제일 큰 모델입니다. 정확도를 더 올리려면 "
                           f"`cd ~/gameedit && bash install-subtitles.sh {nxt}`")
        return names, "받아 둔 것 중 제일 큰 모델입니다."

    available = available_memory_mb()
    blocked = [p.name for p in bigger if not model_fits(p, available)]
    if blocked:
        return names, (f"더 큰 모델({', '.join(blocked)})이 있지만 지금 메모리로는 "
                       f"못 올립니다. 다른 앱을 닫고 다시 재보세요.")
    return names, "더 큰 모델이 있는데 설정이 이 크기를 지정하고 있습니다."


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
    report.models_found, report.model_note = describe_model_choice(model_path)
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

    # 조용한 구간을 재면 인식기가 일찍 끝나서 속도가 몇 배 빠르게 나오고,
    # 결과 글자도 지어낸 헛소리라 "모델이 고장났나" 로 오해하게 된다.
    log("  · 말이 있는 구간을 찾는 중…")
    center = find_talking_point(source, report.source_duration, log=log)
    windows = _sample_windows(report.source_duration, center)
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
                report.no_speech = looks_like_no_speech(text)

    report.load_seconds, report.seconds_per_minute = _solve(report.measured)
    report.ok = report.seconds_per_minute > 0
    if not report.ok:
        report.error = "속도가 0 으로 나왔습니다. 이 조각에 말이 없었을 수 있습니다."
    return report
