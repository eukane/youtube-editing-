"""원본 영상 전체 훑기 (오디오 · 장면 · 대사)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .audio import analyze_audio
from .config import Config
from .media import extract_audio, format_timecode, probe
from .models import Analysis
from .scenes import detect_scenes
from .transcribe import transcribe

Logger = Callable[[str], None]
Progress = Callable[[str, float], None]


def _noop(_msg: str) -> None:
    pass


def _noop_progress(_label: str, _fraction: float) -> None:
    pass


def analyze_video(src: str | Path, config: Config, work_dir: Path, *,
                  log: Logger = _noop, keep_audio: bool = False,
                  skip_transcribe: bool = False,
                  progress: Progress = _noop_progress) -> Analysis:
    src = str(src)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    acfg = config.section("analyze")

    progress("영상 정보 확인", 0.0)
    log(f"[1/4] 영상 정보 확인: {src}")
    media = probe(src)
    log(f"      {media.width}x{media.height} · {media.fps:.2f}fps · "
        f"{format_timecode(media.duration)} · 오디오 {'있음' if media.has_audio else '없음'}")

    audio_analysis = None
    wav_path = work_dir / "audio.wav"
    if media.has_audio:
        progress("오디오 분석", 0.05)
        log("[2/4] 오디오 추출 및 흥분도 분석…")
        extract_audio(src, wav_path)
        audio_analysis = analyze_audio(
            wav_path,
            hop=float(acfg.get("hop", 0.05)),
            silence_db=float(acfg.get("silence_db", -38.0)),
            min_silence=float(acfg.get("min_silence", 0.6)),
            peak_percentile=float(acfg.get("peak_percentile", 0.93)),
        )
        log(f"      피크 {len(audio_analysis.peaks)}곳 · 정적 구간 {len(audio_analysis.silences)}곳")
    else:
        log("[2/4] 오디오 트랙이 없어 오디오 분석을 건너뜁니다.")

    progress("장면 전환 검출", 0.35)
    scene_threshold = float(acfg.get("scene_threshold", 0.35))
    if scene_threshold <= 0:
        # 영상 전체를 디코딩해야 하는 단계라 폰처럼 느린 기기에서는 끄는 편이 낫다
        log("[3/4] 장면 전환 검출 건너뜀 (analyze.scene_threshold=0)")
        scenes = []
    else:
        log("[3/4] 장면 전환 검출…")
        scenes = detect_scenes(src, threshold=scene_threshold,
                               scale_width=int(acfg.get("scene_scale_width", 320)))
        log(f"      컷 {len(scenes)}개")

    tcfg = dict(config.section("transcribe"))
    external = str(tcfg.get("external") or "")
    # 밖에서 만들어 온 자막 파일은 음성 인식이 아니다. 읽는 데 몇 밀리초면 되고
    # 소리가 필요하지도 않다. --no-transcribe 나 무음 영상 때문에 이걸 같이
    # 버리면, 사용자는 자막을 넣었는데 결과물에 한 줄도 안 나온다.
    has_external = bool(external and Path(external).exists())

    transcript = None
    if skip_transcribe and not has_external:
        log("[4/4] 전사 건너뜀 (--no-transcribe)")
    elif not media.has_audio and not has_external:
        log("[4/4] 오디오가 없어 전사를 건너뜁니다.")
    else:
        if has_external:
            progress("자막 읽기", 0.6)
            log("[4/4] 자막 파일 읽는 중…")
            tcfg["backend"] = "external"
        else:
            progress("음성 인식", 0.6)
            log("[4/4] 음성 인식(자막) 진행…")
        transcript = transcribe(wav_path, tcfg, log=lambda m: log(f"      {m}"))
        log(f"      대사 {len(transcript.segments)}줄")

    if not keep_audio and wav_path.exists():
        wav_path.unlink()

    progress("분석 완료", 1.0)
    analysis = Analysis(media=media, scenes=scenes)
    if audio_analysis is not None:
        analysis.audio = audio_analysis
    if transcript is not None:
        analysis.transcript = transcript
    return analysis
