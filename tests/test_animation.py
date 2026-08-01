"""자막이 뜨는 방식.

제일 중요한 성질: **효과가 자막 길이를 넘지 않는 것.** 0.3초짜리 자막에
0.29초짜리 등장 효과를 넣으면 글자가 제 크기가 되기도 전에 사라진다.
화면에 뭐가 스쳤는지도 모르게 되고, 그건 연출이 아니라 결함이다.
"""

import re

import pytest

from gameedit.animation import ENTER_SHARE, MOTIONS, entrance, resolve_level
from gameedit.config import Config
from gameedit.models import MemeCue, SubtitleCue
from gameedit.subtitles import build_ass


def fade_times(tag: str) -> tuple[int, int]:
    m = re.search(r"\\fad\((\d+),(\d+)\)", tag)
    assert m, f"페이드가 없다: {tag}"
    return int(m.group(1)), int(m.group(2))


def anim_end(tag: str) -> int:
    """등장 효과가 끝나는 시각(ms)."""
    ends = [int(m) for m in re.findall(r"\\t\(\d+,(\d+),", tag)]
    fade_in = fade_times(tag)[0]
    return max(ends + [fade_in])


# ------------------------------------------------- 짧은 자막에서 안 깨진다

@pytest.mark.parametrize("style", ["Main", "Narr", "Emph", "Impact", "MemeTop"])
@pytest.mark.parametrize("duration", [0.2, 0.3, 0.5, 1.0, 3.0])
def test_effect_never_outlasts_the_cue(style, duration):
    tag = entrance(style, duration, "full")
    budget = duration * 1000 * ENTER_SHARE
    assert anim_end(tag) <= budget + 1, f"{style} {duration}s: 효과가 자막보다 길다"


def test_short_impact_still_reaches_full_size():
    """0.3초 Impact 도 압축될지언정 제 크기까지는 가야 한다."""
    tag = entrance("Impact", 0.3, "full")
    assert anim_end(tag) <= 0.3 * 1000 * ENTER_SHARE
    assert tag.rstrip("}").endswith("\\fscx100\\fscy100)"), "제 크기로 안 돌아온다"


@pytest.mark.parametrize("duration", [0.05, 0.12, 0.2])
def test_too_short_to_move_falls_back_to_fade_only(duration):
    """한두 프레임짜리 크기 변화는 연출이 아니라 그냥 튐이다."""
    tag = entrance("Impact", duration, "full")
    assert "\\t(" not in tag
    assert "\\fad(" in tag


def test_long_cue_uses_the_full_nominal_timing():
    tag = entrance("Impact", 5.0, "full")
    assert fade_times(tag) == (MOTIONS["Impact"].fade_in, MOTIONS["Impact"].fade_out)


def test_zero_duration_produces_nothing():
    assert entrance("Main", 0.0, "full") == ""
    assert entrance("Main", -1.0, "full") == ""


# --------------------------------------------------------- 스타일별 성격

def test_dialogue_subtitles_stay_cheap():
    """대사는 화면에 제일 자주 나온다. 크기 변화를 주면 렌더가 눈에 띄게 느려진다."""
    tag = entrance("Main", 3.0, "full")
    assert "\\fscx" not in tag and "\\frz" not in tag
    assert "\\fad(" in tag


def test_styles_do_not_all_look_the_same():
    """전부 같은 효과면 넣으나 마나다."""
    tags = {s: entrance(s, 3.0, "full") for s in ("Main", "Narr", "Emph", "Impact", "MemeTop")}
    assert len(set(tags.values())) == len(tags)


def test_impact_slams_in_tilted():
    tag = entrance("Impact", 3.0, "full")
    assert "\\frz-7" in tag          # 비스듬히 시작해서
    assert "\\frz0" in tag           # 똑바로 앉는다


def test_emphasis_overshoots_then_settles():
    tag = entrance("Emph", 3.0, "full")
    assert "\\fscx115" in tag        # 지나쳤다가
    assert "\\fscx100\\fscy100" in tag   # 제자리로


# --------------------------------------------------------------- 단계 설정

def test_light_level_is_fade_only():
    for style in ("Main", "Narr", "Emph", "Impact", "MemeTop"):
        tag = entrance(style, 3.0, "light")
        assert "\\t(" not in tag, f"{style}: light 인데 움직인다"
        assert "\\fad(" in tag


def test_off_level_is_empty():
    for style in ("Main", "Impact", "MemeTop"):
        assert entrance(style, 3.0, "off") == ""


def test_old_config_keeps_working():
    """업데이트했다고 남의 설정이 조용히 달라지면 안 된다."""
    assert resolve_level({"pop_animation": True}) == "full"
    assert resolve_level({"pop_animation": False}) == "off"
    assert resolve_level({}) == "full"
    # 새 설정이 있으면 그게 이긴다
    assert resolve_level({"animation": "light", "pop_animation": False}) == "light"


@pytest.mark.parametrize("raw,expect", [
    ("full", "full"), ("LIGHT", "light"), ("off", "off"),
    ("true", "full"), ("no", "off"), ("이상한값", "full"),
])
def test_level_strings(raw, expect):
    assert resolve_level({"animation": raw}) == expect


# ------------------------------------------------------- 실제 파일에 반영

def sample_ass(**overrides) -> str:
    cfg = Config().section("subtitles")
    cfg.update(overrides)
    cues = [
        SubtitleCue(start=0.0, end=2.0, lines=["평범한 대사"], style="Main"),
        SubtitleCue(start=2.0, end=2.25, lines=["짧은 강조"], style="Impact"),
        SubtitleCue(start=3.0, end=5.0, lines=["(설명)"], style="Narr"),
    ]
    memes = [MemeCue(start=1.0, duration=1.5, meme_id="m", text="ㅋㅋ", style="MemeTop")]
    return build_ass(cues, memes, cfg)


def test_ass_file_has_per_style_effects():
    body = sample_ass()
    assert "\\frz-7" in body            # Impact
    assert "\\fscx0\\fscy0" in body     # 밈 팝

def test_short_cue_in_the_real_file_is_clamped():
    """0.25초짜리 Impact 가 파일에서도 압축돼 있어야 한다."""
    line = [ln for ln in sample_ass().splitlines() if ",Impact," in ln][0]
    assert anim_end(line) <= 0.25 * 1000 * ENTER_SHARE + 1


def test_turning_animation_off_removes_all_of_it():
    body = sample_ass(animation="off")
    assert "\\fad(" not in body and "\\t(" not in body


def test_light_keeps_fades_but_drops_motion():
    body = sample_ass(animation="light")
    assert "\\fad(" in body
    assert "\\t(" not in body
