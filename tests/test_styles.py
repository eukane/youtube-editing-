"""편집 스타일 프리셋.

받은 수치가 설정으로 제대로 옮겨졌는지, 그리고 스타일마다 결과물이
실제로 달라지는지 확인한다. 수치 자체의 진위는 여기서 검증할 수 없다
(영상을 재서 얻은 값이 아니라 전달받은 값이다).
"""

import pytest

from gameedit.config import Config
from gameedit.editing import apply_zooms
from gameedit.models import Clip
from gameedit.plan import build_plan
from gameedit.styles import MEASUREMENTS, STYLES, describe, get, names, resolve


ALL = list(STYLES)


@pytest.mark.parametrize("key", ALL)
def test_every_style_matches_its_source_numbers(key):
    """표에 적힌 수치와 설정값이 어긋나면 안 된다."""
    settings = get(key)
    source = MEASUREMENTS[key]

    assert settings["editing.dead_air_min"] == source["무음 제거"]
    assert settings["highlight.min_clip"] == source["평균 컷"]
    assert settings["editing.cold_open_seconds"] == float(source["도입부"])


@pytest.mark.parametrize("key", ALL)
def test_every_style_loads_into_a_config(key):
    config = Config()
    for dotted, value in get(key).items():
        config.set(dotted, value)

    editing = config.section("editing")
    assert 0.05 <= editing["dead_air_min"] <= 1.5
    assert 1.0 < editing["zoom_min"] <= editing["zoom_max"] <= 4.0
    assert editing["cold_open_pieces"] >= 1
    assert config.section("subtitles")["max_lines"] in (1, 2)


def test_korean_names_work():
    assert resolve("안모리") == "anmori"
    assert resolve("검성 강지원") == "kangjiwon"
    assert resolve("발젭") == "baljep"
    assert get("바테") == get("bate")


def test_unknown_style_is_rejected_with_help():
    with pytest.raises(ValueError) as err:
        get("없는사람")
    assert "anmori" in str(err.value)          # 뭘 쓸 수 있는지 알려준다


def test_describe_reports_the_source_numbers():
    text = describe("승상싱")
    assert "1.6초" in text and "18" in text
    assert names()


# ------------------------------------------------------- 스타일별 차이 확인

def test_baljep_is_tighter_than_kangjiwon():
    """가장 극단(발젭)과 가장 느슨(강지원)이 실제로 반대여야 한다."""
    b, k = get("baljep"), get("kangjiwon")
    assert b["editing.dead_air_min"] < k["editing.dead_air_min"]
    assert b["highlight.min_clip"] < k["highlight.min_clip"]
    assert b["memes.transition_sfx_every"] < k["memes.transition_sfx_every"]


def test_bate_forces_single_line_subtitles():
    assert get("bate")["subtitles.max_lines"] == 1
    assert get("anmori")["subtitles.max_lines"] == 2


def test_seungsangsing_allows_the_biggest_zoom():
    """억까 순간 4배 줌이 이 스타일의 특징."""
    assert get("seungsangsing")["editing.zoom_max"] == 4.0
    assert max(get(k)["editing.zoom_max"] for k in ALL) == 4.0


def test_styles_produce_different_edits(analysis):
    """설정만 다르고 결과가 같으면 프리셋이 무의미하다."""
    results = {}
    for key in ALL:
        config = Config()
        config.set("highlight.target_duration", 90)
        for dotted, value in get(key).items():
            config.set(dotted, value)
        plan = build_plan(analysis, config)
        results[key] = (len(plan.clips), round(plan.duration, 1))

    assert len(set(results.values())) > 1, f"전부 같은 결과가 나왔다: {results}"
    for key, (clips, duration) in results.items():
        assert clips > 0 and duration > 0, f"{key} 에서 빈 결과"


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
    # 제일 센 클립이 가장 크게 당겨진다
    assert max(zoomed, key=lambda c: c.score).zoom == pytest.approx(1.5)


def test_fixed_zoom_style_uses_one_amount():
    """바테처럼 min==max 인 스타일은 배율이 하나로 고정된다."""
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


@pytest.mark.parametrize("key", ALL)
def test_analysis_resolution_matches_the_cutting_target(key):
    """분석이 0.6초 이상 무음만 기록하면 '0.1초부터 잘라라' 는 실행될 수 없다."""
    settings = get(key)
    assert settings["analyze.min_silence"] <= settings["editing.dead_air_min"], (
        f"{key}: 분석 해상도가 편집 기준보다 거칠어서 점프컷이 안 걸린다")


@pytest.mark.parametrize("key", ALL)
def test_target_cut_length_matches_the_source_table(key):
    assert get(key)["editing.target_cut_length"] == MEASUREMENTS[key]["평균 컷"]
