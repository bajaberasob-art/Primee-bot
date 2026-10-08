---
name: PRIME management expansion
description: Project owner's constraints for expanding PRIME Discord administration.
---

Expand existing PRIME subsystems instead of rebuilding them. Preserve all current features and data; never drop or recreate the database. Database changes must be additive and compatible with existing records.

The owner chose a per-server PRIME role map for Admin, Moderator, and Staff. Detect the server owner and Discord Administrator from Discord itself. An empty map preserves current behavior; a configured map only adds restrictions and never bypasses Discord permissions, server policy, or actor/target/bot role-hierarchy checks.

**Why:** The project owner explicitly required that current systems and stored data remain intact and that enhancements not break existing features.

**How to apply:** Before adding admin coverage, map the current slash-command, dashboard, AI, and persistence paths; reuse them, keep changes incremental, and verify affected existing behavior. Apply the role map as an additive shared gate, never as a source of Discord permissions.
