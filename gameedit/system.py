"""기기 상태 읽기 — 메모리와 코어.

폰에서는 이 값을 보고 작업을 잘게 나눠야 한다. 추정으로 잡으면 어떤
기기에서는 남아돌고 어떤 기기에서는 그대로 죽는다.

**같은 판단을 두 군데서 하지 않게 여기로 모았다.** 예전에는 whisper 모델이
메모리에 들어가는지를 transcribe 와 speedtest 가 각자 계산했다. 상수가
양쪽에 흩어져 있어서, 한쪽을 고치면 "재보기는 된다고 하는데 실제로는 안
고르는" 식으로 조용히 어긋난다.
"""

from __future__ import annotations

import os
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


# --------------------------------------------------------------- 코어

def resolve_threads(raw, *, default: int = 0) -> int:
    """작업에 쓸 스레드 수.

        0 이상  그대로 (0 이면 프로그램이 알아서)
        음수    코어 수에서 그만큼 뺀다

    코어를 전부 쓰면 같은 기기에서 브라우저 조작이 불가능해진다. 폰에서
    편집기를 돌리면서 화면도 봐야 하므로 여유를 남기는 쪽이 낫다.
    ffmpeg 와 whisper.cpp 가 같은 규칙을 쓴다.
    """
    try:
        raw = int(raw or 0)
    except (TypeError, ValueError):
        return default
    if raw >= 0:
        return raw
    return max(1, (os.cpu_count() or 2) + raw)


# ------------------------------------------------------------- 메모리 여유

# 모델·버퍼를 올리는 데 실제로 필요한 램은 파일 크기보다 크다.
# (가중치를 통째로 올린 뒤 계산용 버퍼를 더 쓴다)
MEMORY_FACTOR = 1.35
MEMORY_OVERHEAD_MB = 180.0
# 남은 메모리의 이 비율까지만 쓴다. 꽉 채우면 다음 단계에서 죽는다.
MEMORY_HEADROOM = 0.6


def memory_needed_mb(file_bytes: int) -> float:
    """이만한 파일을 메모리에 올리는 데 드는 램(MB)."""
    return max(0, int(file_bytes)) / (1024 * 1024) * MEMORY_FACTOR + MEMORY_OVERHEAD_MB


def fits_in_memory(need_mb: float, available_mb: float | None = None) -> bool:
    """지금 메모리로 감당할 수 있는지. **못 재면 된다고 본다.**

    /proc/meminfo 가 없는 환경(맥·윈도우)에서 못 잰다고 작은 걸 고르면
    그냥 손해다.
    """
    available = available_memory_mb() if available_mb is None else available_mb
    if available <= 0:
        return True
    return need_mb <= available * MEMORY_HEADROOM


def memory_verdict(needed_mb: float, available_mb: float) -> str:
    """ok(넉넉) / tight(빠듯) / impossible(불가) / unknown(못 잼)."""
    if available_mb <= 0:
        return "unknown"
    if needed_mb <= available_mb * MEMORY_HEADROOM:
        return "ok"
    if needed_mb <= available_mb * 0.95:
        return "tight"
    return "impossible"
