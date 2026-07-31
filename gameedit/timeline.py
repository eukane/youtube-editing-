"""편집 계획 검수용 HTML 리포트 생성.

렌더링 전에 "어디를 잘랐고, 어떤 밈이 언제 뜨고, 자막이 어떻게 나가는지"를
브라우저에서 한 번에 확인하기 위한 화면.
"""

from __future__ import annotations

import html
from pathlib import Path

from .media import format_timecode
from .models import Analysis, EditPlan

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; padding:32px; background:#0d1017; color:#e6e9ef;
       font-family:'Pretendard','Apple SD Gothic Neo','Noto Sans KR',system-ui,sans-serif; }
h1 { font-size:24px; margin:0 0 4px; }
h2 { font-size:17px; margin:36px 0 12px; color:#9fb4d4; }
.sub { color:#7d8799; font-size:13px; margin-bottom:24px; }
.stats { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:28px; }
.stat { background:#161b26; border:1px solid #232a38; border-radius:10px; padding:12px 16px; min-width:130px; }
.stat b { display:block; font-size:20px; }
.stat span { color:#7d8799; font-size:12px; }
.track { position:relative; height:52px; background:#161b26; border:1px solid #232a38;
         border-radius:8px; overflow:hidden; margin-bottom:8px; }
.track .bar { position:absolute; bottom:0; width:1px; background:#2f4d7a; }
.track .clip { position:absolute; top:0; height:100%; background:rgba(88,166,255,.32);
               border-left:2px solid #58a6ff; }
.track .meme { position:absolute; top:0; width:2px; height:12px; background:#ffd33d; }
.legend { color:#7d8799; font-size:12px; margin-bottom:8px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid #1e2534; vertical-align:top; }
th { color:#7d8799; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
td.num, th.num { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.tag { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
       background:#1d2738; color:#9fb4d4; }
.tag.emph { background:#3a3113; color:#ffd33d; }
.quote { color:#a9b3c4; }
.empty { color:#7d8799; font-style:italic; }
code { background:#161b26; padding:2px 6px; border-radius:4px; font-size:12px; }
"""


def _esc(text: str) -> str:
    return html.escape(text or "")


def _stat(value, label: str) -> str:
    return f'<div class="stat"><b>{value}</b><span>{_esc(label)}</span></div>'


def _source_track(plan: EditPlan, analysis: Analysis | None) -> str:
    duration = max(plan.media.duration, 1.0)
    pieces: list[str] = []
    if analysis and analysis.audio.excitement:
        step = max(1, len(analysis.audio.excitement) // 600)
        for i in range(0, len(analysis.audio.excitement), step):
            value = analysis.audio.excitement[i]
            t = i * analysis.audio.hop
            left = 100.0 * t / duration
            height = max(2.0, value * 100.0)
            pieces.append(f'<div class="bar" style="left:{left:.3f}%;height:{height:.1f}%"></div>')
    for clip in plan.clips:
        left = 100.0 * clip.source_start / duration
        width = max(0.2, 100.0 * clip.duration / duration)
        title = f"{format_timecode(clip.source_start)} · {clip.label}"
        pieces.append(f'<div class="clip" style="left:{left:.3f}%;width:{width:.3f}%" '
                      f'title="{_esc(title)}"></div>')
    return f'<div class="track">{"".join(pieces)}</div>'


def _output_track(plan: EditPlan) -> str:
    duration = max(plan.duration, 1.0)
    pieces: list[str] = []
    for clip in plan.clips:
        left = 100.0 * clip.out_start / duration
        width = max(0.2, 100.0 * clip.duration / duration)
        pieces.append(f'<div class="clip" style="left:{left:.3f}%;width:{width:.3f}%" '
                      f'title="{_esc(clip.label)}"></div>')
    for cue in plan.memes:
        left = 100.0 * cue.start / duration
        pieces.append(f'<div class="meme" style="left:{left:.3f}%" '
                      f'title="{_esc(cue.text or cue.meme_id)}"></div>')
    return f'<div class="track">{"".join(pieces)}</div>'


def _clip_rows(plan: EditPlan, analysis: Analysis | None) -> str:
    rows: list[str] = []
    for i, clip in enumerate(plan.clips, start=1):
        quote = ""
        if analysis:
            quote = analysis.transcript.text_between(clip.source_start, clip.source_end)
            if len(quote) > 120:
                quote = quote[:120] + "…"
        effects = ", ".join(clip.effects) or "-"
        rows.append(
            "<tr>"
            f"<td class='num'>{i}</td>"
            f"<td>{_esc(clip.label)}</td>"
            f"<td class='num'>{format_timecode(clip.source_start)} – {format_timecode(clip.source_end)}</td>"
            f"<td class='num'>{clip.duration:.1f}s</td>"
            f"<td class='num'>{format_timecode(clip.out_start)}</td>"
            f"<td class='num'>{clip.score:.2f}</td>"
            f"<td>{_esc(effects)}</td>"
            f"<td class='quote'>{_esc(quote)}</td>"
            "</tr>"
        )
    if not rows:
        return "<tr><td colspan='8' class='empty'>선택된 하이라이트가 없습니다.</td></tr>"
    return "".join(rows)


def _meme_rows(plan: EditPlan) -> str:
    rows: list[str] = []
    for cue in plan.memes:
        label = cue.text or Path(cue.asset).name or cue.meme_id
        rows.append(
            "<tr>"
            f"<td class='num'>{format_timecode(cue.start)}</td>"
            f"<td><span class='tag'>{_esc(cue.kind)}</span></td>"
            f"<td>{_esc(cue.meme_id)}</td>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_esc(cue.placement)}</td>"
            f"<td class='num'>{cue.duration:.1f}s</td>"
            f"<td class='quote'>{_esc(cue.trigger)}</td>"
            "</tr>"
        )
    if not rows:
        return "<tr><td colspan='7' class='empty'>배치된 밈이 없습니다.</td></tr>"
    return "".join(rows)


def _subtitle_rows(plan: EditPlan, limit: int = 80) -> str:
    rows: list[str] = []
    for cue in plan.subtitles[:limit]:
        tag = "<span class='tag emph'>강조</span>" if cue.style == "Emph" else "<span class='tag'>기본</span>"
        rows.append(
            "<tr>"
            f"<td class='num'>{format_timecode(cue.start)} – {format_timecode(cue.end)}</td>"
            f"<td>{tag}</td>"
            f"<td>{_esc(cue.text).replace(chr(10), '<br>')}</td>"
            "</tr>"
        )
    if not rows:
        return "<tr><td colspan='3' class='empty'>자막이 없습니다. (음성인식 백엔드 미설치 또는 비활성화)</td></tr>"
    if len(plan.subtitles) > limit:
        rows.append(f"<tr><td colspan='3' class='empty'>… 외 {len(plan.subtitles) - limit}줄</td></tr>")
    return "".join(rows)


def build_html(plan: EditPlan, analysis: Analysis | None = None, *, title: str = "게임 하이라이트") -> str:
    source_duration = max(plan.media.duration, 1.0)
    ratio = 100.0 * plan.duration / source_duration
    stats = "".join([
        _stat(format_timecode(source_duration), "원본 길이"),
        _stat(format_timecode(plan.duration), "편집본 길이"),
        _stat(f"{ratio:.1f}%", "압축률"),
        _stat(len(plan.clips), "하이라이트 클립"),
        _stat(len(plan.memes), "밈 큐"),
        _stat(len(plan.subtitles), "자막 줄"),
    ])
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)} · 편집 계획</title><style>{_CSS}</style></head>
<body>
<h1>{_esc(title)} · 편집 계획</h1>
<div class="sub">원본: <code>{_esc(plan.source)}</code> · 생성 {_esc(str(plan.meta.get('created_at', '')))}</div>
<div class="stats">{stats}</div>

<h2>원본 타임라인 (파랑=살린 구간, 막대=오디오 흥분도)</h2>
{_source_track(plan, analysis)}
<div class="legend">0:00 → {format_timecode(source_duration)}</div>

<h2>편집본 타임라인 (노란 눈금=밈)</h2>
{_output_track(plan)}
<div class="legend">0:00 → {format_timecode(plan.duration)}</div>

<h2>하이라이트 클립</h2>
<table><thead><tr>
<th class="num">#</th><th>제목</th><th class="num">원본 구간</th><th class="num">길이</th>
<th class="num">편집본 위치</th><th class="num">점수</th><th>효과</th><th>대사</th>
</tr></thead><tbody>{_clip_rows(plan, analysis)}</tbody></table>

<h2>밈 배치</h2>
<table><thead><tr>
<th class="num">시각</th><th>종류</th><th>ID</th><th>내용</th><th>위치</th>
<th class="num">길이</th><th>트리거</th>
</tr></thead><tbody>{_meme_rows(plan)}</tbody></table>

<h2>자막</h2>
<table><thead><tr><th class="num">구간</th><th>스타일</th><th>내용</th></tr></thead>
<tbody>{_subtitle_rows(plan)}</tbody></table>

<h2>수정하려면</h2>
<div class="sub">
<code>plan.json</code> 을 직접 고친 뒤 <code>gameedit render</code> 를 다시 실행하면 그대로 반영됩니다.
클립 순서 변경·삭제, 밈 시각 조정, 자막 문구 수정 모두 가능합니다.
</div>
</body></html>
"""


def write_html(path: str | Path, plan: EditPlan, analysis: Analysis | None = None,
               *, title: str = "게임 하이라이트") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(plan, analysis, title=title), encoding="utf-8")
    return path
