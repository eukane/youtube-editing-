"""'짜깁기'가 아니라 '편집'이 되었는지 검증.

각 규칙이 결과물에서 실제로 드러나는지를 본다.
"""

import pytest

from gameedit.config import Config
from gameedit.editing import (apply_editing, apply_speed_ramps, bridge_gaps,
                              enforce_cut_length, pick_cold_open, remove_dead_air,
                              split_dead_air, trim_flat_tail)
from gameedit.models import Clip
from gameedit.plan import build_plan


@pytest.fixture
def ecfg():
    return Config().section("editing")


# ------------------------------------------------------------- 점프컷 (죽은 시간)

def test_dead_air_inside_a_clip_is_cut_out():
    clip = Clip(source_start=0.0, source_end=20.0, score=0.8)
    silences = [(5.0, 9.0)]          # 한가운데 4초 정적

    pieces = split_dead_air(clip, silences, min_silence=0.5, keep=0.1, min_piece=0.8)

    assert len(pieces) == 2
    assert pieces[0].source_end == pytest.approx(5.1)     # 말꼬리 0.1초는 남긴다
    assert pieces[1].source_start == pytest.approx(8.9)
    total = sum(p.source_duration for p in pieces)
    assert total < clip.source_duration - 3.5             # 정적이 실제로 빠졌다


def test_short_pauses_survive_but_long_ones_do_not():
    clip = Clip(source_start=0.0, source_end=30.0)
    silences = [(4.0, 4.2), (10.0, 13.0)]                 # 0.2초 / 3초

    pieces = split_dead_air(clip, silences, min_silence=0.5, keep=0.1, min_piece=0.8)
    assert len(pieces) == 2                               # 0.2초짜리는 안 자른다
    kept = [(p.source_start, p.source_end) for p in pieces]
    assert not any(s < 11.5 < e for s, e in kept)         # 3초짜리 한복판은 사라졌다


def test_all_silent_clip_is_left_alone():
    clip = Clip(source_start=0.0, source_end=5.0)
    assert split_dead_air(clip, [(0.0, 5.0)], min_silence=0.5, keep=0.1,
                          min_piece=0.8) == [clip]


def test_piece_count_is_capped_for_the_phone(ecfg):
    """조각이 수천 개가 되면 폰에서 렌더가 불가능해진다."""
    clips = [Clip(source_start=0.0, source_end=600.0, score=0.7)]
    silences = [(t + 0.4, t + 1.0) for t in range(0, 600, 2)]   # 300개의 짧은 정적

    out = remove_dead_air(clips, silences, dict(ecfg, max_pieces=50))
    assert len(out) <= 50
    assert len(out) > 1, "상한 때문에 아예 안 자르면 안 된다"


def test_dead_air_can_be_turned_off(ecfg):
    clips = [Clip(source_start=0.0, source_end=20.0)]
    out = remove_dead_air(clips, [(5.0, 9.0)], dict(ecfg, dead_air=False))
    assert out == clips


# ------------------------------------------------------------------- 빨리감기

def test_quiet_wordless_stretch_is_sped_up_not_deleted(analysis, ecfg):
    """이동 구간을 통째로 자르면 맥락이 끊긴다. 빨리 감아서 남긴다."""
    quiet = Clip(source_start=60.0, source_end=70.0, score=0.1)   # 대사 없는 구간
    loud = Clip(source_start=28.0, source_end=34.0, score=0.9)

    apply_speed_ramps([quiet, loud], analysis, ecfg)

    assert quiet.speed > 1.0 and "speedup" in quiet.effects
    assert loud.speed == 1.0, "센 장면을 빨리 감으면 안 된다"
    assert quiet.source_duration == 10.0          # 원본은 그대로
    assert quiet.duration < 10.0                  # 결과물에서만 짧아진다


def test_speech_is_never_sped_up(analysis, ecfg):
    talking = Clip(source_start=28.0, source_end=34.0, score=0.0)
    apply_speed_ramps([talking], analysis, ecfg)
    assert talking.speed == 1.0


def test_speed_changes_the_output_timeline():
    clip = Clip(source_start=10.0, source_end=20.0, speed=2.0, out_start=5.0)
    assert clip.duration == pytest.approx(5.0)
    assert clip.to_out(10.0) == pytest.approx(5.0)
    assert clip.to_out(20.0) == pytest.approx(10.0)


# -------------------------------------------------------------------- 콜드오픈

def test_cold_open_puts_the_best_moment_first(ecfg):
    clips = [Clip(source_start=0.0, source_end=20.0, score=0.2),
             Clip(source_start=100.0, source_end=130.0, score=0.95)]

    hook = pick_cold_open(clips, ecfg)
    assert len(hook) == 1
    assert hook[0].source_start == 100.0                   # 제일 센 장면
    assert hook[0].duration <= ecfg["cold_open_max"]
    assert "punch" in hook[0].effects


def test_cold_open_is_skipped_when_there_is_only_one_clip(ecfg):
    assert pick_cold_open([Clip(source_start=0.0, source_end=9.0, score=1.0)], ecfg) == []


def test_cold_open_can_be_turned_off(ecfg):
    clips = [Clip(source_start=0.0, source_end=20.0, score=0.2),
             Clip(source_start=100.0, source_end=130.0, score=0.9)]
    assert pick_cold_open(clips, dict(ecfg, cold_open=False)) == []


def test_long_intro_is_built_from_several_scenes(ecfg):
    """30초짜리 도입부는 한 장면을 30초 트는 게 아니라 여러 장면을 몰아 보여 준다."""
    clips = [Clip(source_start=i * 100.0, source_end=i * 100.0 + 40.0, score=0.9 - i * 0.1)
             for i in range(5)]

    hook = pick_cold_open(clips, dict(ecfg, cold_open_seconds=30.0, cold_open_pieces=4),
                          source_duration=600.0)
    assert len(hook) == 4
    assert sum(c.duration for c in hook) == pytest.approx(30.0)
    assert [c.source_start for c in hook] == [0.0, 100.0, 200.0, 300.0]   # 센 순서대로
    assert all(c.reason == "coldopen" for c in hook)


def test_intro_is_cut_fresh_from_the_source_not_from_the_fragments(ecfg):
    """본편은 점프컷으로 잘게 쪼개져 있다. 그 조각을 쓰면 길이가 안 나온다."""
    clips = [Clip(source_start=100.0 + i * 3.0, source_end=102.0 + i * 3.0, score=0.9)
             for i in range(4)]                       # 2초짜리 조각들
    clips.append(Clip(source_start=400.0, source_end=402.0, score=0.5))

    hook = pick_cold_open(clips, dict(ecfg, cold_open_seconds=16.0, cold_open_pieces=2),
                          source_duration=600.0)
    assert sum(c.duration for c in hook) == pytest.approx(16.0), "조각 길이에 갇혔다"
    assert all(c.source_duration == 8.0 for c in hook)


def test_intro_does_not_repeat_the_same_moment(ecfg):
    """붙어 있는 조각 다섯 개를 뽑으면 같은 장면을 다섯 번 보여 주게 된다."""
    clips = [Clip(source_start=100.0 + i * 2.0, source_end=102.0 + i * 2.0, score=0.9)
             for i in range(6)]                       # 전부 같은 장면의 조각
    clips.append(Clip(source_start=500.0, source_end=510.0, score=0.4))

    hook = pick_cold_open(clips, dict(ecfg, cold_open_seconds=20.0, cold_open_pieces=4),
                          source_duration=600.0)
    starts = [c.source_start for c in hook]
    assert len(starts) == len(set(starts))
    for a, b in zip(sorted(starts), sorted(starts)[1:]):
        assert b - a >= 5.0, f"도입부에 같은 장면이 두 번 들어갔다: {starts}"


def test_intro_never_runs_past_the_end_of_the_source(ecfg):
    clips = [Clip(source_start=95.0, source_end=100.0, score=0.9),
             Clip(source_start=10.0, source_end=20.0, score=0.5)]
    hook = pick_cold_open(clips, dict(ecfg, cold_open_seconds=40.0, cold_open_pieces=2),
                          source_duration=100.0)
    assert all(c.source_end <= 100.0 for c in hook)


# ------------------------------------------------------------------ 끝맺음

def test_flat_ending_is_trimmed(ecfg):
    clips = [Clip(source_start=0.0, source_end=10.0, score=0.9),
             Clip(source_start=20.0, source_end=30.0, score=0.8),
             Clip(source_start=40.0, source_end=50.0, score=0.85),
             Clip(source_start=60.0, source_end=70.0, score=0.02)]   # 밋밋한 마무리

    out = trim_flat_tail(clips, ecfg)
    assert len(out) == 3
    assert out[-1].score == 0.85


def test_strong_ending_is_kept(ecfg):
    clips = [Clip(source_start=0.0, source_end=10.0, score=0.5),
             Clip(source_start=20.0, source_end=30.0, score=0.5),
             Clip(source_start=40.0, source_end=50.0, score=0.9)]
    assert len(trim_flat_tail(clips, ecfg)) == 3


# ---------------------------------------------------------- 전체 파이프라인

def test_editing_pass_changes_a_stitch_into_an_edit(analysis, ecfg):
    # 55~80 구간에는 fixture 의 무음(60~70)이 들어 있다
    selected = [Clip(source_start=20.0, source_end=45.0, score=0.9),
                Clip(source_start=55.0, source_end=80.0, score=0.6)]
    out = apply_editing([Clip(**vars(c)) for c in selected], analysis, ecfg)

    assert out[0].reason == "coldopen", "제일 센 장면이 맨 앞에 와야 한다"
    assert len(out) > len(selected) + 1, "점프컷이 하나도 안 일어났다"
    assert out[1:] == sorted(out[1:], key=lambda c: c.source_start)
    # 10초짜리 무음이 결과물에 그대로 남아 있으면 안 된다
    assert not any(c.source_start < 65.0 < c.source_end for c in out)


def test_editing_can_be_turned_off_entirely(analysis, ecfg):
    clips = [Clip(source_start=20.0, source_end=45.0, score=0.9)]
    assert apply_editing(clips, analysis, dict(ecfg, enabled=False)) == clips


def test_plan_records_what_the_editor_did(analysis):
    plan = build_plan(analysis, Config())
    summary = plan.meta["editing"]
    assert summary["cold_open"] is True
    assert summary["jump_cuts"] >= 1
    assert plan.meta["selected_count"] <= len(plan.clips)


def test_output_is_tighter_than_the_raw_selection(analysis):
    """편집을 켜면 같은 소재로 더 짧고 촘촘한 결과가 나와야 한다."""
    loose = Config()
    loose.set("editing.enabled", False)
    tight = Config()

    a = build_plan(analysis, loose)
    b = build_plan(analysis, tight)
    assert len(b.clips) > len(a.clips), "컷이 더 잘게 나뉘어야 한다"


def test_subtitles_follow_a_repeated_scene(analysis):
    """콜드오픈으로 같은 장면이 두 번 나오면 자막도 두 번 나와야 한다."""
    from gameedit.models import EditPlan
    from gameedit.subtitles import build_subtitle_cues

    plan = EditPlan(source=analysis.media.path, media=analysis.media)
    plan.clips = [                                   # 28~31 구간을 앞에서 한 번 더
        Clip(source_start=28.0, source_end=31.0, reason="coldopen"),
        Clip(source_start=25.0, source_end=45.0),
    ]
    plan.relayout()

    assert len(plan.map_all_ranges(28.5, 29.5)) == 2
    cues = build_subtitle_cues(plan, analysis, Config().section("subtitles"))
    repeats = [c for c in cues if c.source_start == 28.0]
    assert len(repeats) == 2, "콜드오픈 구간에 자막이 안 붙었다"
    assert repeats[0].start < plan.clips[1].out_start <= repeats[1].start


# ---------------------------------------------------------------- 이어붙이기

def test_close_highlights_are_bridged_not_hard_cut(ecfg):
    """20초 떨어진 두 장면을 하드컷으로 붙이면 '언제 저기 갔지' 가 된다."""
    clips = [Clip(source_start=0.0, source_end=10.0, score=0.8),
             Clip(source_start=30.0, source_end=40.0, score=0.8)]

    out = bridge_gaps(clips, ecfg)
    assert len(out) == 3
    bridge = out[1]
    assert bridge.reason == "bridge"
    assert (bridge.source_start, bridge.source_end) == (10.0, 30.0)   # 틈을 그대로 채운다
    assert bridge.speed > 1.0
    assert bridge.duration < 4.0, "20초가 그대로 들어가면 늘어진다"


def test_far_apart_highlights_are_still_cut(ecfg):
    """5분 떨어져 있으면 이어 붙일 게 아니라 잘라내야 한다."""
    clips = [Clip(source_start=0.0, source_end=10.0),
             Clip(source_start=310.0, source_end=320.0)]
    assert len(bridge_gaps(clips, ecfg)) == 2


def test_tiny_gaps_are_not_bridged(ecfg):
    clips = [Clip(source_start=0.0, source_end=10.0),
             Clip(source_start=10.4, source_end=20.0)]
    assert len(bridge_gaps(clips, ecfg)) == 2


def test_bridges_survive_the_jump_cut_pass(analysis, ecfg):
    """이어붙인 구간을 점프컷이 도로 잘라 버리면 의미가 없다."""
    selected = [Clip(source_start=50.0, source_end=58.0, score=0.8),
                Clip(source_start=72.0, source_end=80.0, score=0.8)]
    out = apply_editing(selected, analysis, ecfg)

    bridges = [c for c in out if c.reason == "bridge"]
    assert len(bridges) == 1
    # fixture 의 무음(60~70)이 이 구간 한복판에 있지만 다리는 온전해야 한다
    assert (bridges[0].source_start, bridges[0].source_end) == (58.0, 72.0)


def test_bridging_can_be_turned_off(ecfg):
    clips = [Clip(source_start=0.0, source_end=10.0),
             Clip(source_start=30.0, source_end=40.0)]
    assert len(bridge_gaps(clips, dict(ecfg, bridge_gaps=False))) == 2


# ------------------------------------------------------- 평균 컷 길이 맞추기

def test_over_cut_pieces_are_merged_back_to_the_target():
    """숨을 자주 쉬면 0.5초 조각이 수십 개 나온다. 규격에 맞춰 도로 붙인다."""
    clips = [Clip(source_start=i * 0.7, source_end=i * 0.7 + 0.5, score=0.5)
             for i in range(40)]                       # 평균 0.5초
    out = enforce_cut_length(clips, 2.2)

    avg = sum(c.source_duration for c in out) / len(out)
    assert 1.9 <= avg <= 3.2, f"목표 2.2초에 못 맞췄다: {avg:.2f}"
    assert len(out) < len(clips)
    assert out == sorted(out, key=lambda c: c.source_start)


def test_already_long_enough_clips_are_left_alone():
    clips = [Clip(source_start=i * 20.0, source_end=i * 20.0 + 8.0) for i in range(5)]
    assert enforce_cut_length(clips, 2.2) == clips


def test_merging_never_swallows_a_bridge():
    """빨리감기 다리를 보통 속도 클립에 합치면 속도가 뒤섞인다."""
    clips = [Clip(source_start=0.0, source_end=0.5),
             Clip(source_start=0.6, source_end=10.0, reason="bridge", speed=8.0),
             Clip(source_start=10.1, source_end=10.6)]
    out = enforce_cut_length(clips, 5.0)
    bridges = [c for c in out if c.reason == "bridge"]
    assert len(bridges) == 1 and bridges[0].speed == 8.0


def test_target_zero_disables_merging():
    clips = [Clip(source_start=i * 0.7, source_end=i * 0.7 + 0.5) for i in range(10)]
    assert enforce_cut_length(clips, 0.0) == clips
