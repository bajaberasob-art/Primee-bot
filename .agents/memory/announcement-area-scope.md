---
name: Announcement area scope
description: Product intent and safety boundaries for PRIME's announcement area.
---

“المساحة الإعلانية” means a Discord announcement Auto Reactions control area,
not an advertising marketplace, paid placement system, or new dashboard.

**Why:** The owner requested this destination inside the existing administration
dashboard, specifically for automated reactions on one chosen server channel.

**How to apply:** Keep future improvements in that existing area. Do not introduce
payments, ad inventory, campaign purchases, or a separate application based on
the section's name.

Automatic recovery must not fetch or process historical messages.

**Why:** The owner explicitly limited the system to new messages after activation,
prioritized protecting AI and slash-command responsiveness, and reserved old
message processing for a separate future option.

**How to apply:** Keep reaction work bounded and asynchronous. On overload, show
and log a clear status rather than creating unlimited work or replaying history.
Any future historical-message feature needs its own explicit user opt-in.
