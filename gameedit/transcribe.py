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


def resolve_threads(raw) -> int:
    """whisper.cpp 에 넘길 스레드 수.

        0 이상  그대로 (0 이면 whisper.cpp 기본값 = 보통 4)
        음수    코어 수에서 그만큼 뺀다 (편집기·브라우저가 쓸 여유를 남긴다)
    """
    try:
        raw = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    if raw >= 0:
        return raw
    return max(1, (os.cpu_count() or 2) + raw)


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


# whisper.cpp 저장소에는 자기 단위 테스트용 더미 모델(`for-tests-ggml-*.bin`)이
# 같이 들어 있다. 이름에 tiny/base 가 들어 있어서 골라지지만, 가중치가 없는
# 껍데기라 **대사를 한 줄도 못 알아듣는다.** 오류도 안 나서 자막 없는 영상이
# 조용히 완성된다. 이름과 크기 두 가지로 걸러 낸다.
FAKE_MODEL_MARKERS = ("for-tests", "for_tests", "dummy")
# 제일 작은 진짜 모델(ggml-tiny)이 약 75MB. 그 절반도 안 되면 진짜가 아니다.
MIN_MODEL_BYTES = 30 * 1024 * 1024


def is_real_model(path: Path) -> bool:
    name = path.name.lower()
    if any(mark in name for mark in FAKE_MODEL_MARKERS):
        return False
    try:
        return path.stat().st_size >= MIN_MODEL_BYTES
    except OSError:
        return False


# 모델을 올리는 데 필요한 램은 파일 크기의 약 1.35배 + 계산 버퍼.
MODEL_RAM_FACTOR = 1.35
MODEL_RAM_OVERHEAD_MB = 180.0
# 남은 메모리의 이 비율까지만 쓴다. 꽉 채우면 편집 단계에서 죽는다.
MEMORY_HEADROOM = 0.6


def model_fits(path: Path, available_mb: float) -> bool:
    """이 모델을 지금 메모리로 올릴 수 있는지. 못 재면 그냥 된다고 본다."""
    if available_mb <= 0:
        return True
    try:
        need = path.stat().st_size / (1024 * 1024) * MODEL_RAM_FACTOR + MODEL_RAM_OVERHEAD_MB
    except OSError:
        return False
    return need <= available_mb * MEMORY_HEADROOM


def find_whisper_model(explicit: str = "", size: str = "") -> str | None:
    """whisper.cpp 용 ggml 모델 파일(.bin) 찾기.

    size 가 비었거나 'auto' 면 **메모리에 들어가는 것 중 제일 큰 모델**을
    고른다. 크기를 박아 두면 사용자가 더 좋은 모델을 받아도 프로그램이
    계속 작은 걸 쓴다 — 실제로 small 을 받아 놓고도 base 가 돌았다.
    """
    for candidate in (explicit, os.environ.get("GAMEEDIT_WHISPER_MODEL", "")):
        if candidate and Path(candidate).expanduser().exists():
            return str(Path(candidate).expanduser())   # 직접 지정한 건 그대로 믿는다

    found: list[Path] = []
    for directory in WHISPER_MODEL_DIRS:
        path = Path(directory).expanduser()
        if path.is_dir():
            found.extend(sorted(p for p in path.glob("*.bin") if is_real_model(p)))
    if not found:
        return None

    size = (size or "").strip().lower()
    if size and size != "auto":       # 크기를 콕 집었으면 그걸 따른다
        for model in found:
            if size in model.name.lower():
                return str(model)

    from .system import available_memory_mb
    available = available_memory_mb()
    # 큰 것부터 보면서 메모리에 들어가는 첫 번째를 쓴다
    for model in sorted(found, key=lambda p: p.stat().st_size, reverse=True):
        if model_fits(model, available):
            return str(model)
    # 전부 빠듯하면 제일 작은 것이라도 (아예 못 하는 것보다 낫다)
    return str(min(found, key=lambda p: p.stat().st_size))


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
        # whisper.cpp 는 기본이 4스레드다. 8코어 기기에서 절반만 쓰는 셈이라
        # 그냥 두면 두 배 가까이 손해다. 음수면 그만큼 코어를 남긴다.
        threads = resolve_threads(options.get("threads", 0))
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
    if not transcript.segments:
        # 조용히 넘어가면 자막 없는 영상이 완성된 뒤에야 알게 된다
        log("  ⚠ 대사를 한 줄도 못 알아들었습니다. 자막 없이 계속합니다.")
        log(f"     쓴 모델: {Path(model).name} ({Path(model).stat().st_size // 1024 // 1024}MB)")
        log("     · 영상에 말소리가 없거나")
        log("     · 모델이 너무 작거나 (tiny 는 한국어를 자주 놓칩니다)")
        log("     · 다시 받아야 하는 경우입니다:  bash install-subtitles.sh base")
    return transcript
