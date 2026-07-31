import pytest

from gameedit.config import Config
from gameedit.models import Clip, EditPlan, MemeCue, SubtitleCue
from gameedit.plan import build_plan
from gameedit.srt import parse_subtitle_text, write_srt
from gameedit.subtitles import build_ass, build_subtitle_cues, escape_ass, wrap_text


def test_wrap_text_korean():
    assert wrap_text("짧은 문장", 18) == ["짧은 문장"]
    lines = wrap_text("이건 꽤 길어서 두 줄로 나뉘어야 하는 한국어 자막입니다", 12, 2)
    assert len(lines) == 2
    assert all(line.strip() for line in lines)
    assert "".join(lines).replace(" ", "") == "이건꽤길어서두줄로나뉘어야하는한국어자막입니다"


def test_wrap_text_hard_split_long_token():
    lines = wrap_text("ㅋ" * 40, 10, 2)
    assert len(lines) == 2
    assert sum(len(line.replace(" ", "")) for line in lines) == 40


def test_wrap_text_empty():
    assert wrap_text("", 10) == []


def test_subtitle_cues_map_to_output_timeline(analysis):
    cfg = Config()
    cfg.set("highlight.target_duration", 90)
    plan = build_plan(analysis, cfg)

    assert plan.subtitles, "자막이 만들어지지 않았다"
    for cue in plan.subtitles:
        assert cue.end > cue.start
        assert 0 <= cue.start <= plan.duration + 1
        assert cue.lines
    starts = [c.start for c in plan.subtitles]
    assert starts == sorted(starts)


def test_emphasis_style_on_loud_lines(analysis):
    cfg = Config()
    plan = build_plan(analysis, cfg)
    styles = {c.style for c in plan.subtitles}
    assert "Emph" in styles, "흥분 구간 대사는 강조 스타일이어야 한다"


def test_subtitles_disabled(analysis):
    cfg = Config()
    cfg.set("subtitles.enabled", False)
    plan = build_plan(analysis, cfg)
    assert plan.subtitles == []


def test_min_duration_extends_short_cue():
    from gameedit.models import Analysis, Segment, Transcript, Word

    transcript = Transcript(segments=[
        Segment(start=1.0, end=1.2, text="헐", words=[Word(1.0, 1.2, "헐")]),
    ])
    analysis = Analysis(transcript=transcript)
    plan = EditPlan(clips=[Clip(source_start=0.0, source_end=10.0)])
    plan.relayout()
    cues = build_subtitle_cues(plan, analysis, Config().section("subtitles"))
    assert cues[0].duration >= 0.85


def test_build_ass_contains_styles_and_events():
    cues = [SubtitleCue(start=0.0, end=1.5, lines=["안녕", "하세요"], style="Main"),
            SubtitleCue(start=2.0, end=3.0, lines=["대박!"], style="Emph")]
    memes = [MemeCue(start=1.0, duration=2.0, meme_id="x", kind="text", text="☠️ 사망",
                     style="MemeTop"),
             MemeCue(start=4.0, duration=1.0, meme_id="img", kind="image", asset="a.png")]
    text = build_ass(cues, memes, Config().section("subtitles"), width=1920, height=1080)

    assert "PlayResX: 1920" in text
    assert "Style: Main," in text and "Style: Emph," in text and "Style: MemeTop," in text
    assert "안녕\\N하세요" in text
    assert "☠️ 사망" in text
    assert "a.png" not in text  # 이미지 밈은 ass 가 아니라 오버레이로 처리
    assert text.count("Dialogue:") == 3
    assert "0:00:00.00,0:00:01.50" in text


def test_escape_ass():
    assert escape_ass("{보스}") == "(보스)"
    assert escape_ass("a\nb") == "a\\Nb"


def test_srt_round_trip(tmp_path):
    cues = [SubtitleCue(start=1.5, end=3.25, lines=["첫 줄", "둘째 줄"])]
    path = write_srt(cues, tmp_path / "out.srt")
    text = path.read_text(encoding="utf-8")
    assert "00:00:01,500 --> 00:00:03,250" in text

    parsed = parse_subtitle_text(text)
    assert parsed.segments[0].start == pytest.approx(1.5)
    assert "첫 줄" in parsed.segments[0].text


def test_parse_vtt():
    vtt = "WEBVTT\n\n00:00:02.000 --> 00:00:04.000\n<v 주인공>대박이다\n"
    parsed = parse_subtitle_text(vtt)
    assert parsed.segments[0].text == "대박이다"
    assert parsed.segments[0].end == pytest.approx(4.0)


def test_reference_resolution_keeps_font_scale_consistent():
    from gameedit.subtitles import reference_resolution

    assert reference_resolution(1920, 1080) == (1920, 1080)
    assert reference_resolution(640, 360) == (1920, 1080)   # 저해상도도 같은 좌표계
    assert reference_resolution(1080, 1920) == (608, 1080)  # 세로 영상(쇼츠) 비율 유지
    assert reference_resolution(0, 0) == (1920, 1080)


def test_ass_playres_is_normalised_for_small_video():
    text = build_ass([SubtitleCue(start=0, end=1, lines=["테스트"])], [],
                     Config().section("subtitles"), width=640, height=360)
    assert "PlayResX: 1920" in text and "PlayResY: 1080" in text


def test_card_style_and_show_text_flag():
    """그림 밈의 대체 문구는 화면에 안 나가고, 전환 카드 글자는 나가야 한다."""
    memes = [
        MemeCue(start=1.0, duration=2.0, meme_id="img", kind="image", asset="a.png",
                text="대체문구", show_text=False),
        MemeCue(start=5.0, duration=1.6, meme_id="timeskip_card", kind="image",
                asset="card.png", text="3분 후", style="Card", show_text=True),
    ]
    text = build_ass([], memes, Config().section("subtitles"))

    assert "Style: Card," in text
    assert "대체문구" not in text
    assert "3분 후" in text
    assert text.count("Dialogue:") == 1


def test_split_by_words_never_breaks_a_word():
    """whisper.cpp 처럼 단어 타임스탬프가 없을 때도 단어가 쪼개지면 안 된다."""
    from gameedit.subtitles import split_by_words

    chunks = split_by_words("ask what you can do for your country", 18)
    assert all(len(c) <= 18 for c in chunks)
    assert " ".join(chunks) == "ask what you can do for your country"
    for chunk in chunks:
        for word in chunk.split():
            assert word in "ask what you can do for your country".split()

    # 한 단어가 통째로 넘치면 그때만 강제로 자른다
    assert split_by_words("ㅋ" * 40, 10) == ["ㅋ" * 10] * 4
    assert split_by_words("", 10) == []


def test_segment_without_word_timestamps_keeps_words_intact():
    from gameedit.models import Segment
    from gameedit.subtitles import _split_segment

    cfg = dict(Config().section("subtitles"), max_chars_per_line=10, max_lines=1)
    seg = Segment(start=0.0, end=6.0, text="아 죽었어 죽었어 왜 죽냐고 진짜")
    pieces = _split_segment(seg, cfg)

    assert len(pieces) > 1
    rebuilt = " ".join(text for _, _, text in pieces)
    assert rebuilt == "아 죽었어 죽었어 왜 죽냐고 진짜"
    # 시간이 순서대로 이어지고 원래 구간을 벗어나지 않는다
    assert pieces[0][0] == pytest.approx(0.0)
    assert pieces[-1][1] == pytest.approx(6.0)
    for (s1, e1, _), (s2, _, _) in zip(pieces, pieces[1:]):
        assert e1 == pytest.approx(s2)
        assert e1 > s1
