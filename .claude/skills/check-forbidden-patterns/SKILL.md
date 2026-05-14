---
name: check-forbidden-patterns
description: Check snapd-invest's staged changes (or the whole codebase) against the Forbidden Patterns listed in root CLAUDE.md and engine/CLAUDE.md. Reports violations with file/line references. Use when the user asks to review code against forbidden patterns, check a PR for violations, audit for anti-patterns, or run a forbidden-pattern review.
---

You are checking snapd-invest code (Python engine + .NET CLI) against the Forbidden Patterns defined in `CLAUDE.md` (root) and `engine/CLAUDE.md`.

Procedure:

1. Read `CLAUDE.md` (root) and `engine/CLAUDE.md`. Extract their Forbidden Patterns sections as a list of named rules. The rules below mirror the current state of those files; re-derive from the live files in case they've evolved.

2. Determine scope from the user input:
   - `staged` or no argument: scope = files in `git diff --staged --name-only`.
   - A branch or commit ref: scope = files in `git diff <ref>..HEAD --name-only` (or `git diff origin/main..<ref>` if the user gave a branch name).
   - `all` or `codebase`: scope = whole `engine/src/` and `cli/src/` trees.

3. For each Forbidden Pattern, translate into a concrete check. Suggested mapping (extend in lockstep with CLAUDE.md changes):

   ### Cross-stack
   - **"No live broker calls from tests"** → grep `engine/tests/` and `cli/tests/` for imports/instantiations of real broker classes (`SaxoBroker`, anything in `engine/src/snapd_invest/broker/saxo*`, anything in `cli/src/.../SaxoClient*`). Tests must use `PaperBroker` / `FakeBroker`. **Critical**.
   - **"No live LLM calls in CI"** → grep `engine/tests/` for `OllamaProvider` instantiation; check that any HTTP calls go through `respx` mocks. **Critical**.
   - **"No secrets in code or git"** → grep changed files for likely API-key shapes: `sk-[A-Za-z0-9]{20,}`, `github_pat_[A-Za-z0-9_]{20,}`, `AKIA[A-Z0-9]{16}`. Also check whether `.env` or `appsettings.Development.json` is in the diff. **Critical**.
   - **"Never delete or force-push to main/develop/release/*"** → not code-checkable. Skip.

   ### Python engine (`engine/src/snapd_invest/`)
   - **"No `datetime.utcnow()` / `datetime.now()` in production code"** → `grep -nE "datetime\.(utc)?now\(" engine/src/snapd_invest/` excluding `clock.py` (which legitimately wraps it for `SystemClock`). **Critical** for hits outside `clock.py`.
   - **"No direct DB access from FastAPI route handlers"** → in `engine/src/snapd_invest/api.py`, grep for `session.execute(`, `select(`, `db.add(`, `await session.commit()`. Route handlers should delegate to service functions; only the request scope (DI helper functions) may touch the session shape. **Critical** if found inside a `@app.<method>` or `@router.<method>` decorated function body.
   - **"No `print()` in Python production code"** → `grep -n "^\s*print(" engine/src/snapd_invest/`. **Critical**.
   - **"No raw `uuid.uuid4()` inside business logic"** → grep `engine/src/snapd_invest/` for `uuid.uuid4()` and `uuid4()`. Exempt `models.py` (the `new_id` factory) and `persistence.py`. **Critical** elsewhere.
   - **"No business logic in `api.py`"** → heuristic: flag function bodies in `api.py` longer than ~15 lines that don't read like "validate args → call service → return result." Mark as **Suspect** (heuristic, false positives possible).
   - **"No `time.sleep` in production code"** → grep `engine/src/snapd_invest/` for `time.sleep(`. **Critical**.
   - **"No global mutable state"** → heuristic: module-level non-Final dict/list/set assignments in `engine/src/snapd_invest/`. Allow `Final` constants and `frozenset`/tuples. **Suspect**.
   - **"No `# type: ignore` without inline comment"** → `grep -n "# type: ignore" engine/src/snapd_invest/` and verify each line has explanatory text after the directive (e.g. `# type: ignore[arg-type]  — LLM raw dict, validated above`). Bare `# type: ignore` on its own is **Critical**.
   - **"No new dependency without ADR"** → if scope includes `engine/pyproject.toml` or `cli/Directory.Packages.props`, diff the dependencies list; for each added entry, check `docs/architecture/decision-log.md` mentions it. **Critical** when missing.

   ### .NET CLI (`cli/src/`)
   - **"No raw `DateTime.UtcNow` / `DateTime.Now` in domain code"** → grep `cli/src/` for `DateTime\.UtcNow` and `DateTime\.Now`. Exempt code that uses the injected `TimeProvider`. **Critical** when called directly.
   - **"No raw `Guid.NewGuid()` inside business logic"** → grep `cli/src/` for `Guid.NewGuid()`. Exempt composition-root files (`Program.cs`, `Startup.cs`) and explicit ID-factory wrappers. **Critical** elsewhere.

4. For each rule, list violations with `file:line:snippet` and a one-line explanation.

5. Output the report:

   **Critical violations** (block merge)
   - rule → `file:line` — snippet — why

   **Suspect** (need human review)
   - rule → `file:line` — snippet — why

   **Clean** (rules with zero hits)
   - rule

6. End with a verdict: "No violations", "Suspect findings only", or "Critical violations: N".

Constraints:
- Do not modify code. This skill reports.
- Do not auto-fix. Forbidden-Pattern violations almost always need human judgment to resolve correctly.
- Clearly distinguish **Critical** (pattern is a confident match) from **Suspect** (heuristic, could be a false positive).
- Skip checks where the rule is not pattern-matchable (e.g. branch-protection, naming).
- Tests under `engine/tests/` and `cli/tests/` are scoped by *test-relevant* rules only (no broker/LLM live calls). Production-only rules (`print()`, business logic in api.py, etc.) do not apply to test code.
