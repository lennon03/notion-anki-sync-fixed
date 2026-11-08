"""In-memory sliding-window rate limits for the credential endpoints."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict

WINDOW_SECONDS = 15 * 60


class RateLimiter:
    """Allow ``per_ip`` attempts per client and ``overall`` in total."""

    def __init__(
        self,
        per_ip: int,
        overall: int,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = (per_ip, overall)
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = {}

    def _count(self, key: str, now: float) -> int:
        hits = self._hits.get(key)
        if hits is None:
            return 0
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if not hits:  # idle clients don't accumulate
            del self._hits[key]
            return 0
        return len(hits)

    def _record(self, key: str, now: float) -> None:
        self._hits.setdefault(key, deque()).append(now)

    def allow(self, client: str) -> bool:
        """Record an attempt from ``client``; False when over a limit."""
        now = self._clock()
        mine, everyone = f"ip:{client}", "all"
        with self._lock:
            if self._count(mine, now) >= self._limits[0]:
                return False
            if self._count(everyone, now) >= self._limits[1]:
                return False
            self._record(mine, now)
            self._record(everyone, now)
            return True
