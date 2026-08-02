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
    base = next(x for x in ass.splitlines()
                if x.startswith("Style: Impact,")).split(",")[3]

    def colour_of(line):
        m = re.search(r"\\c(&H[0-9A-F]{8})&", line)
        return m.group(1) if m else base

    used = [colour_of(x) for x in dialogue]
    assert len(set(used)) >= 2, f"같은 색만 계속 나온다: {used}"
    # 빨강과 노랑만 쓴다 (마젠타·주황 같은 건 안 쓴다)
    assert set(used) <= {"&H002020F0", "&H0033E8FF"}


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


# ------------------------------------------------- 강조 단어 자동 선정

def _transcript(*texts):
    from gameedit.models import Segment, Transcript
    return Transcript(segments=[Segment(start=i, end=i + 1, text=t)
                                for i, t in enumerate(texts)])


def test_repeated_proper_noun_is_picked_automatically():
    """사람이 영상마다 단어를 손으로 적게 하면 아무도 안 쓴다."""
    from gameedit.subtitles import auto_highlight_words

    words = auto_highlight_words(_transcript(
        "로토무가 나왔다", "로토무는 강해", "이번에도 로토무", "그냥 평범한 대사",
    ), min_count=3)
    assert "로토무" in words          # 조사가 붙어도 같은 말로 센다


def test_common_words_are_not_picked():
    from gameedit.subtitles import auto_highlight_words

    words = auto_highlight_words(_transcript(
        "진짜 그냥 이거 ㅋㅋ", "진짜 그냥 이거 ㅋㅋ", "진짜 그냥 이거 ㅋㅋ",
    ), min_count=2)
    assert words == [], f"흔한 말에 색을 칠하면 의미가 없다: {words}"


def test_rare_words_are_not_picked():
    from gameedit.subtitles import auto_highlight_words

    words = auto_highlight_words(_transcript("한 번만 나온 뮤츠"), min_count=3)
    assert "뮤츠" not in words


def test_shorter_word_inside_a_longer_one_is_dropped():
    from gameedit.subtitles import auto_highlight_words

    words = auto_highlight_words(_transcript(
        *["전구로토무 로토무"] * 4,
    ), min_count=3)
    assert "전구로토무" in words and "로토무" not in words


def test_manual_list_overrides_the_automatic_one():
    from gameedit.subtitles import resolve_highlight_words

    transcript = _transcript(*["로토무 나왔다"] * 5)
    cfg = dict(Config().section("subtitles"), highlight_words=["내가 정한 말"])
    assert resolve_highlight_words(cfg, transcript) == ["내가 정한 말"]


def test_auto_highlight_can_be_turned_off():
    from gameedit.subtitles import resolve_highlight_words

    transcript = _transcript(*["로토무 나왔다"] * 5)
    cfg = dict(Config().section("subtitles"), auto_highlight=False)
    assert resolve_highlight_words(cfg, transcript) == []


def test_only_one_phrase_per_line_gets_colour(scfg):
    """한 줄에서 여러 단어를 칠하면 정신없다."""
    out = colorize_words("로토무와 메타몽이 같이", ["로토무", "메타몽"],
                         "&H0033E8FF", limit=1)
    assert out.count("{\\c&H0033E8FF&}") == 1


def test_plan_records_the_words_it_chose(analysis):
    from gameedit.plan import build_plan

    plan = build_plan(analysis, Config())
    assert "highlight_words" in plan.meta      # 사람이 보고 고칠 수 있게


def test_narration_is_small_and_plain(scfg):
    """자질구레한 설명은 작게, 색 없이. 색은 강조에만 쓴다."""
    ass = build_ass([], [], scfg)
    styles = {x.split(",")[0].removeprefix("Style: "): x
              for x in ass.splitlines() if x.startswith("Style: ")}

    assert int(styles["Narr"].split(",")[2]) < int(styles["Main"].split(",")[2])
    assert styles["Narr"].split(",")[3] == "&H00FFFFFF"     # 흰색
    assert styles["Narr"].split(",")[18] == "8"             # 화면 위쪽 (대사와 분리)
    assert styles["Main"].split(",")[18] == "2"             # 대사는 하단 가운데


def test_colour_is_reserved_for_emphasis(scfg):
    """평소 자막은 흰색. 색이 붙는 건 강조(노랑)와 초강조(빨강)뿐."""
    ass = build_ass([], [], scfg)
    styles = {x.split(",")[0].removeprefix("Style: "): x.split(",")[3]
              for x in ass.splitlines() if x.startswith("Style: ")}

    assert styles["Main"] == "&H00FFFFFF"
    assert styles["Narr"] == "&H00FFFFFF"
    assert styles["Emph"] == "&H0033E8FF"      # 노랑
    assert styles["Impact"] == "&H002020F0"    # 빨강


# ------------------------------------------- 쇼츠 위아래 빈 자리 채우기

import re as _re

from gameedit.subtitles import content_box_height


def _shorts_ass(**over):
    cfg = dict(Config().section("subtitles"))
    cfg.update(shorts_title="로토무 한 마리로 끝냄", channel="@내채널")
    cfg.update(over)
    cues = [SubtitleCue(start=1.0, end=4.0, lines=["아니 로토무가", "왜 여기서 나와"])]
    # 2000x1200 가로 원본을 1080x1920 세로로 (실제로 나온 경우)
    ch = content_box_height(2000, 1200, 1080, 1920)
    return build_ass(cues, [], cfg, width=1080, height=1920,
                     content_height=ch, total_duration=30.0)


def test_content_box_height():
    assert content_box_height(1920, 1080, 1920, 1080) == pytest.approx(1080)
    assert content_box_height(2000, 1200, 1080, 1920) == pytest.approx(648)
    assert content_box_height(0, 0, 1080, 1920) == 1920


def test_shorts_fills_the_empty_bands_with_title_and_channel():
    body = _shorts_ass()
    assert ",ShortsTitle," in body and "로토무 한 마리로 끝냄" in body
    assert ",ShortsChannel," in body and "@내채널" in body


def test_shorts_text_is_placed_inside_the_bands():
    """제목은 위 띠 안, 채널명은 아래 띠 안에 있어야 한다."""
    body = _shorts_ass()
    ref_h = 1080                                  # PlayResY
    band = (1920 - 648) / 2 / 1920 * ref_h        # 자막 좌표계에서의 띠 높이
    title_y = int(_re.search(r"ShortsTitle.*?\\pos\(\d+,(\d+)\)", body).group(1))
    chan_y = int(_re.search(r"ShortsChannel.*?\\pos\(\d+,(\d+)\)", body).group(1))
    assert 0 < title_y < band
    assert ref_h - band < chan_y < ref_h


def test_dialogue_does_not_land_on_the_channel_name():
    """실제로 겹쳐서 글자가 포개졌다. 대사는 영상 안쪽에 붙어야 한다."""
    body = _shorts_ass()
    ref_h = 1080
    band = (1920 - 648) / 2 / 1920 * ref_h
    main = [ln for ln in body.splitlines() if ln.startswith("Style: Main,")][0]
    margin_v = int(main.split(",")[-2])
    assert margin_v >= band, "대사 자막이 아래 띠까지 내려간다 (채널명과 겹침)"


def test_nothing_added_when_there_is_no_empty_band():
    """16:9 원본을 16:9 로 뽑으면 빈 자리가 없다. 글자를 넣으면 화면을 가린다."""
    cfg = dict(Config().section("subtitles"), shorts_title="제목", channel="@채널")
    body = build_ass([SubtitleCue(start=0.0, end=2.0, lines=["대사"])], [], cfg,
                     width=1280, height=720,
                     content_height=content_box_height(1920, 1080, 1280, 720),
                     total_duration=10.0)
    assert ",ShortsTitle," not in body and ",ShortsChannel," not in body


def test_empty_title_and_channel_add_nothing():
    body = _shorts_ass(shorts_title="", channel="")
    assert ",ShortsTitle," not in body and ",ShortsChannel," not in body


def test_banner_covers_the_whole_video():
    """중간에 사라지면 어색하다. 처음부터 끝까지 떠 있어야 한다."""
    body = _shorts_ass()
    line = [ln for ln in body.splitlines() if ",ShortsTitle," in ln][0]
    assert line.split(",")[1].strip() == "0:00:00.00"
    assert line.split(",")[2].strip() == "0:00:30.00"


# ------------------------- 세로(쇼츠)에서 글자 크기와 자리 (실제로 두 번 틀림)

from gameedit.subtitles import aspect_scale


def _styles(ass):
    """ASS 스타일 줄 → {이름: {크기, 정렬, 아래여백}}"""
    out = {}
    for line in ass.splitlines():
        if not line.startswith("Style: "):
            continue
        f = line[len("Style: "):].split(",")
        out[f[0]] = {"size": int(f[2]), "align": int(f[18]),
                     "margin_v": int(f[21])}
    return out


def test_horizontal_output_is_untouched():
    """16:9 는 배율이 정확히 1.0 이어야 한다.

    이미 뽑아 본 가로 영상이 이 수정 때문에 달라지면 안 된다.
    """
    assert aspect_scale(1920) == 1.0
    assert aspect_scale(2560) == 1.0          # 더 넓어도 키우지는 않는다


def test_vertical_text_shrinks_to_a_readable_line():
    """세로에서는 자막 좌표계 폭이 608 로 줄어든다.

    글자 크기를 그대로 두면 화면 대비 3.2배가 되어 한 줄에 세 글자만 들어갔다.
    실제 쇼츠처럼 한 줄에 열 글자 이상은 들어가야 한다.
    """
    ass = _shorts_ass()
    main = _styles(ass)["Main"]
    play_x = int(_re.search(r"PlayResX: (\d+)", ass).group(1))
    per_line = (play_x - 120) // main["size"]
    assert 10 <= per_line <= 20, f"한 줄에 {per_line}자"


def test_dialogue_never_lands_in_the_empty_band():
    """자막이 영상 밖 띠로 내려가면 채널명과 겹친다. 실제로 겹쳤다.

    Impact 는 대사 여백의 '절반'을 쓰는데, 그 여백에 띠 높이가 이미 더해져
    있어서 띠도 반만 반영됐다. 아래로 붙는 것과 위로 붙는 것 모두 확인한다.
    """
    ass = _shorts_ass()
    play_y = int(_re.search(r"PlayResY: (\d+)", ass).group(1))
    content = content_box_height(1920, 1080, 1080, 1920) / 1920 * play_y
    band = (play_y - content) / 2

    # 화면 전체를 쓰는 것들은 띠 안에 들어가도 되는 것들이다
    whole_frame = {"Card", "TitleBg", "TitleDate", "TitleName",
                   "ShortsTitle", "ShortsChannel"}
    for name, s in _styles(ass).items():
        if name in whole_frame:
            continue
        if s["align"] in (1, 2, 3):          # 아래에 붙음
            assert s["margin_v"] >= band, f"{name} 이 아래 띠로 내려간다"
        elif s["align"] in (7, 8, 9):        # 위에 붙음
            assert s["margin_v"] >= band, f"{name} 이 위 띠로 올라간다"


def test_band_text_is_sized_by_the_band_not_the_dialogue():
    """띠에 넣는 제목·채널명은 띠 높이에 맞춘다.

    대사 자막 크기를 따라가게 두면, 대사를 줄인 순간 제목까지 같이 작아져서
    넓은 띠가 텅 빈다.
    """
    styles = _styles(_shorts_ass())
    play_y = 1080
    content = content_box_height(1920, 1080, 1080, 1920) / 1920 * play_y
    band = (play_y - content) / 2

    title = styles["ShortsTitle"]["size"]
    channel = styles["ShortsChannel"]["size"]
    assert title > styles["Main"]["size"]         # 대사보다 크다
    assert channel > styles["Main"]["size"] * 0.8
    assert title * 2 < band                       # 두 줄이 띠 안에 들어간다
    assert channel < band


def test_centre_meme_text_does_not_sit_on_the_dialogue():
    """쇼츠는 영상이 화면의 3분의 1뿐이라 '한가운데'가 곧 대사 자리다.

    실제로 "?!?!?!" 가 대사 글자 위에 포개져서 둘 다 못 읽었다.
    가운데 밈은 영상 윗부분, 대사는 아랫부분으로 갈라 놓는다.
    """
    ass = _shorts_ass()
    styles = _styles(ass)
    play_y = int(_re.search(r"PlayResY: (\d+)", ass).group(1))

    meme, impact = styles["MemeCenter"], styles["Impact"]
    assert meme["align"] in (7, 8, 9), "띠가 있으면 위쪽에 붙여야 한다"

    meme_bottom = meme["margin_v"] + meme["size"]
    impact_top = play_y - impact["margin_v"] - impact["size"] * 2   # 두 줄까지 가정
    assert meme_bottom < impact_top, f"밈 {meme_bottom} 이 대사 {impact_top} 를 덮는다"


def test_horizontal_keeps_the_centre_meme_in_the_middle():
    """가로 영상은 가운데가 비어 있다. 지금까지 나오던 그대로 둔다."""
    styles = _styles(build_ass([], [], dict(Config().section("subtitles"))))
    assert styles["MemeCenter"]["align"] == 5
    assert styles["MemeCenter"]["margin_v"] == 0
