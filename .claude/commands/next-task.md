---
description: Pick the next available task from tasks/ and start working on it.
---

Find the next available task and start implementing it:

1. Read `tasks/_next.md` to see the next unblocked task ID. If it's missing or the file says "(none)", check `tasks/` directly for the lowest-numbered task with status `pending`.
2. Open the task file (e.g. `tasks/T-007-add-foo.md`) and read it fully.
3. Verify the acceptance criteria are clear. If anything is ambiguous, stop and ask the user.
4. Create a feature branch: `git checkout -b feature/T-NNN-short-slug`.
5. Implement the task. Touch only files listed in "Files in scope".
6. Run the verification commands from the task.
7. If all green, commit with format `<type>(<scope>): <subject>` and reference the task ID in the body.
8. Push the branch.
9. Update the task file to mark it `done` and update `tasks/_next.md`.

If any verification fails, do not commit. Report the failure and stop.
