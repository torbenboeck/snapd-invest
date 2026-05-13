#!/usr/bin/env python3
"""Pre-tool hook for Bash commands.

Validates Bash commands before they execute. Blocks dangerous patterns that
aren't covered by the deny list in settings.json, or that need context-aware
validation.

Receives a JSON payload on stdin:
  {
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    ...
  }

Exit codes:
  0  - allow execution
  1  - block execution (message printed to stderr is shown to Claude)
  2  - block execution AND signal Claude to revise its approach (stronger)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("pre_tool_bash: could not parse stdin payload", file=sys.stderr)
        return 0  # don't block if we can't parse - fail open for non-bash

    if payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "")

    # ---- Branch deletion safety ----
    # Allow `git branch -d` / `-D` only for feature/* and bugfix/* branches
    # that are already merged to main.
    branch_delete_match = re.match(
        r"^\s*git\s+branch\s+(-d|-D)\s+(\S+)", command
    )
    if branch_delete_match:
        flag, branch = branch_delete_match.group(1), branch_delete_match.group(2)
        if not re.match(r"^(feature|bugfix)/", branch):
            print(
                f"BLOCKED: branch deletion only allowed for feature/* and bugfix/*; got '{branch}'",
                file=sys.stderr,
            )
            return 2
        try:
            merged = subprocess.run(
                ["git", "branch", "--merged", "main"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            merged_branches = {
                line.strip().lstrip("* ").strip()
                for line in merged.stdout.splitlines()
            }
            if branch not in merged_branches:
                print(
                    f"BLOCKED: branch '{branch}' is not merged to main; refuse to delete",
                    file=sys.stderr,
                )
                return 2
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print(
                "pre_tool_bash: could not verify merged status; failing closed",
                file=sys.stderr,
            )
            return 2

    # ---- Secrets safety ----
    if re.search(r"\b(cat|less|more|head|tail)\s+\.env\b", command):
        print("BLOCKED: do not print .env contents", file=sys.stderr)
        return 1

    if re.search(r"\bgit\s+add\s+.*\.env\b", command):
        print("BLOCKED: .env files must never be committed", file=sys.stderr)
        return 1

    # ---- Live broker safety ----
    if re.search(r"SAXO_ENV\s*=\s*live", command):
        print(
            "BLOCKED: SAXO_ENV=live must be set manually outside Claude's control",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
