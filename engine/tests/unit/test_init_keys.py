"""Tests for `snapd_invest.tools.init_keys`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from snapd_invest.tools.init_keys import KeyAlreadyExistsError, run

if TYPE_CHECKING:
    from pathlib import Path


class TestInitKeys:
    def test_creates_env_file_with_key_when_missing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        run(env_file=env_file)
        content = env_file.read_text(encoding="utf-8")
        assert "SNAPDINVEST_ENCRYPTION_KEY=" in content
        line = next(
            ln for ln in content.splitlines() if ln.startswith("SNAPDINVEST_ENCRYPTION_KEY=")
        )
        _, _, value = line.partition("=")
        assert len(value) > 30

    def test_appends_to_existing_env_file_without_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SNAPDINVEST_SAXO_ENV=sim\n", encoding="utf-8")
        run(env_file=env_file)
        content = env_file.read_text(encoding="utf-8")
        assert "SNAPDINVEST_SAXO_ENV=sim" in content
        assert "SNAPDINVEST_ENCRYPTION_KEY=" in content

    def test_refuses_when_key_already_present(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SNAPDINVEST_ENCRYPTION_KEY=already-here\n", encoding="utf-8")
        with pytest.raises(KeyAlreadyExistsError):
            run(env_file=env_file)
        assert "SNAPDINVEST_ENCRYPTION_KEY=already-here" in env_file.read_text(encoding="utf-8")

    def test_generated_key_is_unique_per_invocation(self, tmp_path: Path) -> None:
        env_a = tmp_path / "a.env"
        env_b = tmp_path / "b.env"
        run(env_file=env_a)
        run(env_file=env_b)
        a = env_a.read_text(encoding="utf-8")
        b = env_b.read_text(encoding="utf-8")
        assert a != b
