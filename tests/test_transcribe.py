"""전사 백엔드 선택 / whisper.cpp 연동."""

import stat

import pytest

from gameedit.transcribe import (find_whisper_cpp, find_whisper_model, resolve_backend,
                                 transcribe, whisper_cpp_ready)

SRT = """1
00:00:01,000 --> 00:00:03,500
와 이거 진짜 대박이다

2
00:00:04,000 --> 00:00:06,000
아 죽었어 왜 죽냐고
"""


def fake_whisper_cpp(tmp_path, *, output=SRT, fail=False):
    """`-of 접두사` 를 받아 .srt 를 뱉는 가짜 whisper.cpp."""
    binary = tmp_path / "whisper-cli"
    body = "#!/bin/sh\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n"
    body += "    -of) PREFIX=\"$2\"; shift 2;;\n    *) shift;;\n  esac\ndone\n"
    if fail:
        body += "echo 'model load failed' >&2\nexit 1\n"
    else:
        body += "cat > \"$PREFIX.srt\" <<'SRTEOF'\n" + output + "SRTEOF\n"
    binary.write_text(body)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary


def fake_model(tmp_path, name="ggml-tiny.bin"):
    model = tmp_path / name
    model.write_bytes(b"ggml")
    return model


# ------------------------------------------------------------------ 백엔드 선택

def test_external_wins_when_file_given(tmp_path):
    subs = tmp_path / "a.srt"
    subs.write_text(SRT, encoding="utf-8")
    assert resolve_backend("auto", str(subs)) == "external"


def test_explicit_backend_is_respected():
    assert resolve_backend("whisper.cpp") == "whisper.cpp"
    assert resolve_backend("none") == "none"


def test_falls_back_to_none_without_anything(monkeypatch):
    monkeypatch.setattr("gameedit.transcribe._module_available", lambda name: False)
    monkeypatch.setattr("gameedit.transcribe.whisper_cpp_ready", lambda opts=None: False)
    assert resolve_backend("auto") == "none"


def test_whisper_cpp_picked_when_binary_and_model_exist(tmp_path, monkeypatch):
    monkeypatch.setattr("gameedit.transcribe._module_available", lambda name: False)
    binary = fake_whisper_cpp(tmp_path)
    model = fake_model(tmp_path)
    options = {"whisper_cpp_bin": str(binary), "whisper_cpp_model": str(model)}

    assert whisper_cpp_ready(options) is True
    assert resolve_backend("auto", "", options) == "whisper.cpp"


def test_whisper_cpp_not_ready_without_model(tmp_path, monkeypatch):
    monkeypatch.setattr("gameedit.transcribe._module_available", lambda name: False)
    monkeypatch.setenv("GAMEEDIT_WHISPER_MODEL", "")
    monkeypatch.setattr("gameedit.transcribe.WHISPER_MODEL_DIRS", (str(tmp_path / "없음"),))
    binary = fake_whisper_cpp(tmp_path)
    assert whisper_cpp_ready({"whisper_cpp_bin": str(binary)}) is False


# ------------------------------------------------------------------ 모델 탐색

def test_find_model_prefers_matching_size(tmp_path, monkeypatch):
    fake_model(tmp_path, "ggml-base.bin")
    fake_model(tmp_path, "ggml-small.bin")
    monkeypatch.setattr("gameedit.transcribe.WHISPER_MODEL_DIRS", (str(tmp_path),))
    assert find_whisper_model(size="small").endswith("ggml-small.bin")
    assert find_whisper_model(size="tiny").endswith(".bin")  # 없으면 아무거나


def test_find_model_via_env(tmp_path, monkeypatch):
    model = fake_model(tmp_path)
    monkeypatch.setenv("GAMEEDIT_WHISPER_MODEL", str(model))
    assert find_whisper_model() == str(model)


def test_find_binary_via_env(tmp_path, monkeypatch):
    binary = fake_whisper_cpp(tmp_path)
    monkeypatch.setenv("GAMEEDIT_WHISPER_CPP", str(binary))
    assert find_whisper_cpp() == str(binary)


# ------------------------------------------------------------------ 실제 호출

def test_whisper_cpp_transcribes(tmp_path):
    binary = fake_whisper_cpp(tmp_path)
    model = fake_model(tmp_path)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    lines = []
    result = transcribe(audio, {
        "backend": "whisper.cpp",
        "whisper_cpp_bin": str(binary),
        "whisper_cpp_model": str(model),
        "language": "ko",
    }, log=lines.append)

    assert [s.text for s in result.segments] == ["와 이거 진짜 대박이다", "아 죽었어 왜 죽냐고"]
    assert result.segments[0].start == pytest.approx(1.0)
    assert result.segments[1].end == pytest.approx(6.0)
    assert result.language == "ko"
    assert any("whisper.cpp" in line for line in lines)


def test_whisper_cpp_failure_is_reported(tmp_path):
    binary = fake_whisper_cpp(tmp_path, fail=True)
    model = fake_model(tmp_path)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")

    with pytest.raises(RuntimeError, match="whisper.cpp"):
        transcribe(audio, {"backend": "whisper.cpp", "whisper_cpp_bin": str(binary),
                           "whisper_cpp_model": str(model)}, log=lambda m: None)


def test_missing_binary_message(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEEDIT_WHISPER_CPP", "")
    monkeypatch.setattr("gameedit.transcribe.WHISPER_CPP_BINARIES", ("없는실행파일",))
    with pytest.raises(FileNotFoundError, match="whisper.cpp"):
        transcribe(tmp_path / "a.wav", {"backend": "whisper.cpp"}, log=lambda m: None)


def test_external_backend_reads_file(tmp_path):
    subs = tmp_path / "대사.srt"
    subs.write_text(SRT, encoding="utf-8")
    result = transcribe(tmp_path / "a.wav",
                        {"backend": "external", "external": str(subs)}, log=lambda m: None)
    assert len(result.segments) == 2


def test_none_backend_returns_empty():
    result = transcribe("a.wav", {"backend": "none"}, log=lambda m: None)
    assert result.segments == []
