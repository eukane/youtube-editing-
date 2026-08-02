"""휴대폰에서 쓰는 웹 UI 서버 — HTTP 만 담당한다.

폰 안에서 전부 돌릴 수도 있고, 컴퓨터가 일하고 폰은 조작만 할 수도 있다.
같은 와이파이에 있으면 앱 설치 없이 아이폰·안드로이드 모두 쓸 수 있다.

책임을 셋으로 나눠 두었다. 예전에는 한 파일에 다 있어서 1,000 줄을 넘었고,
"작업 순서를 고치려는데 HTTP 코드를 헤집는" 상태였다.

    jobs.py       무엇을 어떤 순서로 할지 (분석 → 계획 → 렌더)
    phoneedit.py  편집 계획 ↔ 폰 화면 사이의 번역
    server.py     주소·권한·파일 주고받기  ← 이 파일
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import Config
from .models import load_json, plan_from_dict
# 작업 큐와 화면 번역은 따로 산다. 여기는 HTTP 만 안다.
# (예전에는 셋이 한 파일에 있어서 1,000 줄을 넘었다)
from .jobs import Job, JobManager, probe_duration, resolve_pace, resolve_style
from .phoneedit import apply_phone_edits, plan_for_phone
from .webui import PAGE

# 예전부터 `gameedit.server` 에서 가져다 쓰던 이름들. 옮겼다고 부르는 쪽을
# 깨뜨릴 이유가 없어서 여기서도 계속 보이게 둔다.
from .jobs import EDIT_PACE, load_job_options, save_job_options

__all__ = [
    "Handler", "create_server", "serve", "cleanup_old_uploads", "disk_free_mb",
    "local_ip", "content_disposition", "on_termux", "phone_media_dirs",
    # 다른 파일로 옮겼지만 여기서도 계속 쓸 수 있게 둔 것들
    "Job", "JobManager", "EDIT_PACE", "probe_duration", "resolve_pace", "resolve_style",
    "save_job_options", "load_job_options", "apply_phone_edits", "plan_for_phone",
]

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".flv"}
# 다른 데서 만들어 온 자막. 폰에서 음성 인식을 돌리는 게 제일 무거운 작업이라
# 유튜브 자동자막·클로바노트·브루 같은 걸로 만들어 넣을 수 있어야 한다.
SUBTITLE_EXTS = {".srt", ".vtt"}
UPLOAD_CHUNK = 1024 * 1024


def on_termux() -> bool:
    """안드로이드 Termux 안에서 돌고 있는지."""
    return "com.termux" in (os.environ.get("PREFIX", "") or "") or \
        Path("/data/data/com.termux/files/usr").exists()


def phone_media_dirs() -> list[Path]:
    """Termux 에서 접근할 수 있는 폰 안의 영상 폴더들.

    `termux-setup-storage` 를 한 번 실행하면 ~/storage 에 연결된다.
    """
    home = Path.home()
    candidates = [
        home / "storage" / "shared" / "DCIM",
        home / "storage" / "shared" / "Movies",
        home / "storage" / "shared" / "Download",
        home / "storage" / "dcim",
        home / "storage" / "movies",
        home / "storage" / "downloads",
        home / "storage" / "shared" / "Android" / "media",
    ]
    return [p for p in candidates if p.is_dir()]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def local_ip() -> str:
    """같은 와이파이의 폰이 접속할 주소를 알아낸다."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # 실제로 패킷을 보내지는 않는다
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def content_disposition(filename: str) -> str:
    """다운로드 파일 이름 헤더.

    HTTP 헤더는 latin-1 만 담을 수 있어서 한글 파일명을 그대로 넣으면 서버가 터진다.
    ASCII 로 만든 대체 이름과 RFC 5987 형식을 같이 보낸다.
    """
    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip() or "video.mp4"
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


class Handler(BaseHTTPRequestHandler):
    server_version = "gameedit"
    manager: JobManager
    access_key: str
    watch_dirs: list[Path]
    device_label: str = "컴퓨터"

    # -- 유틸 --------------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # 콘솔을 조용하게
        pass

    def _authorized(self, query: dict) -> bool:
        if not self.access_key:
            return True
        given = (self.headers.get("X-Key") or "").strip()
        if not given:
            given = (query.get("k") or [""])[0]
        return secrets.compare_digest(given, self.access_key)

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str = "text/html; charset=utf-8",
                   status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, *, download_name: str = "") -> None:
        """Range 지원 (폰에서 결과 영상을 바로 재생하려면 필요)."""
        if not path.exists():
            self._send_json({"error": "파일이 없습니다"}, 404)
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = 200

        range_header = self.headers.get("Range")
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):  # 마지막 N 바이트
                    start = max(0, size - int(m.group(2)))
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download_name:
            self.send_header("Content-Disposition", content_disposition(download_name))
        self.end_headers()

        with path.open("rb") as fp:
            fp.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fp.read(min(UPLOAD_CHUNK, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return  # 폰이 재생을 멈추고 연결을 끊은 경우
                remaining -= len(chunk)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- 라우팅 ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if path == "/health":
            self._send_json({"ok": True})
            return
        if not self._authorized(query):
            if path == "/":
                self._send_text(_LOCKED_PAGE, status=401)
            else:
                self._send_json({"error": "접속 번호가 필요합니다"}, 401)
            return

        if path == "/":
            self._send_text(PAGE.replace("{{DEVICE}}", self.device_label))
            return
        if path == "/api/jobs":
            self._send_json({"jobs": self.manager.listing()})
            return
        if path == "/api/files":
            self._send_json({"files": self._available_files()})
            return
        if path == "/api/speedtest":
            self._send_json(self.manager.speed_status())
            return

        m = re.match(r"^/api/jobs/([0-9a-f]+)$", path)
        if m:
            job = self.manager.get(m.group(1))
            self._send_json(job.as_dict() if job else {"error": "없는 작업"}, 200 if job else 404)
            return

        m = re.match(r"^/api/jobs/([0-9a-f]+)/plan$", path)
        if m:
            job = self.manager.get(m.group(1))
            if not job:
                self._send_json({"error": "없는 작업"}, 404)
                return
            plan_path = Path(job.work_dir) / "plan.json"
            if not plan_path.exists():
                self._send_json({"error": "아직 편집 계획이 없습니다"}, 404)
                return
            plan = plan_from_dict(load_json(plan_path))
            plan.relayout()
            self._send_json(plan_for_phone(plan))
            return

        m = re.match(r"^/api/jobs/([0-9a-f]+)/thumb/(\d+)$", path)
        if m:
            job = self.manager.get(m.group(1))
            if not job:
                self._send_json({"error": "없는 작업"}, 404)
                return
            self._send_file(Path(job.work_dir) / "thumbs" / f"{int(m.group(2))}.jpg")
            return

        m = re.match(r"^/api/jobs/([0-9a-f]+)/video$", path)
        if m:
            job = self.manager.get(m.group(1))
            if not job:
                self._send_json({"error": "없는 작업"}, 404)
                return
            download = (query.get("download") or ["0"])[0] == "1"
            self._send_file(Path(job.output),
                            download_name=f"{job.title}_편집본.mp4" if download else "")
            return

        self._send_json({"error": "없는 주소"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._send_json({"error": "접속 번호가 필요합니다"}, 401)
            return
        path = parsed.path

        if path == "/api/upload":
            self._handle_upload()
            return
        if path == "/api/upload-subs":
            self._handle_upload(subtitle=True)
            return
        if path == "/api/jobs":
            self._handle_create_job()
            return

        if path == "/api/files/delete":
            self._handle_delete_file()
            return
        if path == "/api/speedtest":
            self._handle_speedtest()
            return
        if path == "/api/wishes/check":
            from .wishes import parse as parse_wishes
            data = self._read_json_body()
            self._send_json(parse_wishes(str(data.get("wishes") or "")[:500]).as_dict())
            return

        m = re.match(r"^/api/jobs/([0-9a-f]+)/replan$", path)
        if m:
            self._handle_replan(m.group(1))
            return

        self._send_json({"error": "없는 주소"}, 404)

    def _handle_delete_file(self) -> None:
        """폰에서 고른 영상 파일을 지운다.

        되돌릴 수 없는 동작이라 편집할 수 있는 폴더(업로드·감시 폴더) 안에
        있는 파일만 지운다. 경로를 그대로 믿으면 아무 파일이나 지우게 된다.
        """
        data = self._read_json_body()
        raw = str(data.get("path") or "")
        if not raw:
            self._send_json({"error": "지울 파일을 못 찾았습니다"}, 400)
            return

        target = Path(raw)
        if not self._allowed_source(target):
            self._send_json({"error": "이 폴더의 파일은 지울 수 없습니다"}, 403)
            return
        if target.suffix.lower() not in VIDEO_EXTS or not target.is_file():
            self._send_json({"error": "영상 파일이 아닙니다"}, 400)
            return

        try:
            freed = target.stat().st_size
            target.unlink()
        except OSError as err:
            self._send_json({"error": f"지우지 못했습니다: {err}"}, 500)
            return
        self._send_json({"ok": True, "name": target.name,
                         "freed_mb": round(freed / 1024 / 1024, 1)})

    def _handle_speedtest(self) -> None:
        """고른 영상으로 이 기기의 자막 속도를 재기 시작한다."""
        data = self._read_json_body()
        raw = str(data.get("path") or "")
        target = Path(raw) if raw else None
        if target is None or not self._allowed_source(target) or not target.is_file():
            self._send_json({"error": "잴 영상을 먼저 고르세요"}, 400)
            return
        self._send_json(self.manager.start_speedtest(target))

    # -- 처리 --------------------------------------------------------------
    def _available_files(self) -> list[dict]:
        seen: set[str] = set()
        files: list[dict] = []
        directories = [self.manager.root / "uploads", *self.watch_dirs]
        for directory in directories:
            if not directory.is_dir():
                continue
            for entry in sorted(directory.iterdir()):
                if entry.suffix.lower() not in VIDEO_EXTS or not entry.is_file():
                    continue
                key = str(entry.resolve())
                if key in seen:
                    continue
                seen.add(key)
                files.append({
                    "path": str(entry),
                    "name": entry.name,
                    "size_mb": round(entry.stat().st_size / 1024 / 1024, 1),
                    # 원본보다 긴 완성본은 만들 수 없다. 화면에서 그런 선택지를
                    # 아예 안 보여 주려면 길이를 알아야 한다.
                    "duration": probe_duration(entry),
                })
        return files

    def _handle_upload(self, *, subtitle: bool = False) -> None:
        default = "subs.srt" if subtitle else "video.mp4"
        raw_name = unquote(self.headers.get("X-Filename") or default)
        name = Path(raw_name).name or default
        allowed = SUBTITLE_EXTS if subtitle else VIDEO_EXTS
        if Path(name).suffix.lower() not in allowed:
            name += ".srt" if subtitle else ".mp4"
        folder = "subs" if subtitle else "uploads"
        target = self.manager.root / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        counter = 2
        while target.exists():
            target = target.with_name(f"{Path(name).stem}_{counter}{Path(name).suffix}")
            counter += 1

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send_json({"error": "빈 파일입니다"}, 400)
            return

        remaining = length
        try:
            with target.open("wb") as fp:
                while remaining > 0:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                    if not chunk:
                        break
                    fp.write(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            target.unlink(missing_ok=True)
            self._send_json({"error": f"저장 실패: {exc}"}, 500)
            return

        if remaining > 0:
            target.unlink(missing_ok=True)
            self._send_json({"error": "업로드가 중간에 끊겼습니다"}, 400)
            return
        self._send_json({"path": str(target), "name": target.name})

    def _checked_subs(self, raw) -> str:
        """화면에서 온 자막 경로. 업로드한 자막 폴더 안의 것만 받는다."""
        if not isinstance(raw, str) or not raw.strip():
            return ""
        path = Path(raw)
        if path.suffix.lower() not in SUBTITLE_EXTS or not path.is_file():
            return ""
        try:
            path.resolve().relative_to((self.manager.root / "subs").resolve())
        except (ValueError, OSError):
            return ""
        return str(path)

    def _allowed_source(self, source: Path) -> bool:
        """서버가 편집해도 되는 파일인지.

        업로드 폴더와 사용자가 지정한 감시 폴더 안에 있는 것만 허용한다.
        폰이 보낸 경로를 그대로 믿으면 아무 파일이나 열어보게 된다.
        """
        try:
            resolved = source.resolve()
        except OSError:
            return False
        roots = [self.manager.root / "uploads", *self.watch_dirs]
        for root in roots:
            try:
                resolved.relative_to(root.resolve())
                return True
            except (ValueError, OSError):
                continue
        return False

    def _handle_create_job(self) -> None:
        data = self._read_json_body()
        raw_path = str(data.get("path", "")).strip()
        if not raw_path:
            self._send_json({"error": "영상 파일을 고르지 않았습니다"}, 400)
            return
        source = Path(raw_path)
        if not source.is_file():
            self._send_json({"error": f"파일을 찾을 수 없습니다: {source}"}, 400)
            return
        if source.suffix.lower() not in VIDEO_EXTS:
            self._send_json({"error": f"영상 파일이 아닙니다: {source.name}"}, 400)
            return
        if not self._allowed_source(source):
            self._send_json({"error": "이 폴더의 파일은 편집할 수 없습니다. "
                                      "업로드하거나 감시 폴더에 넣어 주세요."}, 403)
            return
        options = {
            "target_duration": data.get("target_duration"),
            "no_memes": bool(data.get("no_memes")),
            "no_subtitles": bool(data.get("no_subtitles")),
            "shorts": bool(data.get("shorts")),
            "pace": resolve_pace(data.get("pace")),
            "style": resolve_style(data.get("style")),
            "subs": self._checked_subs(data.get("subs")),
            "wishes": str(data.get("wishes") or "")[:500],
            # 쇼츠 위아래 빈 자리에 넣을 글. 길이를 여기서 자른다.
            "shorts_title": str(data.get("shorts_title") or "").strip()[:40],
            "channel": str(data.get("channel") or "").strip()[:24],
        }
        job = self.manager.create(source, options)
        self._send_json(job.as_dict())

    def _handle_replan(self, job_id: str) -> None:
        job = self.manager.get(job_id)
        if not job:
            self._send_json({"error": "없는 작업"}, 404)
            return
        if job.status == "running":
            self._send_json({"error": "아직 작업 중입니다"}, 409)
            return
        plan_path = Path(job.work_dir) / "plan.json"
        if not plan_path.exists():
            self._send_json({"error": "편집 계획이 없습니다"}, 404)
            return

        edits = self._read_json_body()
        plan = plan_from_dict(load_json(plan_path))
        plan.sanitize()
        plan = apply_phone_edits(plan, edits)
        if not plan.clips:
            self._send_json({"error": "클립을 전부 지우면 만들 수 없습니다"}, 400)
            return
        self.manager.rerender(job, plan)
        self._send_json(job.as_dict())


_LOCKED_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>접속 번호가 필요합니다</title>
<style>body{background:#0d1017;color:#e6e9ef;font-family:system-ui,sans-serif;
padding:40px 24px;text-align:center;line-height:1.7}code{background:#1b2230;padding:3px 8px;
border-radius:6px}</style></head><body>
<h2>🔒 접속 번호가 필요합니다</h2>
<p>컴퓨터 화면에 표시된 주소를 <b>그대로</b> 입력해 주세요.<br>
끝에 <code>?k=숫자</code> 가 붙어 있어야 합니다.</p>
</body></html>"""


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------


def create_server(config: Config, *, host: str = "0.0.0.0", port: int = 8000,
                  root: Path | None = None, access_key: str | None = None,
                  watch_dirs: list[str] | None = None) -> tuple[ThreadingHTTPServer, JobManager, str]:
    root = Path(root) if root is not None else \
        Path(config.get("project.work_dir", "work")) / "mobile"
    manager = JobManager(root, config)
    manager.restore()
    key = "" if access_key == "" else (access_key or f"{secrets.randbelow(9000) + 1000}")

    watch = [Path(d) for d in (watch_dirs or [])]
    if on_termux():
        watch.extend(d for d in phone_media_dirs() if d not in watch)

    handler = type("BoundHandler", (Handler,), {
        "manager": manager,
        "access_key": key,
        "watch_dirs": watch,
        "device_label": "폰" if on_termux() else "컴퓨터",
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd, manager, key


def serve(config: Config, *, host: str = "0.0.0.0", port: int = 8000,
          root: Path | None = None, access_key: str | None = None,
          watch_dirs: list[str] | None = None, log=print) -> None:
    httpd, _manager, key = create_server(config, host=host, port=port, root=root,
                                         access_key=access_key, watch_dirs=watch_dirs)
    suffix = f"?k={key}" if key else ""
    local_only = host in ("127.0.0.1", "localhost")
    address = f"http://{'localhost' if local_only else local_ip()}:{port}/{suffix}"

    log("")
    log("=" * 52)
    if local_only:
        log("  📱 준비됐습니다. 크롬을 열고 아래 주소로 들어가세요")
        log("")
        log(f"      {address}")
        log("")
        log("  · 이 앱(Termux)을 끄면 편집도 멈춥니다. 켜 둔 채로 두세요")
        log("  · 홈 버튼으로 나가는 건 괜찮습니다")
        if on_termux():
            log("")
            log("  ⚠ 편집 도중 Termux 가 저절로 꺼진 적이 있다면 딱 한 번만:")
            log("     설정 → 애플리케이션 → Termux → 배터리 → '제한 없음'")
            log("     (안드로이드가 백그라운드 앱을 정리해서 생기는 일입니다)")
    else:
        log("  📱 폰에서 아래 주소로 접속하세요")
        log("")
        log(f"      {address}")
        log("")
        log("  · 폰과 이 컴퓨터가 같은 와이파이에 있어야 합니다")
        log("  · 편집이 끝날 때까지 이 창을 켜 두세요")
    log("  · 끄려면 Ctrl+C")
    log("=" * 52)
    log("")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\n서버를 종료합니다.")
    finally:
        httpd.shutdown()
        httpd.server_close()


def cleanup_old_uploads(root: Path, *, days: float = 7.0) -> int:
    """오래된 업로드 파일 정리 (용량 관리)."""
    uploads = Path(root) / "uploads"
    if not uploads.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for entry in uploads.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def disk_free_mb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / 1024 / 1024
    except OSError:
        return float("inf")


