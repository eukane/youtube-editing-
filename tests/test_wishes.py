"""요구사항 칸 — 적은 말을 설정으로 바꾼다.

이 프로그램은 AI 가 아니라 정해진 표현만 알아듣는다. 그래서 가장 중요한
성질은 "못 알아들은 걸 못 알아들었다고 말하는 것" 이다. 조용히 무시하면
사용자는 반영된 줄 알고 결과를 오해한다.
"""

import pytest

from gameedit.config import Config
from gameedit.wishes import apply, parse


def test_empty_input_does_nothing():
    got = parse("")
    assert got.settings == {} and got.matched == [] and got.ignored == []


def test_length_in_minutes():
    got = parse("3분으로 만들어줘")
    assert got.settings["highlight.target_duration"] == 180
    assert "3분" in got.matched[0]


def test_several_requests_in_one_sentence():
    got = parse("3분으로, 자막 크게, 밈은 빼줘")
    assert got.settings["highlight.target_duration"] == 180
    assert got.settings["subtitles.font_size"] == 80
    assert got.settings["memes.enabled"] is False
    assert len(got.matched) == 3
    assert got.ignored == []


def test_unknown_request_is_reported_not_swallowed():
    """제일 중요한 성질. 모르면 모른다고 해야 한다."""
    got = parse("배경음악 깔아줘")
    assert got.settings == {}
    assert got.ignored == ["배경음악 깔아줘"]


def test_known_and_unknown_are_separated():
    got = parse("3분으로, 배경음악 깔아줘")
    assert got.settings["highlight.target_duration"] == 180
    assert got.ignored == ["배경음악 깔아줘"]
    assert any("3분" in m for m in got.matched)


def test_understood_clause_is_not_reported_as_ignored():
    """알아들은 문장의 남은 글자를 '모르겠다' 로 보고하면 오해를 준다."""
    got = parse("죽는 장면 위주로 편집해줘")
    assert got.matched == ["죽는 장면 위주"]
    assert got.ignored == []


# ------------------------------------------------------------- 구간 지정

def test_must_include_range():
    got = parse("12:30~15:00 은 꼭 넣어줘")
    assert got.settings["highlight.must_include_ranges"] == [[750, 900]]
    assert "꼭 넣기" in got.matched[0]


def test_exclude_range():
    got = parse("0:00~2:00 은 빼줘")
    assert got.settings["highlight.exclude_ranges"] == [[0, 120]]
    assert "빼기" in got.matched[0]


def test_backwards_range_is_ignored():
    got = parse("15:00~12:30 넣어줘")
    assert "highlight.must_include_ranges" not in got.settings


def test_hours_are_supported():
    got = parse("1:02:30~1:05:00 꼭 넣어줘")
    assert got.settings["highlight.must_include_ranges"] == [[3750, 3900]]


# ------------------------------------------------------- 장면 성향 / 사전

@pytest.mark.parametrize("text,expect", [
    ("죽는 장면 위주로", "죽는 장면 위주"),
    ("웃긴 것만 모아줘", "웃긴 장면 위주"),
    ("이기는 장면 위주로", "잘한 장면 위주"),
    ("놀라는 부분 위주로", "놀라는 장면 위주"),
])
def test_focus_keywords(text, expect):
    got = parse(text)
    assert expect in got.matched
    assert got.settings["highlight.focus_keywords"]


def test_glossary_request_turns_on_the_dictionary():
    got = parse("포켓몬 타입 알려줘")
    assert got.settings["glossary.enabled"] is True
    # 특정 게임 파일을 박으면 다른 게임에서는 쓸 수 없다. 가진 사전을 전부 쓴다.
    assert got.settings["glossary.files"] == []


def test_glossary_request_works_without_saying_pokemon():
    """포켓몬만 하는 게 아니다."""
    for text in ("용어 설명 넣어줘", "이름 나오면 정보 알려줘", "정보 표시해줘"):
        assert parse(text).settings.get("glossary.enabled") is True, text


# --------------------------------------------------------- 설정에 반영

def test_apply_changes_the_config():
    config = Config()
    apply(config, "3분으로, 자막 크게")
    assert config.get("highlight.target_duration") == 180
    assert config.section("subtitles")["font_size"] == 80


def test_focus_keywords_are_added_not_replaced():
    """덮어쓰면 다른 장면을 아예 못 찾게 된다."""
    config = Config()
    before = set(config.get("highlight.keywords"))
    apply(config, "죽는 장면 위주로")
    after = set(config.get("highlight.keywords"))
    assert before <= after
    assert "리스폰" in after


def test_shorts_request():
    config = Config()
    got = apply(config, "세로로 만들어줘")
    assert config.get("project.resolution") == "1080x1920"
    assert got.ignored == []


def test_long_input_is_capped_but_still_parsed():
    got = parse("3분으로 " + "그리고 아무말 " * 50)
    assert got.settings["highlight.target_duration"] == 180
    assert len(got.ignored) <= 6, "못 알아들은 걸 끝없이 나열하면 화면이 넘친다"
