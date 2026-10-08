---
name: PRIME management expansion
description: Project owner's constraints for expanding PRIME Discord administration.
---

Expand existing PRIME subsystems instead of rebuilding them. Preserve all current features and data; never drop or recreate the database. Database changes must be additive and compatible with existing records.

**Why:** The project owner explicitly required that current systems and stored data remain intact and that enhancements not break existing features.

**How to apply:** Before adding admin coverage, map the current slash-command, dashboard, AI, and persistence paths; reuse them, keep changes incremental, and verify affected existing behavior.
