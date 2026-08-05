"""AI 판단 → 실제 편집 재료로 옮기는 층.

여기서 지켜야 할 두 가지:
  · AI 가 낸 값을 그대로 믿지 않는다 (영상 밖·겹침·과다 길이가 실제로 온다)
  · AI 가 안 되면 **편집이 멈추지 않고** 규칙 기반으로 간다
"""

import json

import pytest

from gameedit.aiplan import clips_from_decision, memes_from_decision
from gameedit.brain import Decision
from gameedit.config import Config
from gameedit.models import EditPlan, MediaInfo, Clip
from gameedit.plan import build_plan


def _hl_cfg(**over):
    cfg = dict(Config().section("highlight"))
    cfg.update(over)
    return cfg


def _decision(highlights=None, **over):
    d = Decision(highlights=highlights if highlights is not None else [], used=True)
    for k, v in over.items():
        setattr(d, k, v)
    return d


# ------------------------------------------------- AI 가 낸 구간을 못 믿는 이유

def test_ranges_outside_the_video_are_clamped(analysis):
    """AI 는 원본보다 뒤의 시각을 답하기도 한다. 그대로 자르면 렌더가 깨진다."""
    got = clips_from_decision(
        _decision([{"start": 170.0, "end": 500.0, "why": "끝판", "score": 0.9}]),
        analysis, _hl_cfg(target_duration=120))
    assert len(got) == 1
    assert got[0].source_end <= analysis.media.duration


def test_overlapping_ranges_do_not_repeat_the_same_scene(analysis):
    """겹친 채로 두면 같은 장면이 두 번 나온다."""
    got = clips_from_decision(_decision([
        {"start": 20.0, "end": 40.0, "why": "가", "score": 0.9},
        {"start": 30.0, "end": 50.0, "why": "나", "score": 0.8},
    ]), analysis, _hl_cfg(target_duration=120))
    for a, b in zip(got, got[1:]):
        assert a.source_end <= b.source_start


def test_absurdly_long_range_is_cut_to_max_clip(analysis):
    """'2분이 통째로 재밌다' 는 편집이 아니다."""
    got = clips_from_decision(
        _decision([{"start": 0.0, "end": 175.0, "why": "다 재밌음", "score": 1.0}]),
        analysis, _hl_cfg(target_duration=300, max_clip=30.0))
    assert got[0].source_duration <= 30.0 * 1.15


def test_garbage_entries_are_dropped_not_crashed(analysis):
    """AI 가 이상한 걸 보내도 죽으면 안 된다. 죽으면 편집 전체가 멈춘다."""
    got = clips_from_decision(_decision([
        {"start": "삼십", "end": 40.0, "why": "글자", "score": 0.9},
        {"end": 40.0, "why": "start 없음", "score": 0.9},
        {"start": 50.0, "end": 20.0, "why": "거꾸로", "score": 0.9},
        {"start": 28.0, "end": 40.0, "why": "정상", "score": 0.9},
    ]), analysis, _hl_cfg(target_duration=120))
    assert len(got) == 1
    assert "정상" in got[0].label


def test_target_duration_is_respected(analysis):
    """규칙 기반과 **같은 함수**로 길이를 맞춘다. 한쪽만 어긋나면 안 된다."""
    many = [{"start": t, "end": t + 15.0, "why": f"장면{t}", "score": 0.9}
            for t in range(0, 170, 20)]
    got = clips_from_decision(_decision(many), analysis, _hl_cfg(target_duration=40))
    total = sum(c.duration for c in got)
    assert total <= 40 * 1.10 + 0.01, f"목표 40초인데 {total:.1f}초"


def test_reason_becomes_the_clip_label(analysis):
    """왜 골랐는지가 검수 화면에 그대로 보여야 한다. AI 를 쓰는 이유의 절반이다."""
    got = clips_from_decision(
        _decision([{"start": 28.0, "end": 38.0, "why": "죽고 나서 어이없어함", "score": 0.9}]),
        analysis, _hl_cfg(target_duration=120))
    assert "죽고 나서 어이없어함" in got[0].label
    assert got[0].reason == "ai"


def test_clip_edges_snap_to_speech(analysis):
    """AI 는 초 단위로 대충 답한다. 스냅이 없으면 말 중간에서 시작한다."""
    raw_start = 28.4
    got = clips_from_decision(
        _decision([{"start": raw_start, "end": 36.0, "why": "가", "score": 0.9}]),
        analysis, _hl_cfg(target_duration=120, snap_to_speech=True))
    assert got[0].source_start != pytest.approx(raw_start)


# ------------------------------------------------------------------- 밈 옮기기

def _plan_with(clips):
    plan = EditPlan(source="/tmp/x.mp4",
                    media=MediaInfo(path="/tmp/x.mp4", duration=180.0,
                                    width=1920, height=1080))
    plan.clips = clips
    plan.relayout()
    return plan


def test_meme_moves_to_the_output_timeline():
    """AI 는 원본 시각으로 답한다. 결과물 시각으로 옮겨야 제자리에 뜬다."""
    plan = _plan_with([Clip(source_start=100.0, source_end=120.0)])
    got = memes_from_decision(
        _decision(memes=[{"at": 110.0, "text": "이게 왜 죽어", "big": True}]),
        plan, dict(Config().section("memes")))
    assert len(got) == 1
    assert got[0].start == pytest.approx(10.0)      # 클립 시작 100초 → 결과물 0초
    assert got[0].style == "MemeCenter"


def test_meme_in_a_cut_out_region_is_dropped():
    """잘려 나간 구간의 밈을 억지로 옮기면 엉뚱한 장면에 붙는다."""
    plan = _plan_with([Clip(source_start=100.0, source_end=120.0)])
    got = memes_from_decision(
        _decision(memes=[{"at": 5.0, "text": "여긴 잘렸음", "big": False}]),
        plan, dict(Config().section("memes")))
    assert got == []


def test_memes_piled_on_one_spot_are_thinned():
    """같은 자리에 몰리면 글자가 겹쳐서 못 읽는다."""
    plan = _plan_with([Clip(source_start=0.0, source_end=60.0)])
    got = memes_from_decision(_decision(memes=[
        {"at": 10.0, "text": "가", "big": False},
        {"at": 10.3, "text": "나", "big": False},
        {"at": 10.6, "text": "다", "big": False},
    ]), plan, dict(Config().section("memes")))
    assert len(got) == 1


# --------------------------------------------- 편집 전체가 AI 때문에 멈추면 안 된다

def _config(**ai):
    cfg = Config()
    for key, value in ai.items():
        cfg.set(f"ai.{key}", value)
    return cfg


def _fake_api(monkeypatch, payload):
    def fake(key, body, **kw):
        return {"content": [{"type": "text",
                             "text": json.dumps(payload, ensure_ascii=False)}],
                "usage": {"input_tokens": 9000, "output_tokens": 1200}}
    monkeypatch.setattr("gameedit.brain.call_api", fake)
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "sk-ant-fake")


PAYLOAD = {
    "highlights": [
        {"start": 28.0, "end": 40.0, "why": "죽고 나서 어이없어함", "score": 0.95},
        {"start": 88.0, "end": 100.0, "why": "로토무한테 지고 폭소", "score": 0.85},
    ],
    "memes": [{"at": 31.0, "text": "이게 왜 죽어", "big": True}],
    "emphasis_words": ["로토무"],
    "title": "로토무한테 지는 사람",
}


def test_ai_decisions_reach_the_finished_plan(analysis, monkeypatch):
    """붙였다는 걸 여기서 증명한다 — AI 가 고른 구간이 실제 계획에 들어간다."""
    _fake_api(monkeypatch, PAYLOAD)
    plan = build_plan(analysis, _config(enabled=True, limit_krw=0))

    assert plan.meta["ai"]["used"] is True
    assert plan.meta["ai"]["clip_count"] >= 1
    assert plan.meta["ai"]["title"] == "로토무한테 지는 사람"
    assert any("어이없어함" in c.label for c in plan.clips)
    assert any(m.text == "이게 왜 죽어" for m in plan.memes)
    # 실제로 쓴 돈이 남아야 한다
    assert plan.meta["cost"]["total_krw"] > 0


def test_emphasis_words_reach_the_subtitles(analysis, monkeypatch):
    _fake_api(monkeypatch, PAYLOAD)
    plan = build_plan(analysis, _config(enabled=True))
    assert "로토무" in plan.meta["highlight_words"]


def test_network_failure_still_produces_a_plan(analysis, monkeypatch):
    """인터넷이 끊겨도 편집은 나와야 한다. 이게 제일 중요하다."""
    def boom(*a, **kw):
        raise OSError("인터넷 없음")
    monkeypatch.setattr("gameedit.brain.call_api", boom)
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "sk-ant-fake")

    plan = build_plan(analysis, _config(enabled=True))
    assert plan.clips, "AI 가 실패했다고 편집이 비면 안 된다"
    assert plan.meta["ai"]["used"] is False
    assert "인터넷" in plan.meta["ai"]["error"]


def test_missing_key_falls_back_with_a_readable_reason(analysis, monkeypatch):
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "")
    plan = build_plan(analysis, _config(enabled=True))
    assert plan.clips
    assert "키" in plan.meta["ai"]["error"]


def test_spending_cap_blocks_the_call(analysis, monkeypatch):
    """상한을 넘을 것 같으면 아예 부르지 않는다. 자는 사이에 돈이 나가면 안 된다."""
    called = []
    monkeypatch.setattr("gameedit.brain.call_api",
                        lambda *a, **kw: called.append(1))
    monkeypatch.setattr("gameedit.brain.load_key", lambda explicit="": "sk-ant-fake")

    plan = build_plan(analysis, _config(enabled=True, limit_krw=0.01))
    assert not called, "상한을 넘는데도 API 를 불렀다"
    assert plan.clips
    assert "상한" in plan.meta["ai"]["error"]


def test_ai_off_by_default_costs_nothing(analysis):
    plan = build_plan(analysis, Config())
    assert plan.meta["ai"]["used"] is False
    assert "cost" not in plan.meta
    assert plan.clips


def test_ai_returning_nothing_falls_back_to_rules(analysis, monkeypatch):
    """불렀는데 빈 답이 와도 결과물은 나와야 한다."""
    _fake_api(monkeypatch, {"highlights": [], "memes": [],
                            "emphasis_words": [], "title": ""})
    plan = build_plan(analysis, _config(enabled=True))
    assert plan.clips
    assert plan.meta["ai"]["clip_count"] == 0
