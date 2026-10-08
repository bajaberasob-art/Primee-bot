---
name: Announcement area scope
description: Product intent and safety boundaries for PRIME's announcement area.
---

“المساحة الإعلانية” means a Discord announcement Auto Reactions control area,
not an advertising marketplace, paid placement system, or new dashboard.

**Why:** The owner requested this destination inside the existing administration
dashboard for text-message reactions on one or optionally two channels, plus
independently enabled Auto Line image separators.

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

Only genuine text messages qualify for either automatic operation. Media,
including captioned attachments, stickers, image/video embeds, empty messages
and system messages must be skipped. The bot's own messages must never trigger
another separator.

**Why:** The owner explicitly changed the scope to “الرسائل النصيه بس” and
confirmed the uploaded image should be sent as a separator after text messages.

**How to apply:** Maintain strict text filtering and the self-message exclusion
for reactions and Auto Line alike. Keep their activation and channel choices
independent; uploading a new image is a draft until the administrator saves it.
