import re

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


def test_escape_filter_path_windows():
    """윈도우 역슬래시는 ffmpeg 필터 문법과 충돌해서 슬래시로 바꿔 넣는다."""
    escaped = escape_filter_path(r"C:\Users\철수\내 영상\subtitles.ass")
    assert escaped == "C\\:/Users/철수/내 영상/subtitles.ass"
    assert "\\\\" not in escaped  # 역슬래시가 겹겹이 쌓이면 안 된다


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


# ------------------------------------------- 레터박스 / 워터마크 (캡처 참고)

def test_letterbox_shrinks_the_picture_and_leaves_black_bands():
    from gameedit.config import Config
    from gameedit.models import Clip, EditPlan, MediaInfo
    from gameedit.render import build_cut_filter

    plan = EditPlan(source="/tmp/a.mp4",
                    media=MediaInfo(path="/tmp/a.mp4", duration=30.0, width=1920,
                                    height=1080, has_audio=True))
    plan.clips = [Clip(source_start=0.0, source_end=5.0)]
    plan.relayout()

    plain = build_cut_filter(plan, Config().section("render"),
                             width=1920, height=1080, fps=30.0, has_audio=True)
    boxed = build_cut_filter(plan, dict(Config().section("render"), letterbox=0.11),
                             width=1920, height=1080, fps=30.0, has_audio=True)

    assert "scale=1920:1080" in plain
    assert "scale=1920:1080" not in boxed          # 그림이 작아진다
    assert "pad=1920:1080" in boxed                # 남는 자리는 검은 띠

    inner = int(re.search(r"scale=1920:(\d+)", boxed).group(1))
    assert inner % 2 == 0                          # 홀수 높이는 인코더가 싫어한다
    assert 0.74 <= inner / 1080 <= 0.82            # 위아래 11%씩 잘린 만큼


def test_letterbox_is_off_by_default():
    from gameedit.config import Config
    assert Config().section("render")["letterbox"] == 0.0


def test_watermark_is_overlaid_bottom_right(tmp_path):
    from gameedit.config import Config
    from gameedit.models import Clip, EditPlan, MediaInfo
    from gameedit.render import build_dress_command

    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    plan = EditPlan(source="/tmp/a.mp4",
                    media=MediaInfo(path="/tmp/a.mp4", duration=30.0, width=1280, height=720))
    plan.clips = [Clip(source_start=0.0, source_end=5.0)]
    plan.relayout()

    cfg = dict(Config().section("render"), watermark=str(logo))
    cmd = build_dress_command(plan, cfg, tmp_path / "cut.mp4", None,
                              tmp_path / "out.mp4", width=1280, height=720)
    joined = " ".join(cmd)
    assert str(logo) in joined
    assert "overlay=x=W-w-" in joined and "y=H-h-" in joined


def test_missing_watermark_file_is_ignored(tmp_path):
    from gameedit.config import Config
    from gameedit.models import Clip, EditPlan, MediaInfo
    from gameedit.render import build_dress_command

    plan = EditPlan(source="/tmp/a.mp4",
                    media=MediaInfo(path="/tmp/a.mp4", duration=30.0, width=1280, height=720))
    plan.clips = [Clip(source_start=0.0, source_end=5.0)]
    plan.relayout()

    cfg = dict(Config().section("render"), watermark="/없는/로고.png")
    cmd = build_dress_command(plan, cfg, tmp_path / "cut.mp4", None,
                              tmp_path / "out.mp4", width=1280, height=720)
    assert "없는" not in " ".join(cmd)


# --------------------------------- 폰에서 화면이 멈추지 않게 (CPU 양보)

def test_phone_profile_leaves_cpu_for_the_screen():
    """코어를 전부 먹으면 같은 기기에서 브라우저를 조작할 수 없다."""
    import os

    from gameedit.config import Config
    from gameedit.render import resolve_threads

    phone = Config(profile="phone").section("render")
    used = resolve_threads(phone)
    cores = os.cpu_count() or 2

    assert 1 <= used < cores or cores <= 2, f"코어 {cores}개 중 {used}개를 쓴다"
    assert phone["nice"] > 0, "우선순위를 안 낮추면 화면이 멈춘다"


def test_thread_resolution_rules():
    from gameedit.render import resolve_threads

    assert resolve_threads({"threads": 0}) == 0        # ffmpeg 자동
    assert resolve_threads({"threads": 3}) == 3        # 그대로
    assert resolve_threads({"threads": -1}) >= 1       # 한 코어 남김
    assert resolve_threads({"threads": -999}) == 1     # 0 이하로는 안 내려간다


def test_threads_reach_the_ffmpeg_command(tmp_path):
    from gameedit.config import Config
    from gameedit.models import Clip, EditPlan, MediaInfo
    from gameedit.render import build_cut_command

    plan = EditPlan(source="/tmp/a.mp4",
                    media=MediaInfo(path="/tmp/a.mp4", duration=30.0, width=1280, height=720))
    plan.clips = [Clip(source_start=0.0, source_end=5.0)]
    plan.relayout()

    cfg = dict(Config().section("render"), threads=2)
    cmd = build_cut_command(plan, cfg, tmp_path / "o.mp4", width=1280, height=720, fps=30.0)
    assert "-threads" in cmd and cmd[cmd.index("-threads") + 1] == "2"


def test_nice_hook_lowers_priority_only_when_asked():
    from gameedit.media import _lower_priority

    assert _lower_priority(0) is None
    hook = _lower_priority(10)
    assert callable(hook)


def test_termux_gets_the_battery_warning():
    """안드로이드가 앱을 죽이는 건 코드로 못 막는다. 무엇을 바꿔야 하는지 알려준다."""
    import inspect

    from gameedit import server

    source = inspect.getsource(server.serve)
    assert "배터리" in source and "제한 없음" in source
