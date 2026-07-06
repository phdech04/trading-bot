"""Kill switch: cancel first, then close, then halt -- from every entry point."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from trading_bot.config import Settings
from trading_bot.data.synthetic import generate_bars
from trading_bot.data.replay import ReplayDataProvider
from trading_bot.engine.core import TradingEngine
from trading_bot.execution.base import Broker
from trading_bot.execution.kill_switch import flatten_all
from trading_bot.models import AccountInfo, Fill, Order, Position, Timeframe
from trading_bot.risk.risk_manager import RiskManager
from trading_bot.strategy.placeholder import BuyAndHoldPlaceholder


class RecordingBroker(Broker):
    """Fake broker that records the order of calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.equity = 100_000.0

    def submit_order(self, order: Order) -> Fill | None:
        self.calls.append(f"submit:{order.side}:{order.symbol}")
        return None

    def get_positions(self) -> dict[str, Position]:
        return {}

    def get_account(self) -> AccountInfo:
        return AccountInfo("FAKE", self.equity, self.equity, self.equity)

    def cancel_all(self) -> None:
        self.calls.append("cancel_all")

    def close_all_positions(self) -> None:
        self.calls.append("close_all_positions")


def test_flatten_all_cancels_before_closing() -> None:
    broker = RecordingBroker()
    flatten_all(broker)
    assert broker.calls == ["cancel_all", "close_all_positions"]


@pytest.fixture
def engine_parts(settings: Settings):
    bars = generate_bars(
        settings.symbols,
        Timeframe.DAY_1,
        datetime(2024, 1, 2),
        datetime(2024, 3, 29),
    )
    data = ReplayDataProvider(bars)
    strategy = BuyAndHoldPlaceholder(settings.symbols)
    return bars, data, strategy


def test_daily_loss_breach_fires_kill_switch(settings: Settings, engine_parts) -> None:
    _, data, strategy = engine_parts
    broker = RecordingBroker()
    risk = RiskManager(settings.risk)
    # Force the breach: pretend the day started much richer than we are now.
    risk._current_day = datetime(2024, 1, 2).date()
    risk._day_start_equity = 200_000.0

    live_settings = settings.model_copy(update={"dry_run": False})
    engine = TradingEngine(data, strategy, risk, broker, live_settings)
    engine.run()

    assert engine.halted
    assert "cancel_all" in broker.calls
    assert "close_all_positions" in broker.calls
    # Breach on the very first bar: flattening must be the ONLY broker orders.
    assert not any(c.startswith("submit:BUY") for c in broker.calls)


def test_dry_run_breach_halts_without_flattening(settings: Settings, engine_parts) -> None:
    """In dry-run we never sent orders, so the kill switch must NOT touch
    real positions -- halt only."""
    _, data, strategy = engine_parts
    broker = RecordingBroker()
    risk = RiskManager(settings.risk)
    risk._current_day = datetime(2024, 1, 2).date()
    risk._day_start_equity = 200_000.0

    engine = TradingEngine(data, strategy, risk, broker, settings)  # dry_run=True
    engine.run()

    assert engine.halted
    assert broker.calls == []  # nothing touched


def test_stop_halts_the_loop_early(settings: Settings, engine_parts) -> None:
    bars, data, strategy = engine_parts
    broker = RecordingBroker()
    risk = RiskManager(settings.risk)

    engine = TradingEngine(data, strategy, risk, broker, settings)
    engine.stop()  # requested before run: loop must exit on the first check
    engine.run()
    assert engine.halted
