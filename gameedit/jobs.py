"""편집 작업 큐 — 한 번에 하나씩 돌린다.

인코딩이 CPU 를 다 쓰기 때문에 동시에 여러 개를 돌리면 둘 다 느려지고
폰에서는 그대로 죽는다.

작업 하나가 거치는 길:
    분석(소리·대사) → 편집 계획 → 렌더 → 완성본

**중간에 앱이 죽어도 이어할 수 있어야 한다.** 그래서 화면에서 고른 값과
편집 계획을 작업 폴더에 남긴다. 서버를 다시 켜면 그걸 읽어 복구한다.
자세한 근거는 save_job_options 와 render.reusable_segments 참고.

HTTP 는 여기 없다 (server.py). 이 파일은 "무엇을 어떤 순서로 하는가" 만 안다.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analyze import analyze_video
from .config import Config
from .media import extract_thumbnail, format_timecode
from .models import EditPlan, analysis_from_dict, load_json, plan_from_dict, save_json
from .plan import build_plan
from .render import render, resolve_output_size
from .subtitles import content_box_height, with_title_card, write_ass

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
            "wish_matched": self.options.get("wish_matched", []),
            "wish_ignored": self.options.get("wish_ignored", []),
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


_DURATION_CACHE: dict[tuple[str, int, int], float] = {}


def probe_duration(path: Path) -> float:
    """영상 길이(초). 못 읽으면 0.

    목록을 열 때마다 파일마다 ffprobe 를 돌리므로 결과를 기억해 둔다.
    파일이 바뀌면(크기·수정시각) 다시 잰다.
    """
    try:
        stat = path.stat()
    except OSError:
        return 0.0
    key = (str(path), stat.st_size, int(stat.st_mtime))
    if key in _DURATION_CACHE:
        return _DURATION_CACHE[key]
    try:
        from .media import probe
        duration = round(float(probe(path).duration or 0.0), 2)
    except Exception:          # 깨진 파일이 목록 전체를 막으면 안 된다
        duration = 0.0
    if len(_DURATION_CACHE) > 500:
        _DURATION_CACHE.clear()
    _DURATION_CACHE[key] = duration
    return duration


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


# 완성본 길이로 받아들일 범위. 밖의 값은 사용자가 잘못 보낸 것이다.
MIN_TARGET = 5.0
MAX_TARGET = 6 * 3600.0


def resolve_target(value, default: float = 0.0) -> float:
    """화면에서 온 완성본 길이. 못 쓸 값이면 default.

    여기는 네트워크에서 온 JSON 이 그대로 들어오는 자리다. 예전에는
    `float(value)` 를 바로 불러서 `"3분"` 같은 게 오면 **편집 작업 스레드가
    통째로 죽었다.** 사용자에게는 이유 없는 실패로만 보인다.
    `nan` 이나 1e30 은 예외는 안 나지만 이후 계산을 전부 망친다.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):   # nan / inf
        return default
    if not (MIN_TARGET <= number <= MAX_TARGET):
        return default
    return number


OPTIONS_FILE = "options.json"


def save_job_options(job: Job) -> None:
    """화면에서 고른 값을 작업 폴더에 남긴다.

    Termux 가 죽으면 메모리에 있던 이 값이 전부 사라진다. 그 상태로 이어서
    만들면 길이·쇼츠 여부·요구사항이 기본값으로 돌아가고, **만들어 둔 조각과
    설정이 안 맞아서 전부 다시 만들게 된다.** 이어하기가 무의미해진다.
    """
    try:
        folder = Path(job.work_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / OPTIONS_FILE).write_text(
            json.dumps(job.options, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass                     # 남기지 못해도 편집 자체는 계속돼야 한다


def load_job_options(work_dir: Path) -> dict:
    try:
        data = json.loads((work_dir / OPTIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class JobManager:
    """작업 큐. 한 번에 하나씩만 돌린다 (인코딩이 CPU를 다 쓰기 때문)."""

    def __init__(self, root: Path, config: Config):
        self.root = Path(root)
        self.config = config
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.worker_lock = threading.Lock()
        self.speed: dict = {"running": False, "done": False, "source": "", "report": None}
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
                      title=Path(plan.source).stem or entry.name,
                      options=load_job_options(entry))
            job.work_dir = str(entry)
            job.output = str(output)
            if output.exists():
                job.status, job.step, job.progress = "done", "완료", 1.0
                job.summary = self._summary(plan)
            else:
                job.status, job.step = "error", "중단됨"
                job.error = ("중간에 멈췄습니다 — '이어서 만들기' 를 누르면 "
                             "만들어 둔 조각부터 이어서 합니다")
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
        save_job_options(job)
        with self.lock:
            self.jobs[job_id] = job
            self.order.append(job_id)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    # -- 속도 재보기 -------------------------------------------------------
    # 30초쯤 걸려서 요청 하나를 붙잡고 있으면 브라우저가 먼저 끊는다.
    # 따로 돌려 놓고 화면이 물어보게 한다.
    def start_speedtest(self, source: Path) -> dict:
        with self.lock:
            if self.speed.get("running"):
                return dict(self.speed, report=self._speed_report_dict())
            self.speed = {"running": True, "done": False, "source": str(source),
                          "report": None}
        threading.Thread(target=self._run_speedtest, args=(Path(source),),
                         daemon=True).start()
        return self.speed_status()

    def speed_status(self) -> dict:
        with self.lock:
            return {"running": bool(self.speed.get("running")),
                    "done": bool(self.speed.get("done")),
                    "source": self.speed.get("source", ""),
                    "name": Path(self.speed.get("source", "")).name,
                    "report": self._speed_report_dict()}

    def _speed_report_dict(self) -> dict | None:
        report = self.speed.get("report")
        return report.as_dict() if report is not None else None

    def _run_speedtest(self, source: Path) -> None:
        from .speedtest import SpeedReport, measure

        # 편집이 돌고 있으면 그게 CPU를 다 쓰고 있어서 속도가 엉뚱하게 나온다.
        with self.worker_lock:
            try:
                report = measure(source, self.config)
            except Exception as err:                    # 측정 실패로 서버가 죽으면 안 된다
                report = SpeedReport(error=f"속도를 재지 못했습니다: {err}")
        with self.lock:
            self.speed.update(running=False, done=True, report=report)

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
        target = resolve_target(job.options.get("target_duration"))
        if target:
            config.set("highlight.target_duration", target)
        if job.options.get("no_memes"):
            config.set("memes.enabled", False)
        if job.options.get("no_subtitles"):
            config.set("subtitles.enabled", False)
            # 화면에 안 쓸 자막을 만드느라 몇 시간을 쓰면 안 된다.
            # 밖에서 만든 자막이 있으면 그건 그대로 쓴다 (거의 공짜다).
            if not job.options.get("subs"):
                config.set("transcribe.backend", "none")
        subs = job.options.get("subs") or ""
        if subs and Path(subs).exists():
            # 폰에서 음성 인식을 돌리는 게 가장 무거운 단계다. 밖에서 만들어 온
            # 자막이 있으면 그 단계를 통째로 건너뛴다.
            config.set("transcribe.external", subs)
            config.set("transcribe.backend", "external")
        if job.options.get("shorts"):
            config.set("project.resolution", "1080x1920")
            # 세로로 만들면 위아래가 빈다. 실제 쇼츠들처럼 그 자리를 채운다.
            for key, limit in (("shorts_title", 40), ("channel", 24)):
                text = str(job.options.get(key) or "").strip()[:limit]
                if text:
                    config.set(f"project.{key}", text)
        for key, value in EDIT_PACE.get(job.options.get("pace") or "", {}).items():
            config.set(key, value)
        # 손으로 적은 요구사항은 프리셋보다 뒤에 적용해서 그쪽이 이기게 한다
        wishes = job.options.get("wishes") or ""
        if wishes:
            from .wishes import apply as apply_wishes
            got = apply_wishes(config, wishes)
            job.options["wish_matched"] = got.matched
            job.options["wish_ignored"] = got.ignored
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
            # 원본 크기가 아니라 실제로 뽑히는 크기를 써야 한다. 세로(쇼츠)로
            # 뽑을 때 자막 좌표계 비율이 화면과 달라져 글자가 늘어났다.
            width, height, _fps = resolve_output_size(plan, config.section("project"))
            ass_path = write_ass(work_dir / "subtitles.ass", plan.subtitles, plan.memes,
                                 sub_cfg, width=width, height=height,
                                 content_height=content_box_height(
                                     plan.media.width, plan.media.height, width, height),
                                 total_duration=plan.duration)

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


