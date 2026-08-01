"""자막 큐 생성 + ASS 자막 파일 작성.

이 프로그램은 화면에 올라가는 '글자'를 전부 하나의 .ass 파일로 만든다.
  - 주인공 대사 자막 (Main / Emph)
  - 텍스트 밈 (MemeTop / MemeCenter)
  - 클립 시작 라벨 (Label)
ffmpeg 의 drawtext 대신 libass 를 쓰기 때문에 한글 줄바꿈·외곽선·팝 애니메이션이 안정적이다.
"""

from __future__ import annotations

from pathlib import Path

from .animation import entrance, resolve_level
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


def resolve_highlight_words(cfg: dict, transcript) -> list[str]:
    """색을 바꿀 단어 목록. 직접 적은 게 있으면 그것, 없으면 자동으로 찾는다.

    사람이 영상마다 "전구 폼 로토무" 같은 걸 손으로 적게 하면 아무도 안 쓴다.
    """
    manual = _as_word_list(cfg.get("highlight_words", []))
    if manual:
        return manual
    if not cfg.get("auto_highlight", True):
        return []
    return auto_highlight_words(
        transcript,
        min_count=int(cfg.get("auto_highlight_min_count", 3)),
        max_words=int(cfg.get("auto_highlight_max", 8)),
    )


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
    word_list = resolve_highlight_words(cfg, analysis.transcript)

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
            # 줄 전체를 노랗게 칠해 버리면 "바로 |전구 폼 로토무!" 처럼 한 구절만
            # 색을 바꾸는 연출이 묻힌다. 강조 단어가 있으면 바탕은 흰색으로 둔다.
            if style == "Emph" and any(w in text for w in word_list):
                style = "Main"
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


# 자동 강조에서 걸러낼 말. 조사·감탄사·흔한 말은 색을 바꿔 봐야 의미가 없다.
_STOPWORDS = {
    "그래", "그냥", "진짜", "이거", "저거", "그거", "여기", "저기", "거기",
    "아니", "근데", "그니까", "그러니까", "이제", "지금", "우리", "너무", "정말",
    "하는", "하고", "해서", "했는데", "있는", "있어", "없는", "없어", "같은", "같아",
    "이렇게", "저렇게", "그렇게", "어떻게", "무슨", "무슨가", "뭔가", "약간",
    "ㅋㅋ", "ㅋㅋㅋ", "ㅎㅎ", "습니다", "합니다", "입니다", "겁니다",
}
# 한국어는 명사 뒤에 조사가 붙어서 같은 말이 다른 토큰이 된다. 꼬리를 떼고 센다.
_PARTICLES = ("으로써", "이라는", "이라고", "라는", "라고", "에서", "으로", "한테",
              "까지", "부터", "이랑", "하고", "처럼", "보다", "마다", "밖에",
              "이야", "이네", "인데", "구나", "네요", "이다",
              "은", "는", "이", "가", "을", "를", "에", "로", "도", "만",
              "과", "와", "의", "야", "아", "랑", "네", "요", "죠", "지")


def _strip_particle(token: str) -> str:
    for tail in _PARTICLES:
        if len(token) > len(tail) + 1 and token.endswith(tail):
            return token[: -len(tail)]
    return token


def auto_highlight_words(transcript, *, min_count: int = 3, max_words: int = 8) -> list[str]:
    """영상의 대사에서 '그 영상의 핵심어'를 자동으로 뽑는다.

    편집자가 색을 바꾸는 단어는 대개 그 영상에서 반복해서 나오는 고유명사다
    (게임 이름·캐릭터 이름·그날의 주제). 사람이 영상마다 손으로 적게 하는
    대신, 자주 나오면서 흔한 말이 아닌 것을 골라 준다.
    """
    import re
    from collections import Counter

    counts: Counter = Counter()
    for seg in getattr(transcript, "segments", []) or []:
        for raw in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]+", seg.text or ""):
            token = _strip_particle(raw)
            if len(token) < 2 or token in _STOPWORDS:
                continue
            if all(ch in "ㅋㅎㅠㅜ" for ch in token):
                continue
            counts[token] += 1

    picked = [word for word, count in counts.most_common() if count >= min_count]
    # 짧은 말이 긴 말의 일부이면 긴 쪽만 둔다 ("로토무" vs "전구로토무")
    out: list[str] = []
    for word in sorted(picked, key=len, reverse=True):
        if any(word in kept for kept in out):
            continue
        out.append(word)
        if len(out) >= max_words:
            break
    return out


def colorize_words(text: str, words: list[str], color: str, *, limit: int = 0) -> str:
    """문장 **안의 특정 단어만** 다른 색으로.

    실제 편집본을 보면 한 줄 전체를 노랗게 하는 게 아니라 고유명사나 핵심
    단어 하나만 색을 바꾼다. 문장은 흰색으로 두고 그 단어만 튄다.

    (이 함수는 이미 escape_ass 를 거친 문자열을 받는다. `\\c` 는 인자 없이
     쓰면 스타일 기본색으로 되돌아간다.)
    """
    if not text or not words:
        return text
    done = 0
    # 긴 단어부터 칠해야 "메타몽"이 "메타"에 먹히지 않는다
    for word in sorted({w for w in words if w}, key=len, reverse=True):
        if limit and done >= limit:
            break
        if word not in text:
            continue
        if "\\c" in text and f"{{\\c{color}&}}{word}" in text:
            continue
        text = text.replace(word, f"{{\\c{color}&}}{word}{{\\c}}", 1)
        done += 1
    return text


def _card_style_line(name: str, *, font: str, size: int, primary: str) -> str:
    """BorderStyle 3 = 글자 뒤에 불투명 박스. 전환 카드용."""
    # ASS 알파는 00 이 불투명. 40 이면 살짝 비치는 검은 판.
    return (
        f"Style: {name},{font},{int(size)},{primary},&H000000FF,&H40000000,&H40000000,"
        f"-1,0,0,0,100,100,0,0,3,34,0,5,80,80,0,1"
    )


def with_title_card(sub_cfg: dict, project_cfg: dict, plan=None) -> dict:
    """ASS 를 쓰기 직전에 여러 섹션에 흩어진 값을 한 곳으로 모은다.

    타이틀 카드 설정은 project 섹션에 있고, 자동으로 고른 강조 단어는
    편집 계획(plan.meta)에 들어 있다. 둘 다 자막이 그려야 한다.
    """
    merged = dict(sub_cfg,
                  title=project_cfg.get("title", ""),
                  title_date=project_cfg.get("title_date", ""),
                  title_seconds=project_cfg.get("title_seconds", 2.5),
                  shorts_title=project_cfg.get("shorts_title", ""),
                  channel=project_cfg.get("channel", ""))
    words = (getattr(plan, "meta", None) or {}).get("highlight_words") if plan else None
    if words and not _as_word_list(sub_cfg.get("highlight_words", [])):
        merged["highlight_words"] = list(words)
    return merged


def content_box_height(src_w: int, src_h: int, out_w: int, out_h: int) -> float:
    """원본을 출력 화면에 맞춰 넣었을 때 **영상이 실제로 차지하는 높이**.

    나머지 위아래가 빈 띠다. 쇼츠에서 제목·채널명을 넣을 자리를 여기서 잰다.
    """
    if min(src_w or 0, src_h or 0, out_w or 0, out_h or 0) <= 0:
        return float(out_h or 0)
    scale = min(out_w / src_w, out_h / src_h)
    return min(float(out_h), src_h * scale)


def build_ass(cues: list[SubtitleCue], meme_cues: list[MemeCue], cfg: dict,
              *, width: int = 1920, height: int = 1080,
              content_height: float = 0.0, total_duration: float = 0.0) -> str:
    out_h = height or 1
    width, height = reference_resolution(width, height)
    # 자막 좌표계로 환산 (실제 픽셀이 아니라 PlayRes 기준으로 그린다)
    band_content_height = (content_height / out_h * height) if content_height else 0.0
    if not total_duration:
        total_duration = max([c.end for c in cues] + [m.start + m.duration for m in meme_cues]
                             + [1.0])
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
    anim_level = resolve_level(cfg)
    impact_scale = float(cfg.get("impact_scale", 2.7))
    impact_margin_v = int(cfg.get("impact_margin_v", 0) or 0)
    word_color = cfg.get("word_color", emphasis_color)
    word_list = _as_word_list(cfg.get("highlight_words", []))
    per_line = int(cfg.get("highlight_per_line", 1) or 0)
    # 강조색은 한 가지가 아니라 줄마다 돌려 쓴다 (캡처에서 확인)
    palette = _as_word_list(cfg.get("impact_colors", [])) or ["&H002020F0"]
    if cfg.get("impact_color"):
        palette = [cfg["impact_color"]]
    impact_color = palette[0]
    two_tier = bool(cfg.get("two_tier", False))
    two_tier_gap = float(cfg.get("two_tier_gap", 3.0))

    # 세로(쇼츠)에서 위아래 빈 띠. 대사 자막이 이 띠까지 내려가면 채널명과
    # 겹친다. 실제로 겹쳐서 글자가 포개졌다. 자막은 영상 안쪽에 붙인다.
    band = max(0.0, (height - float(band_content_height or height)) / 2.0)
    if band < height * 0.06:
        band = 0.0
    margin_v += int(band)
    if impact_margin_v:
        impact_margin_v += int(band)

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
        _style_line("Narr", font=font, size=int(size * float(cfg.get("narr_scale", 0.7))),
                    primary="&H00FFFFFF", outline_color=outline_color, bold=0,
                    outline=max(1.5, outline - 1.5), shadow=1,
                    align=_ALIGN.get(cfg.get("narr_position", "top"), 8),
                    margin_v=int(height * 0.30) + int(band)),
        # 2단 자막의 윗줄 (직전 대사를 작게 남긴다)
        _style_line("Prev", font=font, size=int(size * impact_scale * 0.42),
                    primary="&H00FFFFFF", outline_color="&H00000000", bold=-1,
                    outline=outline * 1.4, shadow=shadow,
                    align=2,
                    margin_v=(impact_margin_v or max(20, int(margin_v * 0.5)))
                    + int(size * impact_scale * 1.15),
                    margin_l=24, margin_r=24),
        # 타이틀 카드: 검은 화면 + 노란 날짜 + 흰 제목
        _style_line("TitleDate", font=font, size=int(size * 0.95), primary="&H0000E8FF",
                    outline_color="&H00000000", bold=-1, outline=outline, shadow=0,
                    align=5, margin_v=int(height * 0.10)),
        _style_line("TitleName", font=font, size=int(size * 1.35), primary="&H00FFFFFF",
                    outline_color="&H00000000", bold=-1, outline=outline, shadow=0,
                    align=5, margin_v=0),
        f"Style: TitleBg,{font},10,&H00000000,&H000000FF,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        # 쇼츠 위아래 빈 자리에 넣는 제목·채널명
        _style_line("ShortsTitle", font=font, size=int(size * 1.25), primary="&H00FFFFFF",
                    outline_color="&H00000000", bold=-1, outline=outline + 1, shadow=shadow,
                    align=5, margin_v=0, margin_l=24, margin_r=24),
        _style_line("ShortsChannel", font=font, size=int(size * 0.85),
                    primary=cfg.get("channel_color", "&H0033E8FF"),
                    outline_color="&H00000000", bold=-1, outline=outline, shadow=shadow,
                    align=5, margin_v=0, margin_l=24, margin_r=24),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[tuple[float, str]] = []

    # 쇼츠(세로)로 뽑으면 위아래에 빈 띠가 생긴다. 실제 쇼츠들이 그 자리에
    # 제목과 채널명을 넣는 것처럼 채운다. 띠가 좁으면 글자가 영상을 덮으므로
    # 충분히 넓을 때만 넣는다.
    if band >= height * 0.06:
        shorts_title = str(cfg.get("shorts_title", "") or "").strip()
        channel = str(cfg.get("channel", "") or "").strip()
        span = f"{_ass_time(0.0)},{_ass_time(max(1.0, total_duration))}"
        if shorts_title:
            events.append((-1.0,
                           f"Dialogue: 2,{span},ShortsTitle,shorts,0,0,0,,"
                           f"{{\\pos({width // 2},{int(band / 2)})}}"
                           f"{escape_ass(shorts_title)}"))
        if channel:
            events.append((-1.0,
                           f"Dialogue: 2,{span},ShortsChannel,shorts,0,0,0,,"
                           f"{{\\pos({width // 2},{int(height - band / 2)})}}"
                           f"{escape_ass(channel)}"))

    # 타이틀 카드 — 영상 맨 앞에 검은 화면을 깔고 날짜·제목을 얹는다
    title = str(cfg.get("title", "") or "")
    if title:
        card_end = max(0.6, float(cfg.get("title_seconds", 2.5)))
        events.append((-1.0,
                       f"Dialogue: 5,{_ass_time(0.0)},{_ass_time(card_end)},TitleBg,card,"
                       f"0,0,0,,{{\\p1\\an7\\pos(0,0)}}m 0 0 l {int(width)} 0 "
                       f"{int(width)} {int(height)} 0 {int(height)}{{\\p0}}"))
        date = str(cfg.get("title_date", "") or "")
        if date:
            events.append((-0.9,
                           f"Dialogue: 6,{_ass_time(0.15)},{_ass_time(card_end)},TitleDate,"
                           f"card,0,0,0,,{{\\fad(150,200)}}{escape_ass(date)}"))
        events.append((-0.8,
                       f"Dialogue: 6,{_ass_time(0.15)},{_ass_time(card_end)},TitleName,"
                       f"card,0,0,0,,{{\\fad(150,200)}}{escape_ass(title)}"))

    impact_seen = 0
    for i, cue in enumerate(cues):
        if cue.end <= cue.start:
            continue
        # 효과 시간은 자막 길이에 맞춰 줄인다. 0.3초짜리 자막에 0.29초짜리
        # 등장 효과를 넣으면 제 크기가 되기도 전에 사라진다.
        tag = entrance(cue.style, cue.end - cue.start, anim_level)
        text = escape_ass("\n".join(cue.lines))
        if cue.style == "Impact":
            # 줄마다 색을 바꿔 쓴다. 같은 색이 계속 나오면 금방 질린다.
            colour = palette[impact_seen % len(palette)]
            impact_seen += 1
            if colour != impact_color:
                tag += f"{{\\c{colour}&}}"
            # 2단 자막: 직전 대사를 작게 위에 남겨 둔다
            prev = cues[i - 1] if i else None
            if (two_tier and prev is not None and prev.style != "Impact"
                    and 0 <= cue.start - prev.end <= two_tier_gap):
                events.append((cue.start,
                               f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},"
                               f"Prev,{prev.speaker},0,0,0,,"
                               f"{entrance('Prev', cue.end - cue.start, anim_level)}"
                               f"{escape_ass(' '.join(prev.lines))}"))
        else:
            # 초대형 자막은 그 자체로 눈에 띄니 단어 색을 또 바꾸지 않는다
            text = colorize_words(text, word_list, word_color, limit=per_line)
        events.append((cue.start,
                       f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},{cue.style},"
                       f"{cue.speaker},0,0,0,,{tag}{text}"))

    for cue in meme_cues:
        if not cue.text or not getattr(cue, "show_text", cue.kind == "text"):
            continue
        style = (cue.style if cue.style in ("MemeTop", "MemeCenter", "Card", "Label",
                                            "Main", "Emph", "Impact", "Narr")
                 else "MemeTop")
        tag = entrance(style, cue.duration, anim_level)
        text = escape_ass(cue.text)
        events.append((cue.start,
                       f"Dialogue: 1,{_ass_time(cue.start)},{_ass_time(cue.end)},{style},"
                       f"meme,0,0,0,,{tag}{text}"))

    events.sort(key=lambda e: e[0])
    return "\n".join(header + [line for _, line in events]) + "\n"


def write_ass(path: str | Path, cues: list[SubtitleCue], meme_cues: list[MemeCue], cfg: dict,
              *, width: int = 1920, height: int = 1080,
              content_height: float = 0.0, total_duration: float = 0.0) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(cues, meme_cues, cfg, width=width, height=height,
                              content_height=content_height, total_duration=total_duration),
                    encoding="utf-8")
    return path
