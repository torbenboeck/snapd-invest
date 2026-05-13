---
description: Format all code in both Python engine and .NET CLI.
---

Format the codebase:

1. `cd engine && uv run ruff format && uv run ruff check --fix`
2. `cd cli && dotnet format`

Report what was changed. Do not commit - leave the working tree dirty for review.
