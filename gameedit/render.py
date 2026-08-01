"""EditPlan → ffmpeg 렌더링.

두 단계로 나눠서 돌린다.
  1단계(cut)   : 하이라이트 구간만 잘라 붙이고 줌 펀치/페이드 적용
  2단계(dress) : 이미지·영상 밈 오버레이, 효과음 믹스, 자막(.ass) 굽기

중간 파일이 남기 때문에 자막만 고쳐서 2단계만 다시 돌리는 것도 가능하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Callable

from .media import FFmpegError, ffmpeg_bin, run
from .models import EditPlan, MemeCue

Logger = Callable[[str], None]
Progress = Callable[[str, float], None]


def _noop(_msg: str) -> None:
    pass


def _noop_progress(_label: str, _fraction: float) -> None:
    pass


def escape_filter_path(path: str | Path) -> str:
    """filter_complex 안에 파일 경로를 넣기 위한 이스케이프.

    윈도우 경로의 역슬래시는 ffmpeg 필터 문법과 겹쳐서 이스케이프가 겹겹이 쌓인다.
    ffmpeg 는 윈도우에서도 슬래시 경로를 받아주므로 슬래시로 바꾼 뒤
    드라이브 문자의 콜론만 처리하는 편이 훨씬 안전하다.
        C:\\Users\\철수\\subs.ass  →  C\\:/Users/철수/subs.ass
    """
    text = str(path).replace("\\", "/")
    text = text.replace("'", "\\'").replace(":", "\\:")
    text = text.replace("[", "\\[").replace("]", "\\]").replace(",", "\\,")
    return text


def placement_expr(placement: str) -> tuple[str, str]:
    """오버레이 좌표식 (x, y)."""
    table = {
        "top": ("(W-w)/2", "H*0.06"),
        "center": ("(W-w)/2", "(H-h)/2"),
        "bottom": ("(W-w)/2", "H-h-H*0.16"),
        "left": ("W*0.05", "(H-h)/2"),
        "right": ("W-w-W*0.05", "(H-h)/2"),
        "fullscreen": ("(W-w)/2", "(H-h)/2"),
    }
    return table.get(placement, table["top"])


@dataclass
class RenderJob:
    """실행 준비가 끝난 ffmpeg 명령 묶음."""

    cut_cmd: list[str] = field(default_factory=list)
    dress_cmd: list[str] = field(default_factory=list)
    intermediate: Path | None = None
    output: Path | None = None
    # 조각별로 뽑는 방식일 때만 채워진다 (클립이 많을 때)
    segment_cmds: list[list[str]] = field(default_factory=list)
    segment_files: list[Path] = field(default_factory=list)
    concat_cmd: list[str] = field(default_factory=list)

    @property
    def segmented(self) -> bool:
        return bool(self.segment_cmds)

    def commands(self) -> list[list[str]]:
        if self.segmented:
            return [*self.segment_cmds, self.concat_cmd, self.dress_cmd]
        return [c for c in (self.cut_cmd, self.dress_cmd) if c]


# --------------------------------------------------------------------------
# 1단계: 컷 편집
# --------------------------------------------------------------------------


def resolve_threads(cfg: dict) -> int:
    """ffmpeg 에 넘길 스레드 수.

        0 이상  그대로 (0 이면 ffmpeg 가 알아서 = 코어 전부)
        음수    그만큼 코어를 남겨 둔다

    코어를 전부 쓰면 같은 기기에서 브라우저 조작이 불가능해진다. 폰·태블릿
    에서 편집기를 돌리면서 화면도 봐야 하므로 여유를 남기는 쪽이 낫다.
    """
    raw = int(cfg.get("threads", 0) or 0)
    if raw >= 0:
        return raw
    cores = os.cpu_count() or 2
    return max(1, cores + raw)


def _clip_video_chain(clip, cfg: dict, *, width: int, height: int, fps: float,
                      duration: float, speed: float, trim: bool) -> list[str]:
    """클립 하나의 영상 필터. 한 번에 붙이든 따로 뽑든 결과가 같아야 한다."""
    punch_on = bool(cfg.get("punch_zoom", True))
    punch = float(cfg.get("punch_amount", 1.12))
    band = max(0.0, min(0.25, float(cfg.get("letterbox", 0.0) or 0.0)))
    fade = float(cfg.get("clip_fade", 0.12))

    chain: list[str] = []
    if trim:
        chain.append(f"trim=start={clip.source_start:.3f}:end={clip.source_end:.3f}")
    chain.append("setpts=PTS-STARTPTS")
    if speed > 1.0:
        chain.append(f"setpts=PTS/{speed:.4f}")
    # 클립마다 배율을 따로 잡을 수 있다 (편집 스타일이 정한다). 0 이면 전역값.
    amount = float(getattr(clip, "zoom", 0.0) or 0.0) or punch
    if punch_on and "punch" in clip.effects and amount > 1.0:
        chain.append(
            f"crop=trunc(iw/{amount:.3f}/2)*2:trunc(ih/{amount:.3f}/2)*2:"
            f"(iw-trunc(iw/{amount:.3f}/2)*2)/2:(ih-trunc(ih/{amount:.3f}/2)*2)/2"
        )
    # 레터박스를 켜면 영상을 가운데로 줄이고 위아래에 검은 띠를 남긴다
    inner_h = height - 2 * int(height * band / 2) * 2 if band > 0 else height
    inner_h = max(2, inner_h - inner_h % 2)
    chain.append(f"scale={width}:{inner_h}:force_original_aspect_ratio=decrease")
    chain.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    chain.append("setsar=1")
    if fps:
        chain.append(f"fps={fps:g}")
    chain.append("format=yuv420p")
    if fade > 0:
        chain.append(f"fade=t=in:st=0:d={fade:.3f}")
        chain.append(f"fade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}")
    return chain


def _clip_audio_chain(cfg: dict, *, duration: float, speed: float, trim: bool,
                      start: float = 0.0, end: float = 0.0) -> list[str]:
    fade = float(cfg.get("clip_fade", 0.12))
    chain: list[str] = []
    if trim:
        chain.append(f"atrim=start={start:.3f}:end={end:.3f}")
    chain.append("asetpts=PTS-STARTPTS")
    # atempo 는 한 번에 2배까지만 되므로 필요하면 여러 번 건다 (음정은 유지된다)
    remaining = speed
    while remaining > 1.0001:
        step = min(2.0, remaining)
        chain.append(f"atempo={step:.4f}")
        remaining /= step
    chain.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    if fade > 0:
        chain.append(f"afade=t=in:st=0:d={fade:.3f}")
        chain.append(f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}")
    return chain


def build_cut_filter(plan: EditPlan, cfg: dict, *, width: int, height: int,
                     fps: float, has_audio: bool) -> str:
    parts: list[str] = []
    labels: list[str] = []
    audio_src = "0:a" if has_audio else "1:a"

    for i, clip in enumerate(plan.clips):
        start, end = clip.source_start, clip.source_end
        speed = max(1.0, float(getattr(clip, "speed", 1.0) or 1.0))
        duration = max(0.05, (end - start) / speed)
        video = _clip_video_chain(clip, cfg, width=width, height=height, fps=fps,
                                  duration=duration, speed=speed, trim=True)
        audio = _clip_audio_chain(cfg, duration=duration, speed=speed, trim=True,
                                  start=start, end=end)
        parts.append(f"[0:v]{','.join(video)}[v{i}]")
        parts.append(f"[{audio_src}]{','.join(audio)}[a{i}]")
        labels.append(f"[v{i}][a{i}]")

    parts.append(f"{''.join(labels)}concat=n={len(plan.clips)}:v=1:a=1[vcut][acut]")
    return ";".join(parts)


def build_segment_command(clip, cfg: dict, source: str, output: Path, *,
                          width: int, height: int, fps: float, has_audio: bool) -> list[str]:
    """클립 **하나**만 잘라 내는 명령.

    지금까지는 클립 N 개를 `trim` 필터 N 개로 만들어 한 번에 돌렸다. 그러면
    디코딩된 프레임이 필터 N 개로 전부 복사돼서, 같은 길이의 결과물인데도
    클립이 많을수록 메모리와 시간이 함께 늘어난다. 실측으로 24초 결과물이
    클립 1개일 때 95MB / 2.7초, 120개일 때 455MB / 23.3초였다.

    조각을 하나씩 따로 뽑으면 디코더가 항상 하나라서 **클립이 몇 개든 메모리가
    일정**하다. 폰에서 기기가 멈추는 걸 막는 게 이 방식의 목적이다.
    `-ss` 를 입력 앞에 두면 앞부분을 건너뛰고 바로 그 지점부터 읽는다.
    """
    speed = max(1.0, float(getattr(clip, "speed", 1.0) or 1.0))
    source_len = max(0.05, clip.source_end - clip.source_start)
    duration = source_len / speed

    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-ss", f"{clip.source_start:.3f}", "-t", f"{source_len:.3f}", "-i", source]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{duration:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    video = _clip_video_chain(clip, cfg, width=width, height=height, fps=fps,
                              duration=duration, speed=speed, trim=False)
    audio = _clip_audio_chain(cfg, duration=duration, speed=speed, trim=False)
    cmd += [
        "-vf", ",".join(video),
        "-af", ",".join(audio),
        "-map", "0:v:0", "-map", ("0:a:0" if has_audio else "1:a:0"),
        "-c:v", cfg.get("video_codec", "libx264"),
        "-crf", str(max(0, int(cfg.get("crf", 20)) - 2)),
        "-preset", cfg.get("preset", "medium"),
        "-c:a", cfg.get("audio_codec", "aac"),
        "-b:a", cfg.get("audio_bitrate", "192k"),
        "-ar", "48000", "-ac", "2",
        "-video_track_timescale", "90000",
    ]
    threads = resolve_threads(cfg)
    if threads:
        cmd += ["-threads", str(threads)]
    cmd.append(str(output))
    return cmd


def write_concat_list(segments: list[Path], path: Path) -> Path:
    """concat 디먹서용 목록 파일. 작은따옴표를 escape 해야 경로가 깨지지 않는다."""
    lines = []
    for segment in segments:
        safe = str(segment.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_concat_command(list_file: Path, output: Path) -> list[str]:
    """조각들을 다시 인코딩하지 않고 이어 붙인다 (거의 순간)."""
    return [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(output)]


def build_cut_command(plan: EditPlan, cfg: dict, output: Path, *, width: int, height: int,
                      fps: float) -> list[str]:
    has_audio = bool(plan.media.has_audio)
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-stats",
           "-i", str(plan.source)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{max(1.0, plan.media.duration):.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    filt = build_cut_filter(plan, cfg, width=width, height=height, fps=fps, has_audio=has_audio)
    cmd += [
        "-filter_complex", filt,
        "-map", "[vcut]", "-map", "[acut]",
        "-c:v", cfg.get("video_codec", "libx264"),
        "-crf", str(max(0, int(cfg.get("crf", 20)) - 2)),
        "-preset", cfg.get("preset", "medium"),
        "-c:a", cfg.get("audio_codec", "aac"),
        "-b:a", cfg.get("audio_bitrate", "192k"),
        "-ar", "48000",
    ]
    threads = resolve_threads(cfg)
    if threads:
        cmd += ["-threads", str(threads)]
    cmd.append(str(output))
    return cmd


# --------------------------------------------------------------------------
# 2단계: 밈 오버레이 + 효과음 + 자막
# --------------------------------------------------------------------------


def _visual_cues(plan: EditPlan) -> list[MemeCue]:
    return [c for c in plan.memes if c.kind in ("image", "video") and c.asset]


def _audio_cues(plan: EditPlan) -> list[MemeCue]:
    return [c for c in plan.memes if c.sfx or (c.kind == "audio" and c.asset)]


def build_dress_command(plan: EditPlan, cfg: dict, source: Path, ass_path: Path | None,
                        output: Path, *, width: int, height: int) -> list[str]:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-stats",
           "-i", str(source)]

    visual = _visual_cues(plan)
    audio = _audio_cues(plan)
    index = 1
    visual_inputs: list[tuple[int, MemeCue]] = []
    audio_inputs: list[tuple[int, MemeCue]] = []

    for cue in visual:
        if cue.kind == "image":
            cmd += ["-loop", "1", "-t", f"{cue.duration:.3f}", "-i", cue.asset]
        elif cue.asset.lower().endswith(".gif"):
            # 움짤은 노출 시간 동안 계속 돌아야 한다
            cmd += ["-ignore_loop", "0", "-t", f"{cue.duration:.3f}", "-i", cue.asset]
        else:
            cmd += ["-i", cue.asset]
        visual_inputs.append((index, cue))
        index += 1
    for cue in audio:
        cmd += ["-i", cue.sfx or cue.asset]
        audio_inputs.append((index, cue))
        index += 1

    # 채널 로고: 영상 내내 우하단에 얹는다 (실제 편집본들이 다 달고 있다)
    mark = str(cfg.get("watermark", "") or "")
    mark_idx = 0
    if mark and Path(mark).exists():
        cmd += ["-i", mark]
        mark_idx = index
        index += 1

    parts: list[str] = []
    current = "[base]"
    parts.append("[0:v]format=yuv420p[base]")

    if mark_idx:
        mark_w = max(0.01, min(0.3, float(cfg.get("watermark_scale", 0.07))))
        alpha = max(0.0, min(1.0, float(cfg.get("watermark_opacity", 0.85))))
        margin = max(8, int(width * 0.015))
        parts.append(
            f"[{mark_idx}:v]scale={int(width * mark_w)}:-2,format=rgba,"
            f"colorchannelmixer=aa={alpha:.3f}[wm]"
        )
        parts.append(f"{current}[wm]overlay=x=W-w-{margin}:y=H-h-{margin}[wmark]")
        current = "[wmark]"

    for n, (input_idx, cue) in enumerate(visual_inputs):
        overlay_w = max(0.02, min(1.0, cue.scale))
        chain = [f"scale=iw*min(1\\,{overlay_w:.3f}*{width}/iw):-2"]
        if cue.kind == "video":
            chain.insert(0, f"trim=start=0:end={cue.duration:.3f}")
            chain.insert(1, "setpts=PTS-STARTPTS")
        chain.append("format=rgba")
        chain.append("fade=t=in:st=0:d=0.15:alpha=1")
        chain.append(f"fade=t=out:st={max(0.0, cue.duration - 0.25):.3f}:d=0.25:alpha=1")
        chain.append(f"setpts=PTS+{cue.start:.3f}/TB")
        parts.append(f"[{input_idx}:v]{','.join(chain)}[m{n}]")
        x, y = placement_expr(cue.placement)
        out_label = f"[ov{n}]"
        parts.append(
            f"{current}[m{n}]overlay=x={x}:y={y}:"
            f"enable='between(t,{cue.start:.3f},{cue.end:.3f})'{out_label}"
        )
        current = out_label

    if ass_path is not None:
        parts.append(f"{current}ass=filename='{escape_filter_path(ass_path)}'[vout]")
    else:
        parts.append(f"{current}null[vout]")

    # 오디오: 원본 + 효과음 믹스
    audio_labels = ["[0:a]"]
    for n, (input_idx, cue) in enumerate(audio_inputs):
        delay_ms = int(max(0.0, cue.start) * 1000)
        volume = cue.sfx_volume if cue.sfx else cue.volume
        parts.append(
            f"[{input_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={max(0.0, volume):.3f},adelay={delay_ms}|{delay_ms}[s{n}]"
        )
        audio_labels.append(f"[s{n}]")
    if len(audio_labels) > 1:
        parts.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:"
                     f"duration=first:dropout_transition=0:normalize=0[amix]")
        audio_out = "[amix]"
    else:
        audio_out = "[0:a]"
    if cfg.get("loudnorm", True):
        # loudnorm 은 내부적으로 192kHz 로 올려 놓기 때문에 다시 48kHz 로 맞춘다
        parts.append(f"{audio_out}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
        audio_out = "[aout]"

    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", audio_out,
        "-c:v", cfg.get("video_codec", "libx264"),
        "-crf", str(int(cfg.get("crf", 20))),
        "-preset", cfg.get("preset", "medium"),
        "-c:a", cfg.get("audio_codec", "aac"),
        "-b:a", cfg.get("audio_bitrate", "192k"),
        "-ar", "48000",
        "-movflags", "+faststart",
    ]
    threads = resolve_threads(cfg)
    if threads:
        cmd += ["-threads", str(threads)]
    cmd.append(str(output))
    return cmd


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------


def resolve_output_size(plan: EditPlan, project_cfg: dict) -> tuple[int, int, float]:
    resolution = (project_cfg.get("resolution") or "").lower().replace(" ", "")
    width, height = plan.media.width or 1920, plan.media.height or 1080
    if "x" in resolution:
        try:
            w, h = resolution.split("x")
            width, height = int(w), int(h)
        except ValueError:
            pass
    # 상한만 지정하면 큰 영상은 줄이고 작은 영상은 그대로 둔다.
    # (폰에서 480p 영상을 굳이 720p 로 늘려 인코딩할 이유가 없다)
    cap = (project_cfg.get("max_resolution") or "").lower().replace(" ", "")
    if "x" in cap and not ("x" in resolution):
        try:
            cw, ch = (int(v) for v in cap.split("x"))
            if width * height > cw * ch:
                ratio = min(cw / width, ch / height)
                width, height = int(width * ratio), int(height * ratio)
        except ValueError:
            pass

    width = max(2, width - (width % 2))
    height = max(2, height - (height % 2))
    fps = float(project_cfg.get("fps") or 0) or float(plan.media.fps or 0)
    return width, height, fps


def _batched(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def build_batch_command(plan: EditPlan, clips: list, cfg: dict, output: Path, *,
                        width: int, height: int, fps: float) -> list[str]:
    """클립 **몇 개씩 묶어서** 뽑는다.

    한 번에 전부 붙이면 클립 수에 비례해 메모리가 늘고(실측 120개 451MB),
    하나씩 따로 뽑으면 메모리는 일정하지만(77MB) ffmpeg 를 클립 수만큼
    띄우느라 느려진다(22.9초 → 31.1초). 묶어서 뽑으면 둘 다 피할 수 있다.

    그리고 묶음이 필요로 하는 구간만 읽는다. `-ss` 없이 trim 만 쓰면 묶음마다
    원본을 처음부터 디코딩해서, 뒤쪽 묶음일수록 헛일이 커진다. 4K 원본이면
    이 헛일이 전체 시간을 지배한다.
    """
    offset = min(c.source_start for c in clips)
    end = max(c.source_end for c in clips)

    piece = EditPlan(source=plan.source, media=plan.media)
    piece.clips = list(clips)
    piece.relayout()
    cmd = build_cut_command(piece, cfg, output, width=width, height=height, fps=fps)

    # `-ss` 만 쓰면 타임스탬프가 0 부터 다시 시작해서 trim 값을 전부 빼 줘야
    # 하는데, 그 계산이 프레임 단위로 어긋난다(실측에서 20개 중 1개가 다른
    # 프레임으로 나왔다). `-copyts` 로 원본 시각을 그대로 들고 오면 trim 값을
    # 손댈 필요가 없어서 통짜로 뽑을 때와 결과가 같아진다.
    at = cmd.index("-i")
    seek = ["-ss", f"{max(0.0, offset - 0.5):.3f}", "-copyts",
            "-to", f"{end + 0.5:.3f}"]
    return [*cmd[:at], *seek, *cmd[at:]]


def build_render_job(plan: EditPlan, config, ass_path: Path | None, output: Path,
                     work_dir: Path) -> RenderJob:
    render_cfg = config.section("render")
    width, height, fps = resolve_output_size(plan, config.section("project"))
    work_dir.mkdir(parents=True, exist_ok=True)
    intermediate = work_dir / "cut.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    job = RenderJob(intermediate=intermediate, output=output)

    # 클립이 많으면 조각을 하나씩 뽑는다. 한 번에 붙이면 클립 수에 비례해
    # 메모리가 늘어나서 폰·태블릿이 멈춘다 (build_segment_command 주석 참고).
    threshold = int(render_cfg.get("segment_threshold", 8) or 0)
    batch = max(1, int(render_cfg.get("segment_batch", 8) or 1))
    if threshold and len(plan.clips) >= threshold:
        seg_dir = work_dir / "segments"
        seg_dir.mkdir(parents=True, exist_ok=True)
        for i, group in enumerate(_batched(plan.clips, batch)):
            piece = seg_dir / f"{i:04d}.mp4"
            job.segment_files.append(piece)
            job.segment_cmds.append(build_batch_command(
                plan, group, render_cfg, piece, width=width, height=height, fps=fps))
        job.concat_cmd = build_concat_command(seg_dir / "list.txt", intermediate)
    else:
        job.cut_cmd = build_cut_command(plan, render_cfg, intermediate,
                                        width=width, height=height, fps=fps)

    job.dress_cmd = build_dress_command(plan, render_cfg, intermediate, ass_path, output,
                                        width=width, height=height)
    return job


def render(plan: EditPlan, config, ass_path: Path | None, output: Path, work_dir: Path,
           *, dry_run: bool = False, skip_cut: bool = False, log: Logger = _noop,
           progress: Progress = _noop_progress) -> RenderJob:
    if not plan.clips:
        raise FFmpegError("편집 계획에 클립이 없습니다. 하이라이트 설정을 확인하세요.")
    job = build_render_job(plan, config, ass_path, output, work_dir)

    if dry_run:
        for cmd in job.commands():
            log(" ".join(cmd))
        return job

    codec = config.get("render.video_codec", "libx264")
    nice = int(config.get("render.nice", 0) or 0)

    def run_stage(cmd: list[str]) -> None:
        """실패하면 소프트웨어 인코더로 한 번 더 시도.

        폰의 하드웨어 인코더(h264_mediacodec 등)는 기기마다 되고 안 되고가 달라서
        한 번에 판단할 수 없다. 안 되면 조용히 libx264 로 돌아간다.
        """
        try:
            run(cmd, capture=True, nice=nice)
        except FFmpegError:
            if codec == "libx264":
                raise
            log(f"    · {codec} 인코더가 실패해서 libx264 로 다시 시도합니다")
            fallback = [("libx264" if arg == codec else arg) for arg in cmd]
            run(fallback, capture=True, nice=nice)

    if skip_cut and job.intermediate and job.intermediate.exists():
        log(f"1/2 컷 편집 건너뜀 (기존 {job.intermediate.name} 사용)")
    elif job.segmented:
        progress("컷 편집", 0.0)
        log(f"1/2 하이라이트 {len(plan.clips)}개 컷 편집 중… (조각별) → {job.intermediate}")
        total = len(job.segment_cmds)
        for i, cmd in enumerate(job.segment_cmds, start=1):
            run_stage(cmd)
            progress("컷 편집", 0.5 * i / total)
            if i % 10 == 0 or i == total:
                log(f"      {i}/{total} 조각")
        list_file = write_concat_list(job.segment_files, job.segment_files[0].parent / "list.txt")
        run(job.concat_cmd, capture=True, nice=nice)
        if not config.get("render.keep_intermediate", False):
            for piece in job.segment_files:
                piece.unlink(missing_ok=True)
            list_file.unlink(missing_ok=True)
    else:
        progress("컷 편집", 0.0)
        log(f"1/2 하이라이트 {len(plan.clips)}개 컷 편집 중… → {job.intermediate}")
        run_stage(job.cut_cmd)

    progress("밈·자막 합성", 0.55)
    log(f"2/2 밈·자막 합성 중… → {output}")
    run_stage(job.dress_cmd)

    progress("완료", 1.0)
    if not config.get("render.keep_intermediate", False) and job.intermediate:
        try:
            job.intermediate.unlink(missing_ok=True)
        except OSError:
            pass
    return job
