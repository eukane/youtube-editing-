"""용어 설명 자막 — 대사에 나온 이름에 작은 설명을 붙인다.

가장 중요한 성질: **편집 중에 인터넷을 쓰지 않는다.** 미리 받아 둔 파일만
읽는다. 폰이 오프라인이거나 느리면 편집이 통째로 실패하기 때문이다.
"""

import json

import pytest

from gameedit.config import Config
from gameedit.glossary import find_terms, load_glossary, plan_glossary_cues
from gameedit.models import (Analysis, AudioAnalysis, Clip, EditPlan, MediaInfo,
                             Segment, Transcript)


@pytest.fixture
def gcfg():
    # 기본은 꺼져 있다. 여기서는 켠 상태의 동작을 본다 (꺼짐은 아래 별도 테스트).
    return dict(Config().section("glossary"), enabled=True)


def _setup(*texts):
    media = MediaInfo(path="/tmp/x.mp4", duration=300.0, width=1280, height=720,
                      has_audio=True)
    analysis = Analysis(media=media,
                        audio=AudioAnalysis(hop=0.1, rms_db=[-20.0] * 100,
                                            excitement=[0.5] * 100))
    analysis.transcript = Transcript(segments=[
        Segment(start=i * 30.0, end=i * 30.0 + 2.0, text=t) for i, t in enumerate(texts)])
    plan = EditPlan(source="/tmp/x.mp4", media=media)
    plan.clips = [Clip(source_start=0.0, source_end=300.0)]
    plan.relayout()
    return plan, analysis


def test_builtin_pokemon_dictionary_exists():
    """미리 받아 둔 사전. 편집 중에 인터넷을 쓰지 않기 위한 것."""
    gloss = load_glossary(["pokemon.json"])
    assert len(gloss) > 900
    assert gloss["로토무"] == "전기 · 고스트"
    assert gloss["피카츄"] == "전기"


def test_missing_file_is_not_fatal():
    assert load_glossary(["없는파일.json"]) == {}
    assert load_glossary([]) == {}


def test_broken_file_is_skipped(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ 망가진", encoding="utf-8")
    assert load_glossary([str(bad)]) == {}


def test_custom_dictionary(tmp_path):
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps({"우리길드": "3년째 같이 하는 사람들"}, ensure_ascii=False),
                    encoding="utf-8")
    assert load_glossary([str(mine)])["우리길드"] == "3년째 같이 하는 사람들"


def test_one_letter_terms_are_dropped(tmp_path):
    """한 글자짜리는 아무 문장에나 걸려서 도배가 된다."""
    mine = tmp_path / "m.json"
    mine.write_text(json.dumps({"뮤": "에스퍼", "뮤츠": "에스퍼"}, ensure_ascii=False),
                    encoding="utf-8")
    gloss = load_glossary([str(mine)])
    assert "뮤" not in gloss and "뮤츠" in gloss


def test_longer_name_wins():
    gloss = {"로토무": "전기 · 고스트", "전기로토무": "전기"}
    assert find_terms("전기로토무가 나왔다", gloss) == ["전기로토무"]


def test_cue_is_created_for_a_mentioned_name(gcfg):
    plan, analysis = _setup("로토무가 나왔다")
    cues = plan_glossary_cues(plan, analysis, load_glossary(["pokemon.json"]), gcfg)
    assert len(cues) == 1
    assert "로토무" in cues[0].lines[0] and "전기" in cues[0].lines[0]
    assert cues[0].style == "Narr", "설명 자막은 작게, 대사와 다른 자리에"


def test_same_term_is_not_repeated_too_soon(gcfg):
    plan, analysis = _setup("로토무다", "또 로토무네")      # 30초 간격
    cues = plan_glossary_cues(plan, analysis, load_glossary(["pokemon.json"]),
                              dict(gcfg, cooldown=90.0))
    assert len(cues) == 1, "같은 용어를 계속 띄우면 도배가 된다"


def test_same_term_returns_after_the_cooldown(gcfg):
    plan, analysis = _setup("로토무다", "또 로토무네")
    cues = plan_glossary_cues(plan, analysis, load_glossary(["pokemon.json"]),
                              dict(gcfg, cooldown=10.0))
    assert len(cues) == 2


def test_once_per_term_mode(gcfg):
    plan, analysis = _setup("로토무다", "또 로토무네")
    cues = plan_glossary_cues(plan, analysis, load_glossary(["pokemon.json"]),
                              dict(gcfg, cooldown=0.0, once_per_term=True))
    assert len(cues) == 1


def test_only_one_term_per_line(gcfg):
    plan, analysis = _setup("로토무랑 피카츄가 같이 나왔다")
    cues = plan_glossary_cues(plan, analysis, load_glossary(["pokemon.json"]), gcfg)
    assert len(cues) == 1, "한 문장에 여러 개를 띄우면 화면이 복잡하다"


def test_cut_scenes_get_no_cue(gcfg):
    """편집에서 잘려나간 구간의 설명은 나오면 안 된다."""
    plan, analysis = _setup("로토무가 나왔다")
    plan.clips = [Clip(source_start=100.0, source_end=200.0)]   # 0초 부근이 빠짐
    plan.relayout()
    assert plan_glossary_cues(plan, analysis, load_glossary(["pokemon.json"]), gcfg) == []


def test_disabled_by_default():
    assert Config().section("glossary")["enabled"] is False


def test_plan_includes_glossary_when_enabled():
    from gameedit.plan import build_plan

    plan, analysis = _setup("로토무가 나왔다", "피카츄도 있네")
    config = Config()
    config.set("glossary.enabled", True)
    config.set("glossary.files", ["pokemon.json"])
    built = build_plan(analysis, config)
    assert built.meta["glossary_terms"] >= 1
    assert any(c.speaker == "glossary" for c in built.subtitles)
