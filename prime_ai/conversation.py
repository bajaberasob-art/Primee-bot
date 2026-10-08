"""Bounded transient conversation state; durable turns belong to persistence."""

from __future__ import annotations

import time
from typing import Any

from .concurrency import TrackedLock
from .text import sanitize_discord_text


class ConversationStateStore:
    """Short-lived context with serialized turns and deletion-generation guards."""

    def __init__(
        self, *, ttl_seconds: int = 21600, max_sessions: int = 2000,
        max_messages: int = 30,
    ):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_sessions = max(1, int(max_sessions))
        self.max_messages = max(2, min(int(max_messages), 30))
        self._entries: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
        self._locks: dict[tuple[str, str, str], TrackedLock] = {}
        self._user_locks: dict[tuple[str, str], TrackedLock] = {}
        self._epochs: dict[tuple[str, str], int] = {}

    def _prune(self, now: float) -> None:
        for key, (touched, _) in list(self._entries.items()):
            if now - touched >= self.ttl_seconds:
                self._entries.pop(key, None)
        if len(self._entries) > self.max_sessions:
            oldest = sorted(self._entries, key=lambda key: self._entries[key][0])
            for key in oldest[:len(self._entries) - self.max_sessions]:
                self._entries.pop(key, None)
        for key, lock in list(self._locks.items()):
            if key not in self._entries and lock.idle():
                self._locks.pop(key, None)

    @staticmethod
    def _clean_key(key: tuple[Any, Any, Any]) -> tuple[str, str, str]:
        if len(key) != 3 or any(value is None for value in key):
            raise ValueError("conversation_key_requires_guild_channel_user")
        return tuple(str(value) for value in key)

    def lock_for(self, key: tuple[Any, Any, Any]) -> TrackedLock:
        clean_key = self._clean_key(key)
        self._prune(time.monotonic())
        return self._locks.setdefault(clean_key, TrackedLock())

    def mutation_lock_for(self, guild_id: Any, user_id: Any) -> TrackedLock:
        """Serialize storage mutations/deletion across this user's channels."""
        for key, lock in tuple(self._user_locks.items()):
            if lock.idle():
                self._user_locks.pop(key, None)
        key = (str(guild_id), str(user_id))
        return self._user_locks.setdefault(key, TrackedLock())

    def get(self, key: tuple[Any, Any, Any]) -> list[dict]:
        clean_key = self._clean_key(key)
        now = time.monotonic()
        self._prune(now)
        entry = self._entries.get(clean_key)
        if entry is None:
            return []
        _, messages = entry
        self._entries[clean_key] = (now, messages)
        return [dict(item) for item in messages]

    def record_turn(
        self, key: tuple[Any, Any, Any], user_text: Any, assistant_text: Any,
        *, max_messages: int | None = None, expected_epoch: int | None = None,
    ) -> None:
        clean_key = self._clean_key(key)
        if expected_epoch is not None and expected_epoch != self.user_epoch(
            clean_key[0], clean_key[2]
        ):
            return
        cap = self.max_messages if max_messages is None else max(
            0, min(int(max_messages), self.max_messages)
        )
        if cap < 2:
            return
        user = sanitize_discord_text(user_text, 1000)
        assistant = sanitize_discord_text(assistant_text, 1500)
        if not user or not assistant:
            return
        now = time.monotonic()
        self._prune(now)
        _, messages = self._entries.get(clean_key, (now, []))
        messages = list(messages)
        messages.extend((
            {"role": "user", "content": f"[أنت]: {user}"},
            {"role": "assistant", "content": f"[PRIME AI]: {assistant}"},
        ))
        self._entries[clean_key] = (now, messages[-cap:])
        self._prune(now)

    def user_epoch(self, guild_id: Any, user_id: Any) -> int:
        return self._epochs.get((str(guild_id), str(user_id)), 0)

    def clear_user(self, guild_id: Any, user_id: Any) -> None:
        guild_key, user_key = str(guild_id), str(user_id)
        epoch_key = (guild_key, user_key)
        self._epochs[epoch_key] = self._epochs.get(epoch_key, 0) + 1
        for key in list(self._entries):
            if key[0] == guild_key and key[2] == user_key:
                self._entries.pop(key, None)
        for key, lock in list(self._locks.items()):
            if key[0] == guild_key and key[2] == user_key and lock.idle():
                self._locks.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
        # Preserve in-flight locks so a reset cannot permit overlapping turns.
        self._locks = {key: lock for key, lock in self._locks.items() if not lock.idle()}
        # Invalidate all known generations before clearing entries.
        self._user_locks = {key: lock for key, lock in self._user_locks.items() if not lock.idle()}
        active_users = {(key[0], key[2]) for key in self._locks} | set(self._user_locks)
        self._epochs = {key: self._epochs.get(key, 0) + 1 for key in active_users}


CONVERSATION_STATE = ConversationStateStore()
