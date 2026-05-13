---
description: Show a snapshot of repo state for orientation.
---

Show a quick snapshot of the repo:

1. `git status --short`
2. `git log --oneline -10`
3. `git branch --show-current`
4. List the next 3 pending tasks from `tasks/` (or the `tasks/_next.md` pointer).
5. Note whether the engine and CLI are buildable: run `cd engine && uv sync --dry-run` and `cd cli && dotnet restore --no-cache` (timeout 10s each). Report green/red.

Output a compact summary, not raw command output.
