"""Tests for `snapd_invest.config`."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from snapd_invest.config import Settings


class TestSettings:
    def test_defaults(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # Clear any SNAPDINVEST_* env vars
        for key in list(monkeypatch.__dict__.get("_setitem", [])):
            if key.startswith("SNAPDINVEST_"):
                monkeypatch.delenv(key, raising=False)

        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.db_path == Path("./data/snapd_invest.db")
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
        monkeypatch.setenv("SNAPDINVEST_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("SNAPDINVEST_API_PORT", "9000")
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.log_level == "DEBUG"
        assert s.api_port == 9000


def test_scheduler_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.scheduler_enabled is True
    assert s.microtrader_interval_minutes == 1
    assert s.agent_interval_minutes == 30
    assert s.recommendation_expire_interval_minutes == 5
    assert s.default_account_name == "paper"
    assert s.watchlist == ["AAPL@NASDAQ"]


def test_scheduler_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNAPDINVEST_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SNAPDINVEST_MICROTRADER_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("SNAPDINVEST_AGENT_INTERVAL_MINUTES", "15")
    monkeypatch.setenv("SNAPDINVEST_WATCHLIST", "AAPL@NASDAQ,BTC-USD@BINANCE")
    monkeypatch.setenv("SNAPDINVEST_DEFAULT_ACCOUNT_NAME", "sim-account")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.scheduler_enabled is False
    assert s.microtrader_interval_minutes == 5
    assert s.agent_interval_minutes == 15
    assert s.watchlist == ["AAPL@NASDAQ", "BTC-USD@BINANCE"]
    assert s.default_account_name == "sim-account"


def test_scheduler_interval_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, microtrader_interval_minutes=0)  # type: ignore[call-arg]
