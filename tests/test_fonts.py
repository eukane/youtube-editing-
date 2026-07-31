"""한글 폰트 자동 대체."""

from gameedit.config import Config
from gameedit.fonts import resolve_font
from gameedit.models import SubtitleCue
from gameedit.subtitles import build_ass

WINDOWS = ["Malgun Gothic", "Gulim", "Batang"]
LINUX = ["Unifont-JP", "WenQuanYi Zen Hei", "Noto Sans KR"]


def test_configured_font_is_kept_when_installed():
    assert resolve_font("Malgun Gothic", available=WINDOWS) == "Malgun Gothic"


def test_falls_back_to_a_preferred_installed_font():
    # 리눅스에 맑은 고딕은 없으니 Noto Sans KR 로
    assert resolve_font("Malgun Gothic", available=LINUX) == "Noto Sans KR"


def test_unifont_is_last_resort():
    # 선호 목록에 아무것도 없으면 비트맵 폰트(Unifont)보다 다른 걸 고른다
    assert resolve_font("없는폰트", available=["Unifont-JP", "WenQuanYi Zen Hei"]) \
        == "WenQuanYi Zen Hei"
    assert resolve_font("없는폰트", available=["Unifont-JP"]) == "Unifont-JP"


def test_unknown_environment_keeps_setting():
    """폰트 목록을 조회할 수 없는 환경(윈도우 등)에서는 설정값을 건드리지 않는다."""
    assert resolve_font("맑은 고딕", available=[]) == "맑은 고딕"


def test_ass_uses_resolved_font(monkeypatch):
    monkeypatch.setattr("gameedit.subtitles.resolve_font", lambda f: "Noto Sans KR")
    cfg = dict(Config().section("subtitles"), font="없는폰트")
    text = build_ass([SubtitleCue(start=0, end=1, lines=["가"])], [], cfg)
    assert "Noto Sans KR" in text
    assert "없는폰트" not in text


def test_font_fallback_can_be_disabled(monkeypatch):
    monkeypatch.setattr("gameedit.subtitles.resolve_font", lambda f: "다른폰트")
    cfg = dict(Config().section("subtitles"), font="내폰트", font_fallback=False)
    text = build_ass([SubtitleCue(start=0, end=1, lines=["가"])], [], cfg)
    assert "내폰트" in text
