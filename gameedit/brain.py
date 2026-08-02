"""AI 가 대사를 읽고 **무엇을 쓸지** 판단한다.

지금까지는 소리 크기와 키워드 목록으로 하이라이트를 골랐다. "소리가 커진
곳" 은 신호가 되긴 하지만 이유를 모른다. 그래서 게임 효과음이 큰 구간과
진짜 웃긴 구간을 구분하지 못한다.

여기서는 대사 전체를 읽고 판단한다.

    "3분 12초부터 3분 41초 — 죽고 나서 어이없어하는 반응이 웃김"
    "3분 38초에 '이게 왜 죽어' 를 큰 자막으로"

**AI 는 판단만 한다.** 그걸 실제 영상으로 만드는 건 기존 코드가 그대로
한다 (highlights → editing → render). 그래서 이 파일이 없어도, 또는 인터넷이
안 되거나 잔액이 떨어져도 프로그램은 예전 방식으로 계속 돈다.

돈이 나가므로 두 가지를 지킨다.
  · 요청 전에 예상 금액을 재고, 상한을 넘으면 아예 부르지 않는다
  · 실제로 쓴 토큰을 내역서에 남긴다 (cost.Ledger)

**공식 SDK 대신 표준 라이브러리로 직접 호출한다.** 취향이 아니라 이 기기에
설치가 안 되기 때문이다. anthropic SDK 는 `jiter` 와 `pydantic_core` 라는
Rust 확장에 의존하는데, 안드로이드용 미리 빌드된 휠이 없어서 소스에서
컴파일하려 든다. 실제 폰에서 확인한 오류가 이것이다.

    Target triple not supported by rustup: aarch64-unknown-linux-android
    Rust not found, installing into a temporary directory

이 프로그램은 폰에서 도는 게 목적이라 여기서는 SDK 를 쓸 수 없다. 보내는
내용(엔드포인트·헤더·본문)은 SDK 가 보내는 것과 같다.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cost import DEFAULT_MODEL, Ledger, krw, text_tokens
from .models import Analysis

Logger = Callable[[str], None]

# 키를 둘 수 있는 곳. 환경변수가 우선이고, 없으면 파일에서 읽는다.
# 폰에서는 설정 파일을 못 만지는 사람이 많아서 파일 한 줄이 제일 쉽다.
KEY_ENV = "ANTHROPIC_API_KEY"
KEY_FILES = ("~/.gameedit-key", "~/gameedit/.key", "./.key")


def _noop(_msg: str) -> None:
    pass


API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TIMEOUT = 180.0
RETRIES = 3


class ApiError(Exception):
    """API 호출이 실패했다. 사람이 읽을 이유를 담는다."""


def load_key(explicit: str = "") -> str:
    """API 키 찾기. 없으면 빈 문자열.

    키는 절대 로그에 남기지 않는다. 화면에도 앞 8글자만 보여 준다.
    """
    if explicit.strip():
        return explicit.strip()
    from_env = os.environ.get(KEY_ENV, "").strip()
    if from_env:
        return from_env
    for raw in KEY_FILES:
        path = Path(raw).expanduser()
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text.splitlines()[0].strip()
        except OSError:
            continue
    return ""


def mask_key(key: str) -> str:
    """화면에 보여 줄 형태. 키 전체가 로그에 남으면 안 된다."""
    key = (key or "").strip()
    if len(key) < 12:
        return "(없음)" if not key else "(짧은 키)"
    return f"{key[:11]}…{key[-4:]}"


# ---------------------------------------------------------------- 주고받는 형식

# AI 가 지켜야 할 답의 모양. 이걸 지정하면 JSON 이 깨져서 오는 일이 없다.
DECISION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "highlights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number", "description": "원본 시작 시각(초)"},
                    "end": {"type": "number", "description": "원본 끝 시각(초)"},
                    "why": {"type": "string", "description": "왜 재밌는지 한 줄"},
                    "score": {"type": "number", "description": "0~1, 얼마나 센 장면인지"},
                },
                "required": ["start", "end", "why", "score"],
                "additionalProperties": False,
            },
        },
        "memes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "at": {"type": "number", "description": "원본 시각(초)"},
                    "text": {"type": "string", "description": "화면에 띄울 짧은 글"},
                    "big": {"type": "boolean", "description": "화면을 채우는 큰 글씨로"},
                },
                "required": ["at", "text", "big"],
                "additionalProperties": False,
            },
        },
        "emphasis_words": {
            "type": "array",
            "items": {"type": "string"},
            "description": "자막에서 색을 바꿀 말 (이 영상의 핵심어)",
        },
        "title": {"type": "string", "description": "영상 제목 후보"},
    },
    "required": ["highlights", "memes", "emphasis_words", "title"],
    "additionalProperties": False,
}

SYSTEM = """당신은 한국 게임 실황 유튜브 편집자입니다.

원본의 대사와 소리 크기를 보고 **완성본에 쓸 구간**을 고르고, 화면에 띄울
짧은 밈 문구를 정합니다.

지켜야 할 것:
- 고른 구간의 총 길이가 요청한 목표 길이에 맞아야 합니다.
- 구간은 겹치지 않게, 시간 순서대로 냅니다.
- `why` 는 "왜 이게 재밌는지" 를 한 줄로 적습니다. "소리가 큼" 같은 건
  이유가 아닙니다. 무슨 일이 일어났는지 적으세요.
- 밈 문구는 실제 한국 게임 실황 자막처럼 짧고 구어체로. ("이게 왜 죽어",
  "?????", "아 진짜 어이없네") 설명하는 문장은 밈이 아닙니다.
- 대사는 자동 인식이라 틀린 글자가 섞여 있습니다. 문맥으로 짐작은 하되,
  **확신이 없으면 그 말을 밈이나 제목에 쓰지 마세요.** 그럴듯하게 지어낸
  문장이 화면에 박히는 게 오타보다 나쁩니다.
- 대사가 없거나 알아볼 수 없으면 소리 크기만 보고 고르고, `why` 에
  "대사를 알아볼 수 없어 소리로만 고름" 이라고 적으세요."""


@dataclass
class Decision:
    """AI 가 내린 판단."""

    highlights: list[dict] = field(default_factory=list)
    memes: list[dict] = field(default_factory=list)
    emphasis_words: list[str] = field(default_factory=list)
    title: str = ""
    used: bool = False           # 실제로 AI 를 불렀는지
    error: str = ""              # 못 부른 이유 (있으면 규칙 기반으로 돌아간다)

    def as_dict(self) -> dict:
        return {"highlights": self.highlights, "memes": self.memes,
                "emphasis_words": self.emphasis_words, "title": self.title,
                "used": self.used, "error": self.error}


def build_transcript_view(analysis: Analysis, *, max_chars: int = 24000) -> str:
    """AI 에게 보낼 대사 + 소리 크기.

    시각을 같이 줘야 "몇 초부터 몇 초까지" 를 답할 수 있다. 소리 크기를
    붙이는 이유는, 대사가 비어 있는 구간에서도 판단할 근거를 주기 위해서다.
    """
    lines: list[str] = []
    for seg in analysis.transcript.segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        level = analysis.audio.mean_between(seg.start, seg.end)
        lines.append(f"[{seg.start:.0f}s] ({level:.2f}) {text}")

    if not lines:      # 대사가 없으면 소리 크기만이라도 보낸다
        step = 5.0
        total = max(analysis.audio.duration, analysis.media.duration)
        t = 0.0
        while t < total:
            lines.append(f"[{t:.0f}s] ({analysis.audio.mean_between(t, t + step):.2f}) (무음)")
            t += step

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    # 너무 길면 앞뒤를 남기고 가운데를 줄인다 (통째로 자르면 뒷부분을 아예 못 본다)
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2:]
    return f"{head}\n… (중간 생략) …\n{tail}"


def build_prompt(analysis: Analysis, cfg: dict, *, target_seconds: float,
                 wishes: str = "") -> str:
    duration = max(analysis.media.duration, analysis.audio.duration)
    parts = [
        f"원본 길이: {duration:.0f}초",
        f"완성본 목표 길이: {target_seconds:.0f}초",
    ]
    if wishes.strip():
        parts.append(f"편집자 요구사항: {wishes.strip()}")
    if not analysis.transcript.segments:
        parts.append("⚠ 대사 인식 결과가 없습니다. 소리 크기만 보고 판단하세요.")
    parts.append("")
    parts.append("아래는 [시각] (소리크기 0~1) 대사 형식입니다.")
    parts.append(build_transcript_view(analysis))
    return "\n".join(parts)


def estimate_call(prompt: str, model: str) -> float:
    """이 요청에 들 돈(원). 상한 검사에 쓴다."""
    return krw(model, text_tokens(SYSTEM) + text_tokens(prompt), 3000)


# ---------------------------------------------------------------- 호출

def call_api(key: str, body: dict, *, url: str = API_URL) -> dict:
    """Messages API 한 번 호출. 표준 라이브러리만 쓴다.

    429(요청 과다)와 5xx(서버 문제)는 잠깐 쉬었다 다시 시도한다. 그 외
    오류는 다시 해도 같은 결과라 바로 올린다 — 키가 틀렸는데 세 번 더
    부르면 기다리는 시간만 는다.
    """
    # HTTP 헤더는 latin-1 만 담을 수 있다. 폰에서 키를 붙여넣다 보면 한글이나
    # 보이지 않는 글자가 섞이는데, 그대로 보내면 UnicodeEncodeError 라는
    # 알아볼 수 없는 오류로 죽는다. 여기서 잡아서 사람 말로 알려 준다.
    key = (key or "").strip()
    if not key.isascii():
        raise ApiError("키에 한글이나 특수문자가 섞여 있습니다. "
                       "복사할 때 앞뒤가 잘못 붙지 않았는지 확인해 주세요.")

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": API_VERSION,
    }
    last: Exception = ApiError("호출하지 못했습니다")
    for attempt in range(RETRIES):
        request = urllib.request.Request(url, data=payload, headers=headers,
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = _error_detail(err)
            if err.code in (429, 500, 502, 503, 529) and attempt < RETRIES - 1:
                wait = float(err.headers.get("retry-after") or (2 ** attempt))
                time.sleep(min(wait, 20.0))
                last = ApiError(detail)
                continue
            raise ApiError(f"[{err.code}] {detail}") from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
                last = err
                continue
            raise
    raise last


def _error_detail(err: urllib.error.HTTPError) -> str:
    """오류 본문에서 사람이 읽을 부분만."""
    try:
        body = json.loads(err.read().decode("utf-8"))
        return str(body.get("error", {}).get("message") or body)[:200]
    except Exception:
        return err.reason or "알 수 없는 오류"


def decide(analysis: Analysis, cfg: dict, ledger: Ledger, *,
           target_seconds: float, wishes: str = "", log: Logger = _noop) -> Decision:
    """AI 에게 편집 판단을 받는다. 못 부르면 이유를 담아 빈 결과를 돌려준다.

    **여기서 예외를 밖으로 던지지 않는다.** AI 가 안 되는 것과 편집이 안 되는
    것은 다른 일이다. 안 되면 규칙 기반으로 그냥 계속 가야 한다.
    """
    result = Decision()
    if not cfg.get("enabled", False):
        result.error = "AI 편집이 꺼져 있습니다"
        return result
    key = load_key(str(cfg.get("api_key", "")))
    if not key:
        result.error = ("API 키가 없습니다. `~/.gameedit-key` 파일에 키를 "
                        "한 줄로 적어 주세요.")
        return result

    model = str(cfg.get("model") or DEFAULT_MODEL)
    prompt = build_prompt(analysis, cfg, target_seconds=target_seconds, wishes=wishes)
    expected = estimate_call(prompt, model)
    if ledger.would_exceed(expected):
        result.error = (f"상한({ledger.limit_krw:.0f}원)을 넘어서 AI 를 부르지 "
                        f"않았습니다 (이 요청 예상 {expected:.0f}원)")
        log(f"  · {result.error}")
        return result

    log(f"  · AI 에게 판단 요청 ({model}, 예상 {expected:.0f}원)")
    try:
        response = call_api(key, {
            "model": model,
            "max_tokens": 8000,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema",
                                         "schema": DECISION_SCHEMA}},
        })
    except Exception as err:                       # 네트워크·잔액·키 오류 전부
        result.error = _friendly_error(err)
        log(f"  · AI 를 쓰지 못했습니다: {result.error}")
        return result

    # 실제로 쓴 토큰. 예상값이 아니라 응답이 알려 준 값을 남긴다.
    usage = response.get("usage") or {}
    ledger.add("AI 편집 판단", model,
               input_tokens=int(usage.get("input_tokens", 0) or 0),
               output_tokens=int(usage.get("output_tokens", 0) or 0))

    try:
        text = next(b["text"] for b in response.get("content", [])
                    if b.get("type") == "text")
        data = json.loads(text)
    except (StopIteration, ValueError, KeyError, TypeError) as err:
        result.error = f"AI 답을 읽지 못했습니다: {err}"
        return result

    result.highlights = [h for h in data.get("highlights", []) if _sane_range(h)]
    result.memes = [m for m in data.get("memes", []) if str(m.get("text", "")).strip()]
    result.emphasis_words = [w for w in data.get("emphasis_words", []) if str(w).strip()][:10]
    result.title = str(data.get("title", "")).strip()[:60]
    result.used = True
    log(f"  · 구간 {len(result.highlights)}개 · 밈 {len(result.memes)}개 "
        f"· 실제 {ledger.steps[-1].krw:.0f}원")
    return result


def _sane_range(item) -> bool:
    """AI 가 낸 구간이 말이 되는지. 못 믿을 값이 렌더까지 가면 안 된다."""
    try:
        start, end = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= start < end and (end - start) <= 600


def _friendly_error(err: Exception) -> str:
    """오류를 사람 말로. **무엇을 해야 하는지**가 들어 있어야 한다."""
    text = str(err)
    low = text.lower()
    if "[401]" in text or "authentication" in low or "invalid x-api-key" in low:
        return "API 키가 잘못됐습니다. ~/.gameedit-key 의 키를 다시 확인해 주세요."
    if "[403]" in text or "permission" in low:
        return "이 키로는 쓸 수 없습니다. 콘솔에서 키 권한을 확인해 주세요."
    if "credit" in low or "billing" in low or "[402]" in text:
        return "잔액이 부족합니다. console.anthropic.com 의 Billing 에서 충전해 주세요."
    if "[429]" in text or "rate" in low:
        return "요청이 너무 잦습니다. 잠시 뒤에 다시 해 주세요."
    if "[404]" in text or "not_found" in low:
        return "모델 이름이 잘못됐습니다. 설정의 ai.model 을 확인해 주세요."
    if isinstance(err, (urllib.error.URLError, TimeoutError, OSError)):
        return "인터넷 연결이 안 됩니다. 와이파이를 확인해 주세요."
    return text[:160]
