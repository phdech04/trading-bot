"""Typed configuration: merges `.env` (secrets) + `config/config.yaml` (settings).

Everything the bot needs lands in ONE validated `Settings` object created by
`load_settings()`. If a required key is missing or has the wrong type, startup
fails immediately with a message naming the exact field -- never halfway
through a trading session.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_bot.models import Timeframe


class ConfigError(Exception):
    """Raised with a human-readable message when configuration is invalid."""


class EnvSettings(BaseSettings):
    """Secrets and connection details, loaded from `.env` / environment variables.

    Field names map to env vars case-insensitively: `ibkr_host` <- IBKR_HOST.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497  # 7497 TWS paper / 7496 TWS live / 4002 GW paper / 4001 GW live
    ibkr_client_id: int = 1
    ibkr_account: str = ""  # optional; paper accounts usually start with "DU"

    # SAFETY: paper unless explicitly set to "live" (and run_live.py adds more guards).
    trading_mode: Literal["paper", "live"] = "paper"

    gemini_api_key: str = ""  # required only if sentiment.enabled with provider "gemini"
    sentiment_provider: Literal["gemini", "ollama"] = "gemini"

    alert_webhook_url: str = ""  # optional Slack/Telegram-style webhook


class RiskConfig(BaseModel):
    """All values are fractions of account equity (0.25 = 25%). No defaults on

    purpose: risk limits are important enough that we make you write them down.
    """

    max_position_pct: float = Field(gt=0, le=1)
    max_total_exposure_pct: float = Field(gt=0, le=1)
    max_daily_loss_pct: float = Field(gt=0, le=1)
    stop_loss_pct: float = Field(gt=0, le=1)
    # Dead-band: skip rebalance orders smaller than this many dollars, so tiny
    # target drifts don't churn 1-share orders that mostly pay commission.
    min_order_value: float = Field(default=200.0, ge=0)


class FeesConfig(BaseModel):
    """Backtest fill-cost model (SimBroker)."""

    commission_per_share: float = 0.005
    min_commission: float = 1.0
    slippage_bps: float = 2.0  # applied against you on every simulated fill


class SentimentConfig(BaseModel):
    enabled: bool = False
    gemini_model: str = "gemini-2.0-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    news_lookback_hours: int = 24
    refresh_minutes: int = 60  # how often to re-fetch news and re-score


class BacktestConfig(BaseModel):
    start: date
    end: date
    initial_cash: float = 100_000.0


class Settings(BaseModel):
    """The one object the rest of the bot reads configuration from."""

    env: EnvSettings
    symbols: list[str] = Field(min_length=1)
    timeframe: Timeframe
    poll_interval_seconds: int = 60
    dry_run: bool = True
    risk: RiskConfig
    fees: FeesConfig = FeesConfig()
    sentiment: SentimentConfig = SentimentConfig()
    backtest: BacktestConfig


def _format_validation_error(exc: ValidationError, source: str) -> str:
    """Turn pydantic's error list into a short, readable report."""
    lines = [f"Invalid configuration in {source}:"]
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "(top level)"
        lines.append(f"  - {location}: {err['msg']}")
    return "\n".join(lines)


def load_settings(
    config_path: str | Path = "config/config.yaml",
    env_file: str | Path = ".env",
) -> Settings:
    """Load + validate all configuration. Raises ConfigError on any problem.

    `env_file` is overridable so tests can point at a temporary .env
    instead of the real one.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}\n"
            "Expected non-secret settings in config/config.yaml (see README)."
        )

    with open(config_path) as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping, got: {type(raw).__name__}")

    try:
        # _env_file tells pydantic-settings which dotenv file to read.
        # Real environment variables still take precedence over the file.
        env = EnvSettings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, f"{env_file} / environment")) from exc

    try:
        return Settings(env=env, **raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, str(config_path))) from exc
