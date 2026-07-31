"""오디오/장면 분석 단위 테스트."""

import math
import struct
import wave

import pytest

from gameedit.audio import (analyze_audio, excitement_curve, find_peaks,
                            find_silences, moving_average, quantile)
from gameedit.scenes import parse_scene_output, scene_burst_curve
from gameedit.models import SceneChange


def write_wav(path, blocks, rate=16000):
    """blocks = [(길이초, 진폭0~1), ...] 로 테스트용 wav 생성."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        phase = 0.0
        for duration, amplitude in blocks:
            for _ in range(int(duration * rate)):
                phase += 2 * math.pi * 440 / rate
                value = int(amplitude * 30000 * math.sin(phase))
                frames += struct.pack("<h", value)
        wf.writeframes(bytes(frames))
    return path


def test_quantile_and_moving_average():
    assert quantile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert quantile([], 0.5) == 0.0
    assert moving_average([0, 3, 0], 3) == pytest.approx([1.5, 1.0, 1.5])
    assert moving_average([1, 2], 1) == [1, 2]


def test_rms_and_excitement(tmp_path):
    wav = write_wav(tmp_path / "a.wav", [(2.0, 0.05), (1.0, 0.9), (2.0, 0.05)])
    analysis = analyze_audio(wav, hop=0.05)

    assert analysis.duration == pytest.approx(5.0, abs=0.2)
    # 가운데 시끄러운 구간이 조용한 구간보다 흥분도가 높아야 한다
    assert analysis.mean_between(2.2, 2.8) > analysis.mean_between(0.2, 1.5)
    assert 0.0 <= analysis.at(2.5) <= 1.0
    assert analysis.peaks, "피크가 하나도 잡히지 않았다"
    peak_t = analysis.peaks[0][0]
    assert 1.8 <= peak_t <= 3.4


def test_find_silences_detects_quiet_block():
    hop = 0.1
    db = [-60.0] * 10 + [-10.0] * 10 + [-60.0] * 10
    silences = find_silences(db, hop, -38.0, 0.5)
    assert silences[0] == pytest.approx((0.0, 1.0))
    assert silences[1] == pytest.approx((2.0, 3.0))


def test_find_peaks_respects_separation():
    curve = [0.0] * 100
    curve[10] = 0.9
    curve[12] = 0.85
    curve[80] = 0.95
    peaks = find_peaks(curve, 0.1, threshold=0.5, min_separation=5.0)
    times = [t for t, _ in peaks]
    assert times == pytest.approx([1.0, 8.0])


def test_excitement_curve_empty():
    assert excitement_curve([], 0.05) == []


def test_parse_scene_output():
    text = (
        "frame:0 pts:1000 pts_time:1.5\n"
        "lavfi.scene_score=0.421\n"
        "frame:1 pts:2000 pts_time:9.25\n"
        "lavfi.scene_score=0.8\n"
    )
    scenes = parse_scene_output(text)
    assert [s.t for s in scenes] == [1.5, 9.25]
    assert scenes[1].score == pytest.approx(0.8)


def test_scene_burst_curve_peaks_where_cuts_cluster():
    scenes = [SceneChange(t=t) for t in (10.0, 10.5, 11.0, 11.5, 12.0)]
    curve = scene_burst_curve(scenes, duration=30.0, hop=1.0, window=6.0)
    assert curve[11] > curve[25]
    assert max(curve) <= 1.0


def test_two_level_audio_still_produces_contrast(tmp_path):
    """조용한 배경 + 가끔 큰 소리만 있는 영상에서도 흥분도가 살아 있어야 한다."""
    wav = write_wav(tmp_path / "b.wav",
                    [(3.0, 0.004), (2.0, 0.6), (3.0, 0.004), (2.0, 0.6), (3.0, 0.004)])
    analysis = analyze_audio(wav, hop=0.05)
    assert max(analysis.excitement) > 0.6
    assert analysis.mean_between(3.5, 4.5) > 0.5
    assert analysis.mean_between(0.5, 2.5) < 0.2
    assert len(analysis.peaks) >= 2
