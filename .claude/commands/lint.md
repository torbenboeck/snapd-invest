---
description: Run lint checks (no auto-fix) across both stacks.
---

Run lint checks without modifying anything:

1. `cd engine && uv run ruff check && uv run ruff format --check && uv run mypy src`
2. `cd cli && dotnet format --verify-no-changes && dotnet build /warnaserror`

Report any violations. Do not auto-fix.
