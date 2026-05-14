# /review-pr

Review the staged changes (or the diff specified by $ARGUMENTS — a commit SHA, branch ref, or `staged`/`branch`) against Snapd's Forbidden Patterns and supporting conventions.

Procedure:

1. If $ARGUMENTS is empty or `staged`: run `git diff --staged`.
   If $ARGUMENTS is a branch ref: run `git diff origin/main..$ARGUMENTS`.
   If $ARGUMENTS is a commit SHA: run `git diff $ARGUMENTS^..$ARGUMENTS`.
2. Read `CLAUDE.md` and extract the Forbidden Patterns section.
3. For each Forbidden Pattern, check the diff for violations. Quote the specific file/line for each.
4. Then check:
   - Naming alignment with `docs/ubiquitous-language.md` for any new public types in the diff.
   - Tests added for any new public types in `Snapd.Core`.
   - `docs/architecture/decision-log.md` updated if a new entry was introduced in `Directory.Packages.props`.
5. Output as a checklist with ✅/❌ per category:
   - **Critical (block merge):** violations of Forbidden Patterns.
   - **Recommended (fix before merge):** missing tests, naming drift, missing ADR.
   - **Optional (nice to have):** refactor opportunities, documentation gaps.
6. End with a one-line verdict: **Ready to merge** / **Minor fixes needed** / **Do not merge**.

Constraints:
- Do not modify code. This command reports.
- Distinguish heuristic findings (could be false positive) from confident findings.
- If the diff is empty, ask the user what they meant by $ARGUMENTS.
