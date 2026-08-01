"""받아 온 그림의 출처 표시.

`tools/fetch_memes.py` 는 CC 라이선스 그림만 받는데, 그중 **CC BY 는 크레딧이
의무**다. 여기서 중요한 성질 두 가지:

  · 받아 둔 것 전부가 아니라 **이번 영상에 실제로 쓰인 것만** 적는다
  · 퍼블릭 도메인(CC0·PDM)은 적을 필요가 없다

빠뜨리면 라이선스 위반이고, 안 쓴 걸 적으면 그냥 지저분한 거라 애매하면 넣는다.
"""

import json
import sys
from pathlib import Path

import pytest

from gameedit.credits import credit_text, credits_for_plan, load_records, write_credits
from gameedit.models import EditPlan, MediaInfo, MemeCue

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))


def record(folder: Path, entries: list[dict]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "출처.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def library(tmp_path):
    record(tmp_path, [
        {"file": "폭발.jpg", "trigger": "폭발", "license": "by",
         "attribution": '"Explosion" by kevin is licensed under CC BY 2.0.',
         "source": "https://example.com/1"},
        {"file": "웃음.jpg", "trigger": "웃음", "license": "cc0",
         "attribution": '"laughing" by ayes is marked with CC0 1.0.',
         "source": "https://example.com/2"},
        {"file": "물음표.jpg", "trigger": "물음표", "license": "by",
         "attribution": '"question" by someone is licensed under CC BY 2.0.',
         "source": "https://example.com/3"},
    ])
    return tmp_path


def plan_using(*files: str) -> EditPlan:
    plan = EditPlan(source="/tmp/x.mp4", media=MediaInfo(path="/tmp/x.mp4", duration=60.0))
    for index, name in enumerate(files):
        plan.memes.append(MemeCue(start=float(index), duration=2.0, meme_id=name,
                                  kind="image", asset=f"/그림폴더/{name}"))
    return plan


def test_only_used_images_are_credited(library):
    got = credits_for_plan(plan_using("폭발.jpg"), dirs=[library])
    assert [e["file"] for e in got] == ["폭발.jpg"]


def test_public_domain_needs_no_credit(library):
    """CC0 만 썼으면 적을 게 없다."""
    assert credits_for_plan(plan_using("웃음.jpg"), dirs=[library]) == []


def test_unused_downloads_are_not_credited(library):
    """받아만 두고 안 쓴 그림까지 설명란에 적으면 지저분하다."""
    got = credits_for_plan(plan_using("폭발.jpg"), dirs=[library])
    assert all(e["file"] != "물음표.jpg" for e in got)


def test_same_image_used_twice_is_listed_once(library):
    got = credits_for_plan(plan_using("폭발.jpg", "폭발.jpg", "폭발.jpg"), dirs=[library])
    assert len(got) == 1


def test_unknown_images_are_ignored(library):
    """직접 넣은 그림은 기록이 없다. 없다고 터지면 안 된다."""
    assert credits_for_plan(plan_using("내가만든밈.png"), dirs=[library]) == []


def test_sfx_files_are_credited_too(library):
    record(library, [{"file": "펑.mp3", "license": "by", "attribution": "소리 출처",
                      "source": "https://example.com/4"}])
    plan = plan_using()
    plan.memes.append(MemeCue(start=0.0, duration=2.0, meme_id="x", sfx="/소리/펑.mp3"))
    assert [e["file"] for e in credits_for_plan(plan, dirs=[library])] == ["펑.mp3"]


def test_missing_record_file_is_not_an_error(tmp_path):
    assert load_records([tmp_path]) == {}
    assert credits_for_plan(plan_using("아무거나.jpg"), dirs=[tmp_path]) == []


def test_broken_record_file_is_skipped(tmp_path):
    (tmp_path / "출처.json").write_text("{망가진 json", encoding="utf-8")
    assert load_records([tmp_path]) == {}


def test_credit_text_is_pasteable(library):
    text = credit_text(credits_for_plan(plan_using("폭발.jpg"), dirs=[library]))
    assert text.startswith("[사용한 이미지 출처]")
    assert "CC BY 2.0" in text


def test_no_file_written_when_nothing_needs_credit(library, tmp_path):
    out = tmp_path / "완성본"
    assert write_credits(plan_using("웃음.jpg"), out, dirs=[library]) is None
    assert not (out / "크레딧.txt").exists()


def test_credit_file_lands_next_to_the_video(library, tmp_path):
    """폰에서 영상을 올릴 때 바로 옆에 있어야 붙여 넣는 걸 잊지 않는다."""
    out = tmp_path / "완성본"
    path = write_credits(plan_using("폭발.jpg", "물음표.jpg"), out, dirs=[library])
    assert path == out / "크레딧.txt"
    body = path.read_text(encoding="utf-8")
    assert "Explosion" in body and "question" in body


# ------------------------------------------------- 받아 올 때 거르는 규칙

def test_only_youtube_safe_licenses_are_requested():
    """NC(비상업)·ND(변경금지)·SA(동일조건)를 받으면 유튜브에 못 올린다."""
    import fetch_memes

    assert set(fetch_memes.SAFE_LICENSES) == {"cc0", "pdm", "by"}
    for bad in ("by-nc", "by-nd", "by-sa", "by-nc-sa", "by-nc-nd"):
        assert bad not in fetch_memes.SAFE_LICENSES
        assert not fetch_memes.usable({"license": bad})


def test_api_license_field_is_rechecked():
    import fetch_memes

    assert fetch_memes.usable({"license": "cc0"})
    assert not fetch_memes.usable({"license": ""})
    assert not fetch_memes.usable({"license": "cc0", "filesize": 99 * 1024 * 1024})


@pytest.mark.parametrize("head,expect", [
    (b"\x89PNG\r\n\x1a\n" + b"0" * 20, "png"),
    (b"\xff\xd8\xff\xe0" + b"0" * 20, "jpg"),
    (b"GIF89a" + b"0" * 20, "gif"),
    (b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 20, "webp"),
    (b"<svg xmlns=" + b"0" * 20, None),
    (b"<!DOCTYPE html>" + b"0" * 20, None),
])
def test_real_format_is_read_from_the_file_itself(head, expect):
    """확장자와 API 값은 비어 있거나 틀린다. 내용을 봐야 편집할 때 안 터진다."""
    import fetch_memes

    assert fetch_memes.sniff(head) == expect


def test_filename_becomes_the_trigger_so_separators_are_stripped():
    """파일 이름이 곧 트리거다. `,` `_` `@` 가 섞이면 엉뚱한 말에 반응한다."""
    import fetch_memes

    assert fetch_memes.safe_name("폭발,웃음") == "폭발웃음"
    assert fetch_memes.safe_name("개_킹받네") == "개킹받네"
    assert fetch_memes.safe_name("a@b/c") == "abc"
    assert fetch_memes.safe_name("///") == "meme"


def test_extra_copies_keep_the_same_trigger():
    """같은 트리거로 여러 장 받을 때 `폭발2.png` 로 저장하면 '2' 가 트리거가 된다."""
    from gameedit.memes import parse_asset_filename

    assert parse_asset_filename("폭발.jpg")["triggers"] == ["폭발"]
    second = parse_asset_filename("폭발@a.jpg")
    assert second["triggers"] == ["폭발"]
    # `@` 뒤는 옵션 자리라 배치·시간을 건드리면 안 된다
    assert second["placement"] == "top" and second["duration"] == 2.0
