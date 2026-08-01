"""기기 상태 읽기.

지금 쓸 수 있는 메모리가 얼마인지 같은 것. 폰에서는 이 값을 보고 작업을
잘게 나눠야 한다. 추정으로 잡으면 어떤 기기에서는 남아돌고 어떤 기기에서는
그대로 죽는다.
"""

from __future__ import annotations

import re
from pathlib import Path

MEMINFO = Path("/proc/meminfo")


def available_memory_mb() -> float:
    """지금 실제로 더 쓸 수 있는 램(MB). 못 알아내면 0.

    `MemTotal` 이 아니라 `MemAvailable` 을 본다. 안드로이드는 총 4GB 라도
    시스템과 다른 앱(특히 편집 화면을 띄워 둔 크롬)이 이미 절반 넘게 쓰고
    있어서, 총량으로 판단하면 그대로 죽는다.
    """
    try:
        text = MEMINFO.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0
    m = re.search(r"^MemAvailable:\s+(\d+)\s*kB", text, re.MULTILINE)
    if not m:
        return 0.0
    return int(m.group(1)) / 1024.0
