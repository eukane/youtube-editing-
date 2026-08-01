"""AI 를 쓸 때 돈이 얼마나 나가는지 미리 재고, 실제로 쓴 만큼 기록한다.

**시작하기 전에 예상 금액을 보여 주고, 상한선을 넘으면 멈춘다.** 얼마 나올지
모르는 채로 돌리게 하면 아무도 못 쓴다.

요금은 '토큰' 단위로 매겨진다. 토큰은 글자 조각이고, 넣는 말(입력)보다
나오는 말(출력)이 다섯 배쯤 비싸다. 이미지는 픽셀 수에 비례해 토큰으로
환산되는데, 사진 한 장이 한글 수천 자와 맞먹는다. 그래서 화면을 보는 쪽이
대사만 읽는 쪽보다 훨씬 비싸다.

가격표는 2026-06-24 기준이다. 바뀔 수 있으므로 **실제 청구액과 다를 수
있고**, 화면에도 '예상' 이라고 적는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 100만 토큰당 미국 달러 (입력, 출력)
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}
DEFAULT_MODEL = "claude-opus-5"

# 환율은 매일 바뀐다. 정확한 금액이 아니라 '자릿수' 를 보여 주는 게 목적이다.
USD_TO_KRW = 1400.0

# 이미지 한 장의 토큰 수 ≈ 가로 × 세로 / 750
IMAGE_TOKEN_DIVISOR = 750.0

# 한글은 글자당 대략 이만큼의 토큰이 된다 (영어보다 촘촘하다)
KOREAN_TOKENS_PER_CHAR = 0.9


def image_tokens(width: int, height: int) -> int:
    """사진 한 장이 몇 토큰인지.

    작게 보낼수록 싸다. 320x180 이면 77 토큰, 1280x720 이면 1229 토큰으로
    16배 차이가 난다. 전체를 훑을 때 작게 보내는 이유가 이것이다.
    """
    pixels = max(0, int(width)) * max(0, int(height))
    return int(pixels / IMAGE_TOKEN_DIVISOR) if pixels else 0


def text_tokens(text: str) -> int:
    """한글 문장이 몇 토큰인지 (대략)."""
    return int(len(text or "") * KOREAN_TOKENS_PER_CHAR)


def price_of(model: str) -> tuple[float, float]:
    return PRICES.get(model, PRICES[DEFAULT_MODEL])


def krw(model: str, input_tokens: int, output_tokens: int, *,
        rate: float = USD_TO_KRW) -> float:
    """토큰 수 → 원화(대략)."""
    price_in, price_out = price_of(model)
    usd = (max(0, input_tokens) * price_in + max(0, output_tokens) * price_out) / 1_000_000
    return usd * rate


@dataclass
class Step:
    """한 번의 요청."""

    name: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    note: str = ""            # 무엇에 썼는지 (인터넷 검색어 등)

    @property
    def krw(self) -> float:
        return krw(self.model, self.input_tokens, self.output_tokens)


@dataclass
class Ledger:
    """실제로 쓴 내역. **어디에 얼마를 썼는지** 남는다.

    합계만 보여 주면 "왜 이만큼 나왔지" 를 알 수가 없다. 단계별로 남긴다.
    """

    steps: list[Step] = field(default_factory=list)
    limit_krw: float = 0.0        # 0 이면 상한 없음

    def add(self, name: str, model: str, *, input_tokens: int = 0,
            output_tokens: int = 0, images: int = 0, note: str = "") -> Step:
        step = Step(name=name, model=model, input_tokens=input_tokens,
                    output_tokens=output_tokens, images=images, note=note)
        self.steps.append(step)
        return step

    @property
    def total_krw(self) -> float:
        return sum(s.krw for s in self.steps)

    @property
    def total_input(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @property
    def total_output(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    @property
    def total_images(self) -> int:
        return sum(s.images for s in self.steps)

    def remaining_krw(self) -> float:
        """상한까지 남은 금액. 상한이 없으면 무한대."""
        if self.limit_krw <= 0:
            return float("inf")
        return max(0.0, self.limit_krw - self.total_krw)

    def would_exceed(self, expected_krw: float) -> bool:
        """이만큼 더 쓰면 상한을 넘는지. 넘으면 그 단계를 건너뛴다."""
        if self.limit_krw <= 0:
            return False
        return self.total_krw + max(0.0, expected_krw) > self.limit_krw

    def as_dict(self) -> dict:
        return {
            "total_krw": round(self.total_krw),
            "limit_krw": round(self.limit_krw),
            "input_tokens": self.total_input,
            "output_tokens": self.total_output,
            "images": self.total_images,
            "steps": [
                {"name": s.name, "model": s.model, "krw": round(s.krw),
                 "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
                 "images": s.images, "note": s.note}
                for s in self.steps
            ],
            "lines": self.report_lines(),
        }

    def report_lines(self) -> list[str]:
        """화면에 그대로 보여 줄 줄들."""
        if not self.steps:
            return ["AI 를 쓰지 않았습니다 (0원)"]
        lines = []
        for step in self.steps:
            bits = [f"{step.name}", f"약 {step.krw:.0f}원"]
            if step.images:
                bits.append(f"사진 {step.images}장")
            if step.note:
                bits.append(step.note)
            lines.append(" · ".join(bits))
        lines.append(f"합계 약 {self.total_krw:.0f}원")
        if self.limit_krw > 0:
            lines.append(f"(상한 {self.limit_krw:.0f}원)")
        return lines


@dataclass
class Estimate:
    """돌리기 전에 계산한 예상 비용."""

    model: str = DEFAULT_MODEL
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    frames_seconds: float = 0.0     # 몇 초에 한 장씩 봤는지 (설명용)

    @property
    def krw(self) -> float:
        return krw(self.model, self.input_tokens, self.output_tokens)

    def as_dict(self) -> dict:
        return {"model": self.model, "krw": round(self.krw), "images": self.images,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "summary": self.summary()}

    def summary(self) -> str:
        bits = [f"예상 약 {self.krw:.0f}원"]
        if self.images:
            bits.append(f"화면 {self.images}장")
        bits.append(f"({self.model})")
        return " · ".join(bits)


def estimate_edit(duration: float, cfg: dict) -> Estimate:
    """영상 하나를 AI 로 편집할 때의 예상 비용.

    duration 은 원본 길이(초). 설정에 따라 대사만 읽을지, 화면까지 볼지가
    달라지고 그 차이가 열 배 넘게 난다.
    """
    model = str(cfg.get("model") or DEFAULT_MODEL)
    mode = str(cfg.get("mode", "dialogue"))
    est = Estimate(model=model)

    minutes = max(0.0, float(duration)) / 60.0
    # 대사: 1분에 한글 250자쯤 말한다고 본다. 여기에 시간·소리 정보가 붙는다.
    est.input_tokens += int(minutes * 250 * KOREAN_TOKENS_PER_CHAR * 1.6)
    est.input_tokens += 1500                      # 지시문 (매번 같아서 캐시가 듣는다)
    est.output_tokens += int(minutes * 120)       # 편집 지시

    if mode == "vision":
        every = max(0.5, float(cfg.get("frame_every", 1.0)))
        w = int(cfg.get("scan_width", 320))
        h = int(cfg.get("scan_height", 180))
        scan_frames = int(max(0.0, duration) / every)
        est.images += scan_frames
        est.frames_seconds = every
        est.input_tokens += scan_frames * image_tokens(w, h)
        # 2단계: 표시된 지점만 고해상도로 다시 본다
        close = int(cfg.get("closeup_frames", 40))
        est.images += close
        est.input_tokens += close * image_tokens(int(cfg.get("closeup_width", 1280)),
                                                 int(cfg.get("closeup_height", 720)))
        est.output_tokens += int(minutes * 60)
    return est
