"""AI 비용 계산.

제일 중요한 성질 두 가지.
  · **돌리기 전에** 얼마 나올지 보여 준다. 모르는 채로 돌리면 아무도 못 쓴다
  · **상한을 넘으면 멈춘다.** 넘고 나서 알려 주면 이미 늦었다

금액은 '정확한 청구액' 이 아니라 '자릿수' 를 맞추는 게 목적이다.
"""

import pytest

from gameedit.cost import (DEFAULT_MODEL, Estimate, Ledger, estimate_edit,
                           image_tokens, krw, text_tokens)


# ------------------------------------------------------------- 토큰 환산

def test_image_tokens_scale_with_pixels():
    """작게 보낼수록 싸다. 전체를 훑을 때 작게 보내는 근거."""
    assert image_tokens(320, 180) == pytest.approx(76, abs=2)
    assert image_tokens(1280, 720) == pytest.approx(1228, abs=2)
    # 16배 차이
    assert image_tokens(1280, 720) / image_tokens(320, 180) == pytest.approx(16, abs=0.5)


def test_zero_size_image_is_free():
    assert image_tokens(0, 0) == 0


def test_korean_text_tokens():
    assert text_tokens("") == 0
    assert 0 < text_tokens("아니 이게 왜 죽어") < 20


# --------------------------------------------------------------- 금액

def test_output_is_more_expensive_than_input():
    """나오는 말이 다섯 배 비싸다. 이걸 뒤집으면 예상이 크게 틀린다."""
    a = krw(DEFAULT_MODEL, 1_000_000, 0)
    b = krw(DEFAULT_MODEL, 0, 1_000_000)
    assert b == pytest.approx(a * 5, rel=0.01)


def test_cheaper_model_costs_less():
    same = dict(input_tokens=100_000, output_tokens=10_000)
    assert krw("claude-haiku-4-5", **same) < krw("claude-sonnet-5", **same)
    assert krw("claude-sonnet-5", **same) < krw("claude-opus-5", **same)


def test_unknown_model_falls_back_instead_of_crashing():
    assert krw("모르는모델", 1000, 100) == krw(DEFAULT_MODEL, 1000, 100)


# ------------------------------------------------------------- 예상 비용

def test_dialogue_only_is_cheap():
    """1시간 영상 대사 분석이 몇백 원 안쪽이어야 한다."""
    est = estimate_edit(3600.0, {"mode": "dialogue", "model": "claude-sonnet-5"})
    assert est.images == 0
    assert 0 < est.krw < 500, f"{est.krw:.0f}원"


def test_watching_the_screen_costs_much_more():
    """화면까지 보면 자릿수가 달라진다. 사용자가 이걸 알고 골라야 한다."""
    cfg = {"model": "claude-sonnet-5"}
    talk = estimate_edit(3600.0, dict(cfg, mode="dialogue"))
    watch = estimate_edit(3600.0, dict(cfg, mode="vision"))
    assert watch.images > 3000                      # 1초에 한 장
    assert watch.krw > talk.krw * 3


def test_looking_less_often_is_cheaper():
    cfg = {"mode": "vision", "model": "claude-sonnet-5"}
    dense = estimate_edit(600.0, dict(cfg, frame_every=1.0))
    sparse = estimate_edit(600.0, dict(cfg, frame_every=4.0))
    assert sparse.krw < dense.krw
    assert sparse.images < dense.images


def test_smaller_scan_size_is_cheaper():
    cfg = {"mode": "vision", "model": "claude-sonnet-5", "frame_every": 1.0}
    small = estimate_edit(600.0, dict(cfg, scan_width=320, scan_height=180))
    big = estimate_edit(600.0, dict(cfg, scan_width=640, scan_height=360))
    assert small.krw < big.krw


def test_zero_length_video_costs_almost_nothing():
    est = estimate_edit(0.0, {"mode": "dialogue"})
    assert est.krw < 20


def test_estimate_summary_is_readable():
    est = estimate_edit(600.0, {"mode": "vision", "model": "claude-sonnet-5"})
    text = est.summary()
    assert "예상" in text and "원" in text and "화면" in text


# ------------------------------------------------------------- 내역서

def test_ledger_records_each_step_separately():
    """합계만 보여 주면 '왜 이만큼 나왔지' 를 알 수 없다."""
    led = Ledger()
    led.add("대사 분석", "claude-sonnet-5", input_tokens=20_000, output_tokens=3_000)
    led.add("화면 훑기", "claude-sonnet-5", input_tokens=280_000, images=3600)
    assert len(led.steps) == 2
    assert led.total_images == 3600
    assert led.total_krw == pytest.approx(led.steps[0].krw + led.steps[1].krw)


def test_ledger_reports_what_it_was_spent_on():
    led = Ledger(limit_krw=500)
    led.add("인터넷 검색", "claude-sonnet-5", input_tokens=5_000, output_tokens=500,
            note="'로토무 타입' 검색 → 3분12초 자막에 반영")
    lines = led.report_lines()
    assert any("로토무 타입" in ln for ln in lines)
    assert any("합계" in ln for ln in lines)
    assert any("상한" in ln for ln in lines)


def test_limit_blocks_the_next_step_before_spending():
    """넘고 나서 알려 주면 이미 늦었다. 쓰기 전에 막아야 한다."""
    led = Ledger(limit_krw=500)
    led.add("대사 분석", "claude-sonnet-5", input_tokens=20_000, output_tokens=3_000)
    assert led.total_krw == pytest.approx(147, abs=2)      # 500원 중 147원 씀
    assert not led.would_exceed(300)                        # 아직 여유
    assert led.would_exceed(400)                            # 이건 넘는다
    assert led.remaining_krw() == pytest.approx(353, abs=2)


def test_no_limit_means_never_blocked():
    led = Ledger(limit_krw=0)
    led.add("x", DEFAULT_MODEL, input_tokens=10_000_000)
    assert not led.would_exceed(1_000_000)
    assert led.remaining_krw() == float("inf")


def test_empty_ledger_says_zero():
    assert "0원" in Ledger().report_lines()[0]


def test_ledger_dict_has_what_the_screen_needs():
    led = Ledger(limit_krw=500)
    led.add("대사 분석", "claude-sonnet-5", input_tokens=20_000, output_tokens=3_000)
    data = led.as_dict()
    assert data["limit_krw"] == 500
    assert data["steps"][0]["name"] == "대사 분석"
    assert data["lines"]
