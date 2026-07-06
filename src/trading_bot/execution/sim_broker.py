"""SimBroker: simulated executions for backtesting.

Fills market orders instantly at the current bar's close, worsened by a
configurable slippage, plus IBKR-style per-share commission. Deliberately
simple -- the goal of the scaffold is a *believable* cost model, not a
microstructure simulator.
"""

from __future__ import annotations

import logging
from datetime import datetime

from trading_bot.config import FeesConfig
from trading_bot.execution.base import Broker
from trading_bot.models import AccountInfo, Fill, MarketSnapshot, Order, Position
from trading_bot.portfolio import Portfolio

logger = logging.getLogger(__name__)


class SimBroker(Broker):
    def __init__(self, portfolio: Portfolio, fees: FeesConfig) -> None:
        self._portfolio = portfolio
        self._fees = fees
        self._prices: dict[str, float] = {}
        self._now: datetime | None = None
        # client_order_ids we've already filled -- makes duplicate submissions
        # a no-op, mirroring the idempotency real brokers give us via orderRef.
        self._seen_order_ids: set[str] = set()

    def on_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Called by the engine before each iteration so the sim knows the
        current time and prices to fill against. (Live brokers don't need
        this -- the real market plays that role.)"""
        self._prices.update(snapshot.latest_prices)
        self._now = snapshot.timestamp

    # --- Broker interface ----------------------------------------------------

    def submit_order(self, order: Order) -> Fill | None:
        if order.client_order_id and order.client_order_id in self._seen_order_ids:
            logger.warning(
                "Duplicate client_order_id %s -- ignoring (idempotency)", order.client_order_id
            )
            return None

        price = self._prices.get(order.symbol)
        if price is None or self._now is None:
            logger.error("SimBroker has no price for %s -- rejecting order", order.symbol)
            return None

        # Slippage always works against you: buys fill higher, sells lower.
        slip = price * self._fees.slippage_bps / 10_000
        fill_price = price + slip if order.side == "BUY" else price - slip
        commission = max(self._fees.min_commission, order.qty * self._fees.commission_per_share)

        fill = Fill(
            order=order,
            filled_qty=order.qty,
            fill_price=round(fill_price, 4),
            commission=round(commission, 2),
            timestamp=self._now,
        )
        try:
            self._portfolio.apply_fill(fill)
        except ValueError as exc:
            # e.g. not enough cash/shares. The risk manager should prevent
            # this; if it happens anyway we reject rather than corrupt state.
            logger.error("SimBroker rejected %s: %s", order, exc)
            return None

        if order.client_order_id:
            self._seen_order_ids.add(order.client_order_id)
        logger.info(
            "FILL %s %d %s @ %.4f (commission %.2f) id=%s",
            order.side,
            fill.filled_qty,
            order.symbol,
            fill.fill_price,
            fill.commission,
            order.client_order_id,
        )
        return fill

    def get_positions(self) -> dict[str, Position]:
        return self._portfolio.get_positions()

    def get_account(self) -> AccountInfo:
        equity = self._portfolio.equity(self._prices)
        return AccountInfo(
            account_id="SIM",
            equity=equity,
            cash=self._portfolio.cash,
            buying_power=self._portfolio.cash,  # no margin in the sim
        )

    def cancel_all(self) -> None:
        # Market orders fill instantly in the sim, so there is never anything
        # resting to cancel -- but the kill switch calls this on every broker.
        logger.info("SimBroker.cancel_all: no resting orders in simulation")

    def close_all_positions(self) -> None:
        for symbol, pos in self.get_positions().items():
            self.submit_order(
                Order(
                    symbol=symbol,
                    side="SELL",
                    qty=pos.qty,
                    client_order_id=f"flatten-{symbol}-{self._now:%Y%m%d-%H%M%S}",
                )
            )
