"""자막이 **어떻게** 뜨는지.

그냥 켜졌다 꺼지면 검수용 영상처럼 보인다. 뜨는 방식에 성격을 주면 같은
글자라도 편집본으로 읽힌다. 대사는 조용히 스며들고, 강조는 튀어나오고,
결정적인 순간은 비스듬히 내리꽂힌다.

여기서 제일 조심한 것은 **짧은 자막**이다. 0.3초짜리 자막에 0.29초짜리
등장 효과를 넣으면 글자가 제 크기가 되기도 전에 사라진다. 화면에 뭐가
스쳤는지도 모르게 되고, 그건 효과가 아니라 그냥 결함이다. 그래서 모든
시간을 **자막 길이에 맞춰 줄인다.**

성능도 이유가 있다. 크기·각도가 변하는 효과는 매 프레임 글자를 다시
그려야 해서, 투명도만 바꾸는 페이드보다 훨씬 무겁다. 그래서 화면에 가장
자주 나오는 **대사 자막(Main)은 페이드만** 쓰고, 가끔 나오는 강조·밈에만
움직임을 준다. 폰에서 자막 몇백 줄이 전부 크기 변화를 하면 렌더가 눈에
띄게 느려진다.

    off    아무 효과 없음
    light  페이드만 (제일 가벼움)
    full   스타일마다 다른 등장 효과 (기본)
"""

from __future__ import annotations

from dataclasses import dataclass

LEVELS = ("off", "light", "full")

# 등장 효과가 자막 길이에서 차지해도 되는 비율. 이걸 넘으면 글자가 멈춰
# 있는 시간이 없어서 읽을 수가 없다.
ENTER_SHARE = 0.40
EXIT_SHARE = 0.30

# 움직임에 이만큼(ms)도 못 쓰면 아예 안 움직인다. 30fps 에서 3프레임 미만이면
# 글자가 커지는 게 보이는 게 아니라 한 프레임 튀었다 마는 것처럼 보인다.
MIN_MOTION_MS = 90


@dataclass(frozen=True)
class Motion:
    """한 스타일이 뜨고 사라지는 방식."""

    fade_in: int = 80          # ms
    fade_out: int = 80         # ms
    grow_from: int = 100       # 시작 크기 % (100 = 크기 변화 없음)
    overshoot: int = 0         # 지나쳤다 돌아오는 크기 % (0 = 안 함)
    grow_ms: int = 0           # 제 크기가 되기까지
    settle_ms: int = 0         # 지나친 만큼 되돌아오기까지
    tilt: int = 0              # 시작 기울기(도). 0 이면 안 기울인다

    @property
    def moves(self) -> bool:
        return self.grow_from != 100 or self.tilt != 0


# 스타일마다 성격을 다르게. 전부 같은 효과면 넣으나 마나다.
MOTIONS: dict[str, Motion] = {
    # 대사. 화면에 제일 오래·자주 있으므로 페이드만 (가볍고 안 거슬린다)
    "Main": Motion(fade_in=80, fade_out=80),
    "Prev": Motion(fade_in=80, fade_out=80),
    # 작은 설명. 옆에서 스윽 펼쳐지듯이
    "Narr": Motion(fade_in=100, fade_out=120, grow_from=82, grow_ms=140),
    # 노란 강조. 살짝 튀어나왔다 자리 잡는다
    "Emph": Motion(fade_in=60, fade_out=60, grow_from=0, overshoot=115,
                   grow_ms=120, settle_ms=140),
    # 결정적인 순간. 비스듬히 내리꽂힌다
    "Impact": Motion(fade_in=40, fade_out=120, grow_from=0, overshoot=126,
                     grow_ms=110, settle_ms=130, tilt=-7),
    # 밈. 제일 크게 튀어나온다
    "MemeTop": Motion(fade_in=60, fade_out=180, grow_from=0, overshoot=120,
                      grow_ms=140, settle_ms=140),
    "MemeCenter": Motion(fade_in=60, fade_out=180, grow_from=0, overshoot=122,
                         grow_ms=140, settle_ms=140),
    # 라벨·카드는 정보라서 얌전하게
    "Label": Motion(fade_in=120, fade_out=200),
    "Card": Motion(fade_in=180, fade_out=250),
}

DEFAULT = Motion()


def resolve_level(cfg: dict) -> str:
    """설정에서 효과 단계를 읽는다.

    예전 설정(`pop_animation: true/false`)을 쓰던 파일도 그대로 돌아가야
    한다. 업데이트했다고 남의 설정이 조용히 달라지면 안 된다.
    """
    raw = str(cfg.get("animation", "") or "").strip().lower()
    if raw in LEVELS:
        return raw
    if raw in ("true", "on", "yes"):
        return "full"
    if raw in ("false", "off", "no", "none"):
        return "off"
    return "full" if cfg.get("pop_animation", True) else "off"


def _fit(nominal: int, duration: float, share: float) -> int:
    """자막이 짧으면 효과 시간도 줄인다."""
    if nominal <= 0 or duration <= 0:
        return 0
    return max(0, int(min(nominal, duration * 1000.0 * share)))


def entrance(style: str, duration: float, level: str = "full") -> str:
    """이 자막에 붙일 ASS 태그. 붙일 게 없으면 빈 문자열.

    duration 은 자막이 화면에 있는 시간(초). 짧으면 효과가 그만큼 압축된다.
    """
    if level == "off" or duration <= 0:
        return ""
    motion = MOTIONS.get(style, DEFAULT)

    fade_in = _fit(motion.fade_in, duration, ENTER_SHARE)
    fade_out = _fit(motion.fade_out, duration, EXIT_SHARE)
    parts = [f"\\fad({fade_in},{fade_out})"]

    if level == "light" or not motion.moves:
        return "{" + "".join(parts) + "}"

    # 등장 효과가 쓸 수 있는 시간. 페이드인이 이미 먹은 만큼은 빼지 않는다
    # (둘은 동시에 일어나므로 겹쳐도 된다).
    budget = duration * 1000.0 * ENTER_SHARE
    total = motion.grow_ms + motion.settle_ms
    if total <= 0 or budget < MIN_MOTION_MS:
        return "{" + "".join(parts) + "}"      # 너무 짧으면 페이드만
    scale = min(1.0, budget / total)
    grow = int(motion.grow_ms * scale)
    settle = int(motion.settle_ms * scale)
    if grow <= 0:
        return "{" + "".join(parts) + "}"

    # 시작 상태
    start = []
    if motion.grow_from != 100:
        start.append(f"\\fscx{motion.grow_from}\\fscy{motion.grow_from}")
    if motion.tilt:
        start.append(f"\\frz{motion.tilt}")
    parts.extend(start)

    # 제 크기(또는 지나친 크기)까지
    peak = motion.overshoot or 100
    step = f"\\t(0,{grow},\\fscx{peak}\\fscy{peak}"
    if motion.tilt:
        step += "\\frz0"
    parts.append(step + ")")

    # 지나쳤으면 되돌아온다
    if motion.overshoot and settle > 0:
        parts.append(f"\\t({grow},{grow + settle},\\fscx100\\fscy100)")

    return "{" + "".join(parts) + "}"
