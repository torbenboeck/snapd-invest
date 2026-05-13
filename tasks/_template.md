# T-NNN — Short title

**Status:** pending | in-progress | done | blocked
**Created:** YYYY-MM-DD
**Owner:** (Claude / user)
**Blocked by:** (task IDs or "—")

## Context

One paragraph: why does this task exist? What problem does it solve?

## Acceptance criteria

Concrete, verifiable. A reviewer should be able to read this and decide PASS or FAIL without ambiguity.

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Files in scope

Files this task is allowed to create or modify. Anything outside this list requires a follow-up task or an explicit note in the PR.

- `engine/src/snapd_invest/example.py`
- `engine/tests/unit/test_example.py`

## Out of scope

What this task explicitly does NOT touch. Useful to prevent scope creep.

- Doesn't modify `api.py`
- Doesn't add new dependencies

## Verify

Exact commands the agent must run to verify completion. CI runs the same.

```bash
cd engine
uv run ruff check
uv run mypy src
uv run pytest tests/unit/test_example.py -v
```

## Notes

Anything else worth knowing — design decisions, references, prior art.
