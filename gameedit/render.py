"""EditPlan → ffmpeg 렌더링.

두 단계로 나눠서 돌린다.
  1단계(cut)   : 하이라이트 구간만 잘라 붙이고 줌 펀치/페이드 적용
  2단계(dress) : 이미지·영상 밈 오버레이, 효과음 믹스, 자막(.ass) 굽기

중간 파일이 남기 때문에 자막만 고쳐서 2단계만 다시 돌리는 것도 가능하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .media import FFmpegError, ffmpeg_bin, run
from .models import EditPlan, MemeCue

Logger = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def escape_filter_path(path: str | Path) -> str:
    """filter_complex 안에 파일 경로를 넣기 위한 이스케이프."""
    text = str(path)
    text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
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

    def commands(self) -> list[list[str]]:
        return [c for c in (self.cut_cmd, self.dress_cmd) if c]


# --------------------------------------------------------------------------
# 1단계: 컷 편집
# --------------------------------------------------------------------------


def build_cut_filter(plan: EditPlan, cfg: dict, *, width: int, height: int,
                     fps: float, has_audio: bool) -> str:
    punch_on = bool(cfg.get("punch_zoom", True))
    punch = float(cfg.get("punch_amount", 1.12))
    fade = float(cfg.get("clip_fade", 0.12))

    parts: list[str] = []
    labels: list[str] = []
    audio_src = "0:a" if has_audio else "1:a"

    for i, clip in enumerate(plan.clips):
        start, end = clip.source_start, clip.source_end
        duration = max(0.05, end - start)
        chain = [f"trim=start={start:.3f}:end={end:.3f}", "setpts=PTS-STARTPTS"]
        if punch_on and "punch" in clip.effects and punch > 1.0:
            chain.append(
                f"crop=trunc(iw/{punch:.3f}/2)*2:trunc(ih/{punch:.3f}/2)*2:"
                f"(iw-trunc(iw/{punch:.3f}/2)*2)/2:(ih-trunc(ih/{punch:.3f}/2)*2)/2"
            )
        chain.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
        chain.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
        chain.append("setsar=1")
        if fps:
            chain.append(f"fps={fps:g}")
        chain.append("format=yuv420p")
        if fade > 0:
            chain.append(f"fade=t=in:st=0:d={fade:.3f}")
            chain.append(f"fade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}")
        parts.append(f"[0:v]{','.join(chain)}[v{i}]")

        achain = [f"atrim=start={start:.3f}:end={end:.3f}", "asetpts=PTS-STARTPTS",
                  "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
        if fade > 0:
            achain.append(f"afade=t=in:st=0:d={fade:.3f}")
            achain.append(f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}")
        parts.append(f"[{audio_src}]{','.join(achain)}[a{i}]")
        labels.append(f"[v{i}][a{i}]")

    parts.append(f"{''.join(labels)}concat=n={len(plan.clips)}:v=1:a=1[vcut][acut]")
    return ";".join(parts)


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
    if int(cfg.get("threads", 0) or 0):
        cmd += ["-threads", str(int(cfg["threads"]))]
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
        else:
            cmd += ["-i", cue.asset]
        visual_inputs.append((index, cue))
        index += 1
    for cue in audio:
        cmd += ["-i", cue.sfx or cue.asset]
        audio_inputs.append((index, cue))
        index += 1

    parts: list[str] = []
    current = "[base]"
    parts.append("[0:v]format=yuv420p[base]")

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
    if int(cfg.get("threads", 0) or 0):
        cmd += ["-threads", str(int(cfg["threads"]))]
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
    width = max(2, width - (width % 2))
    height = max(2, height - (height % 2))
    fps = float(project_cfg.get("fps") or 0) or float(plan.media.fps or 0)
    return width, height, fps


def build_render_job(plan: EditPlan, config, ass_path: Path | None, output: Path,
                     work_dir: Path) -> RenderJob:
    render_cfg = config.section("render")
    width, height, fps = resolve_output_size(plan, config.section("project"))
    work_dir.mkdir(parents=True, exist_ok=True)
    intermediate = work_dir / "cut.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    job = RenderJob(intermediate=intermediate, output=output)
    job.cut_cmd = build_cut_command(plan, render_cfg, intermediate,
                                    width=width, height=height, fps=fps)
    job.dress_cmd = build_dress_command(plan, render_cfg, intermediate, ass_path, output,
                                        width=width, height=height)
    return job


def render(plan: EditPlan, config, ass_path: Path | None, output: Path, work_dir: Path,
           *, dry_run: bool = False, skip_cut: bool = False, log: Logger = _noop) -> RenderJob:
    if not plan.clips:
        raise FFmpegError("편집 계획에 클립이 없습니다. 하이라이트 설정을 확인하세요.")
    job = build_render_job(plan, config, ass_path, output, work_dir)

    if dry_run:
        for cmd in job.commands():
            log(" ".join(cmd))
        return job

    if skip_cut and job.intermediate and job.intermediate.exists():
        log(f"1/2 컷 편집 건너뜀 (기존 {job.intermediate.name} 사용)")
    else:
        log(f"1/2 하이라이트 {len(plan.clips)}개 컷 편집 중… → {job.intermediate}")
        run(job.cut_cmd, capture=True)

    log(f"2/2 밈·자막 합성 중… → {output}")
    run(job.dress_cmd, capture=True)

    if not config.get("render.keep_intermediate", False) and job.intermediate:
        try:
            job.intermediate.unlink(missing_ok=True)
        except OSError:
            pass
    return job
