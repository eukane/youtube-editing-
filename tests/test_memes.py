from gameedit.config import Config
from gameedit.highlights import is_generic_label
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


def test_no_clip_labels_in_the_finished_video(analysis):
    """완성본에 '하이라이트 3' 이 박혀 있으면 검수용 영상처럼 보인다."""
    plan = build_plan(analysis, Config())
    assert [c for c in plan.memes if c.meme_id == "clip_label"] == []


def test_clip_labels_when_asked_are_content_titles_only(analysis):
    """켜더라도 자동 번호는 빼고 내용에서 뽑은 제목만 낸다."""
    cfg = Config()
    cfg.set("memes.clip_intro_label", True)
    plan = build_plan(analysis, cfg)

    labels = [c for c in plan.memes if c.meme_id == "clip_label"]
    assert labels, "내용 기반 제목까지 사라지면 안 된다"
    for cue in labels:
        assert not is_generic_label(cue.text), f"자동 번호가 화면에 나갔다: {cue.text}"


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


# ---------------------------------------------------------------- 자동 스캔

def test_parse_asset_filename_variants():
    from gameedit.memes import parse_asset_filename

    assert parse_asset_filename("무야호.png")["triggers"] == ["무야호"]
    multi = parse_asset_filename("죽었,사망,뒤졌.png")
    assert multi["triggers"] == ["죽었", "사망", "뒤졌"]

    opts = parse_asset_filename("개킹받네@right@2.5.gif")
    assert opts["triggers"] == ["개킹받네"]
    assert opts["placement"] == "right"
    assert opts["duration"] == 2.5

    ev = parse_asset_filename("hype@_.mp3")
    assert ev["events"] == ["hype"] and ev["triggers"] == []


def _touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_scan_asset_dir_reads_files_as_memes(tmp_path):
    from gameedit.memes import scan_asset_dir

    _touch(tmp_path / "무야호.png")
    _touch(tmp_path / "무야호.mp3")          # 같은 이름 → 위 그림의 효과음
    _touch(tmp_path / "죽었,사망@right@2.5.png")
    _touch(tmp_path / "silence@_.wav")       # 소리만 있는 밈
    _touch(tmp_path / "읽지않음.txt")

    memes = {m.id: m for m in scan_asset_dir(tmp_path)}
    assert set(memes) == {"무야호", "죽었", "silence"}

    assert memes["무야호"].kind == "image"
    assert memes["무야호"].sfx.endswith("무야호.mp3")     # 그림+소리 자동 짝짓기
    assert memes["죽었"].triggers == ["죽었", "사망"]
    assert memes["죽었"].placement == "right"
    assert memes["죽었"].duration == 2.5
    assert memes["silence"].kind == "audio"
    assert memes["silence"].events == ["silence"]
    assert memes["silence"].sfx.endswith("silence@_.wav")


def test_scan_asset_dir_ids_do_not_collide(tmp_path):
    from gameedit.memes import scan_asset_dir

    _touch(tmp_path / "웃음.png")
    _touch(tmp_path / "sub" / "웃음.gif")
    ids = [m.id for m in scan_asset_dir(tmp_path)]
    assert len(ids) == len(set(ids)) == 2


def test_scan_asset_dir_missing_folder():
    from gameedit.memes import scan_asset_dir

    assert scan_asset_dir("/존재하지/않는/폴더") == []


def test_load_packs_includes_scanned_assets(tmp_path):
    _touch(tmp_path / "대박.png")
    memes = load_packs(["default"], asset_dirs=[str(tmp_path)])
    scanned = [m for m in memes if m.id == "대박"]
    assert scanned and scanned[0].kind == "image"
    # 직접 넣은 밈이 기본 텍스트 밈보다 우선순위가 높아야 한다
    assert scanned[0].weight > max(m.weight for m in memes if m.pack == "default")


# ---------------------------------------------------------------- 전환 카드

def test_humanize_gap():
    from gameedit.memes import humanize_gap

    assert humanize_gap(12) == "10초 후"
    assert humanize_gap(185) == "3분 후"
    assert humanize_gap(3600) == "1시간 후"
    assert humanize_gap(7800) == "2시간 후"


def test_timeskip_card_between_distant_clips(analysis):
    cfg = Config()
    cfg.set("highlight.target_duration", 40)   # 클립이 짧아져 사이가 크게 벌어진다
    cfg.set("memes.timeskip_min", 20)
    plan = build_plan(analysis, cfg)

    gaps = [n.source_start - p.source_end for p, n in zip(plan.clips, plan.clips[1:])]
    assert any(g >= 20 for g in gaps), "테스트 전제: 클립 사이가 벌어져 있어야 한다"

    cards = [c for c in plan.memes if c.trigger == "timeskip"]
    assert cards, "멀리 떨어진 클립 사이에 전환 카드가 없다"
    for card in cards:
        assert card.show_text and card.text.endswith("후")
        assert card.style == "Card"
        # 카드가 뜨는 클립에는 라벨을 겹쳐 넣지 않는다
        labels = [c for c in plan.memes
                  if c.meme_id == "clip_label" and abs(c.start - card.start) < 1.0]
        assert not labels


def test_timeskip_disabled(analysis):
    cfg = Config()
    cfg.set("memes.timeskip_min", 0)
    plan = build_plan(analysis, cfg)
    assert not [c for c in plan.memes if c.trigger == "timeskip"]


# --------------------------------------------------- 설정 없이 폴더만으로 등록

def test_default_asset_dirs_are_scanned_without_any_config(tmp_path, monkeypatch):
    """설정을 못 만지는 사람도 폴더에 파일만 넣으면 밈이 늘어나야 한다."""
    from gameedit import memes as M

    drop = tmp_path / "storage" / "shared" / "gameedit-memes"
    drop.mkdir(parents=True)
    (drop / "무야호.png").write_bytes(b"png")
    (drop / "무야호.mp3").write_bytes(b"mp3")

    monkeypatch.setattr(M, "DEFAULT_ASSET_DIRS", (str(drop),))
    loaded = M.load_packs([])                       # packs 도 asset_dirs 도 없이

    hit = [m for m in loaded if "무야호" in m.triggers]
    assert hit, "폴더에 넣은 파일이 밈으로 안 잡혔다"
    assert hit[0].asset and hit[0].sfx              # 같은 이름 소리도 짝지어짐


def test_default_dirs_do_not_duplicate_explicit_ones(tmp_path, monkeypatch):
    from gameedit import memes as M

    drop = tmp_path / "memes"
    drop.mkdir()
    (drop / "무야호.png").write_bytes(b"png")

    monkeypatch.setattr(M, "DEFAULT_ASSET_DIRS", (str(drop),))
    loaded = M.load_packs([], asset_dirs=[str(drop)])
    assert len([m for m in loaded if "무야호" in m.triggers]) == 1


def test_missing_default_dirs_are_ignored(monkeypatch):
    from gameedit import memes as M

    monkeypatch.setattr(M, "DEFAULT_ASSET_DIRS", ("/없는/폴더/입니다",))
    assert M.default_asset_dirs() == []
    assert M.load_packs(["default"])                # 기본 팩은 그대로 나온다


def test_no_review_copy_language_in_the_default_pack():
    """'여기가 하이라이트' 같은 해설 문구는 완성본에 편집자가 주석 단 꼴이다."""
    banned = ("하이라이트", "구간", "클립", "편집자", "여기가")
    for meme in load_packs(["default"]):
        for word in banned:
            assert word not in meme.text, f"{meme.id} 의 문구가 검수용이다: {meme.text}"


def test_hype_reactions_vary():
    """텐션 순간마다 같은 문구가 뜨면 금방 질린다."""
    hype = [m for m in load_packs(["default"]) if "hype" in m.events]
    assert len(hype) >= 3
    assert len({m.text for m in hype}) == len(hype)


def test_generated_sfx_are_wired_up():
    """합성해 넣은 효과음이 실제로 밈에 붙는지."""
    memes = load_packs(["default"])
    with_sfx = [m for m in memes if m.resolved_sfx()]
    assert len(with_sfx) >= 12, "효과음이 붙은 밈이 너무 적다"
    for meme in with_sfx:
        path = meme.resolved_sfx()
        assert path.exists() and path.stat().st_size > 2000, f"{path} 가 비어 있다"
