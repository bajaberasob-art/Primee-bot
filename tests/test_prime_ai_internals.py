"""Deterministic regression tests for PRIME's internal lifecycle and isolation."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import prime_ai_control
import prime_ai_persistence as persistence
import prime_ai_runtime as runtime
import prime_ai_service as service
from cogs.ai_tools import AITools, ActionOutcomeTrackingError
from prime_ai.cache import RuntimePolicyCache
from prime_ai.concurrency import BoundedProviderGate
from prime_ai.conversation import ConversationStateStore
from prime_ai.errors import AIProviderUnavailable
from prime_ai.limits import SlidingWindowLimiter
from prime_ai.providers import (
    GeminiProvider, PROVIDER_MODEL, PROVIDER_NAME,
    FALLBACK_PROVIDER_MODEL, request_with_fallback,
)
from prime_ai.tasks import TaskSupervisor
from prime_ai import dialogue


class CompatibilityTests(unittest.TestCase):
    def test_existing_import_paths_retain_exception_and_provider_identity(self):
        from prime_ai import errors
        self.assertIs(service.AIProviderUnavailable, errors.AIProviderUnavailable)
        self.assertIs(runtime.AccessDenied, errors.AccessDenied)
        self.assertIs(ActionOutcomeTrackingError, errors.ActionOutcomeTrackingError)
        self.assertIs(service.GeminiProvider, GeminiProvider)
        self.assertIs(runtime.ConversationStateStore, ConversationStateStore)

    def test_settings_and_service_share_unchanged_provider_defaults(self):
        settings = prime_ai_control.DEFAULT_CONTROL_SETTINGS["provider"]
        self.assertEqual(settings["name"], PROVIDER_NAME)
        self.assertEqual(settings["model"], PROVIDER_MODEL)
        self.assertEqual(service.PROVIDER_MODEL, "gemini-3.8-flash")

    def test_limiter_does_not_evict_an_active_daily_window(self):
        limiter = SlidingWindowLimiter()
        options = dict(limit=1, max_buckets=1, cleanup_seconds=60)
        key = ("chat", 1, 2)
        self.assertEqual(limiter.allow(key, now=100, window_seconds=86400, **options), 0)
        self.assertEqual(
            limiter.allow(("chat", 1, 3), now=7300, window_seconds=60, **options), 60,
        )
        self.assertEqual(
            limiter.allow(key, now=7300, window_seconds=86400, **options), 79200,
        )
        self.assertEqual(
            limiter.allow(("chat", 1, 3), now=86500, window_seconds=60, **options), 0,
        )

    def test_transient_history_stays_scoped_and_copied(self):
        store = ConversationStateStore(max_sessions=2)
        store.record_turn((1, 2, 3), "hello", "reply")
        copy = store.get((1, 2, 3))
        copy[0]["content"] = "mutated"
        self.assertNotEqual(store.get((1, 2, 3))[0]["content"], "mutated")
        for key in ((2, 2, 3), (1, 3, 3), (1, 2, 4)):
            self.assertEqual(store.get(key), [])


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_queue_timeout_releases_waiter(self):
        gate = BoundedProviderGate(max_active=1, max_waiting=1, queue_timeout=0.1)
        await gate.acquire()
        with self.assertRaisesRegex(AIProviderUnavailable, "provider_queue_timeout"):
            await gate.acquire()
        self.assertEqual(len(gate._waiters), 0)
        await gate.release()
        self.assertEqual(gate._active, 0)

    async def test_cancelled_waiter_cannot_leak_a_handed_off_slot(self):
        gate = BoundedProviderGate(max_active=1)
        await gate.acquire()
        waiter = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
        await gate.release()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(gate._active, 0)
        async with gate.slot():
            self.assertEqual(gate._active, 1)
        self.assertEqual(gate._active, 0)

    async def test_provider_burst_never_exceeds_concurrency(self):
        gate = BoundedProviderGate(max_active=3, max_waiting=100)
        active = peak = 0

        async def request():
            nonlocal active, peak
            async with gate.slot():
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0)
                active -= 1

        await asyncio.gather(*(request() for _ in range(64)))
        self.assertEqual(peak, 3)
        self.assertEqual(gate._active, 0)
        self.assertEqual(len(gate._waiters), 0)

    async def test_conversation_lock_survives_release_handoff(self):
        store = ConversationStateStore()
        key = (1, 2, 3)
        lock = store.lock_for(key)
        await lock.acquire()
        waiter = asyncio.create_task(lock.acquire())
        await asyncio.sleep(0)
        lock.release()
        # The next owner has not resumed yet; locked() alone is not sufficient.
        self.assertFalse(lock.locked())
        self.assertIs(store.lock_for(key), lock)
        await waiter
        lock.release()

    async def test_user_deletion_keeps_queued_lock_and_rejects_stale_turn(self):
        store = ConversationStateStore()
        key = (1, 2, 3)
        epoch = store.user_epoch(1, 3)
        lock = store.lock_for(key)
        await lock.acquire()
        waiter = asyncio.create_task(lock.acquire())
        await asyncio.sleep(0)
        store.clear_user(1, 3)
        lock.release()
        self.assertIs(store.lock_for(key), lock)
        await waiter
        store.record_turn(key, "old private text", "reply", expected_epoch=epoch)
        self.assertEqual(store.get(key), [])
        lock.release()

    async def test_global_clear_invalidates_active_generations(self):
        store = ConversationStateStore()
        key = (1, 2, 3)
        epoch = store.user_epoch(1, 3)
        async with store.lock_for(key):
            store.clear()
            store.record_turn(key, "old", "reply", expected_epoch=epoch)
            self.assertEqual(store.get(key), [])

    async def test_cancelled_lock_waiter_is_cleaned(self):
        store = ConversationStateStore()
        lock = store.lock_for((1, 2, 3))
        await lock.acquire()
        waiter = asyncio.create_task(lock.acquire())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        lock.release()
        self.assertTrue(lock.idle())
        store.get((1, 2, 3))
        self.assertEqual(store._locks, {})

    async def test_policy_cache_single_flight_and_copy_isolation(self):
        loader = AsyncMock(return_value=({"enabled": True}, {"config": {"mode": "CHAT"}}))
        cache = RuntimePolicyCache(loader)
        results = await asyncio.gather(*(cache.get(1) for _ in range(40)))
        loader.assert_awaited_once_with(1)
        results[0][1]["config"]["mode"] = "ACTION"
        self.assertEqual((await cache.get(1))[1]["config"]["mode"], "CHAT")

    async def test_policy_cache_failure_does_not_cache_or_keep_idle_lock(self):
        loader = AsyncMock(side_effect=[ValueError("unavailable"), ({}, {})])
        cache = RuntimePolicyCache(loader)
        with self.assertRaises(ValueError):
            await cache.get(1)
        self.assertEqual(cache.entries, {})
        self.assertEqual(cache.locks, {})
        self.assertEqual(await cache.get(1), ({}, {}))
        self.assertEqual(loader.await_count, 2)

    async def test_policy_cache_bounds_both_snapshots_and_locks(self):
        cache = RuntimePolicyCache(AsyncMock(return_value=({}, {})), capacity=2)
        for guild_id in range(20):
            await cache.get(guild_id)
        self.assertEqual(len(cache.entries), 2)
        self.assertEqual(len(cache.locks), 2)
        cache.clear()
        self.assertEqual(cache.locks, {})

    async def test_cache_expiry_refreshes_settings(self):
        loader = AsyncMock(side_effect=[({"revision": 1}, {}), ({"revision": 2}, {})])
        cache = RuntimePolicyCache(loader, ttl_seconds=0)
        self.assertEqual((await cache.get(1))[0]["revision"], 1)
        self.assertEqual((await cache.get(1))[0]["revision"], 2)

    async def test_provider_retry_and_fallback_share_one_deadline(self):
        gate = BoundedProviderGate(max_active=1)
        calls = []

        async def complete(session, payload, **kwargs):
            calls.append(payload["model"])
            await asyncio.sleep(0.03)
            if len(calls) == 1:
                raise AIProviderUnavailable("unavailable", status_code=503)
            await asyncio.sleep(10)

        async with gate.slot():
            with self.assertRaises(asyncio.TimeoutError):
                await request_with_fallback(
                    None, {"model": PROVIDER_MODEL}, timeout_seconds=0.08,
                    retry_count=1, complete=complete, logger=Mock(),
                )
        self.assertEqual(calls, [PROVIDER_MODEL, FALLBACK_PROVIDER_MODEL])
        self.assertEqual(gate._active, 0)

    async def test_task_supervisor_awaits_cancellation_on_unload(self):
        supervisor = TaskSupervisor(Mock())
        finished = asyncio.Event()

        async def work():
            try:
                await asyncio.sleep(10)
            finally:
                finished.set()

        task = supervisor.spawn(work(), name="test-cleanup")
        await asyncio.sleep(0)
        await supervisor.close()
        self.assertTrue(finished.is_set())
        self.assertTrue(task.cancelled())
        self.assertEqual(supervisor.tasks, set())
        with self.assertRaises(RuntimeError):
            supervisor.spawn(work(), name="after-close")

    async def test_task_failure_is_observed_and_logged(self):
        logger = Mock()
        supervisor = TaskSupervisor(logger)

        async def work():
            raise ValueError("task_failed")

        supervisor.spawn(work(), name="test-failure")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        logger.error.assert_called_once()
        self.assertEqual(supervisor.tasks, set())
        await supervisor.close()

    async def test_cog_unload_cleans_owned_tasks(self):
        cog = AITools(Mock())
        cog._retention_task = cog._background_tasks.spawn(
            asyncio.sleep(10), name="retention-test",
        )
        await cog.cog_unload()
        self.assertIsNone(cog._retention_task)
        self.assertEqual(cog._background_tasks.tasks, set())


class DurableLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patchers = [
            patch.object(persistence, "_pool", None),
            patch.object(persistence, "_POOL_LOCK", asyncio.Lock()),
            patch.object(persistence, "_DISABLED_FOR_TESTS", False),
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://isolated.invalid/test"}),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.connection = Mock()
        self.connection.fetch = AsyncMock()
        self.pool = Mock()
        self.pool.close = AsyncMock()

        @asynccontextmanager
        async def acquire():
            yield self.connection

        self.pool.acquire = acquire
        self.create_pool = AsyncMock(return_value=self.pool)
        patcher = patch.object(persistence.asyncpg, "create_pool", self.create_pool)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_pool_validation_failure_closes_without_publishing(self):
        self.connection.fetch.side_effect = RuntimeError("connection_failed")
        with self.assertRaisesRegex(RuntimeError, "connection_failed"):
            await persistence.start_durable_store()
        self.pool.close.assert_awaited_once()
        self.assertIsNone(persistence._pool)

    async def test_missing_schema_closes_without_publishing(self):
        self.connection.fetch.return_value = []
        with self.assertRaisesRegex(RuntimeError, "schema is missing"):
            await persistence.start_durable_store()
        self.pool.close.assert_awaited_once()
        self.assertFalse(persistence.durable_store_enabled())

    async def test_cancelled_validation_closes_without_publishing(self):
        self.connection.fetch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await persistence.start_durable_store()
        self.pool.close.assert_awaited_once()
        self.assertIsNone(persistence._pool)

    async def test_concurrent_starts_publish_only_one_validated_pool(self):
        self.connection.fetch.return_value = [
            {"tablename": name} for name in (
                "prime_ai_conversation_turns", "prime_ai_durable_memories",
                "prime_ai_durable_profiles", "prime_ai_durable_memory_revisions",
            )
        ]
        results = await asyncio.gather(*(persistence.start_durable_store() for _ in range(5)))
        self.assertEqual(results, [True] * 5)
        self.create_pool.assert_awaited_once()
        self.assertIs(persistence._pool, self.pool)
        await persistence.close_durable_store()
        self.pool.close.assert_awaited_once()
        self.assertFalse(persistence.durable_store_enabled())


class DialogueLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_generated_reply_cannot_restore_forgotten_data(self):
        store = ConversationStateStore()
        guild, actor, channel = (SimpleNamespace(id=value) for value in (1, 2, 3))
        record = AsyncMock()
        profile = AsyncMock()

        async def generate(*args, **kwargs):
            store.clear_user(guild.id, actor.id)
            return "normal reply"

        with (
            patch.object(dialogue.intelligence, "load_user_profile", AsyncMock(return_value={})),
            patch.object(dialogue.persistence, "resolve_topic_key", AsyncMock(return_value="general")),
            patch.object(dialogue.persistence, "load_turns", AsyncMock(return_value=[])),
            patch.object(dialogue.service, "generate_response", generate),
            patch.object(dialogue.persistence, "record_turn", record),
            patch.object(dialogue.intelligence, "update_user_profile", profile),
        ):
            answer = await dialogue.generate_user_response(
                None, guild, actor, channel, "hello", store=store,
                config={}, context={}, mode="CHAT", audit_action="test",
            )
        self.assertEqual(answer, "normal reply")
        record.assert_not_awaited()
        profile.assert_not_awaited()
        self.assertEqual(store.get((1, 3, 2)), [])

    async def test_deletion_waits_for_inflight_storage_then_removes_it(self):
        store = ConversationStateStore()
        guild, actor = SimpleNamespace(id=1), SimpleNamespace(id=2)
        entered, release = asyncio.Event(), asyncio.Event()
        data = []

        async def record(**kwargs):
            entered.set()
            await release.wait()
            data.append("turn")

        async def forget(guild_id, user_id):
            data.clear()
            return {"memories": 0, "profile": 0, "conversations": 1}

        with (
            patch.object(dialogue.persistence, "record_turn", record),
            patch.object(dialogue.intelligence, "update_user_profile", AsyncMock()),
            patch("prime_ai.conversation.CONVERSATION_STATE", store),
            patch.object(service, "_forget_user_storage", side_effect=forget) as deletion,
        ):
            saving = asyncio.create_task(dialogue._persist_turn(
                store, guild, actor, 3, "hello", "reply",
                dialogue.ConversationPolicy(12, 7),
                epoch=0, topic_key="general", intent="CHAT", turn_key="test",
            ))
            await entered.wait()
            deleting = asyncio.create_task(service.forget_user_data(1, 2))
            await asyncio.sleep(0)
            deletion.assert_not_called()
            release.set()
            await saving
            await deleting
        self.assertEqual(data, [])
        self.assertEqual(store.get((1, 3, 2)), [])

    async def test_zero_retention_does_not_write_durable_turns(self):
        store = ConversationStateStore()
        record = AsyncMock()
        with (
            patch.object(dialogue.persistence, "record_turn", record),
            patch.object(dialogue.intelligence, "update_user_profile", AsyncMock()),
        ):
            await dialogue.record_message_turn(
                SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3),
                "hello", "reply", {"retention": {"conversation_days": 0}}, store=store,
            )
        record.assert_not_awaited()

    def test_conversation_policy_retains_existing_defaults_and_bounds(self):
        self.assertEqual(dialogue.ConversationPolicy.from_config({}), dialogue.ConversationPolicy(12, 7))
        policy = dialogue.ConversationPolicy.from_config({
            "context": {"max_messages": 11},
            "retention": {"conversation_days": 9000},
        })
        self.assertEqual(policy, dialogue.ConversationPolicy(10, 3650))
