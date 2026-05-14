---
name: audit-module-map
description: Verify that docs/architecture/module-map.md matches the actual snapd-invest layout (Python engine + .NET CLI). Reports modules in code but not in the map, modules in the map but not in code, and ownership/dependency drift. Use when the user asks to audit the module map, check module structure, verify project layout, or report drift between code and module-map.
---

You are auditing snapd-invest's module map against the actual project layout. The repo has two stacks:

- **Python engine** — flat layout in `engine/src/snapd_invest/*.py`. Each `.py` file is one module. Tests live in `engine/tests/unit/test_<module>.py`.
- **.NET CLI** — standard .NET layout. Projects under `cli/src/<Project>/`, each with a `.csproj`. Tests live in `cli/tests/<Project>.Tests.Unit/`.

The map at `docs/architecture/module-map.md` is split into a `## Python engine modules` table and a `## .NET CLI projects` table.

Procedure:

1. Read `docs/architecture/module-map.md`. Extract:
   - The Python modules table (columns: Module / Owns / Depends on).
   - The .NET CLI projects table (columns: Project / Owns).
   - Any "Boundary discipline" rules at the bottom of the file.

2. List actual artifacts:
   - Python: glob `engine/src/snapd_invest/*.py`. Exclude `__init__.py` and `__pycache__`.
   - .NET: glob `cli/src/*/*.csproj`.
   - Test files exist as pairs and are *not* listed separately in the map; do not flag missing test-file rows unless they violate the convention (`tests/unit/test_<module>.py` for Python, `cli/tests/<Project>.Tests.Unit/` for .NET).

3. For each Python module:
   - Verify it appears in the Python table.
   - Read its top-of-file imports. Internal dependencies are `from snapd_invest.<module> import ...` or `import snapd_invest.<module>`. Treat `TYPE_CHECKING`-guarded imports the same as runtime ones — they're still real dependencies.
   - Compare the set of internal modules imported with what the map's "Depends on" column lists for that module.

4. For each .NET project:
   - Verify it appears in the .NET CLI table.
   - Read its `.csproj` and extract `<ProjectReference Include="..." />` entries — those are the actual project-to-project dependencies.
   - The map currently lists "Owns" only (no explicit dependency column for .NET). If the .csproj references a project not implied by the "Owns" description, flag it as drift.

5. For each module/project named in the map: confirm a matching file or `.csproj` exists in code.

6. Cross-check the boundary discipline rules at the bottom of the map. For each:
   - "`api.py` is the only module that touches HTTP" → grep for `fastapi`/`Request`/`Response` imports outside `api.py`. (Note: middleware/dependency helpers if any are also exempt.)
   - "`persistence.py` and `models.py` are the only modules that import SQLAlchemy" → grep `import sqlalchemy` / `from sqlalchemy` outside those two files. Treat type-only imports (`if TYPE_CHECKING:`) as exempt.
   - "`llm.py` is the only module that knows about Ollama" → grep `ollama` outside `llm.py`.
   - "`broker.py` is the only module that imports Saxo or HTTP broker clients" → grep `saxo` outside `broker.py`.
   - Each violation is **Critical** (the boundary is load-bearing).

7. Output the report in this shape:

   **Modules in code but not in map**
   - `<module path>` — proposed entry skeleton (1 short paragraph: what it owns and what it depends on).

   **Modules in map but not in code**
   - `<module name>` — likely cause (renamed / deleted / aspirational).

   **Dependency drift**
   - `<module name>` — map says X, code imports/references Y.

   **Ownership statements that need refresh**
   - `<module name>` — what the map claims, what the code suggests.

   **Boundary violations**
   - `<rule>` — `<file:line>` — snippet — why.

8. End with a verdict: "Map and code align" / "Minor drift (N items)" / "Significant drift (N items)".

Constraints:
- Do not modify the module map. This skill reports.
- Treat test files as paired with their source module; do not list them separately unless the pairing is missing (`engine/src/snapd_invest/foo.py` with no `engine/tests/unit/test_foo.py` is worth noting under "Ownership statements" as a soft observation, not as drift).
- Alembic migration files under `engine/alembic/versions/` are not modules — exclude them.
- Generated `OpenAPI` client code on the .NET side (if any) is exempt from dependency drift checks; flag separately if encountered.
