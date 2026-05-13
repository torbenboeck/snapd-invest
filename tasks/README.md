# Tasks

This directory holds the work queue for snapd-invest in a Ralph-loop friendly format.

Each task is a self-contained markdown file that an agent can read, implement, verify, and mark complete without further input from the user.

## Files

- `_template.md` — copy this when creating a new task.
- `_next.md` — pointer to the next available task, kept up to date so agents don't have to scan.
- `T-NNN-*.md` — actual tasks, numbered sequentially.

## Lifecycle

1. **Create** — copy `_template.md` to `T-NNN-short-slug.md`. Fill in all sections. Add to `_next.md` if it's next in queue.
2. **Claim** — agent sets status to `in-progress` and creates a feature branch.
3. **Implement** — agent modifies files within scope.
4. **Verify** — agent runs the `Verify` commands.
5. **Submit** — agent commits, pushes, opens PR.
6. **Done** — when merged, status moves to `done`. The file stays in `tasks/` as history.

## Guidelines for writing tasks

- **Acceptance criteria are the contract.** Be specific.
- **Scope is binding.** If the task balloons, split it.
- **Tests are part of acceptance, not an afterthought.**
- **Out of scope matters as much as in scope.** Prevents bloat.
- **Keep them small.** A task should fit in a single coding session (≤ 2 hours).

## Anti-patterns

- ❌ "Improve performance" — vague, can't verify.
- ❌ "Add agents" — too big, must be split per agent.
- ❌ "Refactor X" with no explicit problem statement — refactor toward what?
- ✅ "Add SMACrossoverStrategy that emits buy signal on golden cross. Acceptance: `test_sma_emits_buy_on_golden_cross` passes."
