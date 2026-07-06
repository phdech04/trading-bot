"""End-to-end: synthetic data -> replay -> placeholder -> risk -> sim fills -> PnL."""

from __future__ import annotations

from datetime import datetime

import pytest

from trading_bot.backtest.engine import BacktestResult, run_backtest
from trading_bot.config import Settings
from trading_bot.data.synthetic import generate_bars
from trading_bot.models import Timeframe


@pytest.fixture
def result(settings: Settings) -> BacktestResult:
    bars = generate_bars(
        settings.symbols,
        Timeframe.DAY_1,
        datetime(2024, 1, 2),
        datetime(2024, 6, 28),
    )
    return run_backtest(settings, bars)


def test_backtest_completes_and_trades(result: BacktestResult, settings: Settings) -> None:
    # Buy-and-hold must have bought each symbol at least once.
    assert result.n_trades >= len(settings.symbols)
    assert result.n_bars > 100  # ~6 months of business days
    assert result.final_equity > 0


def test_backtest_accounting_is_consistent(result: BacktestResult) -> None:
    # equity change == realized + unrealized - commissions (fills already
    # embed slippage in their prices, so no separate term for it).
    pnl_from_parts = result.realized_pnl + result.unrealized_pnl - result.total_commission
    assert result.final_equity - result.initial_cash == pytest.approx(
        pnl_from_parts, abs=0.01
    )


def test_summary_mentions_the_key_numbers(result: BacktestResult) -> None:
    text = result.summary()
    assert "Final equity" in text
    assert "Trades executed" in text
    assert f"{result.n_trades:14d}" in text


def test_backtest_is_reproducible(settings: Settings) -> None:
    """Same settings + same (deterministic) data -> identical results."""
    bars = generate_bars(
        settings.symbols, Timeframe.DAY_1, datetime(2024, 1, 2), datetime(2024, 3, 29)
    )
    a = run_backtest(settings, bars)
    b = run_backtest(settings, bars)
    assert a.final_equity == b.final_equity
    assert a.n_trades == b.n_trades
