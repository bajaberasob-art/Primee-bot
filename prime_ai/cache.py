"""Short-lived policy snapshots with bounded storage and single-flight reads."""

from __future__ import annotations

import copy
import time
from collections import OrderedDict

from .concurrency import TrackedLock


class RuntimePolicyCache:
    def __init__(self, loader, *, ttl_seconds: float = 2.0, capacity: int = 256):
        self.loader = loader
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self.entries: OrderedDict[int, tuple[float, dict, dict]] = OrderedDict()
        self.locks: dict[int, TrackedLock] = {}

    def _cached(self, key: int):
        entry = self.entries.get(key)
        if entry is None or time.monotonic() - entry[0] >= self.ttl_seconds:
            return None
        self.entries.move_to_end(key)
        return copy.deepcopy(entry[1]), copy.deepcopy(entry[2])

    def _prune_locks(self):
        for key, lock in tuple(self.locks.items()):
            if key not in self.entries and lock.idle():
                self.locks.pop(key, None)

    async def get(self, guild_id: int) -> tuple[dict, dict]:
        key = int(guild_id)
        cached = self._cached(key)
        if cached is not None:
            return cached
        self._prune_locks()
        lock = self.locks.setdefault(key, TrackedLock())
        try:
            async with lock:
                cached = self._cached(key)
                if cached is not None:
                    return cached
                settings, snapshot = await self.loader(key)
                self.entries[key] = (time.monotonic(), settings, snapshot)
                self.entries.move_to_end(key)
                while len(self.entries) > self.capacity:
                    self.entries.popitem(last=False)
                return copy.deepcopy(settings), copy.deepcopy(snapshot)
        finally:
            self._prune_locks()

    def clear(self):
        self.entries.clear()
        self._prune_locks()
