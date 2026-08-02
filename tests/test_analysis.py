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


def test_rotated_video_reports_real_frame_size():
    """폰으로 찍은 세로 영상은 회전 정보만 붙어 있고 저장은 가로로 돼 있다.

    ffmpeg 이 디코딩할 때 돌려주므로, 우리가 들고 있는 크기도 돌린 뒤 기준이어야
    스케일·자막 좌표계가 맞는다.
    """
    from gameedit.media import _rotation_swaps_dimensions

    assert _rotation_swaps_dimensions(90) is True
    assert _rotation_swaps_dimensions(-90) is True
    assert _rotation_swaps_dimensions(270) is True
    assert _rotation_swaps_dimensions(180) is False
    assert _rotation_swaps_dimensions(0) is False


def test_rotation_parsed_from_ffmpeg_output():
    from gameedit.media import _ROTATION_RE

    text = "    Side data:\n      displaymatrix: rotation of -90.00 degrees\n"
    assert _ROTATION_RE.search(text).group(1) == "-90.00"


# ------------------------------- 밖에서 만들어 온 자막은 음성 인식이 아니다

from gameedit.models import AudioAnalysis

def _fake_probe(has_audio=True):
    from gameedit.models import MediaInfo

    def probe(_src):
        return MediaInfo(path="/tmp/a.mp4", duration=90.0, width=1920, height=1080,
                         fps=30.0, has_audio=has_audio)
    return probe


def _srt(tmp_path):
    path = tmp_path / "밖에서.srt"
    path.write_text(
        "1\n00:00:03,000 --> 00:00:06,500\n와 이거 진짜 대박이다\n\n"
        "2\n00:00:20,000 --> 00:00:23,500\n아 죽었어 미쳤나 진짜\n",
        encoding="utf-8")
    return path


def _analyze_with_srt(tmp_path, monkeypatch, *, skip_transcribe, has_audio=True):
    from gameedit import analyze as mod
    from gameedit.config import Config

    monkeypatch.setattr(mod, "probe", _fake_probe(has_audio))
    monkeypatch.setattr(mod, "extract_audio", lambda *a, **k: tmp_path / "audio.wav")
    monkeypatch.setattr(mod, "analyze_audio", lambda *a, **k: AudioAnalysis(hop=0.5))
    monkeypatch.setattr(mod, "detect_scenes", lambda *a, **k: [])

    config = Config()
    config.set("analyze.scene_threshold", 0)
    config.set("transcribe.external", str(_srt(tmp_path)))
    return mod.analyze_video("/tmp/a.mp4", config, tmp_path / "work",
                             skip_transcribe=skip_transcribe)


def test_external_subtitles_survive_no_transcribe(tmp_path, monkeypatch):
    """--no-transcribe 는 '음성 인식을 돌리지 마라'는 뜻이다.

    .srt 파일을 읽는 건 음성 인식이 아니라 텍스트 파일 읽기다(몇 밀리초).
    같이 건너뛰면 사용자는 자막을 넣었는데 결과물에 한 줄도 안 나온다.
    권장 경로(폰 인식 끄고 .srt 쓰기)가 통째로 조용히 망가진다.
    """
    got = _analyze_with_srt(tmp_path, monkeypatch, skip_transcribe=True)
    assert got.transcript is not None
    assert len(got.transcript.segments) == 2
    assert "대박" in got.transcript.segments[0].text


def test_external_subtitles_work_without_audio(tmp_path, monkeypatch):
    """소리가 없는 영상이어도 밖에서 만든 자막은 넣을 수 있어야 한다."""
    got = _analyze_with_srt(tmp_path, monkeypatch, skip_transcribe=False, has_audio=False)
    assert got.transcript is not None
    assert len(got.transcript.segments) == 2


def test_no_transcribe_without_srt_still_skips(tmp_path, monkeypatch):
    """자막 파일이 없으면 --no-transcribe 는 원래대로 건너뛴다."""
    from gameedit import analyze as mod
    from gameedit.config import Config

    monkeypatch.setattr(mod, "probe", _fake_probe(True))
    monkeypatch.setattr(mod, "extract_audio", lambda *a, **k: tmp_path / "audio.wav")
    monkeypatch.setattr(mod, "analyze_audio", lambda *a, **k: AudioAnalysis(hop=0.5))
    monkeypatch.setattr(mod, "detect_scenes", lambda *a, **k: [])
    monkeypatch.setattr(mod, "transcribe", lambda *a, **k: pytest.fail("음성 인식이 돌면 안 된다"))

    config = Config()
    config.set("analyze.scene_threshold", 0)
    got = mod.analyze_video("/tmp/a.mp4", config, tmp_path / "work", skip_transcribe=True)
    assert not got.transcript.segments        # transcribe 가 아예 안 불렸다
