CREATE TABLE IF NOT EXISTS prime_ai_durable_memories (
    guild_id BIGINT NOT NULL,
    memory_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    created_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    scope TEXT NOT NULL DEFAULT 'SERVER',
    scope_id TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL DEFAULT 'ADMIN',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    owner_user_id BIGINT,
    candidate_expires_at TIMESTAMPTZ,
    confirmation_message_id BIGINT,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    memory_type TEXT NOT NULL DEFAULT 'FACT',
    importance SMALLINT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    source_channel_id BIGINT,
    source_message_id BIGINT,
    related_user_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (guild_id, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_prime_ai_durable_memories_scope
    ON prime_ai_durable_memories (guild_id, scope, scope_id, status);

CREATE TABLE IF NOT EXISTS prime_ai_durable_profiles (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    preferences_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    last_intent TEXT NOT NULL DEFAULT '',
    last_topic TEXT NOT NULL DEFAULT '',
    last_channel_id BIGINT,
    last_seen_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS prime_ai_conversation_turns (
    turn_key TEXT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    thread_id BIGINT,
    user_id BIGINT NOT NULL,
    topic_key TEXT NOT NULL DEFAULT 'general',
    user_message_id BIGINT,
    assistant_message_id BIGINT,
    reference_message_id BIGINT,
    mentioned_user_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    user_content TEXT NOT NULL,
    assistant_content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prime_ai_conversation_context
    ON prime_ai_conversation_turns
       (guild_id, channel_id, user_id, topic_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prime_ai_conversation_expiry
    ON prime_ai_conversation_turns (expires_at);

CREATE INDEX IF NOT EXISTS idx_prime_ai_conversation_reply_target
    ON prime_ai_conversation_turns (guild_id, channel_id, assistant_message_id);

CREATE TABLE IF NOT EXISTS prime_ai_durable_memory_revisions (
    guild_id BIGINT NOT NULL,
    memory_id BIGINT NOT NULL,
    revision_id BIGINT NOT NULL,
    before_content TEXT NOT NULL,
    after_content TEXT NOT NULL,
    changed_by BIGINT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    source_channel_id BIGINT,
    source_message_id BIGINT,
    PRIMARY KEY (guild_id, revision_id)
);

CREATE INDEX IF NOT EXISTS idx_prime_ai_durable_memory_revision_lookup
    ON prime_ai_durable_memory_revisions (guild_id, memory_id, revision_id DESC);
