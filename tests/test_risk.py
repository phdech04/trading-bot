"""RiskManager: caps oversized orders, enforces exposure, trips on daily loss."""

from __future__ import annotations

from datetime import datetime

import pytest

from trading_bot.config import RiskConfig
from trading_bot.models import AccountInfo, Position, Signal
from trading_bot.risk.risk_manager import RiskManager

T0 = datetime(2024, 3, 4, 16, 0)
T1 = datetime(2024, 3, 4, 16, 30)  # same day, later bar
T2 = datetime(2024, 3, 5, 16, 0)  # next day


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_position_pct=0.10,
        max_total_exposure_pct=0.25,
        max_daily_loss_pct=0.03,
        stop_loss_pct=0.05,
    )


@pytest.fixture
def risk(risk_config: RiskConfig) -> RiskManager:
    return RiskManager(risk_config)


def account(equity: float = 100_000.0, cash: float | None = None) -> AccountInfo:
    return AccountInfo(
        account_id="TEST",
        equity=equity,
        cash=cash if cash is not None else equity,
        buying_power=cash if cash is not None else equity,
    )


def test_oversized_position_is_capped(risk: RiskManager) -> None:
    """A 50% target weight must be capped to max_position_pct (10%)."""
    signal = Signal(timestamp=T0, target_weights={"AAPL": 0.50})
    orders = risk.evaluate(signal, account(), positions={}, prices={"AAPL": 100.0})
    assert len(orders) == 1
    # 10% of 100k equity at $100/share = 100 shares, not 500.
    assert orders[0].qty == 100
    assert orders[0].side == "BUY"


def test_total_exposure_is_enforced(risk: RiskManager) -> None:
    """Four 10% buys would be 40% exposure; the 25% cap must trim the batch."""
    weights = {sym: 0.10 for sym in ["AAPL", "MSFT", "NVDA", "SPY"]}
    signal = Signal(timestamp=T0, target_weights=weights)
    prices = dict.fromkeys(weights, 100.0)
    orders = risk.evaluate(signal, account(), positions={}, prices=prices)
    bought_value = sum(o.qty * prices[o.symbol] for o in orders if o.side == "BUY")
    assert bought_value <= 0.25 * 100_000 + 1e-6


def test_buys_never_exceed_cash(risk: RiskManager) -> None:
    """With only $500 cash, a 10% ($10k) target must shrink to what cash allows."""
    signal = Signal(timestamp=T0, target_weights={"AAPL": 0.10})
    acct = account(equity=100_000.0, cash=500.0)
    orders = risk.evaluate(signal, acct, positions={}, prices={"AAPL": 100.0})
    total_cost = sum(o.qty * 100.0 for o in orders)
    assert total_cost <= 500.0


def test_daily_loss_breach_blocks_all_orders(risk: RiskManager) -> None:
    """Losing more than max_daily_loss_pct intraday must block every order."""
    # First call of the day establishes the 100k baseline.
    baseline = Signal(timestamp=T0, target_weights={})
    assert risk.evaluate(baseline, account(100_000.0), {}, {}) == []
    assert risk.daily_loss_breached is False

    # Later the same day equity is down 4% (limit is 3%).
    signal = Signal(timestamp=T1, target_weights={"AAPL": 0.05})
    orders = risk.evaluate(signal, account(96_000.0), positions={}, prices={"AAPL": 100.0})
    assert orders == []
    assert risk.daily_loss_breached is True

    # And the breach is sticky -- even next day with recovered equity.
    signal = Signal(timestamp=T2, target_weights={"AAPL": 0.05})
    assert risk.evaluate(signal, account(100_000.0), {}, {"AAPL": 100.0}) == []


def test_stop_loss_exits_losing_position(risk: RiskManager) -> None:
    """Price 6% below avg cost (stop is 5%) must emit a full-size SELL."""
    positions = {"AAPL": Position(symbol="AAPL", qty=50, avg_cost=100.0)}
    # Strategy still wants to hold AAPL -- the stop-loss must win.
    signal = Signal(timestamp=T0, target_weights={"AAPL": 0.05})
    orders = risk.evaluate(signal, account(), positions, prices={"AAPL": 94.0})
    sells = [o for o in orders if o.symbol == "AAPL" and o.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].qty == 50
    # No contradictory BUY for the same symbol in the same bar.
    assert not any(o.symbol == "AAPL" and o.side == "BUY" for o in orders)


def test_tiny_rebalance_is_skipped(risk: RiskManager) -> None:
    """A 1-share drift (~$100 < min_order_value $200) must not churn an order."""
    positions = {"AAPL": Position(symbol="AAPL", qty=99, avg_cost=99.0)}
    signal = Signal(timestamp=T0, target_weights={"AAPL": 0.10})  # target = 100 shares
    orders = risk.evaluate(signal, account(), positions, prices={"AAPL": 100.0})
    assert orders == []


def test_dropped_symbol_is_fully_exited(risk: RiskManager) -> None:
    """Holding a symbol the strategy no longer targets -> full exit, even if
    the position is smaller than the dead-band."""
    positions = {"AAPL": Position(symbol="AAPL", qty=1, avg_cost=100.0)}
    signal = Signal(timestamp=T0, target_weights={})  # AAPL dropped
    orders = risk.evaluate(signal, account(), positions, prices={"AAPL": 101.0})
    assert len(orders) == 1
    assert orders[0].side == "SELL"
    assert orders[0].qty == 1


def test_no_orders_when_already_at_target(risk: RiskManager) -> None:
    positions = {"AAPL": Position(symbol="AAPL", qty=100, avg_cost=99.0)}
    signal = Signal(timestamp=T0, target_weights={"AAPL": 0.10})
    orders = risk.evaluate(signal, account(), positions, prices={"AAPL": 100.0})
    assert orders == []


def test_client_order_ids_are_deterministic(risk_config: RiskConfig) -> None:
    """Same intent -> same ID, so brokers can de-duplicate retries."""
    signal = Signal(timestamp=T0, target_weights={"AAPL": 0.10})
    ids = []
    for _ in range(2):
        rm = RiskManager(risk_config)
        orders = rm.evaluate(signal, account(), {}, {"AAPL": 100.0})
        ids.append(orders[0].client_order_id)
    assert ids[0] == ids[1]
    assert "AAPL" in ids[0] and "BUY" in ids[0]
