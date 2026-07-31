"""설정 로딩 / 기본값.

`gameedit.yaml`(또는 .json)을 읽어 기본값 위에 깊은 병합(deep merge)한다.
PyYAML 이 없으면 JSON 설정만 사용 가능.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

try:  # PyYAML 은 선택 의존성
    import yaml  # type: ignore
except Exception:  # pragma: no cover - 환경에 따라 다름
    yaml = None


DEFAULTS: dict[str, Any] = {
    "project": {
        "name": "게임 하이라이트",
        "output": "out/final.mp4",
        "work_dir": "work",
        "resolution": "",  # 비우면 원본 해상도 유지
        "max_resolution": "",  # 이보다 크면 줄인다 (작은 영상을 늘리지는 않음)
        "fps": 0,  # 0이면 원본 fps 유지
    },
    "analyze": {
        "scene_threshold": 0.35,
        "scene_scale_width": 320,  # 장면 분석용 축소 해상도 (속도)
        "hop": 0.05,
        "silence_db": -38.0,
        "min_silence": 0.6,
        "peak_percentile": 0.93,
    },
    "transcribe": {
        "backend": "auto",  # auto | faster-whisper | whisper | external | none
        "model": "small",
        "language": "ko",
        "device": "auto",
        "compute_type": "int8",
        "external": "",  # 외부 자막 파일(.srt/.vtt) 경로
        "whisper_cpp_bin": "",  # whisper.cpp 실행 파일 (비우면 자동 탐색)
        "whisper_cpp_model": "",  # ggml 모델 .bin 경로 (비우면 자동 탐색)
        "threads": 0,  # whisper.cpp 스레드 수 (0=자동)
        "initial_prompt": "게임 실황 방송 중계. 리액션과 감탄사가 많음.",
    },
    "highlight": {
        "target_duration": 480.0,  # 최종 목표 길이(초)
        "min_clip": 6.0,
        "max_clip": 45.0,
        "max_clips": 40,
        "pad_before": 1.6,
        "pad_after": 1.4,
        "merge_gap": 3.0,
        "snap_to_speech": True,
        "snap_window": 1.8,
        "drop_silence_tail": True,
        "cut_internal_silence": 2.5,  # 클립 안에 이보다 긴 정적이 있으면 잘라낸다 (0=끄기)
        "weights": {
            "audio": 1.0,
            "keyword": 1.3,
            "scene": 0.6,
            "speech": 0.35,
            "laughter": 1.0,
        },
        "keywords": [
            "대박", "미쳤", "이거 뭐야", "말도 안", "레전드", "역대급", "개쩐", "쩐다",
            "죽었", "죽네", "사망", "터졌", "망했", "망함", "실패", "털렸", "당했",
            "이겼", "클리어", "성공", "1등", "1위", "생존", "살았", "개꿀",
            "헐", "우와", "와씨", "아니", "뭐야", "어떻게", "설마", "진짜",
            "ㅋㅋ", "ㅎㅎ", "웃겨", "레알", "실화", "소름", "지렸",
        ],
        "laughter_tokens": ["ㅋㅋ", "ㅎㅎ", "하하", "크크", "푸흡", "ㅋㅋㅋ"],
        "boost_ranges": [],  # [[start, end, weight], ...] 수동 가점
        "exclude_ranges": [],  # [[start, end], ...] 무조건 제외
        "must_include_ranges": [],  # [[start, end], ...] 무조건 포함
    },
    "subtitles": {
        "enabled": True,
        "font": "Noto Sans KR",
        "font_fallback": True,  # 이 폰트가 없으면 설치된 한글 폰트로 자동 대체
        "font_size": 62,
        "bold": True,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00101010",
        "outline": 4.0,
        "shadow": 2.0,
        "margin_v": 70,
        "max_chars_per_line": 18,
        "max_lines": 2,
        "max_duration": 4.0,
        "min_duration": 0.9,
        "gap_split": 0.55,  # 단어 사이 공백이 이보다 크면 자막을 끊는다
        "emphasis": True,
        "emphasis_threshold": 0.72,
        "emphasis_color": "&H0033E8FF",  # 노랑 (ASS 는 BGR)
        "pop_animation": True,
        "export_srt": True,
    },
    "memes": {
        "enabled": True,
        "packs": ["default"],
        "pack_dirs": [],  # 추가 밈팩 폴더 (pack.yaml 이 있는 폴더)
        "asset_dirs": [],  # 파일만 넣어두면 파일명이 트리거가 되는 폴더
        "timeskip_min": 90.0,  # 클립 사이 원본 시간이 이만큼 벌어지면 "N분 후" 카드
        "max_per_minute": 4.0,
        "cooldown": 7.0,
        "min_gap": 1.5,
        "auto_reaction": True,  # 키워드가 없어도 흥분 피크에 리액션 밈 삽입
        "auto_reaction_threshold": 0.8,
        "silence_meme_min": 2.5,  # 편집본에 이만큼 긴 정적이 남았을 때만 정적 밈
        "clip_intro_label": True,  # 클립 시작마다 하이라이트 제목 표시
        "sfx_volume": 0.8,
        "duck_music": False,
    },
    "render": {
        "video_codec": "libx264",
        "crf": 20,
        "preset": "medium",
        "audio_codec": "aac",
        "audio_bitrate": "192k",
        "punch_zoom": True,
        "punch_amount": 1.12,
        "clip_fade": 0.12,
        "loudnorm": True,
        "threads": 0,
        "keep_intermediate": False,
    },
}


# 실행 환경에 따라 한 번에 바꾸는 묶음 설정
PROFILES: dict[str, dict] = {
    # 폰에서 직접 돌릴 때. 화질을 조금 포기하고 시간을 크게 줄인다.
    "phone": {
        "project": {"max_resolution": "1280x720"},
        "analyze": {
            # 장면 검출은 영상 전체를 디코딩해야 해서 폰에서는 가장 비싸다.
            # 하이라이트는 소리와 대사만으로도 충분히 잡힌다.
            "scene_threshold": 0,
            "scene_scale_width": 160,
            "hop": 0.1,
        },
        "transcribe": {"model": "tiny", "compute_type": "int8"},
        "render": {"preset": "veryfast", "crf": 26, "audio_bitrate": "128k"},
        "highlight": {"max_clips": 25},
    },
    # 화질 우선 (시간이 오래 걸려도 됨)
    "quality": {
        "analyze": {"scene_scale_width": 480},
        "transcribe": {"model": "medium"},
        "render": {"preset": "slow", "crf": 18},
    },
}


def apply_profile(data: dict, name: str) -> dict:
    """프로필을 기본값 위에, 사용자 설정 아래에 끼워 넣는다."""
    profile = PROFILES.get(name)
    if profile is None:
        raise ValueError(f"모르는 프로필입니다: {name} (쓸 수 있는 것: {', '.join(PROFILES)})")
    return deep_merge(profile, data)


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """점 표기법으로 읽는 얇은 설정 래퍼."""

    def __init__(self, data: dict | None = None, path: Path | None = None,
                 profile: str = ""):
        # 사용자가 실제로 적어 준 값만 따로 보관한다. 프로필은 기본값 위·사용자 값 아래에
        # 끼워 넣어야 하는데, 한 번 병합하고 나면 둘을 구분할 수 없기 때문이다.
        self.raw = copy.deepcopy(data or {})
        merged = apply_profile(self.raw, profile) if profile else self.raw
        self.data = deep_merge(DEFAULTS, merged)
        self.path = Path(path) if path else None
        self.profile = profile

    def with_profile(self, profile: str) -> "Config":
        """사용자 설정은 그대로 두고 프로필만 덧입힌 새 설정."""
        if not profile:
            return self
        return Config(self.raw, path=self.path, profile=profile)

    # -- 접근 --------------------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def section(self, name: str) -> dict:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def __contains__(self, dotted: str) -> bool:
        sentinel = object()
        return self.get(dotted, sentinel) is not sentinel

    # -- 입출력 ------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        if not path:
            return cls()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {p}")
        raw = p.read_text(encoding="utf-8")
        data = parse_config_text(raw, p.suffix)
        return cls(data, path=p)

    @classmethod
    def discover(cls, start: str | Path = ".") -> "Config":
        """작업 폴더에서 gameedit.yaml / .yml / .json 을 찾아 로드."""
        base = Path(start)
        for name in ("gameedit.yaml", "gameedit.yml", "gameedit.json"):
            candidate = base / name
            if candidate.exists():
                return cls.load(candidate)
        return cls()

    def dump_yaml(self) -> str:
        if yaml is not None:
            return yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False)
        return json.dumps(self.data, ensure_ascii=False, indent=2)


def parse_config_text(text: str, suffix: str = ".yaml") -> dict:
    suffix = (suffix or "").lower()
    if suffix == ".json":
        return json.loads(text)
    if yaml is None:
        # YAML 파서가 없으면 JSON 으로 한 번 시도해 보고 안내
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "YAML 설정을 읽으려면 PyYAML 이 필요합니다. `pip install pyyaml` 또는 JSON 설정을 사용하세요."
            ) from exc
    return yaml.safe_load(text) or {}


def load_data_file(path: str | Path) -> dict:
    """밈팩 등 YAML/JSON 데이터 파일 로딩."""
    p = Path(path)
    return parse_config_text(p.read_text(encoding="utf-8"), p.suffix)
