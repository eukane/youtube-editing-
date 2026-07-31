"""편집 스타일 프리셋.

한국 게임 실황 편집자 5인의 스타일을 수치로 정리한 자료를 사용자가 제공했고,
그 값을 이 프로그램의 설정으로 옮긴 것이다.

**출처와 신뢰도를 분명히 해 둔다.** 이 수치는 다른 AI 가 정리한 것을 받아 옮긴
것이고, 이 프로그램이 직접 영상을 재서 얻은 값이 아니다. 실제 영상이 있으면
`gameedit learn` 으로 측정해 대조하는 편이 항상 낫다. 측정값과 어긋나면
측정값을 믿어야 한다.

원자료의 항목 → 이 프로그램의 설정 대응은 아래와 같다.

    평균 컷 길이       → editing.target_cut_length, highlight.min_clip
    무음 제거 기준     → editing.dead_air_min + analyze.min_silence
                         (분석이 그보다 짧은 무음을 안 잡으면 잘라낼 수가 없다)
    분당 밈/효과음     → memes.max_per_minute, memes.transition_sfx_every
    리액션 줌 배율/빈도 → editing.zoom_min/zoom_max/zoom_per_minute
    도입부 길이/구성   → editing.cold_open_seconds/cold_open_pieces
    자막 줄수/위치     → subtitles.max_lines, subtitles.margin_v
"""

from __future__ import annotations

# 각 스타일의 근거가 된 원자료 수치 (사람이 대조할 수 있게 남겨 둔다)
MEASUREMENTS: dict[str, dict] = {
    "anmori": {"이름": "안모리", "평균 컷": 2.2, "분당 밈": 12.5,
               "줌": "1.2~1.5배 · 4.2회/분", "무음 제거": 0.15, "도입부": 32},
    "seungsangsing": {"이름": "승상싱", "평균 컷": 1.6, "분당 밈": 18.0,
                      "줌": "1.5~4.0배 · 4회/분", "무음 제거": 0.2, "도입부": 28},
    "kangjiwon": {"이름": "검성 강지원", "평균 컷": 2.8, "분당 밈": 13.5,
                  "줌": "1.2~2.5배 · 2.5회/분", "무음 제거": 0.8, "도입부": 15},
    "bate": {"이름": "바테", "평균 컷": 2.1, "분당 밈": 14.0,
             "줌": "2.5배 고정 · 3.5회/분", "무음 제거": 0.15, "도입부": 30},
    "baljep": {"이름": "발젭", "평균 컷": 1.8, "분당 밈": 20.0,
               "줌": "1.8배 · 5회/분", "무음 제거": 0.1, "도입부": 32},
}

STYLES: dict[str, dict] = {
    # 안모리 — 촘촘하지만 대사가 살아 있는 편. 강조할 때만 자막이 두 줄.
    "anmori": {
        # 분석이 이보다 짧은 무음을 기록하지 않으면 잘라낼 수가 없다.
        # 두 값은 항상 같이 움직여야 한다.
        "analyze.min_silence": 0.15,
        "editing.dead_air_min": 0.15,
        "editing.target_cut_length": 2.2,      # 원자료의 '평균 컷 길이'
        "editing.dead_air_keep": 0.06,
        "editing.dead_air_min_piece": 0.5,
        "editing.zoom_min": 1.2, "editing.zoom_max": 1.5,
        "editing.zoom_per_minute": 4.2,
        "editing.cold_open_seconds": 32.0, "editing.cold_open_pieces": 5,
        "highlight.min_clip": 2.2, "highlight.max_clip": 20.0,
        "highlight.pad_before": 0.7, "highlight.pad_after": 0.6,
        "memes.max_per_minute": 3.5,          # 시각 밈만. 효과음은 아래에서 따로
        "memes.cooldown": 5.0, "memes.min_gap": 1.0,
        "memes.transition_sfx_every": 3,      # 컷 3개마다 (≈ 분당 9회)
        "subtitles.max_lines": 2, "subtitles.margin_v": 60,
    },
    # 승상싱 — 초고압축. 쉴 틈 없이 정보가 이어진다. 억까 순간 4배 줌.
    "seungsangsing": {
        # 분석이 이보다 짧은 무음을 기록하지 않으면 잘라낼 수가 없다.
        # 두 값은 항상 같이 움직여야 한다.
        "analyze.min_silence": 0.2,
        "editing.dead_air_min": 0.2,
        "editing.target_cut_length": 1.6,      # 원자료의 '평균 컷 길이'
        "editing.dead_air_keep": 0.06,
        "editing.dead_air_min_piece": 0.45,
        "editing.zoom_min": 1.5, "editing.zoom_max": 4.0,
        "editing.zoom_per_minute": 4.0,
        "editing.cold_open_seconds": 28.0, "editing.cold_open_pieces": 4,
        "highlight.min_clip": 1.6, "highlight.max_clip": 16.0,
        "highlight.pad_before": 0.5, "highlight.pad_after": 0.4,
        "memes.max_per_minute": 6.0,
        "memes.cooldown": 4.0, "memes.min_gap": 0.8,
        "memes.transition_sfx_every": 2,      # 컷 2개마다 (≈ 분당 12회)
        "subtitles.max_lines": 2, "subtitles.margin_v": 60,
        "subtitles.bold": True,
    },
    # 검성 강지원 — 평시엔 여유롭고 교전에서 확 조인다. 무음 기준이 유일하게 느슨.
    "kangjiwon": {
        # 분석이 이보다 짧은 무음을 기록하지 않으면 잘라낼 수가 없다.
        # 두 값은 항상 같이 움직여야 한다.
        "analyze.min_silence": 0.8,
        "editing.dead_air_min": 0.8,
        "editing.target_cut_length": 2.8,      # 원자료의 '평균 컷 길이'
        "editing.dead_air_keep": 0.12,
        "editing.dead_air_min_piece": 0.8,
        "editing.bridge_gaps": False,         # 이동 구간은 이어 붙이지 않고 하드컷
        "editing.zoom_min": 1.2, "editing.zoom_max": 2.5,
        "editing.zoom_per_minute": 2.5,
        "editing.cold_open_seconds": 15.0, "editing.cold_open_pieces": 3,
        "highlight.min_clip": 2.8, "highlight.max_clip": 24.0,
        "highlight.pad_before": 0.9, "highlight.pad_after": 0.8,
        "memes.max_per_minute": 5.0,
        "memes.cooldown": 5.0, "memes.min_gap": 1.0,
        "memes.transition_sfx_every": 3,
        "subtitles.max_lines": 2, "subtitles.margin_v": 90,
        "subtitles.emphasis": True,           # 핵심 단어 노란색
    },
    # 바테 — 자막은 무조건 1줄, 3초 이상 고정 화면 금지, 줌은 2.5배 고정.
    "bate": {
        # 분석이 이보다 짧은 무음을 기록하지 않으면 잘라낼 수가 없다.
        "analyze.min_silence": 0.15,
        "editing.dead_air_min": 0.15,
        "editing.target_cut_length": 2.1,      # 원자료의 '평균 컷 길이'
        "editing.dead_air_keep": 0.06,
        "editing.dead_air_min_piece": 0.5,
        "editing.zoom_min": 2.5, "editing.zoom_max": 2.5,
        "editing.zoom_per_minute": 3.5,
        "editing.cold_open_seconds": 30.0, "editing.cold_open_pieces": 4,
        "highlight.min_clip": 2.1, "highlight.max_clip": 18.0,
        "highlight.pad_before": 0.6, "highlight.pad_after": 0.5,
        "memes.max_per_minute": 5.0,
        "memes.cooldown": 4.5, "memes.min_gap": 0.9,
        "memes.transition_sfx_every": 2,
        "subtitles.max_lines": 1,             # 1줄 원칙
        "subtitles.max_chars_per_line": 22,
        "subtitles.margin_v": 60,
    },
    # 발젭 — 가장 극단적. 무음 0.1초부터 제거, 거의 모든 컷에 소리.
    "baljep": {
        # 분석이 이보다 짧은 무음을 기록하지 않으면 잘라낼 수가 없다.
        # 두 값은 항상 같이 움직여야 한다.
        "analyze.min_silence": 0.1,
        "editing.dead_air_min": 0.1,
        "editing.target_cut_length": 1.8,      # 원자료의 '평균 컷 길이'
        "editing.dead_air_keep": 0.05,
        "editing.dead_air_min_piece": 0.4,
        "editing.max_pieces": 700,            # 이 정도로 자르면 조각이 많아진다
        "editing.zoom_min": 1.8, "editing.zoom_max": 1.8,
        "editing.zoom_per_minute": 5.0,
        "editing.cold_open_seconds": 32.0, "editing.cold_open_pieces": 5,
        "highlight.min_clip": 1.8, "highlight.max_clip": 16.0,
        "highlight.pad_before": 0.5, "highlight.pad_after": 0.4,
        "memes.max_per_minute": 7.0,
        "memes.cooldown": 3.5, "memes.min_gap": 0.7,
        "memes.transition_sfx_every": 1,      # 매 컷마다
        "subtitles.max_lines": 1,
        "subtitles.max_chars_per_line": 22,
        "subtitles.margin_v": 60,
    },
}

ALIASES = {
    "안모리": "anmori",
    "승상싱": "seungsangsing",
    "강지원": "kangjiwon", "검성강지원": "kangjiwon", "검성": "kangjiwon",
    "바테": "bate",
    "발젭": "baljep",
}


def resolve(name: str) -> str:
    """한글 이름·영문 키 아무거나 받는다."""
    key = (name or "").strip()
    return ALIASES.get(key.replace(" ", ""), key.lower())


def get(name: str) -> dict:
    """스타일 이름 → 설정값. 모르는 이름이면 ValueError."""
    key = resolve(name)
    if key not in STYLES:
        raise ValueError(f"모르는 스타일입니다: {name}\n"
                         f"쓸 수 있는 것: {', '.join(names())}")
    return dict(STYLES[key])


def names() -> list[str]:
    return [f"{k}({MEASUREMENTS[k]['이름']})" for k in STYLES]


def describe(name: str) -> str:
    key = resolve(name)
    m = MEASUREMENTS.get(key)
    if not m:
        return ""
    return (f"{m['이름']} — 평균 컷 {m['평균 컷']}초 · 분당 밈/효과음 {m['분당 밈']}회 · "
            f"줌 {m['줌']} · 무음 {m['무음 제거']}초 이상 제거 · 도입부 {m['도입부']}초")
