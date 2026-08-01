"""편집 요구사항을 한국어 문장으로 받아 설정으로 바꾼다.

화면의 '요구사항' 칸에 적은 말을 읽어서 설정을 조정한다.

**이 프로그램은 AI 가 아니다.** 아무 문장이나 이해하지 못하고, 아래에 적힌
표현만 알아듣는다. 그래서 **무엇을 알아들었고 무엇을 못 알아들었는지 반드시
같이 돌려준다.** 조용히 무시하면 사용자는 반영된 줄 알고 결과를 오해한다.

    "3분으로 짧게, 자막 크게, 죽는 장면 위주로. 밈은 빼줘"
      → highlight.target_duration = 180
      → subtitles.font_size = 80
      → highlight.keywords 에 죽음 관련 가중치
      → memes.enabled = False
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Understood:
    """요구사항을 읽은 결과."""

    settings: dict[str, object] = field(default_factory=dict)
    matched: list[str] = field(default_factory=list)     # 알아들은 것 (사람 말로)
    ignored: list[str] = field(default_factory=list)     # 못 알아들은 조각

    def as_dict(self) -> dict:
        return {"settings": self.settings, "matched": self.matched,
                "ignored": self.ignored}


# (정규식, 사람에게 보여줄 설명, 설정) — 순서대로 검사한다.
# 설명은 화면에 그대로 나가므로 사용자가 읽고 "내 말이 맞게 들어갔나" 확인할 수 있어야 한다.
RULES: list[tuple[str, str, dict]] = [
    # 길이
    (r"(\d+)\s*분\s*(?:짜리|으?로|정도|내외)?", "완성본 길이 {0}분", {"highlight.target_duration": "{0}*60"}),
    # 속도·밀도
    (r"(?:빠르게|촘촘|숨\s*쉴\s*틈|템포\s*(?:빠|올))", "촘촘하게 (죽은 시간 최대한 제거)",
     {"editing.dead_air_min": 0.32, "editing.dead_air_keep": 0.08,
      "editing.dead_air_min_piece": 0.5, "highlight.pad_before": 0.8,
      "highlight.pad_after": 0.6}),
    (r"(?:천천히|여유|느긋|호흡)", "여유롭게 (말 사이 호흡 남김)",
     {"editing.dead_air_min": 1.2, "editing.speed_ramp": False,
      "editing.bridge_gaps": False}),
    # 밈
    (r"밈\s*(?:은|는)?\s*(?:빼|없|끄|말)", "밈 빼기", {"memes.enabled": False}),
    (r"밈\s*(?:을|를)?\s*(?:많이|잔뜩|더)", "밈 많이", {"memes.max_per_minute": 8.0,
                                                    "memes.cooldown": 3.5}),
    (r"밈\s*(?:을|를)?\s*(?:조금|적게|줄)", "밈 적게", {"memes.max_per_minute": 1.5}),
    # 자막
    (r"자막\s*(?:은|는)?\s*(?:빼|없|끄|말)", "대사 자막 빼기", {"subtitles.enabled": False}),
    (r"자막\s*(?:을|를)?\s*(?:크게|키워|크)", "자막 크게", {"subtitles.font_size": 80}),
    (r"자막\s*(?:을|를)?\s*(?:작게|줄여|작)", "자막 작게", {"subtitles.font_size": 48}),
    (r"자막\s*(?:은|는)?\s*(?:한\s*줄|1\s*줄)", "자막 한 줄로", {"subtitles.max_lines": 1}),
    # 연출
    (r"(?:줌|확대)\s*(?:은|는)?\s*(?:빼|없|끄|말)", "줌인 빼기", {"editing.zoom": False}),
    (r"(?:도입부|인트로|앞부분)\s*(?:은|는)?\s*(?:빼|없|끄|말)", "도입부(하이라이트 선공개) 빼기",
     {"editing.cold_open": False}),
    (r"(?:세로|쇼츠|숏폼)", "세로(쇼츠)로", {"project.resolution": "1080x1920"}),
    (r"(?:효과음|사운드)\s*(?:을|를)?\s*(?:매|모든|전부|많이)", "컷마다 효과음",
     {"memes.transition_sfx_every": 1}),
    # 용어 설명 자막 (미리 받아 둔 사전에서 읽는다)
    (r"(?:포켓몬|타입|용어|이름)\s*(?:을|를)?\s*(?:설명|알려|표시|띄워|넣)",
     "이름 나오면 설명 자막 (포켓몬 타입 등)",
     {"glossary.enabled": True, "glossary.files": ["pokemon.json"]}),
    # 화질
    (r"(?:화질|고화질)\s*(?:을|를)?\s*(?:좋게|높|올)", "화질 우선 (느려짐)",
     {"render.crf": 20, "render.preset": "medium"}),
    (r"(?:빨리|빠르게)\s*(?:만들|뽑|끝)", "빨리 만들기 (화질 양보)",
     {"render.crf": 28, "render.preset": "ultrafast"}),
]

# 하이라이트 성향: 어떤 장면을 우선할지
FOCUS: list[tuple[str, str, list[str]]] = [
    (r"(?:죽|사망|데스|털리|당하)", "죽는 장면 위주",
     ["죽었", "죽네", "사망", "터졌", "망했", "털렸", "당했", "리스폰"]),
    (r"(?:웃|개그|재밌|웃긴|ㅋㅋ)", "웃긴 장면 위주",
     ["ㅋㅋ", "ㅎㅎ", "웃겨", "미쳤", "뭐야", "대박"]),
    (r"(?:이기|승리|클리어|성공|잘하)", "잘한 장면 위주",
     ["이겼", "클리어", "성공", "1등", "생존", "살았", "개꿀"]),
    (r"(?:놀라|소름|충격|반전)", "놀라는 장면 위주",
     ["헐", "우와", "말도 안", "설마", "소름", "지렸", "실화"]),
]

_TIME = r"(\d{1,2}):(\d{2})(?::(\d{2}))?"


def _seconds(match: re.Match, offset: int = 0) -> float:
    parts = [match.group(offset + i) for i in (1, 2, 3)]
    if parts[2] is None:
        return int(parts[0]) * 60 + int(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def _clauses(text: str) -> list[str]:
    """문장을 쉼표·마침표·'그리고' 로 나눈다. 요구사항은 대개 이렇게 나열된다."""
    parts = re.split(r"[,.\n·]|\s+그리고\s+|\s+또\s+", text)
    return [p.strip() for p in parts if p.strip()]


def parse(text: str) -> Understood:
    """요구사항 문장 → 설정 + 무엇을 알아들었는지.

    문장 조각별로 검사해서, **아무 규칙에도 안 걸린 조각만** 못 알아들은 것으로
    보고한다. 알아들은 문장의 나머지 글자를 '못 알아들음' 에 넣으면 사용자가
    반영이 안 된 줄 오해한다.
    """
    result = Understood()
    if not text or not text.strip():
        return result

    keywords: list[str] = []
    span = re.compile(_TIME + r"\s*[~\-–]\s*" + _TIME)

    for clause in _clauses(text):
        hit = False

        # 1) 구간 지정 ("12:30~15:00 꼭 넣어줘" / "0:00~1:00 빼줘")
        for m in span.finditer(clause):
            start, end = _seconds(m, 0), _seconds(m, 3)
            if end <= start:
                continue
            drop = re.search(r"(빼|제외|없애|자르|삭제)", clause)
            key = "highlight.exclude_ranges" if drop else "highlight.must_include_ranges"
            result.settings.setdefault(key, [])
            result.settings[key].append([start, end])
            result.matched.append(
                f"{int(start)//60}:{int(start)%60:02d}~{int(end)//60}:{int(end)%60:02d} "
                + ("빼기" if drop else "꼭 넣기"))
            hit = True

        # 2) 일반 규칙
        for pattern, label, settings in RULES:
            m = re.search(pattern, clause)
            if not m:
                continue
            for key, value in settings.items():
                if isinstance(value, str) and "{0}" in value:
                    value = float(m.group(1)) * 60 if "*60" in value else float(m.group(1))
                result.settings[key] = value
            result.matched.append(label.format(*(m.groups() or ())))
            hit = True

        # 3) 어떤 장면 위주로
        for pattern, label, words in FOCUS:
            if re.search(pattern, clause):
                keywords.extend(words)
                result.matched.append(label)
                hit = True

        if not hit:
            result.ignored.append(clause)

    if keywords:
        result.settings["highlight.focus_keywords"] = sorted(set(keywords))
    # 같은 말을 여러 번 적어도 한 번만 보여 준다
    result.matched = list(dict.fromkeys(result.matched))
    result.ignored = result.ignored[:6]
    return result


def apply(config, text: str) -> Understood:
    """요구사항을 Config 에 반영하고 결과를 돌려준다."""
    got = parse(text)
    for key, value in got.settings.items():
        if key == "highlight.focus_keywords":
            # 기존 키워드에 더한다 (덮어쓰면 다른 장면을 아예 못 찾는다)
            existing = list(config.get("highlight.keywords", []) or [])
            config.set("highlight.keywords", existing + [w for w in value if w not in existing])
            continue
        config.set(key, value)
    return got
