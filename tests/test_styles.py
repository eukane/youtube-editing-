"""편집 스타일 프리셋.

전에는 다른 AI 가 정리한 유튜버 5인의 수치를 프리셋으로 들고 있었다.
실제 편집본 캡처를 받아 보니 그 자료의 자막 설명이 틀렸고, 나머지 항목도
확인할 방법이 없어서 근거가 있는 하나만 남겼다. 이 테스트는 그 원칙이
지켜지는지를 본다.
"""

import pytest

from gameedit.config import Config
from gameedit.editing import apply_zooms
from gameedit.models import Clip
from gameedit.styles import EVIDENCE, STYLES, describe, get, names, resolve


def test_only_styles_backed_by_evidence_are_shipped():
    """근거 없는 수치로 만든 프리셋은 맞는지 틀린지 확인할 길이 없다."""
    assert set(STYLES) <= set(EVIDENCE)
    for key in STYLES:
        assert EVIDENCE[key], f"{key} 에 근거가 하나도 없다"


def test_the_unverifiable_presets_are_gone():
    for removed in ("seungsangsing", "kangjiwon", "bate", "baljep"):
        assert removed not in STYLES
        with pytest.raises(ValueError):
            get(removed)


@pytest.mark.parametrize("key", list(STYLES))
def test_style_loads_into_a_config(key):
    config = Config()
    for dotted, value in get(key).items():
        config.set(dotted, value)
    assert config.section("subtitles")["impact_scale"] > 1.0


def test_korean_name_works():
    assert resolve("안모리") == "anmori"
    assert get("안모리") == get("anmori")


def test_unknown_style_points_at_the_alternative():
    with pytest.raises(ValueError) as err:
        get("없는사람")
    assert "편집 강도" in str(err.value)      # 컷 속도는 그쪽으로 안내
    assert names()


def test_describe_lists_what_was_actually_seen():
    text = describe("안모리")
    assert "캡처에서 확인한 것" in text
    assert "2단 자막" in text


def test_style_only_sets_what_the_captures_showed():
    """정지 화면으로는 컷 길이·밈 빈도를 알 수 없다. 넣으면 안 된다."""
    keys = set(get("anmori"))
    assert not any(k.startswith("editing.dead_air") for k in keys)
    assert not any(k.startswith("highlight.") for k in keys)
    assert not any(k.startswith("memes.max_per_minute") for k in keys)


# ------------------------------------------------------------------- 줌 제어

def test_zoom_rate_is_capped_per_minute():
    """분당 횟수를 정하면 그보다 많이 걸리면 안 된다."""
    clips = [Clip(source_start=i * 10.0, source_end=i * 10.0 + 10.0, score=i / 30.0)
             for i in range(30)]                       # 결과물 300초 = 5분
    cfg = {"zoom": True, "zoom_min": 1.2, "zoom_max": 1.5, "zoom_per_minute": 2.0}

    apply_zooms(clips, cfg)
    zoomed = [c for c in clips if "punch" in c.effects]
    assert len(zoomed) == 10                           # 5분 × 2회
    assert all(1.2 <= c.zoom <= 1.5 for c in zoomed)
    assert max(zoomed, key=lambda c: c.score).zoom == pytest.approx(1.5)


def test_fixed_zoom_uses_one_amount():
    clips = [Clip(source_start=i * 10.0, source_end=i * 10.0 + 10.0, score=i / 10.0)
             for i in range(10)]
    apply_zooms(clips, {"zoom": True, "zoom_min": 2.5, "zoom_max": 2.5,
                        "zoom_per_minute": 3.5})
    zoomed = [c for c in clips if "punch" in c.effects]
    assert zoomed and all(c.zoom == 2.5 for c in zoomed)


def test_zoom_can_be_turned_off():
    clips = [Clip(source_start=0.0, source_end=10.0, score=1.0, effects=["punch"])]
    apply_zooms(clips, {"zoom": False})
    assert "punch" not in clips[0].effects


def test_bridges_are_never_zoomed():
    """빨리감기로 흘려보내는 구간을 확대하면 정신없다."""
    clips = [Clip(source_start=0.0, source_end=10.0, score=0.9, reason="bridge"),
             Clip(source_start=20.0, source_end=30.0, score=0.5)]
    apply_zooms(clips, {"zoom": True, "zoom_min": 1.2, "zoom_max": 1.5,
                        "zoom_per_minute": 60.0})
    assert "punch" not in clips[0].effects
    assert "punch" in clips[1].effects
