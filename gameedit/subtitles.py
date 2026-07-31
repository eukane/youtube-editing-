"""자막 큐 생성 + ASS 자막 파일 작성.

이 프로그램은 화면에 올라가는 '글자'를 전부 하나의 .ass 파일로 만든다.
  - 주인공 대사 자막 (Main / Emph)
  - 텍스트 밈 (MemeTop / MemeCenter)
  - 클립 시작 라벨 (Label)
ffmpeg 의 drawtext 대신 libass 를 쓰기 때문에 한글 줄바꿈·외곽선·팝 애니메이션이 안정적이다.
"""

from __future__ import annotations

from pathlib import Path

from .fonts import resolve_font
from .models import Analysis, EditPlan, MemeCue, SubtitleCue, Segment

_ALIGN = {
    "bottom": 2,
    "center": 5,
    "top": 8,
    "left": 4,
    "right": 6,
    "fullscreen": 5,
}


# --------------------------------------------------------------------------
# 텍스트 정리
# --------------------------------------------------------------------------


def wrap_text(text: str, max_chars: int, max_lines: int = 2) -> list[str]:
    """한글 자막용 줄바꿈. 공백 우선, 없으면 글자 수로 강제 분할."""
    text = " ".join((text or "").split())
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    lines: list[str] = []
    current = ""
    for token in text.split(" "):
        while len(token) > max_chars:  # 공백 없는 긴 덩어리
            if current:
                lines.append(current)
                current = ""
            lines.append(token[:max_chars])
            token = token[max_chars:]
        if not current:
            current = token
        elif len(current) + 1 + len(token) <= max_chars:
            current = f"{current} {token}"
        else:
            lines.append(current)
            current = token
    if current:
        lines.append(current)

    if max_lines > 0 and len(lines) > max_lines:
        # 넘치는 줄은 마지막 줄에 이어붙여 정보 손실을 막는다
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1:])
        lines = head + [tail]
    return lines


def split_by_words(text: str, budget: int) -> list[str]:
    """긴 문장을 budget 글자 이하 덩어리로 나눈다. 단어를 쪼개지 않는다."""
    text = " ".join((text or "").split())
    if not text:
        return []
    if len(text) <= budget:
        return [text]

    chunks: list[str] = []
    current = ""
    for token in text.split(" "):
        while len(token) > budget:  # 한 단어가 통째로 넘칠 때만 강제로 자른다
            if current:
                chunks.append(current)
                current = ""
            chunks.append(token[:budget])
            token = token[budget:]
        if not current:
            current = token
        elif len(current) + 1 + len(token) <= budget:
            current = f"{current} {token}"
        else:
            chunks.append(current)
            current = token
    if current:
        chunks.append(current)
    return chunks


def _split_without_timestamps(seg: Segment, budget: int) -> list[tuple[float, float, str]]:
    """단어 타임스탬프가 없는 전사(whisper.cpp, 외부 자막 등) 처리.

    글자 수로만 자르면 'ask' 가 'a' / 'sk' 로 쪼개진다. 단어 경계를 지키고,
    시간은 덩어리 길이에 비례해 나눠 준다.
    """
    chunks = split_by_words(seg.text, budget)
    if not chunks:
        return []
    if len(chunks) == 1:
        return [(seg.start, seg.end, chunks[0])]

    span = max(0.4, seg.end - seg.start)
    total = sum(len(c) for c in chunks)
    out: list[tuple[float, float, str]] = []
    elapsed = 0.0
    for chunk in chunks:
        share = span * len(chunk) / total
        out.append((seg.start + elapsed, seg.start + elapsed + share, chunk))
        elapsed += share
    return out


def _split_segment(seg: Segment, cfg: dict) -> list[tuple[float, float, str]]:
    """세그먼트를 자막 한 장 분량으로 쪼갠다."""
    max_chars = int(cfg.get("max_chars_per_line", 18))
    max_lines = int(cfg.get("max_lines", 2))
    budget = max_chars * max(1, max_lines)
    max_duration = float(cfg.get("max_duration", 4.0))
    gap_split = float(cfg.get("gap_split", 0.55))

    words = [w for w in seg.words if w.text.strip()]
    if not words:
        return _split_without_timestamps(seg, budget)

    chunks: list[tuple[float, float, str]] = []
    buf: list = []
    for word in words:
        if buf:
            gap = word.start - buf[-1].end
            projected = sum(len(w.text) + 1 for w in buf) + len(word.text)
            too_long = projected > budget
            too_slow = (word.end - buf[0].start) > max_duration
            if gap > gap_split or too_long or too_slow:
                chunks.append((buf[0].start, buf[-1].end,
                               " ".join(w.text for w in buf)))
                buf = []
        buf.append(word)
    if buf:
        chunks.append((buf[0].start, buf[-1].end, " ".join(w.text for w in buf)))
    return chunks


# --------------------------------------------------------------------------
# 큐 생성
# --------------------------------------------------------------------------


def build_subtitle_cues(plan: EditPlan, analysis: Analysis, cfg: dict) -> list[SubtitleCue]:
    if not cfg.get("enabled", True):
        return []
    max_chars = int(cfg.get("max_chars_per_line", 18))
    max_lines = int(cfg.get("max_lines", 2))
    min_duration = float(cfg.get("min_duration", 0.9))
    emphasis = bool(cfg.get("emphasis", True))
    threshold = float(cfg.get("emphasis_threshold", 0.72))
    impact = bool(cfg.get("impact", True))
    impact_threshold = float(cfg.get("impact_threshold", 0.88))
    impact_max_chars = int(cfg.get("impact_max_chars", 14))

    cues: list[SubtitleCue] = []
    for seg in analysis.transcript.segments:
        for start, end, text in _split_segment(seg, cfg):
            lines = wrap_text(text, max_chars, max_lines)
            if not lines:
                continue
            style = "Main"
            level = analysis.audio.mean_between(start, end)
            if emphasis and level >= threshold:
                style = "Emph"
            # 제일 센 대사는 화면을 채우는 초대형 자막으로 (실제 편집본의 그 자막)
            if impact and level >= impact_threshold and len(text) <= impact_max_chars:
                style = "Impact"
            # 괄호로 시작하면 대사가 아니라 상황 설명이다
            if text.strip().startswith(("(", "（")):
                style = "Narr"
            # 점프컷으로 쪼개졌거나 콜드오픈으로 두 번 나오는 구간은 그만큼 나온다
            for out_start, out_end in plan.map_all_ranges(start, end):
                cues.append(SubtitleCue(start=round(out_start, 3), end=round(out_end, 3),
                                        lines=lines, style=style, speaker=seg.speaker,
                                        source_start=round(start, 3)))

    cues.sort(key=lambda c: c.start)
    # 너무 짧은 자막은 다음 자막 직전까지 늘려 가독성 확보
    for i, cue in enumerate(cues):
        if cue.duration >= min_duration:
            continue
        limit = cues[i + 1].start - 0.05 if i + 1 < len(cues) else cue.start + min_duration
        cue.end = round(min(max(cue.end, cue.start + min_duration), max(limit, cue.start + 0.2)), 3)
    return cues


# --------------------------------------------------------------------------
# ASS 출력
# --------------------------------------------------------------------------


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def escape_ass(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _style_line(name: str, *, font: str, size: int, primary: str, outline_color: str,
                bold: int, outline: float, shadow: float, align: int, margin_v: int,
                margin_l: int = 60, margin_r: int = 60) -> str:
    return (
        f"Style: {name},{font},{int(size)},{primary},&H000000FF,{outline_color},&H80000000,"
        f"{bold},0,0,0,100,100,0,0,1,{outline},{shadow},{align},{margin_l},{margin_r},{margin_v},1"
    )


def reference_resolution(width: int, height: int, *, base_height: int = 1080) -> tuple[int, int]:
    """자막 좌표계(PlayRes).

    실제 출력 해상도를 그대로 쓰면 720p·360p 영상에서 글자가 터무니없이 커진다.
    가로세로 비율만 유지한 채 높이를 1080 으로 고정해 두면
    어떤 해상도로 뽑아도 글자 크기가 화면 대비 같은 비율로 나온다.
    """
    if width <= 0 or height <= 0:
        return 1920, base_height
    ref_w = max(2, int(round(base_height * width / height)))
    return ref_w - (ref_w % 2), base_height


def _as_word_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [w.strip() for w in value.split(",") if w.strip()]
    return [str(w).strip() for w in value if str(w).strip()]


def colorize_words(text: str, words: list[str], color: str) -> str:
    """문장 **안의 특정 단어만** 다른 색으로.

    실제 편집본을 보면 한 줄 전체를 노랗게 하는 게 아니라 고유명사나 핵심
    단어 하나만 색을 바꾼다. 문장은 흰색으로 두고 그 단어만 튄다.

    (이 함수는 이미 escape_ass 를 거친 문자열을 받는다. `\\c` 는 인자 없이
     쓰면 스타일 기본색으로 되돌아간다.)
    """
    if not text or not words:
        return text
    # 긴 단어부터 칠해야 "메타몽"이 "메타"에 먹히지 않는다
    for word in sorted({w for w in words if w}, key=len, reverse=True):
        if word not in text:
            continue
        if "\\c" in text and f"{{\\c{color}&}}{word}" in text:
            continue
        text = text.replace(word, f"{{\\c{color}&}}{word}{{\\c}}", 1)
    return text


def _card_style_line(name: str, *, font: str, size: int, primary: str) -> str:
    """BorderStyle 3 = 글자 뒤에 불투명 박스. 전환 카드용."""
    # ASS 알파는 00 이 불투명. 40 이면 살짝 비치는 검은 판.
    return (
        f"Style: {name},{font},{int(size)},{primary},&H000000FF,&H40000000,&H40000000,"
        f"-1,0,0,0,100,100,0,0,3,34,0,5,80,80,0,1"
    )


def build_ass(cues: list[SubtitleCue], meme_cues: list[MemeCue], cfg: dict,
              *, width: int = 1920, height: int = 1080) -> str:
    width, height = reference_resolution(width, height)
    font = cfg.get("font", "Noto Sans CJK KR")
    if cfg.get("font_fallback", True):
        font = resolve_font(font)
    size = int(cfg.get("font_size", 62))
    bold = -1 if cfg.get("bold", True) else 0
    primary = cfg.get("primary_color", "&H00FFFFFF")
    outline_color = cfg.get("outline_color", "&H00101010")
    outline = float(cfg.get("outline", 4.0))
    shadow = float(cfg.get("shadow", 2.0))
    margin_v = int(cfg.get("margin_v", 70))
    emphasis_color = cfg.get("emphasis_color", "&H0033E8FF")
    align = _ALIGN.get(cfg.get("position", "bottom"), 2)
    pop = bool(cfg.get("pop_animation", True))
    impact_color = cfg.get("impact_color", "&H002020F0")   # ASS 는 BGR — 빨강
    impact_scale = float(cfg.get("impact_scale", 2.7))
    impact_margin_v = int(cfg.get("impact_margin_v", 0) or 0)
    word_color = cfg.get("word_color", emphasis_color)
    word_list = _as_word_list(cfg.get("highlight_words", []))

    header = [
        "[Script Info]",
        "; gameedit 자동 생성",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        _style_line("Main", font=font, size=size, primary=primary, outline_color=outline_color,
                    bold=bold, outline=outline, shadow=shadow, align=align, margin_v=margin_v),
        _style_line("Emph", font=font, size=int(size * 1.15), primary=emphasis_color,
                    outline_color=outline_color, bold=-1, outline=outline + 1, shadow=shadow,
                    align=align, margin_v=margin_v),
        # 클립 라벨(좌상단)과 겹치지 않도록 밈 텍스트는 조금 아래에서 시작한다
        _style_line("MemeTop", font=font, size=int(size * 1.25), primary="&H0000F0FF",
                    outline_color="&H00101010", bold=-1, outline=outline + 1, shadow=shadow,
                    align=8, margin_v=110),
        _style_line("MemeCenter", font=font, size=int(size * 1.9), primary="&H003C3CFF",
                    outline_color="&H00FFFFFF", bold=-1, outline=outline + 2, shadow=shadow + 1,
                    align=5, margin_v=0),
        # 전환 카드("3분 후"): 화면 가운데에 반투명 검은 판 위 흰 글씨
        _card_style_line("Card", font=font, size=int(size * 1.5), primary="&H00FFFFFF"),
        _style_line("Label", font=font, size=int(size * 0.72), primary="&H00FFFFFF",
                    outline_color="&H00202020", bold=-1, outline=max(1.0, outline - 1), shadow=1,
                    align=7, margin_v=40, margin_l=54),
        # 제일 센 대사용. 실제 편집본에서 화면 폭을 거의 채우는 빨간 글씨 +
        # 두꺼운 검은 외곽선으로 나가는 그 자막.
        _style_line("Impact", font=font, size=int(size * impact_scale),
                    primary=impact_color, outline_color="&H00000000", bold=-1,
                    outline=outline * 2.2, shadow=shadow + 1,
                    align=2, margin_v=(impact_margin_v or max(20, int(margin_v * 0.5))),
                    margin_l=24, margin_r=24),
        # 상황 설명·해설용 (괄호로 시작하는 줄). 대사 자막과 겹치지 않게 위쪽.
        _style_line("Narr", font=font, size=int(size * 0.78), primary="&H0000E8FF",
                    outline_color=outline_color, bold=-1, outline=max(1.5, outline - 1.5),
                    shadow=1, align=8, margin_v=int(height * 0.30)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[tuple[float, str]] = []

    fade_main = "{\\fad(80,80)}" if pop else ""
    fade_emph = ("{\\fad(60,60)\\t(0,120,\\fscx115\\fscy115)\\t(120,260,\\fscx100\\fscy100)}"
                 if pop else "")
    fade_impact = ("{\\fad(50,120)\\t(0,110,\\fscx125\\fscy125)\\t(110,240,\\fscx100\\fscy100)}"
                   if pop else "{\\fad(50,120)}")
    for cue in cues:
        if cue.end <= cue.start:
            continue
        if cue.style == "Impact":
            tag = fade_impact
        elif cue.style == "Emph":
            tag = fade_emph
        else:
            tag = fade_main
        text = escape_ass("\n".join(cue.lines))
        # 초대형 자막은 그 자체로 눈에 띄니 단어 색을 또 바꾸지 않는다
        if cue.style != "Impact":
            text = colorize_words(text, word_list, word_color)
        events.append((cue.start,
                       f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},{cue.style},"
                       f"{cue.speaker},0,0,0,,{tag}{text}"))

    pop_meme = ("{\\fad(60,180)\\t(0,140,\\fscx120\\fscy120)\\t(140,280,\\fscx100\\fscy100)}"
                if pop else "{\\fad(60,180)}")
    for cue in meme_cues:
        if not cue.text or not getattr(cue, "show_text", cue.kind == "text"):
            continue
        style = (cue.style if cue.style in ("MemeTop", "MemeCenter", "Card", "Label",
                                            "Main", "Emph", "Impact", "Narr")
                 else "MemeTop")
        if style == "Label":
            tag = "{\\fad(120,200)}"
        elif style == "Card":
            tag = "{\\fad(180,250)}"
        else:
            tag = pop_meme
        text = escape_ass(cue.text)
        events.append((cue.start,
                       f"Dialogue: 1,{_ass_time(cue.start)},{_ass_time(cue.end)},{style},"
                       f"meme,0,0,0,,{tag}{text}"))

    events.sort(key=lambda e: e[0])
    return "\n".join(header + [line for _, line in events]) + "\n"


def write_ass(path: str | Path, cues: list[SubtitleCue], meme_cues: list[MemeCue], cfg: dict,
              *, width: int = 1920, height: int = 1080) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(cues, meme_cues, cfg, width=width, height=height), encoding="utf-8")
    return path
