"""모바일 웹 서버 (gameedit serve)."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from gameedit.config import Config
from gameedit.models import Clip, EditPlan, MediaInfo, MemeCue, SubtitleCue
from gameedit.server import (apply_phone_edits, create_server, local_ip, plan_for_phone,
                             cleanup_old_uploads)


@pytest.fixture
def plan():
    p = EditPlan(
        source="/tmp/full.mp4",
        media=MediaInfo(path="/tmp/full.mp4", duration=600.0, width=1920, height=1080),
        clips=[
            Clip(source_start=10.0, source_end=20.0, label="A"),
            Clip(source_start=100.0, source_end=110.0, label="B"),
            Clip(source_start=200.0, source_end=210.0, label="C"),
        ],
        memes=[
            MemeCue(start=1.0, duration=2.0, meme_id="m1", text="ㅋㅋ", source_start=11.0),
            MemeCue(start=11.0, duration=2.0, meme_id="m2", text="헐", source_start=101.0),
            MemeCue(start=21.0, duration=2.0, meme_id="m3", text="와", source_start=201.0),
        ],
        subtitles=[
            SubtitleCue(start=1.0, end=3.0, lines=["첫 번째"], source_start=11.0),
            SubtitleCue(start=11.0, end=13.0, lines=["두 번째"], source_start=101.0),
            SubtitleCue(start=21.0, end=23.0, lines=["세 번째"], source_start=201.0),
        ],
    )
    p.relayout()
    return p


# --------------------------------------------------------------------- 편집 반영

def test_removing_a_clip_shifts_everything_after_it(plan):
    """가운데 클립을 빼면 뒤쪽 밈·자막이 통째로 당겨져야 한다."""
    edited = apply_phone_edits(plan, {"removed_clips": [1]})

    assert [c.label for c in edited.clips] == ["A", "C"]
    assert edited.duration == pytest.approx(20.0)

    # B 안에 있던 큐는 사라지고, C 의 큐는 10초 당겨진다
    assert [m.meme_id for m in edited.memes] == ["m1", "m3"]
    assert edited.memes[1].start == pytest.approx(11.0)
    assert [s.text for s in edited.subtitles] == ["첫 번째", "세 번째"]
    assert edited.subtitles[1].start == pytest.approx(11.0)
    assert edited.subtitles[1].duration == pytest.approx(2.0)  # 길이는 유지


def test_removing_first_clip(plan):
    edited = apply_phone_edits(plan, {"removed_clips": [0]})
    assert [c.label for c in edited.clips] == ["B", "C"]
    assert edited.memes[0].meme_id == "m2"
    assert edited.memes[0].start == pytest.approx(1.0)


def test_subtitle_text_edit(plan):
    edited = apply_phone_edits(plan, {"subtitle_edits": {"1": "고친 자막\n두 줄"}})
    assert edited.subtitles[1].lines == ["고친 자막", "두 줄"]


def test_drop_memes_keeps_labels(plan):
    plan.memes.append(MemeCue(start=0.1, duration=2.0, meme_id="clip_label",
                              text="☠️ 사망각", source_start=10.0))
    edited = apply_phone_edits(plan, {"drop_memes": True})
    assert {m.meme_id for m in edited.memes} == {"clip_label"}


def test_plan_for_phone_shape(plan):
    data = plan_for_phone(plan)
    assert len(data["clips"]) == 3
    assert data["clips"][0]["label"] == "A"
    assert data["clips"][0]["start_text"] == "0:10"
    assert data["subtitles"][0]["text"] == "첫 번째"
    assert data["duration_text"] == "0:30"


# --------------------------------------------------------------------- HTTP

@pytest.fixture
def server(tmp_path):
    httpd, manager, key = create_server(Config(), host="127.0.0.1", port=0,
                                        root=tmp_path, access_key="1234",
                                        watch_dirs=[str(tmp_path / "watch")])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, manager, key
    httpd.shutdown()
    httpd.server_close()


def get(url, key="1234", **kw):
    req = urllib.request.Request(url, **kw)
    if key:
        req.add_header("X-Key", key)
    return urllib.request.urlopen(req, timeout=5)


def get_json(url, key="1234", **kw):
    with get(url, key, **kw) as res:
        return json.loads(res.read().decode())


def test_page_and_health(server):
    base, _, _ = server
    with get(f"{base}/health", key=None) as res:
        assert json.loads(res.read())["ok"] is True
    with get(f"{base}/") as res:
        body = res.read().decode()
    assert "하이라이트 편집기" in body
    assert "<script>" in body


def test_access_key_required(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        get_json(f"{base}/api/jobs", key=None)
    assert exc.value.code == 401
    # 잘못된 번호도 막힌다
    with pytest.raises(urllib.error.HTTPError):
        get_json(f"{base}/api/jobs", key="9999")
    # 쿼리스트링으로도 통과할 수 있어야 한다 (폰이 처음 열 때)
    assert "jobs" in get_json(f"{base}/api/jobs?k=1234", key=None)


def test_upload_then_listed_as_file(server, tmp_path):
    base, manager, _ = server
    payload = b"\x00\x11" * 5000
    req = urllib.request.Request(f"{base}/api/upload", data=payload, method="POST")
    req.add_header("X-Key", "1234")
    req.add_header("X-Filename", "%EC%98%A4%EB%8A%98%EB%B0%A9%EC%86%A1.mp4")  # 오늘방송.mp4
    with urllib.request.urlopen(req, timeout=10) as res:
        saved = json.loads(res.read().decode())

    assert saved["name"] == "오늘방송.mp4"
    stored = tmp_path / "uploads" / "오늘방송.mp4"
    assert stored.exists() and stored.read_bytes() == payload

    files = get_json(f"{base}/api/files")["files"]
    assert any(f["name"] == "오늘방송.mp4" for f in files)


def test_upload_rejects_empty_body(server):
    req = urllib.request.Request(f"{server[0]}/api/upload", data=b"", method="POST")
    req.add_header("X-Key", "1234")
    req.add_header("X-Filename", "x.mp4")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400


def test_job_for_missing_file_is_rejected(server):
    base, _, _ = server
    body = json.dumps({"path": "/없는/파일.mp4"}).encode()
    req = urllib.request.Request(f"{base}/api/jobs", data=body, method="POST")
    req.add_header("X-Key", "1234")
    req.add_header("Content-Type", "application/json")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400


def test_unknown_job_returns_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get_json(f"{server[0]}/api/jobs/deadbeef")
    assert exc.value.code == 404


def test_video_range_requests(server, tmp_path):
    """폰에서 결과 영상을 재생하려면 Range 응답이 필요하다."""
    base, manager, _ = server

    from gameedit.server import Job
    fake = Job(id="abc123", source="x.mp4", title="테스트")
    fake.work_dir = str(tmp_path / "jobs" / "abc123")
    (tmp_path / "jobs" / "abc123").mkdir(parents=True)
    output = tmp_path / "jobs" / "abc123" / "final.mp4"
    output.write_bytes(bytes(range(256)) * 40)
    fake.output = str(output)
    fake.status = "done"
    with manager.lock:
        manager.jobs["abc123"] = fake
        manager.order.append("abc123")

    with get(f"{base}/api/jobs/abc123/video") as res:
        assert res.status == 200
        assert res.headers["Accept-Ranges"] == "bytes"
        assert len(res.read()) == 256 * 40

    req = urllib.request.Request(f"{base}/api/jobs/abc123/video")
    req.add_header("X-Key", "1234")
    req.add_header("Range", "bytes=10-19")
    with urllib.request.urlopen(req, timeout=5) as res:
        assert res.status == 206
        assert res.headers["Content-Range"] == f"bytes 10-19/{256*40}"
        assert res.read() == bytes(range(10, 20))

    # 다운로드 요청에는 파일 이름이 붙는다. 한글은 RFC 5987 로 실어 보낸다
    # (HTTP 헤더에 한글을 그대로 넣으면 서버가 UnicodeEncodeError 로 죽는다)
    with get(f"{base}/api/jobs/abc123/video?download=1") as res:
        disposition = res.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    from urllib.parse import unquote
    assert "테스트_편집본.mp4" in unquote(disposition)

    assert get_json(f"{base}/api/jobs/abc123")["has_output"] is True
    assert any(j["id"] == "abc123" for j in get_json(f"{base}/api/jobs")["jobs"])


# --------------------------------------------------------------------- 기타

def test_local_ip_returns_something():
    ip = local_ip()
    assert ip.count(".") == 3


def test_cleanup_old_uploads(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    old = uploads / "old.mp4"
    old.write_bytes(b"x")
    import os
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    fresh = uploads / "new.mp4"
    fresh.write_bytes(b"x")

    assert cleanup_old_uploads(tmp_path, days=7) == 1
    assert not old.exists() and fresh.exists()


def test_content_disposition_handles_korean_names():
    from urllib.parse import unquote

    from gameedit.server import content_disposition

    header = content_disposition("오늘방송_편집본.mp4")
    header.encode("latin-1")  # HTTP 헤더로 실을 수 있어야 한다
    assert unquote(header).endswith("오늘방송_편집본.mp4")

    ascii_only = content_disposition("clip.mp4")
    assert 'filename="clip.mp4"' in ascii_only


def test_previous_jobs_survive_a_restart(tmp_path):
    """서버를 껐다 켜도 지난 편집본이 목록에 남아야 한다."""
    from gameedit.models import save_json
    from gameedit.server import JobManager

    job_dir = tmp_path / "jobs" / "abc123"
    job_dir.mkdir(parents=True)
    plan = EditPlan(source="/영상/오늘방송.mp4",
                    media=MediaInfo(path="/영상/오늘방송.mp4", duration=600.0),
                    clips=[Clip(source_start=0.0, source_end=12.0, label="A")])
    plan.relayout()
    save_json(plan, job_dir / "plan.json")
    (job_dir / "final.mp4").write_bytes(b"video")

    manager = JobManager(tmp_path, Config())
    assert manager.restore() == 1

    restored = manager.get("abc123")
    assert restored.status == "done"
    assert restored.title == "오늘방송"
    assert restored.summary["clips"] == 1
    assert restored.summary["duration_text"] == "0:12"

    # 두 번 불러도 중복되지 않는다
    manager.restore()
    assert len(manager.listing()) == 1


def test_restart_marks_unfinished_jobs(tmp_path):
    from gameedit.models import save_json
    from gameedit.server import JobManager

    job_dir = tmp_path / "jobs" / "dead01"
    job_dir.mkdir(parents=True)
    plan = EditPlan(source="/영상/중단.mp4", media=MediaInfo(duration=60.0),
                    clips=[Clip(source_start=0.0, source_end=5.0)])
    save_json(plan, job_dir / "plan.json")   # final.mp4 없음 = 렌더 중 종료

    manager = JobManager(tmp_path, Config())
    manager.restore()
    assert manager.get("dead01").status == "error"


def test_only_allowed_folders_can_be_edited(server, tmp_path):
    """폰이 보낸 경로를 그대로 믿으면 아무 파일이나 열어보게 된다."""
    outside = tmp_path.parent / "바깥.mp4"
    outside.write_bytes(b"video")
    body = json.dumps({"path": str(outside)}).encode()
    req = urllib.request.Request(f"{server[0]}/api/jobs", data=body, method="POST")
    req.add_header("X-Key", "1234")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 403


def test_non_video_and_empty_paths_rejected(server, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    note = tmp_path / "uploads" / "메모.txt"
    note.write_text("x", encoding="utf-8")

    for payload, expected in [({"path": str(note)}, 400), ({"path": ""}, 400), ({}, 400)]:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(f"{server[0]}/api/jobs", data=body, method="POST")
        req.add_header("X-Key", "1234")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == expected


def test_broken_json_body_does_not_create_a_job(server):
    req = urllib.request.Request(f"{server[0]}/api/jobs", data="{{{깨진".encode("utf-8"), method="POST")
    req.add_header("X-Key", "1234")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400
    assert get_json(f"{server[0]}/api/jobs")["jobs"] == []


def test_garbage_edits_are_ignored_not_crashing(plan):
    """폰에서 이상한 값이 와도 서버가 죽으면 안 된다."""
    for edits in [{"removed_clips": ["abc", None, 1.7]},
                  {"removed_clips": "전부"},
                  {"subtitle_edits": {"안녕": "x"}},
                  {"subtitle_edits": {"0": None}},
                  {"subtitle_edits": "문자열"},
                  {"removed_clips": [99999, -5]}]:
        import copy
        result = apply_phone_edits(copy.deepcopy(plan), edits)
        assert result.clips, edits
        assert all(c.start >= 0 for c in result.memes)


# ------------------------------------------------------- 편집 강도 (폰 화면)

def test_pace_presets_change_the_editing_config():
    from gameedit.config import Config
    from gameedit.server import EDIT_PACE

    assert set(EDIT_PACE) == {"loose", "normal", "fast"}

    base = Config()
    fast = Config()
    for key, value in EDIT_PACE["fast"].items():
        fast.set(key, value)

    # 빠르게 = 더 짧은 무음도 잘라낸다
    assert (fast.section("editing")["dead_air_min"]
            < base.section("editing")["dead_air_min"])
    assert (fast.section("memes")["max_per_minute"]
            > base.section("memes")["max_per_minute"])

    loose = Config()
    for key, value in EDIT_PACE["loose"].items():
        loose.set(key, value)
    assert loose.section("editing")["cold_open"] is False


def test_unknown_pace_falls_back_to_normal():
    """화면에서 온 값을 그대로 믿으면 안 된다."""
    from gameedit.server import resolve_pace

    assert resolve_pace("fast") == "fast"
    assert resolve_pace("설마이런값") == "normal"
    assert resolve_pace(None) == "normal"
    assert resolve_pace({"nested": 1}) == "normal"
