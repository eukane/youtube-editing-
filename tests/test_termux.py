"""안드로이드(Termux) 지원 검증.

진짜 폰이 없어도 확인할 수 있는 것들:
  · Termux 환경 감지와 폰 폴더 인식
  · 설치 스크립트가 실제로 끝까지 돌고 실행 명령을 제대로 만드는지
  · 그렇게 만들어진 명령으로 서버가 실제로 뜨는지

Termux 는 결국 리눅스라서, $PREFIX 와 몇 개의 termux 전용 명령만 흉내 내면
스크립트를 그대로 실행해 볼 수 있다. 확인할 수 없는 것은
whisper.cpp 가 안드로이드에서 실제로 컴파일되는지, 그리고 실제 속도뿐이다.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$STUB_LOG"
exit 0
"""


@pytest.fixture
def termux(tmp_path):
    """가짜 Termux 환경 한 벌."""
    root = tmp_path / "data" / "data" / "com.termux"
    prefix = root / "files" / "usr"
    home = root / "files" / "home"
    (prefix / "bin").mkdir(parents=True)
    home.mkdir(parents=True)

    log = tmp_path / "stub.log"
    for name in ("pkg", "termux-setup-storage", "termux-wake-lock", "termux-api",
                 "pip", "wget", "cmake", "make", "clang", "nproc"):
        stub = prefix / "bin" / name
        stub.write_text(STUB)
        stub.chmod(0o755)
    (prefix / "bin" / "nproc").write_text("#!/bin/sh\necho 4\n")
    (prefix / "bin" / "nproc").chmod(0o755)
    # Termux 에는 python 이라는 이름으로 들어 있다
    py = prefix / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(0o755)

    env = dict(os.environ)
    env.update({
        "PREFIX": str(prefix),
        "HOME": str(home),
        "TERMUX_ROOT": str(root),
        "STUB_LOG": str(log),
        "PATH": f"{prefix / 'bin'}:{env['PATH']}",
        "PYTHONPATH": str(ROOT),
    })
    return {"env": env, "prefix": prefix, "home": home, "log": log, "root": root}


def run_script(script: str, termux, *args, cwd=None, timeout=180):
    return subprocess.run(["bash", str(ROOT / script), *args], env=termux["env"],
                          cwd=str(cwd or ROOT), capture_output=True, text=True,
                          timeout=timeout)


# --------------------------------------------------------------- 환경 감지

def test_detects_termux_from_prefix(monkeypatch, termux):
    from gameedit.server import on_termux

    monkeypatch.setenv("PREFIX", termux["env"]["PREFIX"])
    assert on_termux() is True

    monkeypatch.setenv("PREFIX", "/usr")
    monkeypatch.setattr("gameedit.server.Path", Path)
    # 실제 안드로이드 경로가 없는 환경이면 False
    assert on_termux() is (Path("/data/data/com.termux/files/usr").exists())


def test_phone_media_dirs_found(monkeypatch, tmp_path):
    from gameedit.server import phone_media_dirs

    home = tmp_path / "home"
    for name in ("DCIM", "Movies", "Download"):
        (home / "storage" / "shared" / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    found = [p.name for p in phone_media_dirs()]
    assert found == ["DCIM", "Movies", "Download"]


def test_server_labels_and_folders_on_termux(monkeypatch, tmp_path):
    """폰에서 돌면 화면 문구가 '폰' 이 되고 갤러리 폴더가 목록에 들어간다."""
    from gameedit.config import Config
    from gameedit.server import create_server

    home = tmp_path / "home"
    dcim = home / "storage" / "shared" / "DCIM"
    dcim.mkdir(parents=True)
    (dcim / "오늘방송.mp4").write_bytes(b"video")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")

    httpd, _manager, key = create_server(Config(), host="127.0.0.1", port=0,
                                         root=tmp_path / "work", access_key="")
    try:
        handler = httpd.RequestHandlerClass
        assert handler.device_label == "폰"
        assert dcim in handler.watch_dirs
        assert key == ""  # 폰 단독이면 접속 번호 없이
    finally:
        httpd.server_close()


# --------------------------------------------------------------- 설치 스크립트

def test_installer_refuses_outside_termux(tmp_path):
    proc = subprocess.run(["bash", str(ROOT / "termux설치.sh")],
                          env={**os.environ, "PREFIX": "", "TERMUX_ROOT": str(tmp_path / "없음")},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert "Termux 앱 안에서" in proc.stdout


def test_installer_creates_launch_command(termux):
    proc = run_script("termux설치.sh", termux)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "설치 끝" in proc.stdout

    launcher = termux["prefix"] / "bin" / "편집기"
    assert launcher.exists() and os.access(launcher, os.X_OK)

    body = launcher.read_text()
    assert "--local" in body and "--profile phone" in body
    assert "termux-wake-lock" in body        # 편집 중 잠들지 않게
    assert str(ROOT) in body                 # 저장소 위치를 기억한다

    calls = termux["log"].read_text()
    assert "termux-setup-storage" in calls   # 저장소 권한 요청
    assert "pkg install -y python ffmpeg git" in calls
    assert "pip install -e ." in calls


def test_installer_is_idempotent(termux):
    assert run_script("termux설치.sh", termux).returncode == 0
    assert run_script("termux설치.sh", termux).returncode == 0
    assert (termux["prefix"] / "bin" / "편집기").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_generated_launcher_actually_starts_the_server(termux):
    """설치 스크립트가 만든 '편집기' 명령으로 서버가 진짜 뜨는지."""
    assert run_script("termux설치.sh", termux).returncode == 0

    # 셔뱅이 실제 Termux 경로(#!/data/data/...)를 가리키므로 여기서는 bash 로 실행한다
    launcher = str(termux["prefix"] / "bin" / "편집기")
    proc = subprocess.Popen(["bash", launcher, "--port", "8931"], env=termux["env"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        output = ""
        for _ in range(60):
            line = proc.stdout.readline()
            output += line
            if "localhost:8931" in output:
                break
        assert "localhost:8931" in output, output
        assert "크롬을 열고" in output          # 폰 단독 모드 안내
        assert "?k=" not in output              # 접속 번호 없이 열린다

        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8931/health", timeout=5) as res:
            assert res.status == 200
        with urllib.request.urlopen("http://127.0.0.1:8931/", timeout=5) as res:
            page = res.read().decode()
        assert "{{DEVICE}}" not in page          # 자리표시자가 남아 있으면 안 된다
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    assert "termux-wake-lock" in termux["log"].read_text()


def test_subtitle_installer_wires_whisper_cpp(termux):
    """자막 설치 스크립트가 실행 파일과 모델을 제자리에 놓는지.

    (실제 빌드는 흉내만 낸다. 안드로이드에서 컴파일이 되는지는 여기서 알 수 없다.)
    """
    src = termux["home"] / "whisper.cpp"
    build_bin = src / "build" / "bin"
    build_bin.mkdir(parents=True)
    binary = build_bin / "whisper-cli"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)

    # git clone / cmake 는 흉내만 내고, 모델은 wget 스텁 대신 미리 놓아 둔다
    git = termux["prefix"] / "bin" / "git"
    git.write_text(STUB)
    git.chmod(0o755)
    models = termux["home"] / "whisper-models"
    models.mkdir()
    (models / "ggml-base.bin").write_bytes(b"ggml")

    proc = run_script("termux자막설치.sh", termux)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "자막 준비 끝" in proc.stdout

    link = termux["prefix"] / "bin" / "whisper-cli"
    assert link.exists()
    assert Path(os.path.realpath(link)) == binary


def test_gameedit_finds_whisper_cpp_in_that_layout(termux, monkeypatch):
    """설치 스크립트가 만든 배치를 gameedit 이 그대로 인식하는지."""
    from gameedit import transcribe

    link = termux["prefix"] / "bin" / "whisper-cli"
    link.write_text("#!/bin/sh\nexit 0\n")
    link.chmod(0o755)
    models = termux["home"] / "whisper-models"
    models.mkdir(exist_ok=True)
    (models / "ggml-base.bin").write_bytes(b"ggml")

    monkeypatch.setenv("PATH", termux["env"]["PATH"])
    monkeypatch.setenv("GAMEEDIT_WHISPER_CPP", "")
    monkeypatch.setenv("GAMEEDIT_WHISPER_MODEL", "")
    monkeypatch.setattr(transcribe, "WHISPER_MODEL_DIRS", (str(models),))
    monkeypatch.setattr(transcribe, "_module_available", lambda name: False)

    assert transcribe.find_whisper_cpp() == str(link)
    assert transcribe.find_whisper_model(size="base").endswith("ggml-base.bin")
    assert transcribe.whisper_cpp_ready({}) is True
    assert transcribe.resolve_backend("auto") == "whisper.cpp"
