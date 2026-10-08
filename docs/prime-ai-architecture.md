# PRIME AI internal architecture

## Compatibility boundary

The public modules remain `prime_ai_service`, `prime_ai_control`,
`prime_ai_runtime`, `prime_ai_intelligence`, `prime_ai_persistence`, and
`cogs.ai_tools.AITools`. Existing imports, API routes, Slash-command names,
settings, provider models, prompts, personalities, action IDs, and database
tables remain compatible. Exceptions moved into the internal package are
re-exported at their original import locations.

The dashboard files and API payloads are unchanged by this internal refactor.
No migration, database replacement, data import, or schema recreation is needed.

## Responsibility map

| Boundary | Responsibility |
|---|---|
| `cogs/ai_tools.py` | Discord entry points, message routing, confirmation/review views, user-facing responses, lifecycle |
| `prime_ai_service.py` | Application orchestration: settings/access gates, approved memory retrieval, existing personality prompt, provider invocation, mandatory audit |
| `prime_ai_runtime.py` | Intent/entity resolution, context construction, skill routing, action planning/sandbox, execution-time policy and hierarchy validation, tool execution |
| `prime_ai_control.py` | Revisioned control settings, action/skill inventory, memory approval/revisions, operation state transitions, analytics and retention |
| `prime_ai_intelligence.py` | Compact explicit user preferences, topic and semantic intent, repetition detection, additive profile schema |
| `prime_ai_persistence.py` | SQLite/PostgreSQL storage adapters, durable mirrors/restoration, selected scoped turns and deletion/retention |
| `prime_ai/providers.py` | Gemini/Pollinations transport adapters, SSE/JSON decoding, retry/fallback, one total provider deadline |
| `prime_ai/concurrency.py` | FIFO bounded provider admission and locks that track pending acquirers |
| `prime_ai/conversation.py` | Transient guild/channel/member-isolated history and stale-generation deletion guards |
| `prime_ai/dialogue.py` | Shared conversation policy, topic retrieval, response-quality pass and serialized context/profile persistence |
| `prime_ai/cache.py` | Two-second, bounded policy cache with single-flight loads and isolated returned snapshots |
| `prime_ai/limits.py` | Sliding-window rate accounting and capacity protection using each bucket's actual expiry |
| `prime_ai/linked_commands.py` | Reuse existing Slash-command handlers and their command policies |
| `prime_ai/tasks.py` | Explicit ownership, exception observation, cancellation and draining of cog background tasks |
| `prime_ai/text.py`, `prime_ai/errors.py` | Shared sanitization and stable error contracts |

## Request paths

1. Slash commands, mentions/replies, and the configured Talk channel enter the
   existing `AITools` handlers. Dashboard tests enter the existing web routes.
2. Access, modes, role/channel policies and rate limits are checked as before.
   The short policy cache reduces repeated reads; it does not grant permissions.
3. Context is scoped to the requester, server, channel and topic. Only selected
   exchanges with PRIME can enter durable conversation storage.
4. Application services build the existing system prompt and approved memories.
   No provider adapter may inspect the database, bot, or guild objects.
5. Provider admission allows eight active requests and 32 queued requests, with
   an eight-second queue wait. The configured provider deadline covers retries,
   retry backoff, and the existing same-provider fallback together.
6. Mandatory audit remains fail-closed. Optional request metrics remain
   best-effort. Raw conversation text is never added to audit details.
7. AI action plans use the existing sandbox/confirmation flow. Every execution
   still checks current settings, Discord/PRIME permissions, hierarchy, target
   identity, channel/role allowlists, dry-run and command policy. Linked actions
   invoke the same operation methods as their Slash commands.

## Lifecycle and concurrency rules

- Do not clean a keyed lock based only on `locked()`: after release, a queued
  acquirer may be scheduled but not resumed. `TrackedLock.idle()` includes it.
- Policy load failures are not cached. Cache snapshots are deep-copied for each
  caller and both stored snapshots and idle lock bookkeeping are pruned.
- Rate-limit capacity fails closed; active daily windows cannot be evicted by a
  hardcoded one-hour assumption.
- Every long-lived cog background task belongs to its supervisor. Cog unload
  cancels and awaits owned tasks instead of leaving work running after reload.
- A PostgreSQL pool is published only after schema validation. Concurrent starts
  share one pool; validation failure/cancellation closes the unpublished pool.
- Keep deletion generations: late responses must not restore forgotten transient
  or durable context. Storage mutation locks serialize selected-turn/profile
  commits and user-data deletion across channels. Retention, selected-turn scope,
  privacy and deletion policy are not relaxed by the refactor.

## Verification

Run `PYTHONPATH=. python tests/run_prime_ai.py`. It disables the real PostgreSQL
backend before importing persistence and redirects default SQLite access to
temporary storage. Existing test classes may also use their own temporary
fixtures. Do not run this suite against live storage.

The regressions cover existing prompts, Discord formatting/routing, API access
and settings, memory approval/deletion, action policies/outcomes and sandbox,
plus queue bounds/cancellation, lock handoff, cache isolation/expiry, daily
rate windows, background-task cleanup and pool lifecycle failures.

The tests use simulated provider responses. They do not prove live provider
availability, actual Discord permissions in every server, or full OAuth login.
