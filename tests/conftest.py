"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading_bot.config import (
    BacktestConfig,
    EnvSettings,
    FeesConfig,
    RiskConfig,
    SentimentConfig,
    Settings,
)
from trading_bot.models import Timeframe

# Env vars the bot reads. Cleared before every test so a developer's real
# shell environment (or .env) can never change test results.
BOT_ENV_VARS = [
    "IBKR_HOST",
    "IBKR_PORT",
    "IBKR_CLIENT_ID",
    "IBKR_ACCOUNT",
    "TRADING_MODE",
    "GEMINI_API_KEY",
    "SENTIMENT_PROVIDER",
    "ALERT_WEBHOOK_URL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in BOT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    """A .env file with nothing in it -- all EnvSettings defaults apply."""
    env_file = tmp_path / ".env"
    env_file.write_text("")
    return env_file


@pytest.fixture
def settings(empty_env_file: Path) -> Settings:
    """A complete, valid Settings object built in memory (no file I/O)."""
    return Settings(
        env=EnvSettings(_env_file=empty_env_file),  # type: ignore[call-arg]
        symbols=["SPY", "AAPL", "MSFT"],
        timeframe=Timeframe.DAY_1,
        poll_interval_seconds=60,
        dry_run=True,
        risk=RiskConfig(
            max_position_pct=0.25,
            max_total_exposure_pct=0.80,
            max_daily_loss_pct=0.03,
            stop_loss_pct=0.05,
        ),
        fees=FeesConfig(),
        sentiment=SentimentConfig(),
        backtest=BacktestConfig(
            start=date(2024, 1, 2),
            end=date(2024, 6, 28),
            initial_cash=100_000.0,
        ),
    )
