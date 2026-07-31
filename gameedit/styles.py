"""편집 스타일 프리셋.

**이 파일에는 실제로 확인한 것만 넣는다.**

전에는 다른 AI 가 정리한 유튜버 5인의 수치(평균 컷 길이·분당 밈 수 등)를
그대로 옮겨 놓았었다. 그 뒤 사용자가 실제 편집본 캡처를 보내 줬고, 화면을
직접 보니 그 자료의 자막 설명("하단 1~2줄")이 실제와 달랐다. 한 화면에
크기·색·단수가 다른 자막이 동시에 올라가고, 강조색도 노랑 하나가 아니라
마젠타·주황·빨강·흰색을 돌려 쓴다.

그래서 **캡처가 있는 안모리만 남기고 나머지 네 개는 지웠다.** 근거 없는
수치로 프리셋을 만들어 두면 맞는지 틀린지 아무도 확인할 수 없다.

컷 속도만 조절하고 싶으면 폰 화면의 '편집 강도'(server.EDIT_PACE) 를 쓰면
된다. 그쪽은 특정인을 흉내 낸다고 주장하지 않는다.

좋아하는 편집자의 리듬을 가져오려면 추측 대신 측정하는 길이 있다.

    gameedit learn 참고영상.mp4 -o 내스타일.yaml
"""

from __future__ import annotations

# 각 항목 옆에 어느 캡처에서 봤는지 적어 둔다. 확인 못 한 것은 넣지 않는다.
EVIDENCE: dict[str, list[str]] = {
    "anmori": [
        "제일 센 짧은 대사는 화면 폭 70% 를 채우는 초대형 글씨 + 두꺼운 검은 외곽선",
        "강조색이 하나가 아니다 — 마젠타·주황·빨강·흰색을 줄마다 바꿔 쓴다",
        "문장 앞 구절만 색을 바꾸고 뒤는 흰색으로 둔다 (\"바로 |전구 폼 로토무!\")",
        "이전 줄을 작게 위에 남기고 현재 줄을 크게 아래에 놓는 2단 자막",
        "괄호로 시작하는 줄은 대사가 아니라 상황 설명이고 위쪽에 작게 뜬다",
        "영상 맨 앞에 검은 화면 + 노란 날짜 + 흰 제목의 타이틀 카드",
        "우하단에 채널 로고 상시 노출",
    ],
}

STYLES: dict[str, dict] = {
    # 안모리 — 사용자가 보낸 편집본 캡처 11장에서 확인한 것만.
    # 컷 길이·밈 빈도는 정지 화면으로 알 수 없어서 넣지 않았다.
    "anmori": {
        "subtitles.impact": True,
        "subtitles.impact_scale": 2.7,
        "subtitles.two_tier": True,
        "subtitles.max_lines": 1,
        "subtitles.margin_v": 60,
        "render.watermark_scale": 0.06,
    },
}

ALIASES = {"안모리": "anmori"}


def resolve(name: str) -> str:
    """한글 이름·영문 키 아무거나 받는다."""
    key = (name or "").strip()
    return ALIASES.get(key.replace(" ", ""), key.lower())


def get(name: str) -> dict:
    """스타일 이름 → 설정값. 모르는 이름이면 ValueError."""
    key = resolve(name)
    if key not in STYLES:
        raise ValueError(f"모르는 스타일입니다: {name}\n"
                         f"쓸 수 있는 것: {', '.join(names())}\n"
                         f"컷 속도만 바꾸려면 편집 강도(여유/기본/빠르게)를 쓰세요.")
    return dict(STYLES[key])


def names() -> list[str]:
    return list(STYLES)


def describe(name: str) -> str:
    key = resolve(name)
    if key not in EVIDENCE:
        return ""
    lines = [f"{key} — 편집본 캡처에서 확인한 것:"]
    lines += [f"  · {item}" for item in EVIDENCE[key]]
    return "\n".join(lines)
