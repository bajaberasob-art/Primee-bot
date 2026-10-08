---
name: Discord upload stream ownership
description: Cleanup expectations when passing in-memory files to discord.py.
---

A caller-provided BytesIO remains caller-owned when wrapped in discord.File.
File.close() alone does not close that underlying stream, and discord.File
itself is not a context manager.

**Why:** Separator-upload verification exposed an open stream despite the
Discord File being closed. Repeated sends should not retain upload buffers.

**How to apply:** Restore the stream's close method with File.close(), then close
the caller-owned stream in a finally block, including timeout/cancellation paths.
