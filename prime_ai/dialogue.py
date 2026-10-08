"""Conversation orchestration shared by Discord entry points.

User-facing Discord I/O stays in the cog. This module owns history policy,
topic-scoped retrieval, response quality and generation-guarded persistence.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import prime_ai_intelligence as intelligence
import prime_ai_persistence as persistence
import prime_ai_service as service

LOGGER = logging.getLogger("AITools")


@dataclass(frozen=True)
class ConversationPolicy:
    messages: int
    retention_days: int

    @classmethod
    def from_config(cls, config: dict):
        try:
            messages = int(config.get("context", {}).get("max_messages", 12))
        except (TypeError, ValueError):
            messages = 12
        messages = max(0, min(messages, 30))
        messages -= messages % 2
        try:
            days = int(config.get("retention", {}).get("conversation_days", 7))
        except (TypeError, ValueError):
            days = 7
        return cls(messages, max(0, min(days, 3650)))


async def _persist_turn(
    store, guild, actor, channel_id, user_text, answer, policy,
    *, epoch, topic_key, intent, turn_key, thread_id=None,
    user_message_id=None, assistant_message_id=None,
    reference_message_id=None, mentioned_user_ids=None,
):
    # A deletion either waits for this commit and then removes it, or invalidates
    # its generation first. A pre-deletion reply cannot reinsert forgotten data.
    async with store.mutation_lock_for(guild.id, actor.id):
        if store.user_epoch(guild.id, actor.id) != epoch:
            return
        store.record_turn(
            (guild.id, channel_id, actor.id), user_text, answer,
            max_messages=policy.messages, expected_epoch=epoch,
        )
        if policy.retention_days > 0 and policy.messages:
            await persistence.record_turn(
                turn_key=turn_key, guild_id=int(guild.id),
                channel_id=int(channel_id), user_id=int(actor.id),
                topic_key=topic_key, user_content=user_text,
                assistant_content=answer, retention_days=policy.retention_days,
                thread_id=thread_id, user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                reference_message_id=reference_message_id,
                mentioned_user_ids=mentioned_user_ids,
            )
        try:
            await intelligence.update_user_profile(
                guild.id, actor.id, channel_id=channel_id, intent=intent,
                topic=intelligence._extract_topic(user_text),
                preferences=intelligence.extract_preference_signals(user_text),
            )
        except Exception:
            LOGGER.exception("[AI] Could not persist PRIME user profile.")


async def generate_user_response(
    session, guild, actor, channel, question: str, *, store,
    config: dict, context: dict, mode: str, audit_action: str,
    turn_key=None, user_message_id=None, reference_message_id=None,
    mentioned_user_ids=None, thread_id=None,
) -> str:
    channel_id = getattr(channel, "id", None)
    key = (guild.id, channel_id, actor.id) if guild is not None and channel_id is not None else None
    policy = ConversationPolicy.from_config(config)
    # Capture before any I/O, not after a profile/topic read that may race deletion.
    epoch = store.user_epoch(guild.id, actor.id) if guild is not None else None
    if guild is not None:
        profile = await intelligence.load_user_profile(int(guild.id), int(actor.id))
        context = dict(context or {})
        context["user_profile"] = profile

    async def generate(conversation, request_text=question):
        return await service.generate_response(
            session, guild.id if guild is not None else None, actor.id,
            channel_id, request_text, audit_action=audit_action,
            conversation=conversation, context=context,
            role_ids=[role.id for role in getattr(actor, "roles", ())], mode=mode,
        )

    if key is None:
        return await generate([])
    topic = await persistence.resolve_topic_key(
        int(guild.id), int(channel_id), int(actor.id),
        intelligence._extract_topic(question), reference_message_id,
        allow_inherit=persistence.is_follow_up(question),
    )
    async with store.lock_for(key):
        conversation = []
        if policy.messages and policy.retention_days > 0:
            conversation = await persistence.load_turns(
                int(guild.id), int(channel_id), int(actor.id), topic,
                reference_message_id=reference_message_id, limit=policy.messages // 2,
            )
        answer = await generate(conversation)
        if intelligence.response_repeats_recent(answer, conversation):
            rewrite_request = (
                f"{question}\n\n"
                "[INTERNAL RESPONSE QUALITY RULE: rewrite this answer naturally. "
                "Do not repeat the previous answer's wording or explanation. "
                "Preserve the same factual meaning and answer the current request directly.]"
            )
            try:
                rewritten = await generate(conversation, rewrite_request)
                if rewritten and not intelligence.response_repeats_recent(rewritten, conversation):
                    answer = rewritten
            except Exception:
                LOGGER.exception("[AI] Repetition rewrite failed; keeping first answer.")
        await _persist_turn(
            store, guild, actor, channel_id, question, answer, policy,
            epoch=epoch, topic_key=topic,
            intent=str((context or {}).get("intent") or ""),
            turn_key=str(turn_key or (
                f"generated:{guild.id}:{channel_id}:{actor.id}:"
                f"{int(asyncio.get_running_loop().time() * 1000)}"
            )),
            thread_id=thread_id, user_message_id=user_message_id,
            reference_message_id=reference_message_id,
            mentioned_user_ids=mentioned_user_ids,
        )
        return answer


async def record_message_turn(
    guild, actor, channel, user_text, answer, config, *, store,
    turn_key=None, user_message_id=None, assistant_message_id=None,
    reference_message_id=None, mentioned_user_ids=None, thread_id=None,
):
    channel_id = getattr(channel, "id", None)
    if guild is None or channel_id is None:
        return
    policy = ConversationPolicy.from_config(config)
    epoch = store.user_epoch(guild.id, actor.id)
    try:
        topic = intelligence._extract_topic(user_text)
        if policy.retention_days > 0 and policy.messages:
            topic = await persistence.resolve_topic_key(
                int(guild.id), int(channel_id), int(actor.id), topic,
                reference_message_id, allow_inherit=persistence.is_follow_up(user_text),
            )
        await _persist_turn(
            store, guild, actor, channel_id, user_text, answer, policy,
            epoch=epoch, topic_key=topic, intent="SERVER_ACTION",
            turn_key=str(turn_key or (
                f"turn:{guild.id}:{channel_id}:{actor.id}:"
                f"{int(asyncio.get_running_loop().time() * 1000)}"
            )),
            thread_id=thread_id, user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            reference_message_id=reference_message_id,
            mentioned_user_ids=mentioned_user_ids,
        )
    except Exception:
        LOGGER.exception("[AI] Could not persist PRIME message context.")
