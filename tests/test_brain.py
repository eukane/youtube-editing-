"""AI 편집 판단.

제일 중요한 성질: **AI 가 안 되는 것과 편집이 안 되는 것은 다른 일이다.**
키가 없든 인터넷이 끊기든 잔액이 떨어지든, 프로그램은 규칙 기반으로 그냥
계속 가야 한다. 여기서 예외가 밖으로 새면 완성본이 아예 안 나온다.
"""

import json

import pytest

from gameedit.brain import (DECISION_SCHEMA, Decision, build_prompt, build_transcript_view,
                            decide, estimate_call, load_key, mask_key, _sane_range)
from gameedit.cost import Ledger
from gameedit.models import Analysis, AudioAnalysis, MediaInfo, Segment, Transcript


def _analysis(with_speech=True, duration=180.0):
    hop = 0.5
    n = int(duration / hop)
    audio = AudioAnalysis(hop=hop, rms_db=[-20.0] * n,
                          excitement=[0.2 + 0.7 * ((i // 20) % 2) for i in range(n)])
    segments = []
    if with_speech:
        segments = [
            Segment(start=30.0, end=33.0, text="아니 이게 왜 죽어 진짜"),
            Segment(start=95.0, end=98.0, text="로토무 미쳤네 ㅋㅋㅋ"),
        ]
    return Analysis(media=MediaInfo(path="/tmp/a.mp4", duration=duration,
                                    width=1920, height=1080, fps=30.0),
                    audio=audio, transcript=Transcript(segments=segments))


# ------------------------------------------------- 안 될 때 조용히 물러난다

def test_disabled_returns_empty_without_calling():
    got = decide(_analysis(), {"enabled": False}, Ledger(), target_seconds=60)
    assert got.used is False and got.highlights == []
    assert "꺼져" in got.error


def test_missing_sdk_says_what_to_install(monkeypatch):
    monkeypatch.setattr("gameedit.brain.sdk_available", lambda: False)
    got = decide(_analysis(), {"enabled": True}, Ledger(), target_seconds=60)
    assert got.used is False
    assert "pip install anthropic" in got.error


def test_missing_key_says_where_to_put_it(monkeypatch):
    monkeypatch.setattr("gameedit.brain.sdk_available", lambda: True)
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "")
    got = decide(_analysis(), {"enabled": True}, Ledger(), target_seconds=60)
    assert got.used is False
    assert ".gameedit-key" in got.error


def test_budget_limit_blocks_the_call_before_spending(monkeypatch):
    """넘고 나서 알려 주면 이미 늦었다."""
    monkeypatch.setattr("gameedit.brain.sdk_available", lambda: True)
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "sk-ant-fake")
    called = []
    monkeypatch.setattr("gameedit.brain.estimate_call", lambda *a: 9999.0)

    ledger = Ledger(limit_krw=100)
    got = decide(_analysis(), {"enabled": True}, ledger, target_seconds=60)
    assert got.used is False and "상한" in got.error
    assert called == [] and ledger.steps == []


def test_network_error_does_not_escape(monkeypatch):
    """AI 가 안 되는 것과 편집이 안 되는 것은 다른 일이다."""
    monkeypatch.setattr("gameedit.brain.sdk_available", lambda: True)
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "sk-ant-fake")

    import sys, types
    fake = types.ModuleType("anthropic")

    class _Client:
        def __init__(self, **kw):
            raise ConnectionError("인터넷 없음")
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    got = decide(_analysis(), {"enabled": True}, Ledger(), target_seconds=60)
    assert got.used is False and got.highlights == []
    assert got.error                                   # 이유는 남긴다


# ------------------------------------------------------------- 정상 동작

class _FakeResponse:
    def __init__(self, payload, in_tokens=12000, out_tokens=900):
        self.content = [type("B", (), {"type": "text", "text": json.dumps(payload)})()]
        self.usage = type("U", (), {"input_tokens": in_tokens, "output_tokens": out_tokens})()


def _fake_anthropic(monkeypatch, payload, capture=None):
    import sys, types
    fake = types.ModuleType("anthropic")

    class _Messages:
        def create(self, **kw):
            if capture is not None:
                capture.update(kw)
            return _FakeResponse(payload)

    class _Client:
        def __init__(self, **kw):
            self.messages = _Messages()
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setattr("gameedit.brain.sdk_available", lambda: True)
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "sk-ant-fake")


PAYLOAD = {
    "highlights": [
        {"start": 28.0, "end": 36.0, "why": "죽고 나서 어이없어함", "score": 0.9},
        {"start": 93.0, "end": 100.0, "why": "로토무한테 지고 폭소", "score": 0.8},
    ],
    "memes": [{"at": 31.0, "text": "이게 왜 죽어", "big": True}],
    "emphasis_words": ["로토무"],
    "title": "로토무한테 지는 사람",
}


def test_successful_call_returns_decisions(monkeypatch):
    _fake_anthropic(monkeypatch, PAYLOAD)
    ledger = Ledger()
    got = decide(_analysis(), {"enabled": True}, ledger, target_seconds=60)

    assert got.used is True
    assert len(got.highlights) == 2
    assert got.highlights[0]["why"] == "죽고 나서 어이없어함"
    assert got.memes[0]["text"] == "이게 왜 죽어"
    assert got.emphasis_words == ["로토무"]
    assert got.title == "로토무한테 지는 사람"


def test_actual_usage_is_recorded(monkeypatch):
    """예상이 아니라 실제로 쓴 토큰이 내역서에 남아야 한다."""
    _fake_anthropic(monkeypatch, PAYLOAD)
    ledger = Ledger()
    decide(_analysis(), {"enabled": True}, ledger, target_seconds=60)

    assert len(ledger.steps) == 1
    step = ledger.steps[0]
    assert step.input_tokens == 12000 and step.output_tokens == 900
    assert step.krw > 0


def test_request_uses_the_configured_model_and_schema(monkeypatch):
    sent = {}
    _fake_anthropic(monkeypatch, PAYLOAD, capture=sent)
    decide(_analysis(), {"enabled": True, "model": "claude-sonnet-5"}, Ledger(),
           target_seconds=60)

    assert sent["model"] == "claude-sonnet-5"
    # 형식을 지정해야 JSON 이 깨져서 오는 일이 없다
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["format"]["schema"] == DECISION_SCHEMA


def test_broken_json_is_reported_not_raised(monkeypatch):
    import sys, types
    fake = types.ModuleType("anthropic")

    class _Messages:
        def create(self, **kw):
            r = _FakeResponse({})
            r.content = [type("B", (), {"type": "text", "text": "이건 JSON 이 아님"})()]
            return r

    class _Client:
        def __init__(self, **kw):
            self.messages = _Messages()
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setattr("gameedit.brain.sdk_available", lambda: True)
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "sk-ant-fake")

    got = decide(_analysis(), {"enabled": True}, Ledger(), target_seconds=60)
    assert got.used is False and "읽지 못했습니다" in got.error


# ------------------------------------------------- 못 믿을 값 걸러내기

@pytest.mark.parametrize("item,ok", [
    ({"start": 10, "end": 20}, True),
    ({"start": 20, "end": 10}, False),        # 거꾸로
    ({"start": -5, "end": 10}, False),        # 음수
    ({"start": 10, "end": 10}, False),        # 길이 0
    ({"start": 0, "end": 9999}, False),       # 말이 안 되게 김
    ({"start": "가", "end": 10}, False),      # 숫자가 아님
    ({}, False),
])
def test_nonsense_ranges_are_dropped(item, ok):
    """AI 가 낸 값이 그대로 렌더까지 가면 안 된다."""
    assert _sane_range(item) is ok


def test_empty_meme_text_is_dropped(monkeypatch):
    payload = dict(PAYLOAD, memes=[{"at": 1.0, "text": "  ", "big": False},
                                   {"at": 2.0, "text": "ㅋㅋ", "big": False}])
    _fake_anthropic(monkeypatch, payload)
    got = decide(_analysis(), {"enabled": True}, Ledger(), target_seconds=60)
    assert [m["text"] for m in got.memes] == ["ㅋㅋ"]


# ------------------------------------------------------------- 보낼 내용

def test_transcript_view_has_times_and_levels():
    view = build_transcript_view(_analysis())
    assert "[30s]" in view and "이게 왜 죽어" in view
    assert "(" in view                      # 소리 크기도 같이

def test_transcript_view_falls_back_to_loudness_without_speech():
    """대사가 없어도 판단할 근거는 줘야 한다."""
    view = build_transcript_view(_analysis(with_speech=False))
    assert "무음" in view and "[0s]" in view


def test_long_transcript_keeps_both_ends():
    """통째로 자르면 영상 뒷부분을 아예 못 본다."""
    big = _analysis()
    big.transcript.segments = [
        Segment(start=float(i), end=float(i) + 1, text=f"{i}번째 대사입니다 " * 3)
        for i in range(4000)
    ]
    view = build_transcript_view(big, max_chars=2000)
    assert len(view) < 3000
    assert "[0s]" in view and "3999" in view
    assert "중간 생략" in view


def test_prompt_carries_the_target_and_wishes():
    prompt = build_prompt(_analysis(), {}, target_seconds=180, wishes="죽는 장면 위주로")
    assert "180초" in prompt and "죽는 장면 위주로" in prompt


def test_prompt_warns_when_there_is_no_transcript():
    prompt = build_prompt(_analysis(with_speech=False), {}, target_seconds=60)
    assert "대사 인식 결과가 없습니다" in prompt


def test_estimate_grows_with_the_prompt():
    small = estimate_call("짧은 글", "claude-sonnet-5")
    big = estimate_call("긴 글 " * 5000, "claude-sonnet-5")
    assert 0 < small < big


# ------------------------------------------------------------------ 키

def test_key_is_never_shown_in_full():
    assert mask_key("sk-ant-api03-abcdefghijklmnop") == "sk-ant-api0…mnop"
    assert mask_key("") == "(없음)"


def test_key_comes_from_env_first(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    assert load_key() == "sk-ant-from-env"
    assert load_key("sk-ant-explicit") == "sk-ant-explicit"   # 직접 준 게 최우선


def test_key_read_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    key_file = tmp_path / ".gameedit-key"
    key_file.write_text("sk-ant-from-file\n두번째줄은무시\n", encoding="utf-8")
    monkeypatch.setattr("gameedit.brain.KEY_FILES", (str(key_file),))
    assert load_key() == "sk-ant-from-file"


def test_no_key_anywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("gameedit.brain.KEY_FILES", (str(tmp_path / "없음"),))
    assert load_key() == ""
