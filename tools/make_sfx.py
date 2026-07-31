#!/usr/bin/env python3
"""기본 팩의 효과음을 ffmpeg 로 직접 합성한다.

인터넷에서 받아 온 파일이 아니라 **사인파·잡음에서 계산으로 만들어 낸 소리**다.
그래서 출처가 이 스크립트 하나로 끝나고 라이선스 문제가 생기지 않는다.

    python tools/make_sfx.py

목소리가 들어가야 하는 소리(무야호·웃음·비명 등)는 합성으로 흉내 낼 수 없어
비워 둔다. 그 자리는 각자 가진 소스를 넣으면 된다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "memepacks" / "default" / "sfx"
RATE = 44100


def tone(freq: float, start: float, length: float, *, decay: float = 8.0,
         gain: float = 0.5, wave: str = "sin") -> str:
    """start 초부터 length 초 동안 울리다 사그라드는 음 하나."""
    osc = {
        "sin": f"sin(2*PI*{freq}*t)",
        # 사각파 느낌 (경보음처럼 날카로운 소리에 쓴다).
        # ffmpeg 식에는 '>' 가 없어서 gt() 를 쓴다.
        "sq": f"(2*gt(sin(2*PI*{freq}*t),0)-1)",
    }[wave]
    env = f"exp(-{decay}*(t-{start}))"
    return f"{gain}*{osc}*{env}*between(t,{start},{start + length})"


def sweep(f_start: float, f_end: float, start: float, length: float, *,
          gain: float = 0.5, decay: float = 2.0) -> str:
    """주파수가 미끄러지는 소리 (삐용/추락)."""
    # 순간 주파수를 선형으로 바꾸려면 위상은 그 적분이라 t^2 항이 붙는다
    k = (f_end - f_start) / length
    phase = f"({f_start}*(t-{start}) + {k / 2}*(t-{start})*(t-{start}))"
    return (f"{gain}*sin(2*PI*{phase})*exp(-{decay}*(t-{start}))"
            f"*between(t,{start},{start + length})")


def noise(start: float, length: float, *, gain: float = 0.3, decay: float = 10.0) -> str:
    """잡음 한 줌 (타격감·전환음의 재료)."""
    return (f"{gain}*(2*random(0)-1)*exp(-{decay}*(t-{start}))"
            f"*between(t,{start},{start + length})")


# 이름 → (전체 길이, 소리를 이루는 항들)
SOUNDS: dict[str, tuple[float, list[str]]] = {
    # 타격·충격
    "두둥": (1.2, [tone(58, 0, 1.2, decay=3.0, gain=0.9),
                  tone(87, 0, 0.8, decay=5.0, gain=0.4),
                  noise(0, 0.12, gain=0.35, decay=30)]),
    "놀람": (0.7, [tone(1568, 0, 0.25, decay=14, gain=0.45),
                  tone(2093, 0.02, 0.25, decay=16, gain=0.3),
                  noise(0, 0.2, gain=0.25, decay=18)]),
    "보스등장": (2.2, [tone(55, 0, 2.2, decay=1.2, gain=0.9),
                    tone(82.4, 0, 2.2, decay=1.4, gain=0.5),
                    tone(110, 0.3, 1.8, decay=1.6, gain=0.3)]),

    # 획득·성공
    "돈": (0.55, [tone(988, 0, 0.1, decay=16, gain=0.45),
                 tone(1319, 0.07, 0.45, decay=9, gain=0.45)]),
    "득템": (0.75, [tone(523, 0, 0.12, decay=14, gain=0.4),
                  tone(659, 0.09, 0.12, decay=14, gain=0.4),
                  tone(784, 0.18, 0.12, decay=14, gain=0.4),
                  tone(1047, 0.27, 0.5, decay=7, gain=0.45)]),
    "승리": (1.6, [tone(523, 0, 0.18, decay=6, gain=0.4),
                 tone(659, 0.16, 0.18, decay=6, gain=0.4),
                 tone(784, 0.32, 0.18, decay=6, gain=0.4),
                 tone(1047, 0.48, 1.1, decay=2.2, gain=0.5),
                 tone(1319, 0.48, 1.1, decay=2.4, gain=0.28)]),
    "알림": (0.7, [tone(880, 0, 0.16, decay=11, gain=0.4),
                 tone(1175, 0.13, 0.5, decay=7, gain=0.4)]),

    # 실패·사망
    "사망": (1.6, [tone(220, 0, 0.3, decay=5, gain=0.5),
                 tone(185, 0.25, 0.3, decay=5, gain=0.5),
                 tone(147, 0.5, 0.35, decay=4.5, gain=0.5),
                 tone(110, 0.8, 0.8, decay=2.5, gain=0.55)]),
    "실패": (1.2, [tone(392, 0, 0.22, decay=7, gain=0.45),
                 tone(349, 0.2, 0.22, decay=7, gain=0.45),
                 tone(311, 0.4, 0.22, decay=7, gain=0.45),
                 tone(262, 0.6, 0.6, decay=3.5, gain=0.5)]),
    "추락": (1.3, [sweep(900, 90, 0, 1.2, gain=0.5, decay=1.2)]),

    # 연출
    "띠용": (0.5, [sweep(180, 900, 0, 0.13, gain=0.45, decay=3),
                 sweep(900, 260, 0.12, 0.33, gain=0.45, decay=5)]),
    "전환": (0.8, [noise(0, 0.75, gain=0.32, decay=3.2),
                 sweep(200, 1800, 0, 0.4, gain=0.22, decay=3.5),
                 sweep(1800, 200, 0.38, 0.4, gain=0.22, decay=4.0)]),
    "경보": (1.8, [tone(800, 0.0, 0.28, decay=1.2, gain=0.32, wave="sq"),
                 tone(1000, 0.3, 0.28, decay=1.2, gain=0.32, wave="sq"),
                 tone(800, 0.6, 0.28, decay=1.2, gain=0.32, wave="sq"),
                 tone(1000, 0.9, 0.28, decay=1.2, gain=0.32, wave="sq"),
                 tone(800, 1.2, 0.28, decay=1.2, gain=0.32, wave="sq"),
                 tone(1000, 1.5, 0.28, decay=1.2, gain=0.32, wave="sq")]),
    "드럼롤": (1.8, [f"0.32*(2*random(0)-1)*(0.55+0.45*sin(2*PI*26*t))"
                  f"*min(1,t*3)*between(t,0,1.55)",
                  tone(196, 1.55, 0.25, decay=9, gain=0.5),
                  noise(1.55, 0.2, gain=0.4, decay=16)]),
}

# 목소리가 있어야 하는 소리는 합성으로 흉내 낼 수 없다
SKIP = {"무야호": "사람 목소리", "웃음": "웃음소리", "비명": "사람 비명",
        "귀뚜라미": "실제 풀벌레 소리"}


def build(name: str, duration: float, terms: list[str], out_dir: Path) -> Path:
    expr = "+".join(terms)
    target = out_dir / f"{name}.mp3"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"aevalsrc='{expr}':d={duration}:s={RATE}",
        # 클리핑 방지 + 살짝 다듬기
        "-af", "alimiter=limit=0.95,afade=t=out:st="
               f"{max(0.0, duration - 0.05):.3f}:d=0.05",
        "-ac", "1", "-b:a", "128k", str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{name} 합성 실패:\n{proc.stderr[-800:]}")
    return target


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = []
    for name, (duration, terms) in SOUNDS.items():
        path = build(name, duration, terms, OUT_DIR)
        made.append(f"  ✓ {path.name:14s} {path.stat().st_size / 1024:6.1f} KB")
    print(f"효과음 {len(made)}개를 만들었습니다 → {OUT_DIR}")
    print("\n".join(made))
    if SKIP:
        print("\n비워 둔 것 (합성으로는 못 만듭니다. 직접 넣으세요):")
        for name, why in SKIP.items():
            print(f"  · {name}.mp3 — {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
