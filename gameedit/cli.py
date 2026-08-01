"""gameedit 명령줄 인터페이스."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__
from .analyze import analyze_video
from .config import PROFILES, Config
from .fonts import installed_korean_fonts, resolve_font
from .media import FFmpegError, find_binary, format_timecode
from .memes import (AUDIO_EXTS, BUILTIN_PACK_DIR, IMAGE_EXTS, VIDEO_EXTS, load_packs,
                    missing_assets)
from .models import Analysis, EditPlan, analysis_from_dict, load_json, save_json
from .plan import build_plan, load_plan
from .render import render
from .srt import write_srt
from .subtitles import with_title_card, write_ass
from .timeline import write_html

DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?$", re.IGNORECASE)


def log(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except BrokenPipeError:  # `| head` 처럼 파이프가 먼저 닫힌 경우
        raise SystemExit(0)


def parse_duration(text: str) -> float:
    """'8m', '1h20m', '00:08:30', '480' 을 초로 변환."""
    text = str(text).strip()
    if not text:
        raise ValueError("빈 길이 값")
    if ":" in text:
        parts = [float(p) for p in text.split(":")]
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds
    m = DURATION_RE.match(text)
    if not m or not any(m.groups()):
        raise ValueError(f"길이 형식을 이해할 수 없습니다: {text}")
    hours, minutes, seconds = m.groups()
    return (float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0))


# --------------------------------------------------------------------------
# 공통 헬퍼
# --------------------------------------------------------------------------


def load_config(args) -> Config:
    if getattr(args, "config", None):
        config = Config.load(args.config)
    else:
        config = Config.discover(Path.cwd())
    profile = getattr(args, "profile", "")
    if profile:
        config = config.with_profile(profile)
    style = getattr(args, "style", "")
    if style:
        from .styles import describe, get as get_style
        for key, value in get_style(style).items():
            config.set(key, value)
        note = describe(style)
        if note:
            log(f"편집 스타일: {note}")
    for override in getattr(args, "set", None) or []:
        if "=" not in override:
            raise SystemExit(f"--set 형식이 잘못됐습니다 (key=value): {override}")
        key, _, value = override.partition("=")
        config.set(key.strip(), _coerce(value.strip()))
    if getattr(args, "target", None):
        config.set("highlight.target_duration", parse_duration(args.target))
    if getattr(args, "subs", None):
        config.set("transcribe.external", str(args.subs))
        config.set("transcribe.backend", "external")
    if getattr(args, "no_memes", False):
        config.set("memes.enabled", False)
    if getattr(args, "no_subtitles", False):
        config.set("subtitles.enabled", False)
    return config


def _coerce(value: str):
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def resolve_work_dir(config: Config, args, source: str | None = None) -> Path:
    if getattr(args, "work", None):
        return Path(args.work)
    base = Path(config.get("project.work_dir", "work"))
    if source:
        return base / Path(source).stem
    # 소스가 없으면 work 아래 폴더가 하나뿐일 때 그것을 사용
    if base.is_dir():
        candidates = [p for p in sorted(base.iterdir()) if (p / "analysis.json").exists()
                      or (p / "plan.json").exists()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(p.name for p in candidates)
            raise SystemExit(f"작업 폴더가 여러 개입니다. --work 로 지정하세요: {names}")
    return base


def load_analysis(work_dir: Path) -> Analysis:
    path = work_dir / "analysis.json"
    if not path.exists():
        raise SystemExit(f"분석 결과가 없습니다: {path}\n먼저 `gameedit analyze <영상>` 을 실행하세요.")
    return analysis_from_dict(load_json(path))


def write_plan_outputs(plan: EditPlan, analysis: Analysis | None, config: Config,
                       work_dir: Path) -> dict[str, Path]:
    outputs: dict[str, Path] = {}
    outputs["plan"] = save_json(plan, work_dir / "plan.json")

    sub_cfg = with_title_card(config.section("subtitles"), config.section("project"), plan)
    width = plan.media.width or 1920
    height = plan.media.height or 1080
    resolution = (config.get("project.resolution") or "").lower()
    if "x" in resolution:
        try:
            w, h = resolution.split("x")
            width, height = int(w), int(h)
        except ValueError:
            pass

    if sub_cfg.get("enabled", True) or plan.memes:
        outputs["ass"] = write_ass(work_dir / "subtitles.ass", plan.subtitles, plan.memes,
                                   sub_cfg, width=width, height=height)
    if sub_cfg.get("export_srt", True) and plan.subtitles:
        outputs["srt"] = write_srt(plan.subtitles, work_dir / "subtitles.srt")
    outputs["html"] = write_html(work_dir / "plan.html", plan, analysis,
                                 title=config.get("project.name", "게임 하이라이트"))
    return outputs


# --------------------------------------------------------------------------
# 명령 구현
# --------------------------------------------------------------------------


def cmd_init(args) -> int:
    target = Path(args.directory or ".")
    target.mkdir(parents=True, exist_ok=True)
    path = target / "gameedit.yaml"
    if path.exists() and not args.force:
        log(f"이미 존재합니다: {path} (덮어쓰려면 --force)")
        return 1
    config = Config()
    header = (
        "# gameedit 설정 파일\n"
        "# 값은 전부 선택 사항입니다. 지운 항목은 기본값이 적용됩니다.\n"
        "# 자세한 설명: README.md\n\n"
    )
    path.write_text(header + config.dump_yaml(), encoding="utf-8")
    log(f"설정 파일 생성: {path}")
    log("다음 단계:  gameedit auto <영상파일>")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    ffmpeg = find_binary("ffmpeg")
    ffprobe = find_binary("ffprobe")
    log(f"ffmpeg  : {ffmpeg or '✗ 없음 (필수)'}")
    log(f"ffprobe : {ffprobe or '△ 없음 (ffmpeg 출력 파싱으로 대체)'}")
    if not ffmpeg:
        ok = False

    from .transcribe import (_module_available, find_whisper_cpp, find_whisper_model,
                             resolve_backend)

    config = load_config(args)
    tcfg = config.section("transcribe")
    backend = resolve_backend("auto", tcfg.get("external", ""), tcfg)
    hint = ""
    if backend == "none":
        hint = ("  → PC: `pip install faster-whisper` / "
                "폰(Termux): `bash ~/gameedit/install-subtitles.sh` / "
                "또는 --subs 로 자막 파일 지정")
    log(f"음성인식 : {backend}{hint}")
    log(f"  faster-whisper: {'설치됨' if _module_available('faster_whisper') else '없음'}")
    log(f"  openai-whisper: {'설치됨' if _module_available('whisper') else '없음'}")
    cpp_bin = find_whisper_cpp(tcfg.get("whisper_cpp_bin", ""))
    cpp_model = find_whisper_model(tcfg.get("whisper_cpp_model", ""), tcfg.get("model", ""))
    log(f"  whisper.cpp   : {cpp_bin or '없음'}"
        + (f" · 모델 {Path(cpp_model).name}" if cpp_model else " · 모델 없음"))

    font = config.get("subtitles.font", "Noto Sans CJK KR")
    installed = list(installed_korean_fonts())
    if installed:
        mark = "✓" if any(font.lower() in f.lower() for f in installed) else "△"
        log(f"한글 폰트 : {mark} 설치된 한글 폰트 {len(installed)}개 (설정값: {font})")
        if mark == "△":
            log(f"  · '{font}' 가 없어서 '{resolve_font(font)}' 로 대체됩니다.")
    else:
        log("한글 폰트 : △ 확인 불가 (fc-list 없음). 자막이 □□□ 로 나오면 Noto Sans KR 을 설치하세요.")

    packs = load_packs(["default"])
    log(f"기본 밈팩 : {len(packs)}개 밈 ({BUILTIN_PACK_DIR / 'default'})")
    missing = missing_assets(packs)
    if missing:
        log(f"  · 파일이 없는 에셋 {len(missing)}개 (텍스트로 대체되거나 무시됩니다)")
    return 0 if ok else 1


def cmd_analyze(args) -> int:
    config = load_config(args)
    work_dir = resolve_work_dir(config, args, args.video)
    analysis = analyze_video(args.video, config, work_dir, log=log,
                             keep_audio=args.keep_audio, skip_transcribe=args.no_transcribe)
    path = save_json(analysis, work_dir / "analysis.json")
    log(f"\n분석 저장: {path}")
    return 0


def cmd_speedtest(args) -> int:
    """이 기기에서 자막이 얼마나 걸릴지 실제로 재 본다."""
    from .speedtest import human_time, measure

    config = load_config(args)
    log("이 기기 속도를 재는 중… (30초쯤 걸립니다)")
    report = measure(args.video, config, log=log)

    log("")
    label = report.backend + (f" ({report.model})" if report.model and report.ok else "")
    log(f"자막 엔진 : {label}")
    if report.memory_available_mb:
        log(f"메모리    : 쓸 수 있는 {report.memory_available_mb:.0f}MB / "
            f"필요한 {report.memory_needed_mb:.0f}MB — {report.memory_note}")
    if not report.ok:
        log(f"\n❌ {report.error}")
        return 1

    log(f"속도      : 오디오 1분당 약 {report.seconds_per_minute:.0f}초"
        f" (모델 올리는 데 {report.load_seconds:.0f}초)")
    log("")
    if report.source_duration:
        log(f"이 영상({human_time(report.source_duration)}) 자막 : "
            f"약 {human_time(report.predict(report.source_duration))}")
    for label, seconds in (("30분", 1800.0), ("1시간", 3600.0), ("2시간", 7200.0)):
        log(f"  {label} 짜리면 : 약 {human_time(report.predict(seconds))}")
    if report.sample_text:
        log("")
        log("인식된 대사 (정확한지 직접 보세요):")
        log(f"  {report.sample_text}")
    return 0


def cmd_plan(args) -> int:
    config = load_config(args)
    work_dir = resolve_work_dir(config, args, getattr(args, "video", None))
    analysis = load_analysis(work_dir)
    plan = build_plan(analysis, config)
    outputs = write_plan_outputs(plan, analysis, config, work_dir)
    _print_plan_summary(plan, outputs)
    return 0


def _print_plan_summary(plan: EditPlan, outputs: dict[str, Path]) -> None:
    log("")
    if plan.meta.get("fallback"):
        log("⚠ 소리·대사에서 하이라이트 신호를 찾지 못해 균등 간격으로 잘랐습니다.")
        log("  (마이크 없이 녹화했거나, 영상이 짧거나, 전체가 조용한 경우입니다)")
        log("")
    log(f"하이라이트 {len(plan.clips)}개 · 밈 {len(plan.memes)}개 · 자막 {len(plan.subtitles)}줄")
    log(f"원본 {format_timecode(plan.media.duration)} → 편집본 {format_timecode(plan.duration)}")
    for i, clip in enumerate(plan.clips[:12], start=1):
        log(f"  {i:2d}. {format_timecode(clip.source_start)}–{format_timecode(clip.source_end)}"
            f"  {clip.duration:5.1f}s  {clip.label}  (점수 {clip.score:.2f})")
    if len(plan.clips) > 12:
        log(f"  … 외 {len(plan.clips) - 12}개")
    log("")
    for key, path in outputs.items():
        log(f"{key:5s} → {path}")
    if "html" in outputs:
        log(f"\n검수: 브라우저로 {outputs['html']} 열기")


def _print_credits(plan: EditPlan, out_dir: Path) -> None:
    """받아 온 그림 중 **이번 영상에 실제로 쓰인 것만** 크레딧을 남긴다.

    설명란에 붙여 넣는 걸 잊지 않게 완성본과 같은 폴더에 둔다.
    """
    from .credits import write_credits

    path = write_credits(plan, out_dir)
    if path is None:
        return
    log(f"\n⚠ 크레딧이 필요한 그림이 쓰였습니다: {path}")
    log("  이 파일 내용을 유튜브 설명란에 붙여 넣으세요.")


def cmd_preview(args) -> int:
    config = load_config(args)
    work_dir = resolve_work_dir(config, args)
    plan = load_plan(work_dir / "plan.json")
    analysis = None
    if (work_dir / "analysis.json").exists():
        analysis = load_analysis(work_dir)
    path = write_html(work_dir / "plan.html", plan, analysis,
                      title=config.get("project.name", "게임 하이라이트"))
    log(f"미리보기: {path}")
    return 0


def cmd_render(args) -> int:
    config = load_config(args)
    work_dir = resolve_work_dir(config, args)
    plan_path = work_dir / "plan.json"
    if not plan_path.exists():
        raise SystemExit(f"편집 계획이 없습니다: {plan_path}\n먼저 `gameedit plan` 을 실행하세요.")
    plan = load_plan(plan_path, log=log)
    if not plan.clips:
        raise SystemExit("쓸 수 있는 클립이 없습니다. plan.json 의 clips 를 확인하세요.")

    # plan.json 을 손으로 고쳤을 수 있으므로 자막 파일을 다시 만든다
    sub_cfg = config.section("subtitles")
    ass_path = None
    if sub_cfg.get("enabled", True) or plan.memes:
        width = plan.media.width or 1920
        height = plan.media.height or 1080
        ass_path = write_ass(work_dir / "subtitles.ass", plan.subtitles, plan.memes,
                             sub_cfg, width=width, height=height)

    output = Path(args.output or config.get("project.output", "out/final.mp4"))
    job = render(plan, config, ass_path, output, work_dir,
                 dry_run=args.dry_run, skip_cut=args.skip_cut, log=log)
    if args.dry_run:
        return 0
    log(f"\n완성: {job.output}  ({format_timecode(plan.duration)})")
    _print_credits(plan, Path(job.output).parent)
    return 0


def cmd_auto(args) -> int:
    config = load_config(args)
    work_dir = resolve_work_dir(config, args, args.video)

    analysis_path = work_dir / "analysis.json"
    if args.reuse_analysis and analysis_path.exists():
        log(f"기존 분석 재사용: {analysis_path}")
        analysis = load_analysis(work_dir)
    else:
        analysis = analyze_video(args.video, config, work_dir, log=log,
                                 keep_audio=args.keep_audio,
                                 skip_transcribe=args.no_transcribe)
        save_json(analysis, analysis_path)

    log("\n[편집 계획 수립]")
    plan = build_plan(analysis, config)
    outputs = write_plan_outputs(plan, analysis, config, work_dir)
    _print_plan_summary(plan, outputs)

    if args.plan_only:
        log("\n--plan-only: 렌더링은 건너뜁니다. 확인 후 `gameedit render` 를 실행하세요.")
        return 0

    log("\n[렌더링]")
    output = Path(args.output or config.get("project.output", "out/final.mp4"))
    job = render(plan, config, outputs.get("ass"), output, work_dir,
                 dry_run=args.dry_run, log=log)
    if args.dry_run:
        return 0
    log(f"\n완성: {job.output}  ({format_timecode(plan.duration)})")
    _print_credits(plan, Path(job.output).parent)
    return 0


def typed_while_running(line: str) -> str:
    """편집기가 돌고 있을 때 명령을 치면 그 글자는 그냥 삼켜진다.

    처음 쓰는 사람은 서버가 떠 있는 줄 모르고 update / edit 를 계속 친다.
    아무 반응이 없으면 고장 난 줄 알기 때문에 무슨 상황인지 알려 준다.
    """
    word = (line or "").strip().split(" ")[0].lower()
    if not word:
        return ""
    known = {"update": "최신 버전 받기", "edit": "편집기 켜기",
             "gogo": "편집기 켜기", "편집기": "편집기 켜기",
             "exit": "끄기", "quit": "끄기", "ls": "", "cd": ""}
    if word in known:
        what = known[word] or "그 명령"
        return (f"\n  ℹ️  지금은 편집기가 **돌고 있는 중**이라 여기에 친 글자는 실행되지 않습니다.\n"
                f"     · 편집을 하려면 → 크롬에서 localhost:8000 을 여세요\n"
                f"     · '{word}'({what})를 쓰려면 → 먼저 CTRL+C 로 편집기를 끄고 치세요\n")
    return ("\n  ℹ️  편집기가 돌고 있어서 여기에 친 글자는 실행되지 않습니다. "
            "끄려면 CTRL+C.\n")


def _watch_typing(log) -> None:
    """편집기가 도는 동안 사용자가 뭔가 치면 안내한다."""
    import sys as _sys
    import threading

    if not _sys.stdin or not _sys.stdin.isatty():
        return

    def loop():
        try:
            for line in _sys.stdin:
                message = typed_while_running(line)
                if message:
                    log(message)
        except (OSError, ValueError):
            pass

    threading.Thread(target=loop, daemon=True).start()


def cmd_serve(args) -> int:
    from .server import serve

    config = load_config(args)
    host = args.host
    access_key = "" if args.no_key else args.key
    if args.local:
        # 폰 안에서 서버와 브라우저가 같이 돌아가는 경우
        host = "127.0.0.1"
        access_key = ""
    _watch_typing(log)
    serve(config, host=host, port=args.port,
          root=Path(args.work) if args.work else None,
          access_key=access_key,
          watch_dirs=args.watch or [], log=log)
    return 0


def cmd_learn(args) -> int:
    """좋아하는 편집자의 완성본을 재서 그 리듬을 설정으로 옮긴다."""
    from .learn import describe, measure_style, merge_profiles, save_style, style_to_config

    config = load_config(args)
    acfg = config.section("analyze")

    profiles = []
    for i, src in enumerate(args.sources, start=1):
        if not Path(src).exists():
            log(f"⚠ 파일이 없습니다: {src}")
            continue
        log(f"[{i}/{len(args.sources)}] {Path(src).name}")
        profile = measure_style(src, acfg, log=lambda m: log(f"      {m}"))
        log(indent(describe(profile), "      "))
        log("")
        profiles.append(profile)

    if not profiles:
        log("❌ 잴 수 있는 영상이 없습니다.")
        return 1

    merged = merge_profiles(profiles)
    if len(profiles) > 1:
        log("=== 전체 평균 ===")
        log(indent(describe(merged), "  "))
        log("")

    settings = style_to_config(merged)
    if not settings:
        log("⚠ 옮길 만한 수치를 못 뽑았습니다. 컷이 뚜렷한 편집본으로 다시 시도해 주세요.")
        return 1

    log("이 영상의 리듬을 우리 설정으로 옮기면:")
    for key, value in settings.items():
        log(f"  {key} = {value}")
    log("")

    out = save_style(args.output, settings, merged)
    log(f"저장했습니다 → {out}")
    log("")
    log("이 스타일로 편집하려면:")
    log(f"  gameedit auto 내영상.mp4 -c {out}")
    log(f"  (폰이면)  edit -c {out}")
    log("")
    log("⚠ 잰 것은 컷 리듬과 죽은 시간뿐입니다. 자막·밈·줌은 화면에 구워져 있어")
    log("  이 방법으로는 측정할 수 없습니다.")
    return 0


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def cmd_packs(args) -> int:
    config = load_config(args)
    meme_cfg = config.section("memes")
    memes = load_packs(meme_cfg.get("packs", ["default"]), meme_cfg.get("pack_dirs", []),
                       asset_dirs=meme_cfg.get("asset_dirs", []))
    with_file = [m for m in memes if m.resolved_asset() or m.resolved_sfx()]
    log(f"밈 {len(memes)}개 (그림·소리 파일이 붙은 밈 {len(with_file)}개)")
    log("")
    for meme in memes:
        triggers = ", ".join(meme.triggers[:5]) or (", ".join(meme.events) or "-")
        has_image = meme.resolved_asset() is not None
        has_sfx = meme.resolved_sfx() is not None
        if has_image:
            mark = "🖼 "
        elif has_sfx:
            mark = "🔊"
        else:
            mark = "💬"
        label = meme.text or Path(meme.asset).stem
        log(f"  {mark} {meme.id:15s} {label:22s} ← {triggers}")
        if args.missing and not has_image and meme.asset:
            log(f"       └ 넣을 파일: {Path(meme.base_dir or '.') / meme.asset}"
                + (f" · {Path(meme.base_dir or '.') / meme.sfx}" if meme.sfx and not has_sfx else ""))
    if not args.missing:
        log("\n(💬=자막으로 나감 · 🖼=그림 있음 · 🔊=효과음 있음."
            " `gameedit packs --missing` 으로 넣을 파일 경로를 볼 수 있습니다)")
    return 0


def cmd_add_meme(args) -> int:
    """이미지·효과음 파일 하나를 밈 팩에 등록한다."""
    source = Path(args.file)
    if not source.exists():
        raise SystemExit(f"파일이 없습니다: {source}")

    pack_dir = Path(args.pack) if args.pack else BUILTIN_PACK_DIR / "default"
    pack_file = pack_dir / "pack.yaml"
    if not pack_file.exists():
        raise SystemExit(f"밈 팩을 찾을 수 없습니다: {pack_file}\n"
                         f"`--pack <폴더>` 로 지정하거나 pack.yaml 을 먼저 만드세요.")

    suffix = source.suffix.lower()
    if suffix in AUDIO_EXTS:
        kind, subdir, field = "audio", "sfx", "sfx"
    elif suffix in VIDEO_EXTS:
        kind, subdir, field = "video", "images", "asset"
    elif suffix in IMAGE_EXTS:
        kind, subdir, field = "image", "images", "asset"
    else:
        raise SystemExit(f"지원하지 않는 파일 형식입니다: {suffix}")

    target_dir = pack_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if source.resolve() != target.resolve():
        target.write_bytes(source.read_bytes())

    meme_id = args.id or source.stem
    triggers = [t for t in (args.trigger or []) if t]
    events = [e for e in (args.event or []) if e]
    if not triggers and not events:
        triggers = [source.stem]

    entry = [
        "",
        f"  - id: {meme_id}",
        f"    kind: {kind}",
        f"    {field}: {subdir}/{source.name}",
        f"    triggers: [{', '.join(_yaml_quote(t) for t in triggers)}]",
    ]
    if events:
        entry.append(f"    events: [{', '.join(events)}]")
    entry += [
        f"    placement: {args.placement}",
        f"    duration: {args.duration}",
        f"    weight: {args.weight}",
        f"    cooldown: {args.cooldown}",
    ]
    with pack_file.open("a", encoding="utf-8") as fp:
        fp.write("\n".join(entry) + "\n")

    log(f"등록 완료: {target}")
    log(f"  id={meme_id} · 트리거: {', '.join(triggers + events)}")
    log(f"  정의 추가됨 → {pack_file}")
    return 0


def _yaml_quote(text: str) -> str:
    return '"' + str(text).replace('"', '\\"') + '"'


# --------------------------------------------------------------------------
# 파서
# --------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", help="설정 파일 경로 (기본: ./gameedit.yaml)")
    parser.add_argument("-w", "--work", help="작업 폴더 경로")
    parser.add_argument("--style", default="",
                        help="편집 스타일 프리셋 (anmori/seungsangsing/kangjiwon/bate/baljep)")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="설정 개별 덮어쓰기 (예: --set highlight.target_duration=600)")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="",
                        help="phone=폰에서 돌릴 때(빠르게) / quality=화질 우선")


def _add_edit_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-t", "--target", help="최종 목표 길이 (예: 8m, 1h20m, 480)")
    parser.add_argument("--no-memes", action="store_true", help="밈 삽입 끄기")
    parser.add_argument("--no-subtitles", action="store_true", help="자막 끄기")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gameedit",
        description="게임 실황 풀영상을 하이라이트 + 밈 + 자막이 있는 유튜브용 영상으로 자동 편집합니다.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"gameedit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="설정 파일 생성")
    p.add_argument("directory", nargs="?", default=".")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="필요한 도구 설치 상태 점검")
    _add_common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("analyze", help="영상 전체 분석 (오디오·장면·대사)")
    p.add_argument("video")
    p.add_argument("--subs", help="외부 자막 파일(.srt/.vtt) 사용")
    p.add_argument("--no-transcribe", action="store_true", help="음성 인식 건너뛰기")
    p.add_argument("--keep-audio", action="store_true", help="추출한 wav 남기기")
    _add_common(p)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("speedtest", help="이 기기에서 자막에 걸릴 시간 재보기")
    p.add_argument("video")
    _add_common(p)
    p.set_defaults(func=cmd_speedtest)

    p = sub.add_parser("plan", help="분석 결과로 편집 계획 만들기")
    p.add_argument("video", nargs="?", help="작업 폴더를 찾기 위한 원본 파일명 (선택)")
    _add_common(p)
    _add_edit_options(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("preview", help="plan.json 으로 검수용 HTML 다시 만들기")
    _add_common(p)
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("render", help="편집 계획대로 최종 영상 렌더링")
    p.add_argument("-o", "--output", help="출력 파일 경로")
    p.add_argument("--dry-run", action="store_true", help="ffmpeg 명령만 출력")
    p.add_argument("--skip-cut", action="store_true", help="이미 만든 cut.mp4 재사용")
    _add_common(p)
    _add_edit_options(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("auto", help="분석 → 계획 → 렌더링 한 번에")
    p.add_argument("video")
    p.add_argument("-o", "--output", help="출력 파일 경로")
    p.add_argument("--subs", help="외부 자막 파일(.srt/.vtt) 사용")
    p.add_argument("--no-transcribe", action="store_true")
    p.add_argument("--keep-audio", action="store_true")
    p.add_argument("--reuse-analysis", action="store_true", help="기존 analysis.json 재사용")
    p.add_argument("--plan-only", action="store_true", help="렌더링 없이 계획까지만")
    p.add_argument("--dry-run", action="store_true")
    _add_common(p)
    _add_edit_options(p)
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("serve", help="폰에서 쓰는 웹 UI 서버 실행")
    p.add_argument("-p", "--port", type=int, default=8000, help="포트 (기본 8000)")
    p.add_argument("--host", default="0.0.0.0", help="바인딩 주소 (기본 0.0.0.0)")
    p.add_argument("--key", help="접속 번호 (기본: 실행할 때마다 4자리 자동 생성)")
    p.add_argument("--no-key", action="store_true", help="접속 번호 없이 열기 (집 네트워크 전용)")
    p.add_argument("--watch", action="append", metavar="폴더",
                   help="폰에서 고를 수 있게 보여줄 영상 폴더 (여러 번 지정 가능)")
    p.add_argument("--local", action="store_true",
                   help="폰 안에서 단독 실행 (127.0.0.1 로만 열고 접속 번호 없음)")
    _add_common(p)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("learn", help="편집된 영상의 컷 리듬을 재서 설정으로 옮기기")
    p.add_argument("sources", nargs="+",
                   help="이미 편집이 끝난 참고 영상 (여러 개 넣으면 평균을 냅니다)")
    p.add_argument("-o", "--output", default="style.yaml", help="저장할 설정 파일")
    _add_common(p)
    p.set_defaults(func=cmd_learn)

    p = sub.add_parser("packs", help="사용 중인 밈 목록 보기")
    p.add_argument("--missing", action="store_true",
                   help="그림·소리 파일이 비어 있는 밈과 넣을 경로를 보여줍니다")
    _add_common(p)
    p.set_defaults(func=cmd_packs)

    p = sub.add_parser("add-meme", help="이미지·움짤·효과음 파일을 밈으로 등록")
    p.add_argument("file", help="등록할 파일 (png/jpg/gif/mp4/mp3/wav …)")
    p.add_argument("-t", "--trigger", action="append",
                   help="이 말이 나오면 발동 (여러 번 지정 가능, 생략 시 파일명)")
    p.add_argument("-e", "--event", action="append", choices=["hype", "silence", "timeskip"],
                   help="대사 대신 상황으로 발동")
    p.add_argument("--id", help="밈 id (기본: 파일명)")
    p.add_argument("--pack", help="등록할 팩 폴더 (기본: 내장 default 팩)")
    p.add_argument("--placement", default="top",
                   choices=["top", "center", "bottom", "left", "right", "fullscreen"])
    p.add_argument("--duration", type=float, default=2.0, help="노출 시간(초)")
    p.add_argument("--weight", type=float, default=1.5, help="우선순위")
    p.add_argument("--cooldown", type=float, default=20.0, help="재등장 최소 간격(초)")
    _add_common(p)
    p.set_defaults(func=cmd_add_meme)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FFmpegError as exc:
        log(f"\n[ffmpeg 오류] {exc}")
        return 2
    except (FileNotFoundError, ValueError) as exc:
        log(f"\n[오류] {exc}")
        return 2
    except KeyboardInterrupt:
        log("\n중단했습니다.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
