"""참고 영상에서 편집 리듬을 재는 기능.

영상을 '보는' 건 못 하지만 컷 간격과 죽은 시간은 정확히 잴 수 있다.
그 측정값이 설정으로 제대로 옮겨지는지를 본다.
"""

import pytest

from gameedit.config import Config
from gameedit.learn import (StyleProfile, describe, merge_profiles, save_style,
                            style_to_config)


def make(**kw) -> StyleProfile:
    base = dict(source="ref.mp4", duration=600.0, cut_count=200,
                cuts_per_minute=20.0, median_cut=2.0, p25_cut=1.2, p75_cut=4.0,
                silence_ratio=0.03, longest_silence=1.1)
    base.update(kw)
    return StyleProfile(**base)


# ------------------------------------------------------------- 측정 → 설정

def test_tight_reference_produces_tight_settings():
    """무음이 거의 없는 편집본을 넣으면 우리도 짧은 무음부터 잘라내야 한다."""
    settings = style_to_config(make(silence_ratio=0.02))
    assert settings["editing.dead_air_min"] == 0.3
    assert settings["editing.dead_air_keep"] == 0.08


def test_loose_reference_produces_loose_settings():
    settings = style_to_config(make(silence_ratio=0.25))
    assert settings["editing.dead_air_min"] == 0.8
    assert settings["editing.dead_air_keep"] == 0.15


def test_faster_reference_means_denser_settings():
    """컷이 잦은 영상을 참고하면 조각도 짧고 밈도 촘촘해져야 한다."""
    fast = style_to_config(make(cuts_per_minute=32.0, median_cut=1.2, p25_cut=0.7,
                                p75_cut=2.0))
    slow = style_to_config(make(cuts_per_minute=4.0, median_cut=9.0, p25_cut=5.0,
                                p75_cut=15.0))

    assert fast["editing.dead_air_min_piece"] < slow["editing.dead_air_min_piece"]
    assert fast["highlight.min_clip"] < slow["highlight.min_clip"]
    assert fast["memes.max_per_minute"] > slow["memes.max_per_minute"]
    assert fast["memes.cooldown"] < slow["memes.cooldown"]


def test_extreme_reference_is_clamped():
    """이상한 영상 하나로 설정이 망가지면 안 된다."""
    settings = style_to_config(make(cuts_per_minute=600.0, median_cut=0.05,
                                    p25_cut=0.02, p75_cut=0.1))
    assert settings["editing.dead_air_min_piece"] >= 0.4
    assert settings["highlight.min_clip"] >= 2.0
    assert settings["memes.max_per_minute"] <= 9.0

    huge = style_to_config(make(cuts_per_minute=0.1, median_cut=400.0,
                                p25_cut=300.0, p75_cut=500.0))
    assert huge["highlight.min_clip"] <= 12.0
    assert huge["highlight.max_clip"] <= 60.0


def test_empty_profile_yields_nothing():
    assert style_to_config(StyleProfile()) == {}


# --------------------------------------------------------------- 여러 편 평균

def test_multiple_references_are_averaged():
    """한 편만 보고 정하면 그 편의 특성에 휘둘린다."""
    merged = merge_profiles([make(cuts_per_minute=10.0, median_cut=4.0),
                             make(cuts_per_minute=30.0, median_cut=2.0)])
    assert merged.cuts_per_minute == pytest.approx(20.0)
    assert merged.median_cut == pytest.approx(3.0)
    assert "2편" in merged.source


def test_unmeasurable_references_are_skipped():
    merged = merge_profiles([StyleProfile(), make(cuts_per_minute=12.0)])
    assert merged.cuts_per_minute == pytest.approx(12.0)


def test_merging_nothing_is_safe():
    assert merge_profiles([]).duration == 0.0
    assert style_to_config(merge_profiles([])) == {}


# --------------------------------------------------------------- 저장 / 설명

def test_saved_style_is_a_usable_config(tmp_path):
    """저장한 파일을 -c 로 그대로 넘길 수 있어야 한다."""
    profile = make(silence_ratio=0.02, cuts_per_minute=30.0, median_cut=1.5,
                   p25_cut=0.9, p75_cut=3.0)
    path = save_style(tmp_path / "style.yaml", style_to_config(profile), profile)

    config = Config.load(path)
    assert config.section("editing")["dead_air_min"] == 0.3
    assert config.section("highlight")["min_clip"] < Config().section("highlight")["min_clip"]
    # 어떤 영상에서 뽑았는지 되짚을 수 있어야 한다
    assert config.data["_measured"]["cuts_per_minute"] == 30.0


def test_describe_is_honest_about_what_it_could_not_measure():
    profile = make(cut_count=0, cuts_per_minute=0.0, median_cut=0.0,
                   notes=["컷을 거의 못 찾았습니다"])
    text = describe(profile)
    assert "⚠" in text and "컷을 거의 못 찾았습니다" in text


@pytest.mark.parametrize("cpm,word", [(30.0, "촘촘"), (14.0, "빠른"),
                                      (6.0, "보통"), (1.0, "느긋")])
def test_describe_classifies_the_pace(cpm, word):
    assert word in describe(make(cuts_per_minute=cpm))
