"""RiskManager: the only gate between a Strategy's wishes and the Broker.

The engine hands every Signal to `evaluate()`, and only submits the orders it
returns. That means NO order can reach a broker without passing:

  1. the daily-loss circuit breaker (blocks everything for the rest of the day),
  2. stop-loss exits on losing positions,
  3. per-position size caps,
  4. total-exposure and available-cash caps.

Every cap or rejection is logged with its reason -- the logs should always be
able to answer "why did/didn't the bot trade?".
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime

from trading_bot.config import RiskConfig
from trading_bot.models import AccountInfo, Order, Position, Signal
from trading_bot.utils import make_client_order_id

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        # Equity at the start of the current trading day, used as the
        # baseline for the daily-loss circuit breaker.
        self._day_start_equity: float | None = None
        self._current_day: date | None = None
        # Sticky: once the daily loss limit trips, it stays tripped until
        # the process is restarted. The engine reads this and halts.
        self.daily_loss_breached = False
        # Symbols stopped out today: no re-entry until the next trading day,
        # otherwise a target weight would buy right back into a falling knife.
        self._stopped_today: set[str] = set()

    def evaluate(
        self,
        signal: Signal,
        account: AccountInfo,
        positions: dict[str, Position],
        prices: dict[str, float],
    ) -> list[Order]:
        """Turn target weights into risk-checked, sized orders."""
        self._roll_day(signal.timestamp, account.equity)
        equity = account.equity

        # --- 1. Daily-loss circuit breaker --------------------------------
        assert self._day_start_equity is not None
        max_loss = self._day_start_equity * self.config.max_daily_loss_pct
        drawdown = self._day_start_equity - equity
        if self.daily_loss_breached or drawdown >= max_loss:
            if not self.daily_loss_breached:
                logger.critical(
                    "DAILY LOSS LIMIT BREACHED: down %.2f (limit %.2f) from day-start "
                    "equity %.2f -- blocking all new orders",
                    drawdown,
                    max_loss,
                    self._day_start_equity,
                )
            self.daily_loss_breached = True
            return []

        orders: list[Order] = []
        # Symbols we are force-exiting this bar; don't also process their targets.
        stopped_out: set[str] = set()

        # --- 2. Stop-loss exits --------------------------------------------
        for symbol, pos in positions.items():
            price = prices.get(symbol)
            if price is None or pos.qty <= 0:
                continue
            stop_price = pos.avg_cost * (1 - self.config.stop_loss_pct)
            if price <= stop_price:
                logger.warning(
                    "STOP-LOSS %s: price %.2f <= %.2f (avg cost %.2f - %.0f%%) -> SELL %d",
                    symbol,
                    price,
                    stop_price,
                    pos.avg_cost,
                    self.config.stop_loss_pct * 100,
                    pos.qty,
                )
                orders.append(self._order(symbol, "SELL", pos.qty, signal.timestamp))
                stopped_out.add(symbol)
                self._stopped_today.add(symbol)

        # --- 3. Size targets, capped per position --------------------------
        # Track projected exposure/cash as we go so the batch as a whole
        # respects the caps, not just each order individually.
        projected_exposure = sum(
            pos.qty * prices.get(sym, pos.avg_cost)
            for sym, pos in positions.items()
            if sym not in stopped_out
        )
        projected_cash = account.cash
        max_exposure = equity * self.config.max_total_exposure_pct

        # A held symbol missing from target_weights means "target = 0":
        # the strategy dropped it, so we should exit the position.
        all_symbols = set(signal.target_weights) | set(positions)
        for symbol in sorted(all_symbols):
            weight = signal.target_weights.get(symbol, 0.0)
            if symbol in stopped_out or symbol in self._stopped_today:
                logger.info("Skipping target for %s: stopped out today", symbol)
                continue
            price = prices.get(symbol)
            if price is None or price <= 0:
                logger.warning("No price for %s -- skipping its target", symbol)
                continue

            capped_weight = min(weight, self.config.max_position_pct)
            if capped_weight < weight:
                logger.info(
                    "Capping %s weight %.3f -> %.3f (max_position_pct)",
                    symbol,
                    weight,
                    capped_weight,
                )

            target_qty = math.floor(capped_weight * equity / price)
            current_qty = positions[symbol].qty if symbol in positions else 0
            delta = target_qty - current_qty
            if delta == 0:
                continue

            # Dead-band: ignore tiny rebalances (unless it's a full exit) --
            # a 2-share order mostly pays commission, not moves the portfolio.
            is_full_exit = target_qty == 0 and current_qty > 0
            if abs(delta) * price < self.config.min_order_value and not is_full_exit:
                logger.debug(
                    "Skipping tiny rebalance %s: %+d shares (~%.2f < min_order_value %.2f)",
                    symbol,
                    delta,
                    abs(delta) * price,
                    self.config.min_order_value,
                )
                continue

            if delta > 0:
                # --- 4a. Total-exposure cap ---------------------------------
                headroom_value = max_exposure - projected_exposure
                affordable = min(headroom_value, projected_cash)
                allowed_qty = min(delta, math.floor(affordable / price))
                if allowed_qty < delta:
                    logger.info(
                        "Trimming BUY %s %d -> %d (exposure/cash cap: headroom %.2f, cash %.2f)",
                        symbol,
                        delta,
                        max(allowed_qty, 0),
                        headroom_value,
                        projected_cash,
                    )
                if allowed_qty <= 0:
                    continue
                projected_exposure += allowed_qty * price
                projected_cash -= allowed_qty * price
                orders.append(self._order(symbol, "BUY", allowed_qty, signal.timestamp))
            else:
                # Sells reduce exposure and free cash; always allowed.
                sell_qty = min(-delta, current_qty)  # never short in this scaffold
                projected_exposure -= sell_qty * price
                projected_cash += sell_qty * price
                orders.append(self._order(symbol, "SELL", sell_qty, signal.timestamp))

        return orders

    def _order(self, symbol: str, side: str, qty: int, timestamp: datetime) -> Order:
        return Order(
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            qty=qty,
            client_order_id=make_client_order_id(symbol, side, timestamp),
        )

    def _roll_day(self, timestamp: datetime, equity: float) -> None:
        """Reset the daily-loss baseline when a new trading day starts."""
        day = timestamp.date()
        if day != self._current_day:
            self._current_day = day
            self._day_start_equity = equity
            self._stopped_today.clear()
            # Note: `daily_loss_breached` is NOT reset -- after a breach we
            # want a human to look at what happened before trading resumes.
