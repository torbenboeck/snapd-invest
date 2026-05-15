"""Generate a fresh `SNAPDINVEST_ENCRYPTION_KEY` and append it to `.env`.

Invoked from the project Makefile (`make init-keys`). Refuses to overwrite
an existing key — rotation is a deliberate manual step, not a side-effect
of running this tool twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

from snapd_invest.crypto import generate_key

ENV_VAR = "SNAPDINVEST_ENCRYPTION_KEY"


class KeyAlreadyExistsError(RuntimeError):
    """Raised when the target `.env` already declares `SNAPDINVEST_ENCRYPTION_KEY`."""


def run(env_file: Path) -> str:
    """Generate a key and append it to `env_file`.

    Returns the generated key. Raises `KeyAlreadyExistsError` if the file
    already declares the env var.
    """
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{ENV_VAR}="):
                raise KeyAlreadyExistsError(
                    f"{ENV_VAR} already set in {env_file}. To rotate, remove the line first."
                )

    key = generate_key()
    needs_separator = (
        env_file.exists() and not env_file.read_text(encoding="utf-8").endswith("\n")
    )
    separator = "\n" if needs_separator else ""
    with env_file.open("a", encoding="utf-8") as f:
        f.write(f"{separator}{ENV_VAR}={key}\n")
    return key


def main() -> int:
    env_file = Path(__file__).resolve().parents[3] / ".env"  # engine/.env
    try:
        run(env_file=env_file)
    except KeyAlreadyExistsError as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 1
    sys.stdout.write(f"wrote {ENV_VAR} to {env_file}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
