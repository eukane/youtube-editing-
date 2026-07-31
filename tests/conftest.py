import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gameedit.models import (Analysis, AudioAnalysis, MediaInfo, SceneChange,  # noqa: E402
                             Segment, Transcript, Word)


def make_words(text: str, start: float, per_word: float = 0.35) -> list[Word]:
    words = []
    t = start
    for token in text.split():
        words.append(Word(start=round(t, 3), end=round(t + per_word, 3), text=token))
        t += per_word
    return words


def make_segment(start: float, text: str, per_word: float = 0.35) -> Segment:
    words = make_words(text, start, per_word)
    end = words[-1].end if words else start + 1.0
    return Segment(start=start, end=end, text=text, words=words)


@pytest.fixture
def analysis() -> Analysis:
    """3분짜리 가짜 실황: 30초/90초/150초 부근에서 텐션이 오른다."""
    hop = 0.5
    n = int(180 / hop)
    excitement = [0.15] * n

    def hump(center: float, width: float, height: float) -> None:
        for i in range(n):
            t = i * hop
            if abs(t - center) <= width:
                excitement[i] = max(excitement[i], height * (1 - abs(t - center) / width))

    hump(30.0, 8.0, 0.95)
    hump(90.0, 6.0, 0.85)
    hump(150.0, 7.0, 0.9)

    audio = AudioAnalysis(
        hop=hop,
        rms_db=[-20.0] * n,
        excitement=excitement,
        silences=[(60.0, 70.0), (115.0, 122.0)],
        peaks=[(30.0, 0.95), (90.0, 0.85), (150.0, 0.9)],
    )

    transcript = Transcript(segments=[
        make_segment(28.0, "야 이거 진짜 대박이다 미쳤다"),
        make_segment(31.0, "아 죽었어 죽었어 왜 죽냐고"),
        make_segment(75.0, "그냥 걸어가는 중이에요"),
        make_segment(89.0, "헐 이게 뭐야 말도 안 돼"),
        make_segment(148.0, "이겼다 클리어 ㅋㅋㅋ"),
    ])

    scenes = [SceneChange(t=t, score=0.5) for t in (29.0, 30.2, 31.5, 90.5, 149.0, 151.0)]

    return Analysis(
        media=MediaInfo(path="/tmp/full.mp4", duration=180.0, width=1920, height=1080,
                        fps=30.0, has_audio=True),
        audio=audio,
        scenes=scenes,
        transcript=transcript,
    )
