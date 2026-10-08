---
name: PRIME AI authorization scope
description: Durable safety boundary for PRIME AI Discord actions.
---

PRIME AI action fixes may improve request interpretation and target resolution, but must not bypass Discord permissions or PRIME policy, or grant broader access.

**Why:** The project owner explicitly requires administrative actions to remain subject to both Discord and PRIME authorization checks.

**How to apply:** Keep execution-time permission checks, channel/role allowlists, role hierarchy, action enablement, and dry-run/confirmation policy intact when changing natural-language routing or adding actions.
