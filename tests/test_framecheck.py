"""화면 보기를 붙이기 전 실측.

값은 계산할 수 있지만 **폰이 견디는지는 계산으로 알 수 없다.** 그래서
실제로 뽑아 보고 환산한다. 이 파일은 그 환산과 판정이 맞는지 본다.
"""

import pytest

from gameedit.framecheck import FrameReport, SLOW_RATIO, measure


def test_hourly_numbers_come_from_the_sample():
    """30초치를 재서 1시간치로 환산한다."""
    r = FrameReport(ok=True, sample_seconds=30.0, sample_took=15.0,
                    kb_per_frame=10.0, frames_per_hour=3600,
                    seconds_per_hour=15.0 / 30.0 * 3600.0,
                    mb_per_hour=10.0 * 3600 / 1024.0, every=1.0)
    assert r.seconds_per_hour == pytest.approx(1800.0)     # 30분
    assert r.ratio == pytest.approx(0.5)


def test_slow_device_is_told_to_use_a_wider_interval():
    """느린 기기에 1초 간격을 권하면 Termux 가 죽는다."""
    r = FrameReport(ok=True, sample_seconds=30.0, sample_took=25.0,
                    kb_per_frame=8.0, frames_per_hour=3600,
                    seconds_per_hour=3000.0, mb_per_hour=28.0)
    text = " ".join(r.advice(every=1.0))
    assert "권합니다" in text and "2" in text


def test_fast_device_gets_a_green_light():
    r = FrameReport(ok=True, sample_seconds=30.0, sample_took=2.0,
                    kb_per_frame=8.0, frames_per_hour=3600,
                    seconds_per_hour=240.0, mb_per_hour=28.0)
    assert "쓸 만합니다" in " ".join(r.advice(every=1.0))
    assert FrameReport(ok=True, seconds_per_hour=240.0).ratio < SLOW_RATIO


def test_large_upload_warns_about_wifi():
    r = FrameReport(ok=True, sample_seconds=30.0, sample_took=2.0,
                    kb_per_frame=40.0, frames_per_hour=3600,
                    seconds_per_hour=240.0, mb_per_hour=140.0)
    assert "와이파이" in " ".join(r.advice())


def test_missing_file_reports_instead_of_raising():
    """재보기가 죽으면 안 된다. 이유를 적어 돌려준다."""
    r = measure("/없는/영상.mp4")
    assert r.ok is False and r.error
    assert "재보지 못했습니다" in r.advice()[0]
