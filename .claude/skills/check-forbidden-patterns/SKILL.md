---
name: check-forbidden-patterns
description: Check Snapd's staged changes (or the whole codebase) against the Forbidden Patterns listed in CLAUDE.md. Reports violations with file/line references. Use when the user asks to review code against forbidden patterns, check a PR for violations, audit for anti-patterns, or run a forbidden-pattern review.
---

You are checking Snapd code against the Forbidden Patterns defined in CLAUDE.md.

Procedure:

1. Read `CLAUDE.md` and extract the Forbidden Patterns section as a list of named rules.
2. Determine scope from the user input:
   - `staged` or no argument: `git diff --staged --name-only`.
   - A branch or commit ref: `git diff <ref>..HEAD --name-only` (or `git diff origin/main..<ref>` for a branch).
   - `all` or `codebase`: scan the whole `src/` tree.
3. For each Forbidden Pattern, translate into a concrete check. Suggested mapping (extend per CLAUDE.md changes):
   - "No business logic in `Snapd.Api` or `Snapd.Web`" → heuristic: grep these projects for `if`/`switch` blocks longer than 5 lines outside endpoint mapping and validator delegation. Mark as **suspect** (heuristic).
   - "No direct `DbContext` access from `Snapd.Api` or `Snapd.Web`" → grep for `DbContext` types or `_db.`/`Db.` usage in those projects. **Critical**.
   - "No service locator outside composition root" → grep for `IServiceProvider.GetService` / `GetRequiredService` outside `Program.cs`. **Critical**.
   - "No `static` mutable state" → grep for `static` fields not marked `readonly` or `const`. **Critical**.
   - "No fire-and-forget" → grep for `Task.Run(` and `_ = .*Async\(` patterns. **Critical**.
   - "No `catch (Exception)` without rethrow" → grep for `catch (Exception` and check each block contains `throw`. **Critical** outside `Snapd.Api`'s global handler.
   - "No magic numbers/strings in domain code" → heuristic, **suspect** only.
   - "No `TODO`/`FIXME` without issue link" → grep for `TODO`/`FIXME` and check the comment line includes a URL or issue ref. **Suspect**.
   - "No raw `Guid.NewGuid()` inside domain entities" → grep for `Guid.NewGuid()` in `Snapd.Core/`. **Critical**.
   - "No leaking `DanskeBank*`/`Ollama*` types" → grep for those prefixes in projects other than `Snapd.Banking`/`Snapd.Llm`. **Critical**.
   - "No `DateTime.Now` / `DateTime.UtcNow`" → grep for both. **Critical**.
   - "No reflection in hot paths" → grep for `typeof(.*).GetMethod`, `Activator.CreateInstance`, etc. in extraction/request pipeline. **Suspect**.
   - "No 200-with-error-in-body" → heuristic: grep for `Ok(new \{ error` or similar. **Suspect**.
   - "No new dependency without ADR" → list new entries in `Directory.Packages.props` from the diff; check `docs/architecture/decision-log.md` for an entry. **Critical**.
   - "Never log PII / credentials / tokens" → grep `LogInformation`/`LogWarning`/`LogDebug` for parameter names like `Password`, `Token`, `Secret`, `Pii`, `VendorName`, `Body`. **Critical** in `Snapd.Banking` and payment endpoints; **suspect** elsewhere.
   - "Never delete or force-push to `main`/`develop`/`release/*`" → not code-checkable; skip.
4. For each rule, list violations with `file:line:snippet` and a one-line explanation.
5. Output the report:

   **Critical violations** (block merge)
   - rule → file:line — snippet — why

   **Suspect** (need human review)
   - rule → file:line — snippet — why

   **Clean** (rules with zero hits)
   - rule

6. End with a verdict: "No violations", "Suspect findings only", or "Critical violations: X".

Constraints:
- Do not modify code. This skill reports.
- Do not auto-fix. Forbidden-Pattern violations almost always need human judgment to resolve correctly.
- Clearly distinguish **Critical** (pattern is a confident match) from **Suspect** (heuristic could be a false positive).
- Skip checks where the rule is not pattern-matchable (e.g. branch-protection rule, naming alignment).
