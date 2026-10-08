---
name: PRIME management expansion
description: Project owner's constraints for expanding PRIME Discord administration.
---

Internal PRIME AI reorganization is allowed, including moving, merging and splitting modules. Preserve all externally visible features, commands, dashboard, settings, personality and expected behavior. Never drop or recreate the database; database changes must be additive and compatible with existing records.

**Why:** The owner explicitly authorized a substantive internal refactor on 2026-10-08, on the condition that the existing user experience and data remain intact.

**How to apply:** Treat existing integration interfaces and behavioral tests as compatibility boundaries. Fix internal performance/lifecycle defects without redesigning the UI, changing prompts or introducing new product behavior.

The AI dashboard presentation may be radically redesigned; the previous UI-preservation restriction applied to the internal refactor, not to a separately requested redesign.

**Why:** The owner explicitly requested a complete creative, professional AI dashboard redesign using Magic UI on 2026-10-08.

**How to apply:** Keep changes scoped to AI/Talk surfaces and preserve their working controls, permission gates, save/conflict handling and stored data. Do not redesign unrelated dashboard destinations without a request.

The owner chose a per-server PRIME role map for Admin, Moderator, and Staff. Detect the server owner and Discord Administrator from Discord itself. An empty map preserves current behavior; a configured map only adds restrictions and never bypasses Discord permissions, server policy, or actor/target/bot role-hierarchy checks.

**Why:** The project owner explicitly required that current systems and stored data remain intact and that enhancements not break existing features.

**How to apply:** Before adding admin coverage, map the current slash-command, dashboard, AI, and persistence paths; reuse them, keep changes incremental, and verify affected existing behavior. Apply the role map as an additive shared gate, never as a source of Discord permissions.

PRIME Talk is a distinct top-level dashboard destination. Keep conversation activation, access rules, response/context/personality settings, retention, and the permissions for actions requested through Talk together there. Keep AI operations, audit history, and non-conversation controls in the AI area.

**Why:** The owner requested a dedicated Talk menu containing its editable permissions and settings.

**How to apply:** Add future Talk-specific controls to that destination without duplicating their underlying settings. Preserve server-side permission checks and mandatory safeguards for any Discord actions.
