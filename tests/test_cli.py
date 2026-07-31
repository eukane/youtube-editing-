import json

import pytest

from gameedit.cli import build_parser, main, parse_duration
from gameedit.config import Config, deep_merge
from gameedit.models import save_json, to_dict
from gameedit.plan import build_plan, load_plan
from gameedit.timeline import build_html


def test_parse_duration():
    assert parse_duration("480") == 480
    assert parse_duration("8m") == 480
    assert parse_duration("1h20m") == 4800
    assert parse_duration("00:08:30") == 510
    assert parse_duration("90s") == 90
    with pytest.raises(ValueError):
        parse_duration("나중에")


def test_deep_merge_keeps_untouched_defaults():
    merged = deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 9}})
    assert merged == {"a": {"x": 1, "y": 9}}


def test_config_dot_access_and_override():
    cfg = Config({"highlight": {"target_duration": 120}})
    assert cfg.get("highlight.target_duration") == 120
    assert cfg.get("highlight.min_clip") == 6.0  # 기본값 유지
    assert cfg.get("없는.키", "fallback") == "fallback"
    cfg.set("render.crf", 18)
    assert cfg.get("render.crf") == 18


def test_config_load_yaml(tmp_path):
    (tmp_path / "gameedit.yaml").write_text(
        "project:\n  name: 내채널\nhighlight:\n  target_duration: 300\n", encoding="utf-8")
    cfg = Config.discover(tmp_path)
    assert cfg.get("project.name") == "내채널"
    assert cfg.get("highlight.target_duration") == 300


def test_init_creates_config(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    path = tmp_path / "gameedit.yaml"
    assert path.exists()
    assert "highlight" in path.read_text(encoding="utf-8")
    assert main(["init", str(tmp_path)]) == 1  # 덮어쓰기 방지
    assert main(["init", str(tmp_path), "--force"]) == 0


def test_plan_round_trip_through_json(tmp_path, analysis):
    cfg = Config()
    plan = build_plan(analysis, cfg)
    path = save_json(plan, tmp_path / "plan.json")
    loaded = load_plan(path)

    assert len(loaded.clips) == len(plan.clips)
    assert len(loaded.memes) == len(plan.memes)
    assert len(loaded.subtitles) == len(plan.subtitles)
    assert loaded.duration == pytest.approx(plan.duration)
    assert loaded.clips[0].label == plan.clips[0].label


def test_hand_edited_plan_is_respected(tmp_path, analysis):
    plan = build_plan(analysis, Config())
    data = to_dict(plan)
    data["clips"] = data["clips"][:1]           # 편집자가 클립 하나만 남김
    (tmp_path / "plan.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    loaded = load_plan(tmp_path / "plan.json")
    assert len(loaded.clips) == 1
    assert loaded.clips[0].out_start == 0.0     # 다시 이어붙여진다


def test_analysis_json_round_trip(tmp_path, analysis):
    from gameedit.models import analysis_from_dict, load_json

    path = save_json(analysis, tmp_path / "analysis.json")
    loaded = analysis_from_dict(load_json(path))
    assert loaded.media.duration == analysis.media.duration
    assert len(loaded.transcript.segments) == len(analysis.transcript.segments)
    assert loaded.transcript.segments[0].words[0].text == analysis.transcript.segments[0].words[0].text
    assert loaded.audio.peaks == analysis.audio.peaks
    assert loaded.scene_density(28, 32) > 0


def test_timeline_html_report(analysis):
    plan = build_plan(analysis, Config())
    html = build_html(plan, analysis, title="테스트 채널")

    assert "<!doctype html>" in html
    assert "테스트 채널" in html
    assert "하이라이트 클립" in html
    assert plan.clips[0].label in html
    assert html.count("class=\"clip\"") >= len(plan.clips)


def test_cli_parser_accepts_all_commands():
    parser = build_parser()
    for argv in (["init"], ["doctor"], ["analyze", "a.mp4"], ["plan"], ["preview"],
                 ["render", "--dry-run"], ["auto", "a.mp4", "-t", "8m"], ["packs"]):
        args = parser.parse_args(argv)
        assert callable(args.func)


def test_cli_set_override(tmp_path):
    from gameedit.cli import load_config

    parser = build_parser()
    args = parser.parse_args(["plan", "--set", "highlight.target_duration=333",
                              "--set", "memes.enabled=false"])
    cfg = load_config(args)
    assert cfg.get("highlight.target_duration") == 333
    assert cfg.get("memes.enabled") is False


def test_packs_command_runs(capsys):
    assert main(["packs"]) == 0
    out = capsys.readouterr().out
    assert "death_again" in out


def test_add_meme_registers_file_into_pack(tmp_path):
    """gameedit add-meme 로 넣은 파일이 그대로 밈으로 로드되어야 한다."""
    from gameedit.memes import load_pack

    pack_dir = tmp_path / "mypack"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text("name: mypack\nmemes:\n", encoding="utf-8")
    source = tmp_path / "무야호.png"
    source.write_bytes(b"\x89PNG")

    rc = main(["add-meme", str(source), "--pack", str(pack_dir),
               "-t", "무야호", "-t", "신난다", "--placement", "center", "--duration", "2.5"])
    assert rc == 0
    assert (pack_dir / "images" / "무야호.png").exists()

    memes = {m.id: m for m in load_pack(pack_dir).memes}
    assert memes["무야호"].kind == "image"
    assert memes["무야호"].triggers == ["무야호", "신난다"]
    assert memes["무야호"].placement == "center"
    assert memes["무야호"].matches("와 신난다 진짜") == "신난다"


def test_add_meme_audio_goes_to_sfx(tmp_path):
    from gameedit.memes import load_pack

    pack_dir = tmp_path / "p"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text("name: p\nmemes:\n", encoding="utf-8")
    source = tmp_path / "두둥.mp3"
    source.write_bytes(b"ID3")

    assert main(["add-meme", str(source), "--pack", str(pack_dir), "-e", "hype"]) == 0
    meme = load_pack(pack_dir).memes[0]
    assert meme.kind == "audio"
    assert meme.sfx == "sfx/두둥.mp3"
    assert meme.events == ["hype"]
    assert meme.resolved_sfx() is not None


def test_add_meme_rejects_unknown_format(tmp_path, capsys):
    pack_dir = tmp_path / "p"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text("name: p\nmemes:\n", encoding="utf-8")
    source = tmp_path / "메모.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["add-meme", str(source), "--pack", str(pack_dir)])
