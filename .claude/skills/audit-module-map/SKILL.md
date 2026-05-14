---
name: audit-module-map
description: Verify that docs/architecture/module-map.md matches the actual src/ and tests/ project structure. Reports modules in code but not in the map, modules in the map but not in code, and ownership/dependency drift. Use when the user asks to audit the module map, check module structure, verify project layout, or report drift between code and module-map.
---

You are auditing Snapd's module map against the actual project structure.

Procedure:

1. Read `docs/architecture/module-map.md`. Extract:
   - The list of modules described.
   - For each, what it "owns", "depends on", and "doesn't" (these are the conventional sections in the file).
2. List actual modules:
   - `ls src/` and `ls tests/` — every `Snapd.<Module>` and `Snapd.<Module>.Tests.Unit` (plus `Snapd.Tests.EndToEnd`).
3. For each source module:
   - Verify it appears in the module map.
   - Read its `.csproj` and extract `<ProjectReference>` entries — these are the *actual* dependencies.
   - Compare against the map's "depends on" statement.
4. For each module in the map: confirm a matching directory exists.
5. Output the report in this shape:

   **Modules in code but not in map**
   - module name — proposed entry skeleton (1 paragraph)

   **Modules in map but not in code**
   - module name — likely cause (renamed / deleted / aspirational)

   **Dependency drift**
   - module name — map says X, code references Y

   **Ownership statements that need refresh**
   - module name — what the map claims, what the code suggests

6. End with a verdict: "Map and code align" / "Minor drift (N items)" / "Significant drift (N items)".

Constraints:
- Do not modify the module map. This skill reports.
- Treat test projects as paired with their source project; do not list them separately unless they violate the convention.
- Do not flag temporary or experimental projects (those with `Snapd.Experimental.*` or similar prefix) as drift — note them separately.
