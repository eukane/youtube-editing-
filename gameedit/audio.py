"""오디오 흥분도 분석 (표준 라이브러리만 사용).

wav(모노 16bit) 를 스트리밍으로 읽어 hop 단위 RMS 를 구하고,
'평소보다 얼마나 시끄러운가' 를 0~1 흥분도로 정규화한다.
게임 실황에서는 이 값이 리액션/교전/사망 순간과 잘 맞는다.
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

from .models import AudioAnalysis

_EPS = 1e-9
_FULL_SCALE = 32768.0


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * min(max(q, 0.0), 1.0)
    low = int(math.floor(pos))
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def quantile(values: list[float], q: float) -> float:
    return _quantile(sorted(values), q)


def moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1 or not values:
        return list(values)
    half = window // 2
    out: list[float] = []
    acc = 0.0
    # 누적합으로 O(n)
    prefix = [0.0]
    for v in values:
        acc += v
        prefix.append(acc)
    n = len(values)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def rms_envelope(wav_path: str | Path, hop: float = 0.05) -> tuple[list[float], float]:
    """wav 에서 hop 간격 RMS(dBFS) 목록을 구한다."""
    with wave.open(str(wav_path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        if width != 2:
            raise ValueError("분석용 wav 는 16bit PCM 이어야 합니다.")
        hop_frames = max(1, int(round(hop * rate)))
        db_values: list[float] = []
        block_frames = hop_frames * 200  # 한 번에 여러 hop 씩 읽는다
        leftover = array.array("h")
        while True:
            raw = wf.readframes(block_frames)
            if not raw:
                break
            samples = array.array("h")
            samples.frombytes(raw)
            if channels > 1:
                samples = array.array("h", samples[::channels])
            if leftover:
                merged = leftover
                merged.extend(samples)
                samples = merged
                leftover = array.array("h")
            total = len(samples)
            usable = (total // hop_frames) * hop_frames
            for i in range(0, usable, hop_frames):
                chunk = samples[i: i + hop_frames]
                acc = 0
                for s in chunk:
                    acc += s * s
                rms = math.sqrt(acc / len(chunk)) / _FULL_SCALE
                db_values.append(20.0 * math.log10(rms + _EPS))
            if usable < total:
                leftover = array.array("h", samples[usable:])
        if leftover:
            acc = sum(s * s for s in leftover)
            rms = math.sqrt(acc / len(leftover)) / _FULL_SCALE
            db_values.append(20.0 * math.log10(rms + _EPS))
    return db_values, hop


def find_silences(db_values: list[float], hop: float, threshold_db: float,
                  min_duration: float) -> list[tuple[float, float]]:
    silences: list[tuple[float, float]] = []
    start: int | None = None
    for i, db in enumerate(db_values):
        if db < threshold_db:
            if start is None:
                start = i
        elif start is not None:
            if (i - start) * hop >= min_duration:
                silences.append((start * hop, i * hop))
            start = None
    if start is not None and (len(db_values) - start) * hop >= min_duration:
        silences.append((start * hop, len(db_values) * hop))
    return silences


def find_peaks(excitement: list[float], hop: float, *, threshold: float,
               min_separation: float = 5.0) -> list[tuple[float, float]]:
    """서로 min_separation 이상 떨어진 지역 최댓값."""
    candidates = [(i * hop, v) for i, v in enumerate(excitement) if v >= threshold]
    if not candidates:
        return []
    candidates.sort(key=lambda x: x[1], reverse=True)
    chosen: list[tuple[float, float]] = []
    for t, v in candidates:
        if all(abs(t - ct) >= min_separation for ct, _ in chosen):
            chosen.append((t, v))
    chosen.sort(key=lambda x: x[0])
    return chosen


def excitement_curve(db_values: list[float], hop: float, *, silence_db: float = -38.0) -> list[float]:
    """dBFS 곡선을 0~1 흥분도로 변환.

    - 말/소리가 있는 구간의 중앙값을 '평소', 상위 5% 를 '최대'로 두고 정규화
    - 갑작스러운 상승(리액션 시작)에 가산점
    """
    if not db_values:
        return []
    everything = sorted(db_values)
    voiced = sorted(v for v in db_values if v > silence_db)
    median = _quantile(voiced, 0.5) if len(voiced) >= 4 else _quantile(everything, 0.5)
    loud = _quantile(voiced, 0.95) if len(voiced) >= 4 else _quantile(everything, 0.95)
    if loud - median < 6.0:
        # 말소리 구간만으로는 대비가 안 나오는 영상(정적 BGM, 두 단계 볼륨 등).
        # 전체 분포 기준으로 다시 정규화한다.
        median = _quantile(everything, 0.5)
        loud = _quantile(everything, 0.95)
    span = max(loud - median, 6.0)

    base = [min(1.0, max(0.0, (v - median) / span)) for v in db_values]
    smooth_window = max(1, int(round(0.4 / hop)))
    smoothed = moving_average(base, smooth_window)

    rise_lag = max(1, int(round(1.0 / hop)))
    curve: list[float] = []
    for i, v in enumerate(smoothed):
        prev = smoothed[max(0, i - rise_lag)]
        rise = max(0.0, v - prev)
        curve.append(min(1.0, v + 0.35 * rise))
    return curve


def analyze_audio(wav_path: str | Path, *, hop: float = 0.05, silence_db: float = -38.0,
                  min_silence: float = 0.6, peak_percentile: float = 0.93) -> AudioAnalysis:
    db_values, hop = rms_envelope(wav_path, hop)
    curve = excitement_curve(db_values, hop, silence_db=silence_db)
    silences = find_silences(db_values, hop, silence_db, min_silence)
    threshold = max(0.45, quantile(curve, peak_percentile)) if curve else 1.0
    peaks = find_peaks(curve, hop, threshold=threshold)
    return AudioAnalysis(
        hop=hop,
        rms_db=[round(v, 2) for v in db_values],
        excitement=[round(v, 4) for v in curve],
        silences=silences,
        peaks=peaks,
    )
