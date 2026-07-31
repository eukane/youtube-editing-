import pytest

from gameedit.config import Config
from gameedit.models import Clip, EditPlan, MediaInfo, MemeCue
from gameedit.render import (build_cut_command, build_cut_filter, build_dress_command,
                             escape_filter_path, placement_expr, resolve_output_size)


@pytest.fixture
def plan(tmp_path):
    src = tmp_path / "full.mp4"
    src.write_bytes(b"")
    p = EditPlan(
        source=str(src),
        media=MediaInfo(path=str(src), duration=600.0, width=1920, height=1080,
                        fps=30.0, has_audio=True),
        clips=[
            Clip(source_start=10.0, source_end=25.0, label="A", effects=["punch"]),
            Clip(source_start=100.0, source_end=118.5, label="B"),
        ],
    )
    p.relayout()
    return p


def test_relayout_and_time_mapping(plan):
    assert plan.clips[0].out_start == 0.0
    assert plan.clips[1].out_start == pytest.approx(15.0)
    assert plan.duration == pytest.approx(33.5)

    assert plan.map_time(12.0) == pytest.approx(2.0)
    assert plan.map_time(105.0) == pytest.approx(20.0)
    assert plan.map_time(50.0) is None
    assert plan.map_range(101.0, 103.0) == pytest.approx((16.0, 18.0))
    assert plan.map_range(50.0, 60.0) is None


def test_cut_filter_has_one_chain_per_clip(plan):
    filt = build_cut_filter(plan, Config().section("render"), width=1920, height=1080,
                            fps=30, has_audio=True)
    assert filt.count("[0:v]trim=start=") == 2
    assert filt.count("atrim=start=") == 2
    assert "concat=n=2:v=1:a=1[vcut][acut]" in filt
    assert "crop=" in filt          # punch 효과
    assert "fade=t=in" in filt
    assert "scale=1920:1080" in filt


def test_cut_filter_without_punch(plan):
    cfg = dict(Config().section("render"), punch_zoom=False)
    filt = build_cut_filter(plan, cfg, width=1280, height=720, fps=0, has_audio=True)
    assert "crop=" not in filt
    assert "scale=1280:720" in filt


def test_cut_filter_uses_silent_source_when_no_audio(plan):
    plan.media.has_audio = False
    filt = build_cut_filter(plan, Config().section("render"), width=1920, height=1080,
                            fps=30, has_audio=False)
    assert "[1:a]atrim" in filt
    cmd = build_cut_command(plan, Config().section("render"), "out.mp4",
                            width=1920, height=1080, fps=30)
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in cmd


def test_dress_command_wires_overlays_sfx_and_ass(plan, tmp_path):
    img = tmp_path / "meme.png"
    img.write_bytes(b"x")
    sfx = tmp_path / "boom.mp3"
    sfx.write_bytes(b"x")
    ass = tmp_path / "subtitles.ass"
    ass.write_text("[Script Info]", encoding="utf-8")

    plan.memes = [
        MemeCue(start=3.0, duration=2.0, meme_id="t", kind="text", text="ㅋㅋㅋ"),
        MemeCue(start=5.0, duration=2.0, meme_id="i", kind="image", asset=str(img),
                placement="right"),
        MemeCue(start=9.0, duration=1.0, meme_id="s", kind="text", text="쾅", sfx=str(sfx)),
    ]
    cmd = build_dress_command(plan, Config().section("render"), tmp_path / "cut.mp4",
                              ass, tmp_path / "final.mp4", width=1920, height=1080)
    joined = " ".join(cmd)
    filt = cmd[cmd.index("-filter_complex") + 1]

    assert str(img) in cmd and str(sfx) in cmd
    assert "-loop" in cmd                       # 이미지 밈은 loop 입력
    assert filt.count("overlay=") == 1          # 텍스트 밈은 오버레이 아님
    assert "enable='between(t,5.000,7.000)'" in filt
    assert "adelay=9000|9000" in filt
    assert "amix=inputs=2" in filt
    assert "ass=filename=" in filt
    assert "loudnorm" in filt
    assert "-map [vout]" in joined.replace("'", "")


def test_dress_command_without_memes(plan, tmp_path):
    cmd = build_dress_command(plan, Config().section("render"), tmp_path / "cut.mp4",
                              None, tmp_path / "final.mp4", width=1920, height=1080)
    filt = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=" not in filt
    assert "ass=" not in filt
    assert "[vout]" in filt


def test_escape_filter_path():
    escaped = escape_filter_path("/tmp/my dir/subs:1.ass")
    assert "\\:" in escaped
    assert " " in escaped  # 공백은 따옴표로 감싸므로 그대로


def test_placement_expressions():
    for name in ("top", "center", "bottom", "left", "right"):
        x, y = placement_expr(name)
        assert x and y
    assert placement_expr("알수없음") == placement_expr("top")


def test_resolve_output_size(plan):
    assert resolve_output_size(plan, {}) == (1920, 1080, 30.0)
    assert resolve_output_size(plan, {"resolution": "1280x720", "fps": 60}) == (1280, 720, 60.0)
    # 홀수 해상도는 짝수로 보정 (libx264 요구사항)
    plan.media.width, plan.media.height = 1921, 1081
    assert resolve_output_size(plan, {}) == (1920, 1080, 30.0)


def test_max_resolution_only_shrinks(plan):
    """상한은 큰 영상만 줄이고 작은 영상은 건드리지 않는다."""
    plan.media.width, plan.media.height = 3840, 2160
    assert resolve_output_size(plan, {"max_resolution": "1280x720"})[:2] == (1280, 720)

    plan.media.width, plan.media.height = 640, 360
    assert resolve_output_size(plan, {"max_resolution": "1280x720"})[:2] == (640, 360)

    # 세로 영상도 비율을 지킨다
    plan.media.width, plan.media.height = 1080, 1920
    w, h = resolve_output_size(plan, {"max_resolution": "1280x720"})[:2]
    assert (w, h) == (404, 720)

    # 정확한 해상도를 지정하면 그쪽이 우선
    plan.media.width, plan.media.height = 3840, 2160
    assert resolve_output_size(plan, {"resolution": "1920x1080",
                                      "max_resolution": "640x360"})[:2] == (1920, 1080)
