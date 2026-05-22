"""Application configuration.

Configuration is loaded from environment variables (and an optional `.env` file
during local development). Values are validated by Pydantic at startup; an
invalid configuration causes a fail-fast crash rather than a runtime surprise.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationInfo, field_validator
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
    bar_refresh_interval_minutes: int = Field(
        default=5,
        ge=1,
        description=(
            "Minutes between SaxoBroker chart-refresh ticks. Each tick "
            "fetches the latest candles for every SIM watchlist instrument."
        ),
    )
    bar_refresh_horizon: Literal["1m", "5m", "15m", "1h", "60m", "1d"] = Field(
        default="1d",
        description=(
            "Candle size for the bar-refresh job. Must match the interval "
            "the active strategy reads via `load_recent_bars` (SMA default: 1d)."
        ),
    )
    bar_refresh_count: int = Field(
        default=250,
        ge=1,
        description=(
            "How many candles to fetch per refresh. 250 covers the SMA200 "
            "long_period default plus a margin."
        ),
    )

    # ------------------------------------------------------------------
    # MicroTrader strategy (SMA crossover) — env-overridable so the
    # operator can pick between conservative defaults (50/200/1d, the
    # classic golden-cross setup) and an aggressive smoke-test profile
    # (5/20/1h) without touching code.
    # ------------------------------------------------------------------
    microtrader_sma_short_period: int = Field(
        default=50,
        ge=2,
        description=(
            "Short SMA period for the MicroTrader. The shorter this is "
            "relative to `long_period`, the more often crossovers fire."
        ),
    )
    microtrader_sma_long_period: int = Field(
        default=200,
        ge=3,
        description=(
            "Long SMA period for the MicroTrader. Must be strictly greater than `short_period`."
        ),
    )
    microtrader_signal_quantity: Decimal = Field(
        default=Decimal("1"),
        gt=Decimal("0"),
        description=(
            "Quantity (in instrument units) emitted per buy/sell signal. "
            "FX pairs on Saxo SIM require a minimum lot — 5000 is safe."
        ),
    )

    @field_validator("microtrader_sma_long_period")
    @classmethod
    def _long_period_must_exceed_short(cls, value: int, info: ValidationInfo) -> int:
        short = info.data.get("microtrader_sma_short_period")
        if isinstance(short, int) and value <= short:
            raise ValueError(
                f"microtrader_sma_long_period ({value}) must be > "
                f"microtrader_sma_short_period ({short})"
            )
        return value

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

    # ------------------------------------------------------------------
    # Saxo OAuth + at-rest encryption
    # ------------------------------------------------------------------
    saxo_env: str | None = Field(
        default=None,
        description=(
            "Saxo environment selector. Only 'sim' is permitted at MVP; 'live' is hard-blocked."
        ),
    )
    saxo_client_id: str | None = Field(
        default=None,
        description="Saxo OAuth app key (the 'client_id' for Authorization Code + PKCE).",
    )
    saxo_redirect_uri: str | None = Field(
        default=None,
        description=(
            "Callback URL registered with Saxo. Must match exactly, "
            "including scheme and trailing slash."
        ),
    )
    encryption_key: str | None = Field(
        default=None,
        description=(
            "Fernet master key for at-rest encryption (oauth_tokens). "
            "Generate via `make init-keys`."
        ),
    )

    @field_validator("watchlist", mode="before")
    @classmethod
    def _split_watchlist_from_string(cls, value: object) -> object:
        """Allow comma-separated strings for env-var convenience."""
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @field_validator("saxo_env")
    @classmethod
    def _validate_saxo_env(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v == "live":
            raise ValueError(
                "SNAPDINVEST_SAXO_ENV=live is hard-blocked at MVP. Use 'sim' for Saxo simulation."
            )
        if v != "sim":
            raise ValueError(f"SNAPDINVEST_SAXO_ENV must be 'sim' (or unset), got {v!r}")
        return v

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
