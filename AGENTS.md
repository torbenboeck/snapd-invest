# AGENTS.md — Operating Manual for Claude Code

This file tells Claude Code (and other AI agents) how to work in this repository.
It is loaded automatically when an agent operates inside the repo root.

---

## Quick orientation

This is a **hybrid Python + .NET** repository:

- `engine/` — Python 3.12+ service (FastAPI). Owns trading logic and persistence.
- `cli/` — .NET 10 client (Spectre.Console). Owns user-facing UX.

When working in a subdirectory, the nearest `CLAUDE.md` provides additional context. Always read it before making changes.

---

## Build & test commands

The agent must verify changes by running these commands. CI runs the same.

### Python engine

```bash
cd engine
uv sync                          # install dependencies
uv run pytest                    # run all tests
uv run pytest tests/unit         # unit tests only
uv run pytest -k <pattern>       # specific test
uv run ruff check                # lint
uv run ruff format               # format
uv run mypy src                  # type check
uv run alembic upgrade head      # apply migrations
```

### .NET CLI

```bash
cd cli
dotnet restore
dotnet build /warnaserror
dotnet test
dotnet format --verify-no-changes
```

### Both at once

When at repo root:

```bash
make test       # runs engine + CLI tests
make lint       # runs ruff + dotnet format checks
make format     # applies formatting to both
```

---

## Before any commit

1. Run formatter for the stack you touched.
2. Run lint for the stack you touched.
3. Run tests for the stack you touched.
4. If you touched both stacks: run both.
5. Commit only when everything is green.

If any step fails, **fix it before committing**. Do not bypass with `--no-verify`.

---

## Task workflow (Ralph-loop friendly)

Work items live in `tasks/` as markdown files. The typical loop:

1. Read `tasks/_next.md` to find the next unblocked task (or use the Cowork TaskList).
2. Read the task file in full. Confirm acceptance criteria.
3. Create a feature branch: `feature/<task-id>-<short-slug>`.
4. Implement the smallest change that satisfies the acceptance criteria.
5. Run the verification commands listed in the task.
6. Commit with a message that references the task ID.
7. Push the branch and open a PR.
8. Mark the task as done.

### Task file format

See [`tasks/_template.md`](tasks/_template.md). Every task must have:

- **ID** (e.g. `T-001`)
- **Acceptance criteria** — concrete, verifiable
- **Files in scope** — explicit list to keep blast radius small
- **Verify** — exact commands to run after implementation
- **Out of scope** — what not to touch in this task

---

## Hard rules

These are non-negotiable. If a task seems to require breaking one, **stop and ask**.

- **Never force-push to `main`, `develop`, or any branch matching `release/*`.**
- **Never delete branches with `-D` outside of `feature/*` or `bugfix/*`.**
- **Never commit secrets, API keys, tokens, OAuth credentials, or `.env` files.**
- **Never call live brokers in tests.** Use `PaperBroker` or `FakeBroker`.
- **Never call live LLM endpoints in CI.** Use `FakeLlmProvider` or recorded responses.
- **Never use real `datetime.utcnow()` / `DateTime.UtcNow` in production code paths.** Use the injected clock.
- **Never bypass the risk gate.** Even human-approved trades pass through it.
- **Never modify files outside the task's "files in scope" list** without explicitly noting why in the PR.

---

## Branching

- Default branch: `main`. Protected — no direct pushes, no force-push, no deletion.
- Feature branches: `feature/<task-id>-<slug>`.
- Bugfix branches: `bugfix/<task-id>-<slug>`.
- Chore branches: `chore/<short-slug>` for cross-cutting maintenance work.
- Branches may be deleted by Claude Code only if they match `feature/*` or `bugfix/*` **and** are merged into `main`. Enforced by `.claude/hooks/pre_tool_bash.py`.

### Local branch protection

A repo-tracked git pre-commit hook in `scripts/git-hooks/pre-commit` rejects
direct commits to `main`, `develop`, and `release/*`. Activate it once after
cloning:

```bash
make install-hooks
# or:
git config core.hooksPath scripts/git-hooks
```

The hook protects against accidental direct commits — including manual ones,
not just Claude Code's. Bypass with `--no-verify` is reserved for emergency
hotfixes and is logged in the commit message.

---

## Commit messages

Format: `<type>(<scope>): <subject>`

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`.

Examples:

- `feat(strategy): add SMA crossover strategy`
- `fix(risk): correct position size calculation for short positions`
- `test(broker): add fake broker for unit tests`
- `chore(repo): scaffold engine module structure`

Include the task ID in the body if there is one:

```
feat(strategy): add SMA crossover strategy

Implements SMACrossoverStrategy in engine/src/algo_invest/strategy.py.
Generates buy on golden cross, sell on death cross.

Task: T-004
```

---

## Permissions and harness

Claude Code permissions are configured in `.claude/settings.json`. Some operations are denied outright (force-push, repo deletion, etc.). Others require explicit approval (any push, branch deletion).

Hooks in `.claude/hooks/` validate operations before execution. They are not optional — bypassing them is a violation of the operating manual.

Custom slash commands live in `.claude/commands/`. Use them; do not duplicate their logic inline.

---

## When in doubt

Ask. The user prefers being asked once over fixing a mess afterward.

If a task is ambiguous: refuse to proceed, write your specific questions, and mark the task as `blocked`.
