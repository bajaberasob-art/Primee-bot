---
name: PRIME AI authorization scope
description: Durable safety boundary for PRIME AI Discord actions.
---

PRIME AI action fixes may improve request interpretation and target resolution, but must not bypass Discord permissions or PRIME policy, or grant broader access.

**Why:** The project owner explicitly requires administrative actions to remain subject to both Discord and PRIME authorization checks.

**How to apply:** Keep execution-time permission checks, channel/role allowlists, role hierarchy, action enablement, and dry-run/confirmation policy intact when changing natural-language routing or adding actions.

Raw Discord conversation text and rolling chat history must remain transient in memory; persist only separately approved, privacy-safe preferences or summaries.

**Why:** The project owner explicitly chose temporary conversation context to avoid retaining raw chats.

**How to apply:** Do not add raw prompts, replies, or channel history to durable storage, audit details, or analytics. Clear the caller's transient context when they request deletion.
