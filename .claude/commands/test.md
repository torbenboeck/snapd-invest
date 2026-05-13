---
description: Run all tests across engine (Python) and CLI (.NET).
---

Run the full test suite:

1. `cd engine && uv run pytest`
2. `cd cli && dotnet test`

Report any failures with file:line locations. Do not attempt to fix failures unless explicitly asked - just report them.
