"""음성 → 자막 텍스트 전사.

백엔드 우선순위 (backend=auto 일 때)
  1. external      : 설정에 외부 자막(.srt/.vtt) 경로가 있으면 그걸 사용
  2. faster-whisper: 설치돼 있으면 가장 빠르고 단어 타임스탬프 지원
  3. whisper       : openai-whisper
  4. whisper.cpp   : 실행 파일 + 모델 파일이 있으면 사용.
                     안드로이드(Termux)처럼 파이토치를 못 쓰는 환경의 유일한 선택지다.
  5. none          : 전사 없이 진행 (자막 없이 컷/밈만)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .models import Segment, Transcript, Word
from .srt import parse_subtitle_file

WHISPER_CPP_BINARIES = ("whisper-cli", "whisper-cpp", "whisper.cpp", "whisper", "main")
WHISPER_MODEL_DIRS = ("~/whisper-models", "~/.cache/whisper.cpp", "~/whisper.cpp/models",
                      "./models")

Logger = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def _module_available(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def find_whisper_cpp(explicit: str = "") -> str | None:
    """whisper.cpp 실행 파일 찾기."""
    for candidate in (explicit, os.environ.get("GAMEEDIT_WHISPER_CPP", "")):
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    for name in WHISPER_CPP_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_whisper_model(explicit: str = "", size: str = "") -> str | None:
    """whisper.cpp 용 ggml 모델 파일(.bin) 찾기."""
    for candidate in (explicit, os.environ.get("GAMEEDIT_WHISPER_MODEL", "")):
        if candidate and Path(candidate).expanduser().exists():
            return str(Path(candidate).expanduser())

    found: list[Path] = []
    for directory in WHISPER_MODEL_DIRS:
        path = Path(directory).expanduser()
        if path.is_dir():
            found.extend(sorted(path.glob("*.bin")))
    if not found:
        return None
    if size:  # 설정한 크기(tiny/base/small…)와 이름이 맞는 것 우선
        for model in found:
            if size.lower() in model.name.lower():
                return str(model)
    return str(found[0])


def whisper_cpp_ready(options: dict | None = None) -> bool:
    options = options or {}
    return bool(find_whisper_cpp(options.get("whisper_cpp_bin", ""))
                and find_whisper_model(options.get("whisper_cpp_model", ""),
                                       options.get("model", "")))


def resolve_backend(requested: str, external: str = "", options: dict | None = None) -> str:
    requested = (requested or "auto").lower()
    if requested != "auto":
        return requested
    if external and Path(external).exists():
        return "external"
    if _module_available("faster_whisper"):
        return "faster-whisper"
    if _module_available("whisper"):
        return "whisper"
    if whisper_cpp_ready(options):
        return "whisper.cpp"
    return "none"


def transcribe(audio_path: str | Path, options: dict, *, log: Logger = _noop) -> Transcript:
    external = options.get("external") or ""
    backend = resolve_backend(options.get("backend", "auto"), external, options)
    log(f"전사 백엔드: {backend}")

    if backend == "none":
        log("  · 사용할 수 있는 음성인식 백엔드가 없어 자막 없이 진행합니다."
            " (`pip install faster-whisper`, whisper.cpp 설치, 또는 transcribe.external 설정)")
        return Transcript(language=options.get("language", "ko"))
    if backend == "external":
        if not external or not Path(external).exists():
            raise FileNotFoundError(f"외부 자막 파일을 찾을 수 없습니다: {external}")
        transcript = parse_subtitle_file(external)
        transcript.language = options.get("language", "ko")
        log(f"  · 외부 자막 {len(transcript.segments)}줄 로드")
        return transcript
    if backend in ("faster-whisper", "faster_whisper"):
        return _faster_whisper(audio_path, options, log)
    if backend == "whisper":
        return _openai_whisper(audio_path, options, log)
    if backend in ("whisper.cpp", "whisper-cpp", "whispercpp"):
        return _whisper_cpp(audio_path, options, log)
    raise ValueError(f"알 수 없는 전사 백엔드: {backend}")


def _pick_device(requested: str) -> str:
    if requested and requested != "auto":
        return requested
    try:  # pragma: no cover - GPU 유무에 따라 다름
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _faster_whisper(audio_path, options: dict, log: Logger) -> Transcript:  # pragma: no cover - 무거운 의존성
    from faster_whisper import WhisperModel  # type: ignore

    device = _pick_device(options.get("device", "auto"))
    compute_type = options.get("compute_type") or ("float16" if device == "cuda" else "int8")
    log(f"  · 모델 로딩: {options.get('model', 'small')} ({device}/{compute_type})")
    model = WhisperModel(options.get("model", "small"), device=device, compute_type=compute_type)

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=options.get("language") or None,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=options.get("initial_prompt") or None,
    )
    segments: list[Segment] = []
    for seg in segments_iter:
        words = [
            Word(start=float(w.start), end=float(w.end), text=w.word.strip(),
                 confidence=float(getattr(w, "probability", 1.0) or 1.0))
            for w in (seg.words or [])
            if w.start is not None and w.end is not None and w.word.strip()
        ]
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(Segment(start=float(seg.start), end=float(seg.end), text=text, words=words))
        if len(segments) % 25 == 0:
            log(f"  · 전사 진행 {segments[-1].end:.0f}초까지")
    return Transcript(language=getattr(info, "language", options.get("language", "ko")),
                      segments=segments)


def _openai_whisper(audio_path, options: dict, log: Logger) -> Transcript:  # pragma: no cover - 무거운 의존성
    import whisper  # type: ignore

    log(f"  · 모델 로딩: {options.get('model', 'small')}")
    model = whisper.load_model(options.get("model", "small"))
    result = model.transcribe(
        str(audio_path),
        language=options.get("language") or None,
        word_timestamps=True,
        initial_prompt=options.get("initial_prompt") or None,
        verbose=False,
    )
    segments: list[Segment] = []
    for seg in result.get("segments", []):
        words = [
            Word(start=float(w["start"]), end=float(w["end"]), text=str(w.get("word", "")).strip(),
                 confidence=float(w.get("probability", 1.0)))
            for w in seg.get("words", []) or []
            if str(w.get("word", "")).strip()
        ]
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append(Segment(start=float(seg["start"]), end=float(seg["end"]),
                                text=text, words=words))
    return Transcript(language=result.get("language", options.get("language", "ko")),
                      segments=segments)


def _whisper_cpp(audio_path, options: dict, log: Logger) -> Transcript:
    """whisper.cpp 실행 파일을 불러 SRT 를 받아 온다.

    파이토치가 없는 환경(안드로이드 Termux 등)에서 쓰는 경로.
    단어 단위 타임스탬프는 없어서 자막이 문장 단위로 끊긴다.
    """
    binary = find_whisper_cpp(options.get("whisper_cpp_bin", ""))
    model = find_whisper_model(options.get("whisper_cpp_model", ""), options.get("model", ""))
    if not binary:
        raise FileNotFoundError(
            "whisper.cpp 실행 파일을 찾을 수 없습니다. 설치했다면 "
            "transcribe.whisper_cpp_bin 에 경로를 적어 주세요.")
    if not model:
        raise FileNotFoundError(
            "whisper.cpp 모델 파일(.bin)을 찾을 수 없습니다. "
            "~/whisper-models 에 넣거나 transcribe.whisper_cpp_model 에 경로를 적어 주세요.")

    log(f"  · whisper.cpp: {Path(model).name}")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "out"
        cmd = [binary, "-m", model, "-f", str(audio_path), "-osrt", "-of", str(prefix)]
        language = options.get("language") or ""
        if language:
            cmd += ["-l", language]
        threads = int(options.get("threads", 0) or 0)
        if threads:
            cmd += ["-t", str(threads)]

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, errors="replace")
        srt_path = prefix.with_suffix(".srt")
        if not srt_path.exists():
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
            raise RuntimeError(f"whisper.cpp 실행에 실패했습니다.\n{tail}")
        transcript = parse_subtitle_file(srt_path)

    transcript.language = options.get("language", "ko")
    log(f"  · 대사 {len(transcript.segments)}줄 인식")
    return transcript
