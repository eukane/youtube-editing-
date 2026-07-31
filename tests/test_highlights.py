import pytest

from gameedit.config import Config
from gameedit.highlights import build_clips, build_score_grid, label_for


@pytest.fixture
def hcfg():
    return Config().section("highlight")


def test_score_grid_favours_loud_and_keyword_moments(analysis, hcfg):
    grid = build_score_grid(analysis, hcfg)
    assert len(grid.values) >= 180
    assert grid.mean(28, 34) > grid.mean(70, 80)
    assert grid.mean(88, 94) > grid.mean(70, 80)


def test_build_clips_picks_the_hype_moments(analysis, hcfg):
    hcfg = dict(hcfg, target_duration=60.0)
    clips = build_clips(analysis, hcfg)

    assert clips, "하이라이트를 하나도 못 골랐다"
    assert clips == sorted(clips, key=lambda c: c.source_start)
    covered = [c for c in clips if c.source_start <= 30.0 <= c.source_end]
    assert covered, "가장 텐션 높은 30초 부근이 빠졌다"
    for clip in clips:
        assert clip.duration > 0
        assert 0 <= clip.source_start < clip.source_end <= analysis.media.duration
        assert clip.label


def test_target_duration_is_respected(analysis, hcfg):
    short = build_clips(analysis, dict(hcfg, target_duration=20.0))
    long = build_clips(analysis, dict(hcfg, target_duration=120.0))
    assert sum(c.duration for c in short) <= sum(c.duration for c in long)
    assert sum(c.duration for c in short) < 60.0


def test_exclude_range_is_dropped(analysis, hcfg):
    clips = build_clips(analysis, dict(hcfg, target_duration=90.0,
                                       exclude_ranges=[[20.0, 45.0]]))
    for clip in clips:
        assert not (25.0 <= clip.source_start <= 40.0)


def test_must_include_range_is_kept(analysis, hcfg):
    clips = build_clips(analysis, dict(hcfg, target_duration=30.0,
                                       must_include_ranges=[[70.0, 80.0]]))
    assert any(c.source_start <= 75.0 <= c.source_end for c in clips)


def test_clips_snap_to_word_boundaries(analysis, hcfg):
    clips = build_clips(analysis, dict(hcfg, target_duration=60.0, snap_to_speech=True,
                                       pad_before=0.0, pad_after=0.0))
    words = analysis.transcript.words()
    for clip in clips:
        # 컷 지점이 단어 한가운데를 자르면 안 된다 (스냅 허용 오차 내)
        for w in words:
            if w.start < clip.source_start < w.end:
                pytest.fail(f"단어 중간에서 시작: {w.text}")


def test_punch_effect_on_peak_clips(analysis, hcfg):
    clips = build_clips(analysis, dict(hcfg, target_duration=60.0))
    assert any("punch" in c.effects for c in clips)


def test_label_categories():
    assert label_for("아 죽었다 진짜", 1)[0] == "death"
    assert label_for("이겼다!", 2)[0] == "win"
    assert label_for("평범한 대사", 3)[1].startswith("🔥")


def test_zero_length_video_returns_no_clips(analysis, hcfg):
    analysis.audio.excitement = []
    analysis.transcript.segments = []
    analysis.scenes = []
    analysis.media.duration = 0.0
    assert build_clips(analysis, hcfg) == []


def test_falls_back_to_even_clips_when_there_is_no_signal(analysis, hcfg):
    """마이크 없이 녹화해서 소리도 대사도 없을 때 빈손으로 끝내면 안 된다."""
    analysis.audio.excitement = []
    analysis.audio.peaks = []
    analysis.audio.silences = []
    analysis.transcript.segments = []
    analysis.scenes = []
    analysis.media.duration = 300.0

    clips = build_clips(analysis, dict(hcfg, target_duration=60.0))
    assert clips, "신호가 없어도 클립은 나와야 한다"
    assert all(c.reason == "fallback" for c in clips)
    assert clips == sorted(clips, key=lambda c: c.source_start)
    for clip in clips:
        assert 0 <= clip.source_start < clip.source_end <= 300.0
    total = sum(c.duration for c in clips)
    assert 30 <= total <= 90          # 목표(60초) 언저리
    assert all(c.label for c in clips)


def test_video_shorter_than_min_clip_uses_whole_thing(analysis, hcfg):
    """4초짜리 테스트 영상으로 돌려봐도 결과가 나와야 한다."""
    analysis.audio.excitement = []
    analysis.transcript.segments = []
    analysis.scenes = []
    analysis.media.duration = 4.0

    clips = build_clips(analysis, hcfg)
    assert len(clips) == 1
    assert clips[0].source_start == 0.0
    assert clips[0].source_end == 4.0
