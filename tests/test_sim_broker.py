"""SimBroker: fills, slippage, commissions, idempotency, flattening."""

from __future__ import annotations

from datetime import datetime

import pytest

from trading_bot.config import FeesConfig
from trading_bot.execution.sim_broker import SimBroker
from trading_bot.models import MarketSnapshot, Order
from trading_bot.portfolio import Portfolio

T0 = datetime(2024, 3, 4, 16, 0)


@pytest.fixture
def broker() -> SimBroker:
    fees = FeesConfig(commission_per_share=0.005, min_commission=1.0, slippage_bps=10.0)
    broker = SimBroker(Portfolio(cash=100_000.0), fees)
    broker.on_snapshot(
        MarketSnapshot(timestamp=T0, bars={}, latest_prices={"AAPL": 100.0, "MSFT": 200.0})
    )
    return broker


def test_buy_fill_includes_slippage_and_commission(broker: SimBroker) -> None:
    fill = broker.submit_order(Order(symbol="AAPL", side="BUY", qty=100, client_order_id="a"))
    assert fill is not None
    # 10 bps slippage on a $100 buy -> fill at $100.10 (worse for us).
    assert fill.fill_price == pytest.approx(100.10)
    # 100 shares * 0.005 = 0.50, below the $1 minimum -> $1.
    assert fill.commission == pytest.approx(1.0)
    assert broker.get_positions()["AAPL"].qty == 100


def test_sell_realizes_pnl(broker: SimBroker) -> None:
    broker.submit_order(Order(symbol="AAPL", side="BUY", qty=100, client_order_id="a"))
    # Price rises to 110.
    broker.on_snapshot(MarketSnapshot(timestamp=T0, bars={}, latest_prices={"AAPL": 110.0}))
    fill = broker.submit_order(Order(symbol="AAPL", side="SELL", qty=100, client_order_id="b"))
    assert fill is not None
    assert fill.fill_price == pytest.approx(109.89)  # 110 minus 10 bps against us
    assert broker.get_positions() == {}
    account = broker.get_account()
    # We made roughly (109.89 - 100.10) * 100 minus $2 commissions.
    assert account.equity > 100_900


def test_duplicate_client_order_id_is_ignored(broker: SimBroker) -> None:
    order = Order(symbol="AAPL", side="BUY", qty=10, client_order_id="same-id")
    assert broker.submit_order(order) is not None
    assert broker.submit_order(order) is None  # idempotent retry
    assert broker.get_positions()["AAPL"].qty == 10  # not 20


def test_insufficient_cash_rejected(broker: SimBroker) -> None:
    order = Order(symbol="MSFT", side="BUY", qty=10_000, client_order_id="big")  # ~$2M
    assert broker.submit_order(order) is None
    assert broker.get_positions() == {}
    assert broker.get_account().cash == pytest.approx(100_000.0)


def test_unknown_symbol_rejected(broker: SimBroker) -> None:
    assert broker.submit_order(Order(symbol="NOPE", side="BUY", qty=1)) is None


def test_close_all_positions_flattens(broker: SimBroker) -> None:
    broker.submit_order(Order(symbol="AAPL", side="BUY", qty=50, client_order_id="a"))
    broker.submit_order(Order(symbol="MSFT", side="BUY", qty=20, client_order_id="b"))
    broker.close_all_positions()
    assert broker.get_positions() == {}
