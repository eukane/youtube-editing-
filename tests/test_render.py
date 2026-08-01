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


# ------------------------- 클립이 많아도 메모리가 폭발하지 않게 (조각별 렌더)

def _plan_with(n):
    from gameedit.models import Clip, EditPlan, MediaInfo

    plan = EditPlan(source="/tmp/a.mp4",
                    media=MediaInfo(path="/tmp/a.mp4", duration=600.0, width=1280,
                                    height=720, fps=30.0, has_audio=True))
    plan.clips = [Clip(source_start=i * 5.0, source_end=i * 5.0 + 2.0) for i in range(n)]
    plan.relayout()
    return plan


def test_many_clips_are_rendered_in_batches(tmp_path):
    """클립을 전부 한 필터그래프에 넣으면 메모리가 클립 수에 비례해 늘어난다."""
    from gameedit.config import Config
    from gameedit.render import build_render_job

    job = build_render_job(_plan_with(40), Config(profile="phone"), None,
                           tmp_path / "out.mp4", tmp_path)
    assert job.segmented
    assert len(job.segment_cmds) == 7          # 40개를 6개씩 → 7묶음
    assert job.concat_cmd and "concat" in job.concat_cmd
    assert not job.cut_cmd, "조각별로 갈 때는 통짜 명령을 만들지 않는다"


def test_few_clips_stay_in_one_pass(tmp_path):
    from gameedit.config import Config
    from gameedit.render import build_render_job

    job = build_render_job(_plan_with(3), Config(profile="phone"), None,
                           tmp_path / "out.mp4", tmp_path)
    assert not job.segmented and job.cut_cmd


def test_segments_seek_instead_of_decoding_from_the_start(tmp_path):
    """-ss 를 입력 앞에 둬야 앞부분을 건너뛴다. 뒤에 두면 처음부터 디코딩한다."""
    from gameedit.config import Config
    from gameedit.render import build_render_job

    job = build_render_job(_plan_with(40), Config(profile="phone"), None,
                           tmp_path / "out.mp4", tmp_path)
    first = job.segment_cmds[0]
    assert "-i" in first


def test_batch_size_controls_memory_tradeoff(tmp_path):
    from gameedit.config import Config
    from gameedit.render import build_render_job

    small = Config(profile="phone")
    small.set("render.segment_batch", 4)
    big = Config(profile="phone")
    big.set("render.segment_batch", 20)

    a = build_render_job(_plan_with(40), small, None, tmp_path / "a.mp4", tmp_path / "a")
    b = build_render_job(_plan_with(40), big, None, tmp_path / "b.mp4", tmp_path / "b")
    assert len(a.segment_cmds) > len(b.segment_cmds)


def test_concat_list_escapes_quotes(tmp_path):
    from gameedit.render import write_concat_list

    odd = tmp_path / "it's a clip.mp4"
    odd.write_bytes(b"x")
    listing = write_concat_list([odd], tmp_path / "list.txt")
    text = listing.read_text()
    assert "'\\''" in text, "작은따옴표가 들어간 경로에서 목록이 깨진다"


def test_phone_profile_prefers_memory_over_speed():
    from gameedit.config import Config

    phone = Config(profile="phone").section("render")
    plain = Config().section("render")
    assert phone["segment_batch"] < plain["segment_batch"]


def test_batches_seek_instead_of_decoding_from_the_start(tmp_path):
    """묶음마다 원본을 처음부터 디코딩하면 뒤쪽 묶음일수록 헛일이 커진다."""
    from gameedit.config import Config
    from gameedit.render import build_render_job

    job = build_render_job(_plan_with(40), Config(profile="phone"), None,
                           tmp_path / "out.mp4", tmp_path)
    last = job.segment_cmds[-1]
    at = last.index("-i")
    head = last[:at]
    assert "-ss" in head, "마지막 묶음도 앞부분부터 읽고 있다"
    # -copyts 없이 -ss 만 쓰면 타임스탬프가 0 부터 시작해서 trim 값이 어긋난다
    assert "-copyts" in head
    seek_at = float(head[head.index("-ss") + 1])
    assert seek_at > 100, f"마지막 묶음인데 {seek_at}초부터 읽는다"


def test_batch_seek_does_not_shift_the_trim_times(tmp_path):
    """-copyts 를 쓰므로 클립 시각은 원본 그대로여야 한다."""
    from gameedit.config import Config
    from gameedit.render import build_render_job

    plan = _plan_with(40)
    job = build_render_job(plan, Config(profile="phone"), None,
                           tmp_path / "out.mp4", tmp_path)
    last = job.segment_cmds[-1]
    graph = last[last.index("-filter_complex") + 1]
    # 마지막 묶음에 남은 클립 수만큼 뒤에서 세어 첫 클립을 찾는다
    batch = Config(profile="phone").section("render")["segment_batch"]
    remainder = len(plan.clips) % batch or batch
    expected = plan.clips[-remainder].source_start
    assert f"trim=start={expected:.3f}" in graph, graph[:200]


# --------------------------------------------- 해상도에 따른 조각 크기

def test_batch_shrinks_for_high_resolution_sources():
    """메모리는 `조각당 클립 수 × 원본 프레임 크기` 에 비례한다.

    클립 수만 고정해 두면 해상도가 올라갈 때 그대로 터진다. 실제로
    2000x1200 태블릿 녹화에서 6개씩 묶었다가 컷 편집 도중에 죽었다.
    """
    from gameedit.render import resolve_batch

    cfg = {"segment_batch": 6}
    assert resolve_batch(cfg, 1280, 720) == 6          # 기준 해상도는 그대로
    assert resolve_batch(cfg, 2000, 1200) == 2         # 죽었던 그 해상도
    assert resolve_batch(cfg, 1920, 1080) == 2
    assert resolve_batch(cfg, 3840, 2160) == 1         # 4K 도 최소 1개는 나온다


def test_batch_grows_for_small_sources_but_is_capped():
    from gameedit.render import BATCH_MAX_FACTOR, resolve_batch

    cfg = {"segment_batch": 6}
    assert resolve_batch(cfg, 640, 360) == 6 * BATCH_MAX_FACTOR
    assert resolve_batch(cfg, 854, 480) > 6


def test_unknown_source_size_keeps_the_configured_batch():
    from gameedit.render import resolve_batch

    assert resolve_batch({"segment_batch": 6}, 0, 0) == 6
    assert resolve_batch({"segment_batch": 6}) == 6


def test_high_resolution_plan_is_split_into_more_pieces(tmp_path):
    """설정이 아니라 실제 렌더 계획에 반영되는지."""
    from gameedit.config import Config
    from gameedit.models import Clip, EditPlan, MediaInfo
    from gameedit.render import build_render_job

    def make(width, height):
        plan = EditPlan(source="/tmp/a.mp4",
                        media=MediaInfo(path="/tmp/a.mp4", duration=600.0,
                                        width=width, height=height, fps=30.0))
        plan.clips = [Clip(source_start=i * 10.0, source_end=i * 10.0 + 5.0)
                      for i in range(12)]
        plan.relayout()
        return build_render_job(plan, Config().with_profile("phone"), None,
                                tmp_path / "out.mp4", tmp_path / f"w{width}")

    small = make(1280, 720)
    big = make(2000, 1200)
    assert len(big.segment_cmds) > len(small.segment_cmds)
    assert len(big.segment_cmds) == 6      # 클립 12개를 2개씩


def test_low_free_memory_forces_one_clip_at_a_time():
    """계산이 맞아도 그 순간 기기에 여유가 없으면 죽는다.

    편집 화면을 띄워 둔 크롬이 같은 기기에서 수백 MB 를 쓰고 있는 상황이
    실제로 있었다. 마지막 안전장치.
    """
    from gameedit.render import resolve_batch

    cfg = {"segment_batch": 6}
    assert resolve_batch(cfg, 1280, 720, free_mb=3000) == 6
    assert resolve_batch(cfg, 1280, 720, free_mb=400) == 1
    assert resolve_batch(cfg, 640, 360, free_mb=400) == 1


def test_memory_guard_can_be_turned_off():
    from gameedit.render import resolve_batch

    cfg = {"segment_batch": 6, "memory_guard": False}
    assert resolve_batch(cfg, 1280, 720, free_mb=400) == 6


def test_unknown_free_memory_does_not_shrink_anything():
    """/proc/meminfo 가 없는 환경(맥·윈도우)에서 1개씩으로 떨어지면 안 된다."""
    from gameedit.render import resolve_batch

    assert resolve_batch({"segment_batch": 6}, 1280, 720, free_mb=0.0) == 6


# ------------------------------------------------------------ 이어하기

def _seg_job(tmp_path, clips=12, width=2000, height=1200):
    from gameedit.config import Config
    from gameedit.models import Clip, EditPlan, MediaInfo
    from gameedit.render import build_render_job

    plan = EditPlan(source="/tmp/a.mp4",
                    media=MediaInfo(path="/tmp/a.mp4", duration=600.0,
                                    width=width, height=height, fps=30.0))
    plan.clips = [Clip(source_start=i * 10.0, source_end=i * 10.0 + 5.0)
                  for i in range(clips)]
    plan.relayout()
    return build_render_job(plan, Config().with_profile("phone"), None,
                            tmp_path / "out.mp4", tmp_path / "work")


def test_segments_are_written_to_a_temp_name_first(tmp_path):
    """도중에 죽으면 반쯤 쓰다 만 파일이 남는다. 그걸 완성품으로 착각하면
    영상이 중간에 끊긴 채로 이어 붙는다."""
    from gameedit.render import part_path

    job = _seg_job(tmp_path)
    for piece, cmd in zip(job.segment_files, job.segment_cmds):
        assert cmd[-1] == str(part_path(piece))
        assert cmd[-1] != str(piece)


def test_finished_segments_are_reused(tmp_path):
    from gameedit.render import reusable_segments

    job = _seg_job(tmp_path)
    seg_dir = job.segment_files[0].parent
    assert reusable_segments(job, seg_dir) == 0        # 처음엔 없다

    for piece in job.segment_files[:3]:
        piece.write_bytes(b"\x00" * 2048)
    assert reusable_segments(job, seg_dir) == 3


def test_half_written_segments_are_thrown_away(tmp_path):
    from gameedit.render import part_path, reusable_segments

    job = _seg_job(tmp_path)
    seg_dir = job.segment_files[0].parent
    reusable_segments(job, seg_dir)                    # 지문 기록
    job.segment_files[0].write_bytes(b"\x00" * 2048)
    leftover = part_path(job.segment_files[1])
    leftover.write_bytes(b"\x00" * 500)

    assert reusable_segments(job, seg_dir) == 1
    assert not leftover.exists()


def test_changing_the_edit_discards_old_segments(tmp_path):
    """설정을 바꿔 다시 만들 때 예전 조각을 이어 붙이면 다른 영상이 섞인다."""
    from gameedit.render import reusable_segments

    job = _seg_job(tmp_path, clips=12)
    seg_dir = job.segment_files[0].parent
    reusable_segments(job, seg_dir)
    for piece in job.segment_files:
        piece.write_bytes(b"\x00" * 2048)
    assert reusable_segments(job, seg_dir) == len(job.segment_files)

    other = _seg_job(tmp_path, clips=12, width=1280, height=720)  # 편집이 달라짐
    assert reusable_segments(other, seg_dir) == 0
    assert not any(p.exists() for p in job.segment_files)


def test_phone_profile_limits_expensive_bridges():
    """8배속 브릿지는 결과물 1초에 원본 8초를 읽는다. 폰에서는 짧게만."""
    from gameedit.config import Config

    phone = Config().with_profile("phone")
    assert phone.get("editing.bridge_max") < Config().get("editing.bridge_max")
    assert phone.get("editing.bridge_speed") <= Config().get("editing.bridge_speed")
