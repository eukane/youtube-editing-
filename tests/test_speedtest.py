"""자막 속도 재보기.

기기마다 열 배씩 차이나서 "1시간이면 20분쯤" 같은 추정은 의미가 없다. 짧은
조각을 실제로 돌려 재는데, 여기서 제일 중요한 건 **모델 올리는 시간을
길이에 비례한다고 착각하지 않는 것**이다. 그러면 긴 영상 시간을 크게
부풀려서 사용자가 겁먹고 시작도 안 한다.
"""

import pytest

from gameedit.speedtest import (SpeedReport, _sample_windows, _solve, human_time,
                                memory_verdict, model_memory_mb)

# ------------------------------------------------------- 두 점으로 속도 풀기


def test_load_time_is_separated_from_speed():
    """6초에 13초, 45초에 52초 → 고정 10초 + 오디오 1초당 0.933초."""
    load, per_minute = _solve([(6.0, 13.6), (45.0, 52.0)])
    assert load == pytest.approx(8.0, abs=0.5)
    assert per_minute == pytest.approx(59.0, abs=1.0)


def test_one_point_estimate_would_overshoot():
    """왜 두 점을 재는지. 한 점만 쓰면 고정 비용까지 비례한다고 본다."""
    two = SpeedReport(ok=True)
    two.load_seconds, two.seconds_per_minute = _solve([(6.0, 13.6), (45.0, 52.0)])
    one = SpeedReport(ok=True)
    one.load_seconds, one.seconds_per_minute = _solve([(6.0, 13.6)])
    # 짧은 조각 하나로 재면 1시간 예상이 두 배 넘게 부풀어 오른다
    assert one.predict(3600) > two.predict(3600) * 2


def test_backwards_measurement_falls_back_to_simple_ratio():
    """짧은 조각이 더 오래 걸린 이상한 측정. 직선을 믿으면 안 된다."""
    load, per_minute = _solve([(6.0, 30.0), (45.0, 20.0)])
    assert load == 0.0
    assert per_minute == pytest.approx(20.0 / 45.0 * 60.0, abs=0.1)


def test_no_measurement_is_zero_not_crash():
    assert _solve([]) == (0.0, 0.0)


def test_predict_is_linear_after_load():
    report = SpeedReport(load_seconds=10.0, seconds_per_minute=30.0)
    assert report.predict(60) == pytest.approx(40.0)
    assert report.predict(3600) == pytest.approx(1810.0)
    assert report.predict(0) == pytest.approx(10.0)


# ------------------------------------------------------------ 잴 구간 고르기


def test_samples_come_from_the_middle():
    """앞뒤는 인트로·엔딩이라 말이 없다. 조용한 데를 재면 속도가 뻥튀기된다."""
    (short_start, short_len), (long_start, long_len) = _sample_windows(3600.0)
    assert 3600 * 0.3 < long_start < 3600 * 0.7
    assert long_len == 45.0
    assert short_len == 6.0
    assert short_start + short_len <= long_start


def test_short_video_still_gets_two_samples():
    windows = _sample_windows(20.0)
    assert len(windows) == 2
    for start, length in windows:
        assert length > 0
        assert start + length <= 20.0 + 0.001


def test_unknown_duration_does_not_crash():
    assert len(_sample_windows(0.0)) == 2


# ------------------------------------------------------------------ 메모리


@pytest.mark.parametrize("needed,available,expect", [
    (550, 2000, "ok"),
    (1100, 1400, "tight"),
    (2700, 1800, "impossible"),
    (550, 0, "unknown"),
])
def test_memory_verdict(needed, available, expect):
    assert memory_verdict(needed, available) == expect


def test_model_size_comes_from_the_real_file_when_present(tmp_path):
    """표에 없는 이름(양자화 모델 등)이어도 파일이 있으면 정확히 잰다."""
    model = tmp_path / "ggml-base-q5_1.bin"
    model.write_bytes(b"0" * (60 * 1024 * 1024))
    need = model_memory_mb("base-q5_1", str(model))
    assert 240 < need < 280       # 60MB × 1.35 + 180


def test_model_size_falls_back_to_table():
    assert model_memory_mb("medium") > model_memory_mb("base")
    assert model_memory_mb("모르는이름") == model_memory_mb("base")


# -------------------------------------------------------------- 보고 문구


@pytest.mark.parametrize("seconds,expect", [
    (12, "12초"), (90, "2분"), (1800, "30분"), (3600, "1시간"), (4500, "1시간 15분"),
])
def test_human_time(seconds, expect):
    assert human_time(seconds) == expect


def test_failed_report_says_why():
    report = SpeedReport(ok=False, error="자막 프로그램이 없습니다")
    assert "없습니다" in report.summary()
    assert report.as_dict()["ok"] is False


def test_report_dict_has_what_the_screen_needs():
    report = SpeedReport(ok=True, load_seconds=8.0, seconds_per_minute=30.0,
                         source_duration=600.0, backend="whisper.cpp",
                         model="ggml-base.bin")
    data = report.as_dict()
    assert data["predicted_seconds"] == pytest.approx(308.0)
    assert data["predicted_hour_seconds"] == pytest.approx(1808.0)
    assert "약 5분" in data["summary"]
