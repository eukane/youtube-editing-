"""휴대폰에서 쓰는 웹 UI 서버.

핸드폰으로 3시간짜리 영상을 인코딩하는 건 현실적이지 않다. 그래서
  · 무거운 일(분석·렌더링)은 이 서버가 도는 컴퓨터가 하고
  · 조작·검수·다운로드는 폰 브라우저가 한다.
같은 와이파이에 있으면 앱 설치 없이 아이폰·안드로이드 모두 쓸 수 있다.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .analyze import analyze_video
from .config import Config
from .media import extract_thumbnail, format_timecode
from .models import EditPlan, analysis_from_dict, load_json, plan_from_dict, save_json
from .plan import build_plan
from .render import render
from .subtitles import with_title_card, write_ass
from .webui import PAGE

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".flv"}
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
# 작업(Job)
# --------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    source: str
    title: str
    status: str = "queued"  # queued | running | done | error
    step: str = "대기 중"
    progress: float = 0.0
    log: list[str] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    output: str = ""
    work_dir: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "step": self.step,
            "progress": round(self.progress, 4),
            "error": self.error,
            "created_at": self.created_at,
            "has_output": bool(self.output) and Path(self.output).exists(),
            "summary": self.summary,
            "log": self.log[-40:],
        }


# 폰 화면에서 고르는 '편집 강도'. 값의 근거는 editing.py 를 보면 된다.
EDIT_PACE: dict[str, dict] = {
    # 원본 흐름을 살린다. 말 사이 호흡이 필요한 토크형 영상용.
    "loose": {
        "editing.dead_air_min": 1.2,
        "editing.speed_ramp": False,
        "editing.bridge_gaps": False,
        "editing.cold_open": False,
        "memes.max_per_minute": 2.0,
    },
    # 기본. 죽은 시간은 없애되 대사는 온전히 남긴다.
    "normal": {},
    # 실황 하이라이트 편집의 표준 속도. 숨 쉴 틈을 거의 남기지 않는다.
    "fast": {
        "editing.dead_air_min": 0.32,
        "editing.dead_air_keep": 0.08,
        "editing.dead_air_min_piece": 0.5,
        "editing.ramp_speed": 3.0,
        "editing.bridge_speed": 12.0,
        "editing.bridge_max": 35.0,
        "editing.ramp_min_duration": 1.8,
        "editing.cold_open_max": 3.5,
        "memes.max_per_minute": 7.0,
        "memes.cooldown": 4.0,
        "highlight.pad_before": 0.8,
        "highlight.pad_after": 0.6,
    },
}


def resolve_style(value) -> str:
    """편집 스타일 이름도 화면에서 오니 검증한다. 빈 값이면 스타일 없음."""
    from .styles import STYLES, resolve
    if not isinstance(value, str) or not value.strip():
        return ""
    key = resolve(value)
    return key if key in STYLES else ""


def resolve_pace(value) -> str:
    """화면에서 온 값은 못 믿는다. 모르는 값이면 기본으로."""
    return value if isinstance(value, str) and value in EDIT_PACE else "normal"


class JobManager:
    """작업 큐. 한 번에 하나씩만 돌린다 (인코딩이 CPU를 다 쓰기 때문)."""

    def __init__(self, root: Path, config: Config):
        self.root = Path(root)
        self.config = config
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.worker_lock = threading.Lock()
        (self.root / "uploads").mkdir(parents=True, exist_ok=True)
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)

    def restore(self) -> int:
        """서버를 껐다 켜도 지난 작업이 목록에 남아 있게 복구한다.

        작업 상태는 메모리에만 있지만 결과물과 편집 계획은 디스크에 남아 있다.
        """
        jobs_dir = self.root / "jobs"
        if not jobs_dir.is_dir():
            return 0
        found: list[tuple[float, Job]] = []
        for entry in jobs_dir.iterdir():
            plan_path = entry / "plan.json"
            output = entry / "final.mp4"
            if not entry.is_dir() or not plan_path.exists():
                continue
            try:
                plan = plan_from_dict(load_json(plan_path))
                plan.relayout()
            except (ValueError, OSError, KeyError, TypeError):
                continue
            job = Job(id=entry.name, source=plan.source,
                      title=Path(plan.source).stem or entry.name)
            job.work_dir = str(entry)
            job.output = str(output)
            if output.exists():
                job.status, job.step, job.progress = "done", "완료", 1.0
                job.summary = self._summary(plan)
            else:
                job.status, job.step = "error", "중단됨"
                job.error = "이전에 중간에 멈춘 작업입니다"
            found.append((plan_path.stat().st_mtime, job))

        with self.lock:
            for _, job in sorted(found, key=lambda x: x[0]):
                if job.id in self.jobs:
                    continue
                self.jobs[job.id] = job
                self.order.append(job.id)
        return len(found)

    # -- 조회 --------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def listing(self) -> list[dict]:
        with self.lock:
            jobs = [self.jobs[i] for i in reversed(self.order) if i in self.jobs]
        return [j.as_dict() for j in jobs]

    # -- 생성 --------------------------------------------------------------
    def create(self, source: Path, options: dict) -> Job:
        job_id = uuid.uuid4().hex[:10]
        job = Job(id=job_id, source=str(source), title=Path(source).stem, options=options)
        job.work_dir = str(self.root / "jobs" / job_id)
        job.output = str(Path(job.work_dir) / "final.mp4")
        with self.lock:
            self.jobs[job_id] = job
            self.order.append(job_id)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def rerender(self, job: Job, plan: EditPlan) -> None:
        """폰에서 클립·자막을 고친 뒤 다시 만들기 (분석은 건너뛴다)."""
        save_json(plan, Path(job.work_dir) / "plan.json")
        job.status = "queued"
        job.step = "다시 만들기 대기 중"
        job.progress = 0.0
        job.error = ""
        threading.Thread(target=self._run, args=(job,), kwargs={"render_only": True},
                         daemon=True).start()

    # -- 실행 --------------------------------------------------------------
    def _job_config(self, job: Job) -> Config:
        config = Config(self.config.data)
        target = job.options.get("target_duration")
        if target:
            config.set("highlight.target_duration", float(target))
        if job.options.get("no_memes"):
            config.set("memes.enabled", False)
        if job.options.get("no_subtitles"):
            config.set("subtitles.enabled", False)
        if job.options.get("shorts"):
            config.set("project.resolution", "1080x1920")
        for key, value in EDIT_PACE.get(job.options.get("pace") or "", {}).items():
            config.set(key, value)
        style = job.options.get("style") or ""
        if style:
            from .styles import get as get_style
            try:
                for key, value in get_style(style).items():
                    config.set(key, value)
            except ValueError:
                pass          # 모르는 이름이면 그냥 무시 (기본 설정으로 간다)
        return config

    def _run(self, job: Job, *, render_only: bool = False) -> None:
        with self.worker_lock:  # 동시에 여러 개 인코딩하지 않는다
            try:
                job.status = "running"
                config = self._job_config(job)
                work_dir = Path(job.work_dir)
                work_dir.mkdir(parents=True, exist_ok=True)

                def log(msg: str) -> None:
                    if msg.strip():
                        job.log.append(msg.strip())

                if render_only:
                    plan = plan_from_dict(load_json(work_dir / "plan.json"))
                    for problem in plan.sanitize():
                        log(f"⚠ {problem}")
                else:
                    plan = self._analyze_and_plan(job, config, work_dir, log)

                self._render(job, config, plan, work_dir, log)

                job.summary = self._summary(plan)
                job.step = "완료"
                job.progress = 1.0
                job.status = "done"
            except Exception as exc:  # noqa: BLE001 - 서버가 죽으면 안 된다
                job.status = "error"
                job.step = "오류"
                job.error = str(exc) or exc.__class__.__name__
                job.log.append(f"[오류] {job.error}")
                job.log.extend(traceback.format_exc().splitlines()[-6:])

    def _analyze_and_plan(self, job: Job, config: Config, work_dir: Path, log) -> EditPlan:
        def on_step(label: str, fraction: float) -> None:
            job.step = label
            job.progress = 0.05 + 0.5 * fraction

        analysis_path = work_dir / "analysis.json"
        if analysis_path.exists() and job.options.get("reuse_analysis", True):
            log("기존 분석 결과 재사용")
            analysis = analysis_from_dict(load_json(analysis_path))
        else:
            analysis = analyze_video(job.source, config, work_dir, log=log, progress=on_step)
            save_json(analysis, analysis_path)

        job.step = "편집 계획 세우는 중"
        job.progress = 0.57
        plan = build_plan(analysis, config)
        save_json(plan, work_dir / "plan.json")
        save_json(analysis, analysis_path)
        self._make_thumbnails(job, plan, work_dir)
        return plan

    def _render(self, job: Job, config: Config, plan: EditPlan, work_dir: Path, log) -> None:
        sub_cfg = with_title_card(config.section("subtitles"), config.section("project"), plan)
        ass_path = None
        if sub_cfg.get("enabled", True) or plan.memes:
            ass_path = write_ass(work_dir / "subtitles.ass", plan.subtitles, plan.memes,
                                 sub_cfg, width=plan.media.width or 1920,
                                 height=plan.media.height or 1080)

        def on_step(label: str, fraction: float) -> None:
            job.step = label
            job.progress = 0.6 + 0.4 * fraction

        render(plan, config, ass_path, Path(job.output), work_dir, log=log, progress=on_step)

    def _make_thumbnails(self, job: Job, plan: EditPlan, work_dir: Path) -> None:
        thumb_dir = work_dir / "thumbs"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        job.step = "미리보기 이미지 만드는 중"
        for i, clip in enumerate(plan.clips):
            target = thumb_dir / f"{i}.jpg"
            if target.exists():
                continue
            middle = clip.source_start + min(2.0, clip.duration / 2)
            extract_thumbnail(job.source, middle, target, width=320)

    def _summary(self, plan: EditPlan) -> dict:
        return {
            "fallback": bool(plan.meta.get("fallback")),
            "clips": len(plan.clips),
            "memes": len(plan.memes),
            "subtitles": len(plan.subtitles),
            "duration": round(plan.duration, 1),
            "duration_text": format_timecode(plan.duration),
            "source_duration_text": format_timecode(plan.media.duration),
        }


# --------------------------------------------------------------------------
# 편집 계획 ↔ 폰 화면
# --------------------------------------------------------------------------


def plan_for_phone(plan: EditPlan) -> dict:
    clips = []
    for i, clip in enumerate(plan.clips):
        clips.append({
            "index": i,
            "label": clip.label,
            "start": round(clip.source_start, 2),
            "end": round(clip.source_end, 2),
            "start_text": format_timecode(clip.source_start),
            "duration": round(clip.duration, 1),
            "score": round(clip.score, 2),
            "out_start_text": format_timecode(clip.out_start),
        })
    subtitles = [{
        "index": i,
        "start_text": format_timecode(sub.start),
        "text": sub.text,
        "style": sub.style,
    } for i, sub in enumerate(plan.subtitles)]
    memes = [{
        "index": i,
        "start_text": format_timecode(cue.start),
        "label": cue.text or Path(cue.asset).stem or cue.meme_id,
        "meme_id": cue.meme_id,
    } for i, cue in enumerate(plan.memes)]
    return {"clips": clips, "subtitles": subtitles, "memes": memes,
            "duration_text": format_timecode(plan.duration)}


def _as_index(value) -> int | None:
    """폰에서 온 값은 문자열일 수도, 쓰레기일 수도 있다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def apply_phone_edits(plan: EditPlan, edits: dict) -> EditPlan:
    """폰에서 보낸 수정 사항을 편집 계획에 반영.

    잘못된 값이 섞여 와도 서버가 죽지 않도록 조용히 무시한다.
    """
    raw_removed = edits.get("removed_clips") or []
    if isinstance(raw_removed, (str, bytes)) or not hasattr(raw_removed, "__iter__"):
        raw_removed = []
    removed = {i for i in (_as_index(v) for v in raw_removed) if i is not None}
    if removed:
        plan.clips = [c for i, c in enumerate(plan.clips) if i not in removed]

    subtitle_edits = edits.get("subtitle_edits") or {}
    if not isinstance(subtitle_edits, dict):
        subtitle_edits = {}
    for raw_index, text in subtitle_edits.items():
        index = _as_index(raw_index)
        if index is None or text is None or not (0 <= index < len(plan.subtitles)):
            continue
        lines = [ln for ln in str(text).split("\n") if ln.strip()]
        plan.subtitles[index].lines = lines or [str(text)]

    if edits.get("drop_memes"):
        plan.memes = [c for c in plan.memes if c.meme_id == "clip_label"]

    plan.remap_cues()
    return plan


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
        if path == "/api/jobs":
            self._handle_create_job()
            return

        m = re.match(r"^/api/jobs/([0-9a-f]+)/replan$", path)
        if m:
            self._handle_replan(m.group(1))
            return

        self._send_json({"error": "없는 주소"}, 404)

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
                })
        return files

    def _handle_upload(self) -> None:
        raw_name = unquote(self.headers.get("X-Filename") or "video.mp4")
        name = Path(raw_name).name or "video.mp4"
        if Path(name).suffix.lower() not in VIDEO_EXTS:
            name += ".mp4"
        target = self.manager.root / "uploads" / name
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


__all__ = ["serve", "create_server", "JobManager", "Job", "plan_for_phone", "content_disposition",
           "on_termux", "phone_media_dirs",
           "apply_phone_edits", "local_ip", "cleanup_old_uploads", "disk_free_mb"]
