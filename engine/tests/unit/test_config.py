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


class TestSaxoSettings:
    def test_defaults_are_none_when_env_vars_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "SNAPDINVEST_SAXO_ENV",
            "SNAPDINVEST_SAXO_CLIENT_ID",
            "SNAPDINVEST_SAXO_REDIRECT_URI",
            "SNAPDINVEST_ENCRYPTION_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.saxo_env is None
        assert settings.saxo_client_id is None
        assert settings.saxo_redirect_uri is None
        assert settings.encryption_key is None

    def test_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNAPDINVEST_SAXO_ENV", "sim")
        monkeypatch.setenv("SNAPDINVEST_SAXO_CLIENT_ID", "abc123")
        monkeypatch.setenv(
            "SNAPDINVEST_SAXO_REDIRECT_URI",
            "http://localhost:8000/v1/oauth/saxo/callback",
        )
        monkeypatch.setenv("SNAPDINVEST_ENCRYPTION_KEY", "k" * 44)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert settings.saxo_env == "sim"
        assert settings.saxo_client_id == "abc123"
        assert (
            settings.saxo_redirect_uri
            == "http://localhost:8000/v1/oauth/saxo/callback"
        )
        assert settings.encryption_key == "k" * 44

    def test_saxo_env_live_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SNAPDINVEST_SAXO_ENV", "live")
        with pytest.raises(ValidationError, match="live"):
            Settings(_env_file=None)  # type: ignore[call-arg]

    def test_saxo_env_invalid_value_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SNAPDINVEST_SAXO_ENV", "production")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]
