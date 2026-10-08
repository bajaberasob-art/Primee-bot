---
name: PRIME AI authorization scope
description: Durable safety boundary for PRIME AI Discord actions.
---

PRIME AI action fixes may improve request interpretation and target resolution, but must not bypass Discord permissions or PRIME policy, or grant broader access.

**Why:** The project owner explicitly requires administrative actions to remain subject to both Discord and PRIME authorization checks.

**How to apply:** Keep execution-time permission checks, channel/role allowlists, role hierarchy, action enablement, and dry-run/confirmation policy intact when changing natural-language routing or adding actions.

As of 2026-10-08, the owner approved durable retention of selected PRIME-directed conversation turns so PRIME can restore context after restarts. Retention defaults to 7 days; 0 disables and purges it. Never persist all server or channel history. Keep retained turns isolated by server, channel, member, and topic; automatic unsolicited replies are limited to the configured Talk channel.

**Why:** The owner approved limited, configurable conversation persistence while explicitly rejecting broad server-history collection.

**How to apply:** Record only selected exchanges with PRIME, apply the configured retention and Talk-channel gate, preserve Discord/PRIME permissions, and clear the member's stored conversations and transient context when they request deletion.
