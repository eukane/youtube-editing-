"""실제 편집본 캡처를 보고 맞춘 자막 연출.

참고한 것 (안모리 편집본 캡처 3장):
  · 제일 센 대사는 화면 폭의 70% 를 채우는 빨간 초대형 글씨 + 검은 외곽선
  · 보통 대사는 흰색인데 **문장 안의 고유명사 하나만** 노란색
  · 괄호로 시작하는 줄은 대사가 아니라 상황 설명이고 위쪽에 작게 뜬다
"""

import pytest

from gameedit.config import Config
from gameedit.models import Analysis, AudioAnalysis, EditPlan, MediaInfo, Clip, SubtitleCue
from gameedit.subtitles import build_ass, build_subtitle_cues, colorize_words


@pytest.fixture
def scfg():
    return Config().section("subtitles")


# --------------------------------------------------------- 문장 안 부분 색

def test_only_the_named_word_changes_colour():
    out = colorize_words("포코피아은 몸이 굳어 버렸나 봅니다", ["포코피아"], "&H0033E8FF")
    assert out.startswith("{\\c&H0033E8FF&}포코피아{\\c}")
    assert "몸이 굳어" in out
    assert out.count("{\\c") == 2          # 켜고 끄고 한 번씩


def test_longer_word_wins_over_its_prefix():
    """'메타몽' 을 칠할 때 '메타' 규칙이 먼저 먹으면 안 된다."""
    out = colorize_words("아니 메타몽인데", ["메타", "메타몽"], "&H0033E8FF")
    assert "{\\c&H0033E8FF&}메타몽{\\c}" in out


def test_word_colouring_is_applied_once_per_line():
    out = colorize_words("포코피아 포코피아", ["포코피아"], "&H0033E8FF")
    assert out.count("{\\c&H0033E8FF&}") == 1


def test_no_words_configured_leaves_text_alone():
    assert colorize_words("그대로", [], "&H0033E8FF") == "그대로"
    assert colorize_words("", ["뭐든"], "&H0033E8FF") == ""


def test_configured_words_reach_the_ass_file(scfg):
    cues = [SubtitleCue(start=0.0, end=2.0, lines=["포코피아은 굳었다"], style="Main")]
    ass = build_ass(cues, [], dict(scfg, highlight_words=["포코피아"]))
    assert "{\\c&H0033E8FF&}포코피아{\\c}" in ass


# ------------------------------------------------------------- 초대형 자막

def test_impact_style_exists_and_is_much_bigger(scfg):
    ass = build_ass([], [], scfg)
    styles = {line.split(",")[0].removeprefix("Style: "): line
              for line in ass.splitlines() if line.startswith("Style: ")}
    assert "Impact" in styles and "Narr" in styles

    def size(name):
        return int(styles[name].split(",")[2])

    assert size("Impact") > size("Main") * 2.5      # 캡처에서 잰 비율
    assert size("Narr") < size("Main")
    assert styles["Impact"].split(",")[5] == "&H00000000"      # 두꺼운 검은 외곽선


def test_impact_scale_is_configurable(scfg):
    small = build_ass([], [], dict(scfg, impact_scale=1.5))
    big = build_ass([], [], dict(scfg, impact_scale=3.0))

    def impact_size(ass):
        line = next(x for x in ass.splitlines() if x.startswith("Style: Impact,"))
        return int(line.split(",")[2])

    assert impact_size(big) > impact_size(small)


# --------------------------------------------- 어떤 대사가 어떤 스타일이 되나

def _analysis(level: float) -> Analysis:
    hop = 0.1
    n = 200
    return Analysis(
        media=MediaInfo(path="/tmp/x.mp4", duration=20.0, width=1920, height=1080,
                        has_audio=True),
        audio=AudioAnalysis(hop=hop, rms_db=[-20.0] * n, excitement=[level] * n,
                            silences=[], peaks=[]),
    )


def _plan() -> EditPlan:
    plan = EditPlan(source="/tmp/x.mp4",
                    media=MediaInfo(path="/tmp/x.mp4", duration=20.0, width=1920, height=1080))
    plan.clips = [Clip(source_start=0.0, source_end=20.0)]
    plan.relayout()
    return plan


def _styles_for(text: str, level: float, scfg, **over) -> list[str]:
    from gameedit.models import Segment, Transcript

    analysis = _analysis(level)
    analysis.transcript = Transcript(segments=[Segment(start=1.0, end=3.0, text=text)])
    cues = build_subtitle_cues(_plan(), analysis, dict(scfg, **over))
    return [c.style for c in cues]


def test_loudest_short_line_becomes_impact(scfg):
    assert _styles_for("와 이거 큰일 났다", 0.95, scfg) == ["Impact"]


def test_long_line_stays_normal_even_when_loud(scfg):
    """초대형 글씨로 긴 문장을 띄우면 화면을 다 덮는다."""
    long_line = "와 이거 진짜 큰일 났다니까 어떻게 이럴 수가 있지"
    assert "Impact" not in _styles_for(long_line, 0.95, scfg)


def test_quiet_line_is_plain(scfg):
    assert _styles_for("그냥 걸어가는 중", 0.2, scfg) == ["Main"]


def test_parenthesised_line_is_narration(scfg):
    assert _styles_for("(특성이 괴짜인 모양)", 0.95, scfg) == ["Narr"]


def test_impact_can_be_turned_off(scfg):
    assert "Impact" not in _styles_for("와 큰일 났다", 0.95, scfg, impact=False)


def test_impact_margin_can_be_pushed_into_a_letterbox_band(scfg):
    """레터박스를 켜면 글자가 검은 띠 안에 들어가도록 올릴 수 있어야 한다."""
    def margin(ass):
        line = next(x for x in ass.splitlines() if x.startswith("Style: Impact,"))
        return int(line.split(",")[-2])

    assert margin(build_ass([], [], dict(scfg, impact_margin_v=140))) == 140
    assert margin(build_ass([], [], scfg)) > 0        # 0 이면 자동값


# ------------------------------- 캡처에서 확인한 것들 (강조색 로테이션·2단·타이틀)

def _impact_cues(n):
    return [SubtitleCue(start=i * 3.0, end=i * 3.0 + 2.0, lines=[f"대사{i}"], style="Impact")
            for i in range(n)]


def test_impact_colour_rotates_between_lines(scfg):
    """캡처를 보면 마젠타·주황·빨강·흰색을 줄마다 바꿔 쓴다."""
    ass = build_ass(_impact_cues(4), [], scfg)
    dialogue = [x for x in ass.splitlines() if x.startswith("Dialogue:") and "Impact" in x]
    assert len(dialogue) == 4

    import re
    overrides = {m.group(1) for x in dialogue
                 for m in re.finditer(r"\\c(&H[0-9A-F]{8})&", x)}
    assert len(overrides) >= 2, f"같은 색만 계속 나온다: {overrides}"


def test_single_impact_colour_can_be_pinned(scfg):
    ass = build_ass(_impact_cues(4), [], dict(scfg, impact_color="&H002020F0"))
    dialogue = [x for x in ass.splitlines() if x.startswith("Dialogue:") and "Impact" in x]
    assert not any("\\c&H" in x for x in dialogue)   # 스타일 색 그대로
    assert "&H002020F0" in next(x for x in ass.splitlines()
                                if x.startswith("Style: Impact,"))


def test_two_tier_keeps_the_previous_line_above(scfg):
    """캡처: 이전 줄은 작게 위, 현재 줄은 크게 아래."""
    cues = [SubtitleCue(start=0.0, end=1.8, lines=["하다보니 재미있어서"], style="Main"),
            SubtitleCue(start=2.0, end=4.0, lines=["개추~"], style="Impact")]
    ass = build_ass(cues, [], dict(scfg, two_tier=True))

    prev = [x for x in ass.splitlines() if ",Prev," in x]
    assert len(prev) == 1
    assert "하다보니 재미있어서" in prev[0]

    styles = {x.split(",")[0].removeprefix("Style: "): x
              for x in ass.splitlines() if x.startswith("Style: ")}
    assert int(styles["Prev"].split(",")[2]) < int(styles["Impact"].split(",")[2])
    assert int(styles["Prev"].split(",")[-2]) > int(styles["Impact"].split(",")[-2])


def test_two_tier_ignores_a_distant_previous_line(scfg):
    cues = [SubtitleCue(start=0.0, end=1.0, lines=["한참 전 대사"], style="Main"),
            SubtitleCue(start=30.0, end=32.0, lines=["개추~"], style="Impact")]
    ass = build_ass(cues, [], dict(scfg, two_tier=True, two_tier_gap=3.0))
    assert ",Prev," not in ass


def test_two_tier_is_off_by_default(scfg):
    cues = [SubtitleCue(start=0.0, end=1.8, lines=["앞줄"], style="Main"),
            SubtitleCue(start=2.0, end=4.0, lines=["개추~"], style="Impact")]
    assert ",Prev," not in build_ass(cues, [], scfg)


def test_title_card_draws_black_then_date_then_title(scfg):
    """캡처: 검은 화면 + 노란 날짜 + 흰 제목."""
    ass = build_ass([], [], dict(scfg, title="방송 시작전 썰 모음",
                                 title_date="2024. 03. 20 (수)", title_seconds=2.5))
    assert ",TitleBg," in ass and "m 0 0 l" in ass       # 전체 화면 검은 판
    assert "2024. 03. 20 (수)" in ass and ",TitleDate," in ass
    assert "방송 시작전 썰 모음" in ass and ",TitleName," in ass

    styles = {x.split(",")[0].removeprefix("Style: "): x
              for x in ass.splitlines() if x.startswith("Style: ")}
    assert styles["TitleDate"].split(",")[3] == "&H0000E8FF"    # 노랑
    assert styles["TitleName"].split(",")[3] == "&H00FFFFFF"    # 흰색


def test_no_title_card_when_not_configured(scfg):
    assert ",TitleBg," not in build_ass([], [], scfg)


def test_title_card_without_a_date_still_works(scfg):
    ass = build_ass([], [], dict(scfg, title="제목만"))
    assert ",TitleName," in ass and ",TitleDate," not in ass


def test_with_title_card_pulls_from_the_project_section():
    from gameedit.subtitles import with_title_card

    config = Config()
    config.set("project.title", "오늘의 방송")
    config.set("project.title_date", "2026. 07. 31")
    merged = with_title_card(config.section("subtitles"), config.section("project"))
    assert merged["title"] == "오늘의 방송"
    assert merged["title_date"] == "2026. 07. 31"
    assert merged["max_lines"] == config.section("subtitles")["max_lines"]


def test_phrase_colour_wins_over_whole_line_emphasis(scfg):
    """줄 전체를 노랗게 칠하면 '바로 |전구 폼 로토무!' 의 대비가 사라진다."""
    styles = _styles_for("바로 전구 폼 로토무!", 0.8, scfg,
                         highlight_words=["전구 폼 로토무"])
    assert styles == ["Main"], "강조 단어가 있는 줄은 바탕을 흰색으로 둬야 한다"

    # 강조 단어가 없으면 평소대로 Emph
    assert _styles_for("그냥 큰 소리", 0.8, scfg, highlight_words=["없는말"]) == ["Emph"]


def test_multi_word_phrase_is_coloured_as_one_block():
    out = colorize_words("바로 전구 폼 로토무!", ["전구 폼 로토무"], "&H002090F0")
    assert out.startswith("바로 {\\c&H002090F0&}전구 폼 로토무{\\c}")
