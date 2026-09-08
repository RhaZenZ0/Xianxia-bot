from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Awaitable


class AsyncWorkQueue:
    """Small concurrency gate for expensive external work such as cloud narration."""

    def __init__(self, max_concurrency: int = 2, history: int = 200):
        self._sem = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._history: deque[dict[str, Any]] = deque(maxlen=max(10, int(history)))

    async def run(self, label: str, awaitable: Awaitable[Any]) -> Any:
        queued = time.perf_counter()
        async with self._sem:
            started = time.perf_counter()
            try:
                return await awaitable
            finally:
                ended = time.perf_counter()
                self._history.append({
                    "label": str(label),
                    "queue_ms": round((started - queued) * 1000.0, 2),
                    "run_ms": round((ended - started) * 1000.0, 2),
                    "finished_at": time.time(),
                })

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._history)
