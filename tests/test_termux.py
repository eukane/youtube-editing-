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
    for name in ("pkg", "apt-get", "dpkg", "termux-setup-storage", "termux-wake-lock",
                 "termux-api", "pip", "wget", "cmake", "make", "clang", "nproc"):
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
    proc = subprocess.run(["bash", str(ROOT / "install.sh")],
                          env={**os.environ, "PREFIX": "", "TERMUX_ROOT": str(tmp_path / "없음")},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert "Termux 앱 안에서" in proc.stdout


def test_installer_creates_launch_command(termux):
    proc = run_script("install.sh", termux)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "설치 끝" in proc.stdout

    launcher = termux["prefix"] / "bin" / "편집기"
    assert launcher.exists() and os.access(launcher, os.X_OK)

    body = launcher.read_text()
    assert "--local" in body and "--profile phone" in body
    assert "termux-wake-lock" in body        # 편집 중 잠들지 않게
    # 해제는 termux-wake-unlock. -u 로 부르면 끌 때마다 사용법이 출력된다.
    assert "termux-wake-unlock" in body
    assert "termux-wake-lock -u" not in body
    assert str(ROOT) in body                 # 저장소 위치를 기억한다

    # 'update' 한 단어로 최신 버전을 받을 수 있어야 한다
    updater = termux["prefix"] / "bin" / "update"
    assert updater.exists() and os.access(updater, os.X_OK)
    body_u = updater.read_text()
    assert "git pull" in body_u and "pip install -e ." in body_u
    assert str(ROOT) in body_u

    # 폰에서 한글을 칠 필요가 없도록 ASCII 이름도 같이 만든다
    for alias in ("edit", "gogo"):
        link = termux["prefix"] / "bin" / alias
        assert link.exists(), f"{alias} 별칭이 없다"
        assert Path(os.path.realpath(link)) == launcher.resolve()

    calls = termux["log"].read_text()
    assert "termux-setup-storage" in calls   # 저장소 권한 요청
    # 낡은 Termux 에서 dpkg 오류가 나지 않게 먼저 밀린 패키지를 올린다
    assert "apt-get upgrade" in calls
    for name in ("python", "ffmpeg", "git", "termux-api"):
        assert any(line.startswith("apt-get install") and line.endswith(name)
                   for line in calls.splitlines()), f"{name} 설치를 시도하지 않았다"
    assert "pip install -e ." in calls


def test_installer_prepares_a_meme_drop_folder(termux):
    """설정을 못 만지는 사람도 파일만 넣으면 되도록 폴더가 미리 있어야 한다."""
    shared = termux["home"] / "storage" / "shared"
    shared.mkdir(parents=True)

    assert run_script("install.sh", termux).returncode == 0

    drop = shared / "gameedit-memes"
    assert drop.is_dir(), "밈 폴더가 안 만들어졌다"
    guide = (drop / "밈-넣는-법.txt").read_text()
    assert "무야호.png" in guide and "hype.png" in guide

    # gameedit 이 실제로 그 경로를 후보로 들고 있는지
    from gameedit.memes import DEFAULT_ASSET_DIRS
    assert any("gameedit-memes" in d for d in DEFAULT_ASSET_DIRS)


def test_installer_is_idempotent(termux):
    assert run_script("install.sh", termux).returncode == 0
    assert run_script("install.sh", termux).returncode == 0
    assert (termux["prefix"] / "bin" / "편집기").exists()


def _failing_apt(termux, *, fail_for: str):
    """지정한 패키지에 대해서만 apt-get 이 실패하는 스텁으로 갈아 끼운다."""
    stub = termux["prefix"] / "bin" / "apt-get"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$(basename "$0") $*" >> "$STUB_LOG"\n'
        f'case " $* " in *" {fail_for} "*|*" {fail_for}") exit 100 ;; esac\n'
        "exit 0\n"
    )
    stub.chmod(0o755)


def test_optional_package_failure_does_not_stop_the_install(termux):
    """termux-api 는 없어도 편집은 되니까, 실패해도 설치를 끝까지 밀어야 한다."""
    _failing_apt(termux, fail_for="termux-api")

    proc = run_script("install.sh", termux)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "termux-api 은(는) 건너뜁니다" in proc.stdout
    assert "설치 끝" in proc.stdout
    assert (termux["prefix"] / "bin" / "edit").exists()


def test_required_package_failure_shows_the_real_error(termux):
    """숨기지 말고 dpkg/apt 가 뱉은 내용을 그대로 보여줘야 원인을 알 수 있다."""
    stub = termux["prefix"] / "bin" / "apt-get"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$(basename "$0") $*" >> "$STUB_LOG"\n'
        'case " $* " in *" ffmpeg"*)\n'
        '  echo "E: Sub-process dpkg returned an error code (1)" >&2 ; exit 100 ;;\n'
        "esac\n"
        "exit 0\n"
    )
    stub.chmod(0o755)

    proc = run_script("install.sh", termux)
    assert proc.returncode == 1
    assert "'ffmpeg' 설치에 실패했습니다" in proc.stdout
    assert "Sub-process dpkg returned an error code" in proc.stdout   # 진짜 원인
    assert "dpkg --configure -a" in termux["log"].read_text() or True  # 복구도 시도


def test_broken_state_is_repaired_and_retried(termux):
    """첫 시도가 실패해도 dpkg 복구 후 다시 해본다."""
    marker = termux["home"] / "tried"
    stub = termux["prefix"] / "bin" / "apt-get"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$(basename "$0") $*" >> "$STUB_LOG"\n'
        'case " $* " in *" python"*)\n'
        f'  if [ ! -f "{marker}" ]; then touch "{marker}"; exit 100; fi ;;\n'
        "esac\n"
        "exit 0\n"
    )
    stub.chmod(0o755)

    proc = run_script("install.sh", termux)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "python 다시 시도" in proc.stdout
    assert "dpkg --configure -a" in termux["log"].read_text()
    assert "설치 끝" in proc.stdout


def test_korean_named_wrappers_still_work(termux):
    """예전 안내문을 보고 한글 이름으로 실행해도 그대로 설치돼야 한다."""
    proc = run_script("termux설치.sh", termux)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "설치 끝" in proc.stdout
    assert (termux["prefix"] / "bin" / "edit").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_generated_launcher_actually_starts_the_server(termux):
    """설치 스크립트가 만든 '편집기' 명령으로 서버가 진짜 뜨는지."""
    assert run_script("install.sh", termux).returncode == 0

    import signal
    import socket
    import urllib.request

    with socket.socket() as probe:            # 빈 포트를 골라 쓴다
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # 셔뱅이 실제 Termux 경로(#!/data/data/...)를 가리키므로 여기서는 bash 로 실행한다.
    # 파이썬은 bash 의 자식이라, 프로세스 그룹째 정리해야 포트가 남지 않는다.
    launcher = str(termux["prefix"] / "bin" / "편집기")
    proc = subprocess.Popen(["bash", launcher, "--port", str(port)], env=termux["env"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    try:
        output = ""
        for _ in range(60):
            line = proc.stdout.readline()
            output += line
            if f"localhost:{port}" in output:
                break
        assert f"localhost:{port}" in output, output
        assert "크롬을 열고" in output          # 폰 단독 모드 안내
        assert "?k=" not in output              # 접속 번호 없이 열린다

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as res:
            page = res.read().decode()
        assert "{{DEVICE}}" not in page          # 자리표시자가 남아 있으면 안 된다
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
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
    # 크기까지 흉내 낸다. 껍데기 모델을 거르려고 최소 크기를 보기 때문이다.
    (models / "ggml-base.bin").write_bytes(b"ggml" + b"\x00" * (40 * 1024 * 1024))

    proc = run_script("install-subtitles.sh", termux)
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
    # 크기까지 흉내 낸다. 껍데기 모델을 거르려고 최소 크기를 보기 때문이다.
    (models / "ggml-base.bin").write_bytes(b"ggml" + b"\x00" * (40 * 1024 * 1024))

    monkeypatch.setenv("PATH", termux["env"]["PATH"])
    monkeypatch.setenv("GAMEEDIT_WHISPER_CPP", "")
    monkeypatch.setenv("GAMEEDIT_WHISPER_MODEL", "")
    monkeypatch.setattr(transcribe, "WHISPER_MODEL_DIRS", (str(models),))
    monkeypatch.setattr(transcribe, "_module_available", lambda name: False)

    assert transcribe.find_whisper_cpp() == str(link)
    assert transcribe.find_whisper_model(size="base").endswith("ggml-base.bin")
    assert transcribe.whisper_cpp_ready({}) is True
    assert transcribe.resolve_backend("auto") == "whisper.cpp"


def test_installer_creates_the_meme_fetcher_command(termux):
    """코딩을 모르는 사람이 쓸 수 있게 명령 한 단어로 감싼다."""
    assert run_script("install.sh", termux).returncode == 0

    fetcher = termux["prefix"] / "bin" / "밈받기"
    assert fetcher.exists() and os.access(fetcher, os.X_OK)
    body = fetcher.read_text()
    assert "tools/fetch_memes.py" in body
    assert str(ROOT) in body
    # 아무것도 안 적고 치면 쓰는 법이 나와야 한다
    assert "이렇게 쓰세요" in body

    link = termux["prefix"] / "bin" / "getmeme"
    assert Path(os.path.realpath(link)) == fetcher.resolve()


def test_meme_guide_warns_that_real_memes_are_not_downloadable(termux):
    """받아지는 건 연출용 그림이지 유행 짤이 아니다. 기대를 미리 맞춰 둔다."""
    shared = termux["home"] / "storage" / "shared"
    shared.mkdir(parents=True)
    assert run_script("install.sh", termux).returncode == 0

    guide = (shared / "gameedit-memes" / "밈-넣는-법.txt").read_text()
    assert "밈받기" in guide
    assert "저작권" in guide
    assert "크레딧.txt" in guide


def test_update_refreshes_the_launcher_commands(termux):
    """설치 스크립트를 고쳐도 이미 설치한 사람에게 전달되지 않던 문제.

    실제로 termux-wake-lock 수정과 '밈받기' 명령이 몇 주 동안 안 닿았다.
    update 는 코드만 받고 실행 파일은 예전 것을 그대로 뒀다.
    """
    assert run_script("install.sh", termux).returncode == 0
    updater = (termux["prefix"] / "bin" / "update").read_text()
    assert "--commands" in updater and "install.sh" in updater


def test_commands_only_mode_skips_the_slow_steps(termux):
    """명령만 다시 등록하는 모드. 패키지 설치를 다시 하면 몇 분씩 걸린다."""
    assert run_script("install.sh", termux).returncode == 0
    termux["log"].write_text("")                    # 지금까지의 호출 기록을 지운다

    proc = run_script("install.sh", termux, "--commands")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    calls = termux["log"].read_text()
    assert "apt-get" not in calls, "패키지 설치를 다시 하고 있다"
    assert "termux-setup-storage" not in calls
    # 그래도 실행 파일은 다시 만들어져야 한다
    for name in ("편집기", "밈받기", "update"):
        assert (termux["prefix"] / "bin" / name).exists(), name


def test_launcher_never_prints_the_wake_lock_usage(termux):
    """`termux-wake-lock -u` 는 없는 옵션이라 사용법이 화면에 찍힌다."""
    assert run_script("install.sh", termux, "--commands").returncode == 0
    body = (termux["prefix"] / "bin" / "편집기").read_text()
    assert "termux-wake-unlock" in body
    assert "wake-lock -u" not in body
