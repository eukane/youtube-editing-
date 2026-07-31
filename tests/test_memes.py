from gameedit.config import Config
from gameedit.memes import MemeDef, _enforce_spacing, load_pack, load_packs
from gameedit.models import MemeCue
from gameedit.plan import build_plan


def test_builtin_pack_loads():
    memes = load_packs(["default"])
    assert len(memes) >= 10
    ids = {m.id for m in memes}
    assert "death_again" in ids
    assert all(m.duration > 0 for m in memes)
    assert any("hype" in m.events for m in memes), "오디오 피크용 밈이 필요하다"
    assert any("silence" in m.events for m in memes)


def test_pack_file_round_trip(tmp_path):
    (tmp_path / "pack.yaml").write_text(
        "name: t\nmemes:\n"
        "  - id: boom\n    text: 쾅\n    triggers: ['터졌']\n    duration: 1.5\n",
        encoding="utf-8",
    )
    pack = load_pack(tmp_path)
    assert pack.memes[0].id == "boom"
    assert pack.memes[0].matches("아 터졌다") == "터졌"
    assert pack.memes[0].matches("평온함") is None


def test_missing_asset_falls_back_to_text(tmp_path):
    meme = MemeDef(id="x", kind="image", asset="nope.png", text="대체 문구",
                   base_dir=tmp_path, triggers=["가나다"])
    from gameedit.memes import _to_cue

    cue = _to_cue(meme, 1.0, 1.0, "가나다")
    assert cue.kind == "text"
    assert cue.text == "대체 문구"


def test_plan_memes_places_on_output_timeline(analysis):
    cfg = Config()
    cfg.set("highlight.target_duration", 90)
    plan = build_plan(analysis, cfg)

    assert plan.memes, "밈이 하나도 배치되지 않았다"
    for cue in plan.memes:
        assert 0 <= cue.start <= plan.duration + 1
        assert cue.duration > 0
    # 대사 기반 밈은 원본 시각이 살아 있는 클립 안에 있어야 한다
    for cue in plan.memes:
        if cue.trigger in ("clip_start", "peak", "silence"):
            continue
        assert plan.map_time(cue.source_start) is not None


def test_clip_labels_are_emitted(analysis):
    cfg = Config()
    plan = build_plan(analysis, cfg)
    labels = [c for c in plan.memes if c.meme_id == "clip_label"]
    assert len(labels) == len(plan.clips)


def test_memes_can_be_disabled(analysis):
    cfg = Config()
    cfg.set("memes.enabled", False)
    plan = build_plan(analysis, cfg)
    assert plan.memes == []


def test_spacing_rules():
    cues = [MemeCue(start=t, duration=1.0, meme_id=f"m{i%2}") for i, t in
            enumerate([0.0, 0.5, 3.0, 6.0, 30.0])]
    kept = _enforce_spacing(cues, cooldown=5.0, min_gap=1.5, max_per_minute=100)
    starts = [c.start for c in kept]
    assert 0.5 not in starts   # min_gap 위반
    assert 3.0 not in starts   # 같은 밈(m0) 쿨다운 위반
    assert starts == [0.0, 6.0, 30.0]
    for a, b in zip(kept, kept[1:]):
        assert b.start - a.start >= 1.5


def test_max_per_minute_limits_spam():
    cues = [MemeCue(start=i * 2.0, duration=1.0, meme_id=f"m{i}") for i in range(20)]
    kept = _enforce_spacing(cues, cooldown=0.0, min_gap=0.0, max_per_minute=3)
    assert len(kept) <= 4


def test_per_meme_cooldown_is_honoured():
    """팩에 적어 둔 밈별 쿨다운이 전역 설정보다 길면 그쪽을 따른다."""
    cues = [MemeCue(start=0.0, duration=1.0, meme_id="hype", cooldown=45.0),
            MemeCue(start=10.0, duration=1.0, meme_id="hype", cooldown=45.0),
            MemeCue(start=50.0, duration=1.0, meme_id="hype", cooldown=45.0)]
    kept = _enforce_spacing(cues, cooldown=5.0, min_gap=1.0, max_per_minute=0)
    assert [c.start for c in kept] == [0.0, 50.0]


def test_higher_priority_meme_wins_a_close_slot():
    cues = [MemeCue(start=3.0, duration=1.0, meme_id="auto", priority=1.0),
            MemeCue(start=3.6, duration=1.0, meme_id="dialogue", priority=1.4)]
    kept = _enforce_spacing(cues, cooldown=0.0, min_gap=1.5, max_per_minute=0)
    assert [c.meme_id for c in kept] == ["dialogue"]


def test_pack_names_accept_plain_string(tmp_path):
    """--set memes.packs=default 처럼 문자열 하나만 와도 동작해야 한다."""
    (tmp_path / "pack.yaml").write_text("name: s\nmemes:\n  - id: a\n    text: A\n",
                                        encoding="utf-8")
    assert len(load_packs("default")) >= 10
    assert len(load_packs([], str(tmp_path))) == 1
