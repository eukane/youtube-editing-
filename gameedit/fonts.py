"""한글 자막용 폰트 찾기.

설정한 폰트가 시스템에 없으면 자막이 □□□ 로 나온다.
처음 실행할 때 제일 자주 겪는 문제라, 설치된 한글 폰트 중 하나로 자동 대체한다.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache

# 앞에 있을수록 자막용으로 보기 좋은 폰트
PREFERRED = [
    "Noto Sans KR",
    "Noto Sans CJK KR",
    "Pretendard",
    "NanumGothic",
    "나눔고딕",
    "NanumBarunGothic",
    "Malgun Gothic",
    "맑은 고딕",
    "Apple SD Gothic Neo",
    "Spoqa Han Sans Neo",
    "Source Han Sans KR",
]


@lru_cache(maxsize=1)
def installed_korean_fonts() -> tuple[str, ...]:
    """설치된 한글 지원 폰트 이름들.

    fontconfig(fc-list)가 있는 환경에서만 조회할 수 있다.
    윈도우·맥에는 보통 없으므로 빈 값이 나오고, 그때는 libass 자체 대체에 맡긴다.
    """
    if not shutil.which("fc-list"):
        return ()
    try:
        proc = subprocess.run(["fc-list", ":lang=ko", "family"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ()

    names: list[str] = []
    for line in (proc.stdout or "").splitlines():
        for name in line.split(","):
            # fc-list 는 하이픈 등을 역슬래시로 이스케이프해서 준다 (ASS 에 그대로 못 씀)
            name = name.replace("\\", "").strip()
            if name and name not in names:
                names.append(name)
    return tuple(names)


def _has(font: str, available) -> bool:
    target = (font or "").strip().lower()
    if not target:
        return False
    return any(target == a.lower() or target in a.lower() for a in available)


def resolve_font(preferred: str, *, available=None) -> str:
    """설정한 폰트를 그대로 쓸지, 설치된 다른 한글 폰트로 바꿀지 결정."""
    fonts = tuple(available) if available is not None else installed_korean_fonts()
    if not fonts:
        return preferred  # 확인할 방법이 없으면 설정값 그대로
    if _has(preferred, fonts):
        return preferred
    for candidate in PREFERRED:
        if _has(candidate, fonts):
            return candidate
    # Unifont 는 마지막 수단용 비트맵 폰트라 자막용으로는 최후순위
    usable = [f for f in fonts if "unifont" not in f.lower()]
    return (usable or list(fonts))[0]
