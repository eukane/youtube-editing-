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
    """진짜 모델처럼 보이는 파일.

    크기까지 흉내 내야 한다. 껍데기 모델(whisper.cpp 의 for-tests-*.bin)을
    거르려고 최소 크기를 보기 때문에, 4바이트짜리로는 후보에서 빠진다.
    """
    model = tmp_path / name
    model.write_bytes(b"ggml" + b"\x00" * (40 * 1024 * 1024))
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


# ------------------------------------------- 가짜(테스트용) 모델 걸러내기

def test_whisper_cpp_test_models_are_ignored(tmp_path, monkeypatch):
    """whisper.cpp 저장소의 `for-tests-*.bin` 은 가중치가 없는 껍데기다.

    이름에 tiny 가 들어 있어서 골라지는데, 대사를 한 줄도 못 알아듣고 오류도
    안 난다. 자막 없는 영상이 조용히 완성돼서 원인을 찾기가 아주 어렵다.
    """
    from gameedit import transcribe as tr

    models = tmp_path / "models"
    models.mkdir()
    fake = models / "for-tests-ggml-tiny.bin"
    fake.write_bytes(b"\x00" * 1024)                       # 1KB 짜리 껍데기
    real = models / "ggml-base.bin"
    real.write_bytes(b"\x00" * (60 * 1024 * 1024))         # 60MB
    monkeypatch.setattr(tr, "WHISPER_MODEL_DIRS", (str(models),))

    # 폰 프로필은 'tiny' 를 원한다. 그래도 껍데기를 고르면 안 된다.
    assert tr.find_whisper_model(size="tiny") == str(real)
    assert tr.find_whisper_model() == str(real)


def test_undersized_models_are_ignored(tmp_path, monkeypatch):
    """이름이 멀쩡해도 크기가 말이 안 되면 진짜 모델이 아니다."""
    from gameedit import transcribe as tr

    models = tmp_path / "models"
    models.mkdir()
    (models / "ggml-tiny.bin").write_bytes(b"\x00" * 2048)
    monkeypatch.setattr(tr, "WHISPER_MODEL_DIRS", (str(models),))
    assert tr.find_whisper_model(size="tiny") is None


def test_explicitly_configured_model_is_trusted(tmp_path, monkeypatch):
    """직접 경로를 적었으면 그대로 쓴다. 사용자가 알고 넣은 것이다."""
    from gameedit import transcribe as tr

    chosen = tmp_path / "for-tests-ggml-tiny.bin"
    chosen.write_bytes(b"\x00" * 10)
    monkeypatch.setattr(tr, "WHISPER_MODEL_DIRS", ())
    assert tr.find_whisper_model(str(chosen)) == str(chosen)


def test_real_model_check(tmp_path):
    from gameedit.transcribe import is_real_model

    big = tmp_path / "ggml-small.bin"
    big.write_bytes(b"\x00" * (60 * 1024 * 1024))
    assert is_real_model(big)
    assert not is_real_model(tmp_path / "없는파일.bin")
