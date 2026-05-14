"""Application configuration.

Configuration is loaded from environment variables (and an optional `.env` file
during local development). Values are validated by Pydantic at startup; an
invalid configuration causes a fail-fast crash rather than a runtime surprise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level settings for the engine."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SNAPDINVEST_",
        extra="ignore",
        case_sensitive=False,
    )

    db_path: Path = Field(
        default=Path("./data/snapd_invest.db"),
        description="SQLite database file path. Created on first run if missing.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Minimum log level.",
    )
    log_format: Literal["console", "json"] = Field(
        default="console",
        description="`console` for pretty local output, `json` for production-friendly logs.",
    )
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000)

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------
    scheduler_enabled: bool = Field(
        default=True,
        description="Start the background scheduler on app startup. Disable for tests/dev.",
    )
    microtrader_interval_minutes: int = Field(
        default=1,
        ge=1,
        description="Minutes between MicroTrader ticks.",
    )
    agent_interval_minutes: int = Field(
        default=30,
        ge=1,
        description="Minutes between agent runs.",
    )
    recommendation_expire_interval_minutes: int = Field(
        default=5,
        ge=1,
        description="Minutes between recommendation-expiry sweeps.",
    )

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------
    watchlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["AAPL@NASDAQ"],
        description=(
            "Instruments the scheduler runs strategies/agents against. "
            "Comma-separated SYMBOL@EXCHANGE entries when set via env."
        ),
    )
    default_account_name: str = Field(
        default="paper",
        description="Account name the scheduled jobs operate against.",
    )

    @field_validator("watchlist", mode="before")
    @classmethod
    def _split_watchlist_from_string(cls, value: object) -> object:
        """Allow comma-separated strings for env-var convenience."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @property
    def db_url(self) -> str:
        """Async SQLAlchemy URL for the configured SQLite path."""
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def db_url_sync(self) -> str:
        """Sync SQLAlchemy URL (used by Alembic migrations)."""
        return f"sqlite:///{self.db_path.as_posix()}"


def get_settings() -> Settings:
    """Build a `Settings` instance. Cached at the FastAPI app level via DI."""
    return Settings()
