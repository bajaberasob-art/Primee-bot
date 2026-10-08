"""Bounded sliding-window accounting shared by PRIME's request entry points."""

from __future__ import annotations

import math
from collections import deque


class SlidingWindowLimiter:
    def __init__(self):
        self.buckets: dict[tuple[str, int, int], deque[float]] = {}
        self._expires: dict[tuple[str, int, int], float] = {}
        self._last_cleanup = 0.0

    def allow(
        self, key: tuple[str, int, int], *, now: float, limit: int,
        window_seconds: float, max_buckets: int, cleanup_seconds: float,
    ) -> float:
        if limit < 1 or not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("invalid_rate_limit")
        bucket = self.buckets.get(key)
        if bucket is None:
            if len(self.buckets) >= max_buckets:
                if now - self._last_cleanup >= cleanup_seconds:
                    for stale_key, stale_bucket in list(self.buckets.items()):
                        expires = self._expires.get(stale_key, float("inf"))
                        if not stale_bucket or now >= expires:
                            self.buckets.pop(stale_key, None)
                            self._expires.pop(stale_key, None)
                    self._last_cleanup = now
                if len(self.buckets) >= max_buckets:
                    # Fail closed instead of evicting active rate-limit history.
                    return max(1.0, float(window_seconds))
            # Compatibility callers may clear the public bucket dictionary.
            if len(self._expires) > len(self.buckets):
                self._expires = {
                    k: v for k, v in self._expires.items() if k in self.buckets
                }
            bucket = self.buckets.setdefault(key, deque())
        while bucket and now - bucket[0] >= window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return max(0.0, window_seconds - (now - bucket[0]))
        bucket.append(now)
        self._expires[key] = max(
            self._expires.get(key, 0.0), now + window_seconds,
        )
        return 0.0
