---
name: Dashboard browser verification
description: Avoid false UI failures from warm browser sessions during iterative dashboard verification.
---

Treat a testing helper's follow-up browser as a warm client, not a new browser.
Before judging a frontend correction, confirm that it actually loaded the new
asset version; use a fresh context or clear the worker's caches when necessary.

**Why:** Successive protected-dashboard checks reported unchanged empty forms
and unreadable counters even though direct HTTP responses contained both fixes.
The browser had retained older assets across follow-ups. Repeating code edits
against that stale client would have changed correct code unnecessarily.

**How to apply:** Distinguish a current-code failure from a stale-client result
using the loaded script URL and rendered properties. In isolated verification,
clear service workers/cache storage or use a fresh browser context before the
targeted correction check. Keep normal caching enabled for the real dashboard,
and release frontend changes with consistent new asset and worker versions.
