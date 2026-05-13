"""Tests for `algo_invest.config`."""

from __future__ import annotations

from pathlib import Path

from algo_invest.config import Settings


class TestSettings:
    def test_defaults(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # Clear any ALGOINVEST_* env vars
        for key in list(monkeypatch.__dict__.get("_setitem", [])):
            if key.startswith("ALGOINVEST_"):
                monkeypatch.delenv(key, raising=False)

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_path == Path("./data/algo_invest.db")
        assert s.log_level == "INFO"
        assert s.log_format == "console"
        assert s.api_host == "127.0.0.1"
        assert s.api_port == 8000

    def test_db_url_uses_async_driver(self) -> None:
        s = Settings(_env_file=None, db_path=Path("/tmp/test.db"))  # type: ignore[call-arg]
        assert s.db_url == "sqlite+aiosqlite:////tmp/test.db"

    def test_db_url_sync_omits_async_driver(self) -> None:
        s = Settings(_env_file=None, db_path=Path("/tmp/test.db"))  # type: ignore[call-arg]
        assert s.db_url_sync == "sqlite:////tmp/test.db"

    def test_env_override(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("ALGOINVEST_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("ALGOINVEST_API_PORT", "9000")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.log_level == "DEBUG"
        assert s.api_port == 9000
