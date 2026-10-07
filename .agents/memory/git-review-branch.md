---
name: Git review branch safety
description: Keep GitHub changes isolated from the production branch until the user explicitly approves promotion.
---

Keep GitHub edits on a review branch. Do not merge them into `main` or publish them unless the user explicitly asks.

**Why:** The user chose a separate review branch for source changes and wants `main` left untouched during review.

**How to apply:** Fetch and inspect the review branch, then update the matching workspace branch. Treat promotion to `main` and deployment as separate actions.
