"""Portfolio ledger: positions, cash, realized + unrealized PnL.

Used by the SimBroker to simulate an account during backtests, and by the
backtest report. In paper/live mode IBKR itself is the source of truth --
the IBKRBroker adapter reads state from the API instead of from this class.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_bot.models import Fill, Position


@dataclass
class _PositionState:
    """Mutable internal record; exposed externally as a frozen Position."""

    qty: int
    avg_cost: float


class Portfolio:
    def __init__(self, cash: float) -> None:
        self.cash = cash
        self.realized_pnl = 0.0  # from closed trades, before commissions
        self.total_commission = 0.0
        self.fills: list[Fill] = []  # full trade history, for reporting
        self._positions: dict[str, _PositionState] = {}

    def apply_fill(self, fill: Fill) -> None:
        """Update cash and positions to reflect a confirmed execution."""
        symbol = fill.order.symbol
        pos = self._positions.get(symbol)
        self.total_commission += fill.commission

        if fill.order.side == "BUY":
            cost = fill.filled_qty * fill.fill_price + fill.commission
            if cost > self.cash + 1e-9:
                raise ValueError(f"BUY {symbol} costs {cost:.2f} but cash is {self.cash:.2f}")
            self.cash -= cost
            if pos is None:
                self._positions[symbol] = _PositionState(fill.filled_qty, fill.fill_price)
            else:
                # New average cost = total spent on shares / total shares.
                total_shares = pos.qty + fill.filled_qty
                pos.avg_cost = (
                    pos.qty * pos.avg_cost + fill.filled_qty * fill.fill_price
                ) / total_shares
                pos.qty = total_shares
        else:  # SELL
            if pos is None or pos.qty < fill.filled_qty:
                held = pos.qty if pos else 0
                raise ValueError(f"SELL {fill.filled_qty} {symbol} but only {held} held")
            self.cash += fill.filled_qty * fill.fill_price - fill.commission
            self.realized_pnl += (fill.fill_price - pos.avg_cost) * fill.filled_qty
            pos.qty -= fill.filled_qty
            if pos.qty == 0:
                del self._positions[symbol]

        self.fills.append(fill)

    def get_positions(self) -> dict[str, Position]:
        return {
            sym: Position(symbol=sym, qty=p.qty, avg_cost=p.avg_cost)
            for sym, p in self._positions.items()
        }

    def market_value(self, prices: dict[str, float]) -> float:
        """Current value of all held shares. Falls back to avg_cost when a
        price is missing so a data gap can't zero out the account value."""
        return sum(p.qty * prices.get(sym, p.avg_cost) for sym, p in self._positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            p.qty * (prices.get(sym, p.avg_cost) - p.avg_cost) for sym, p in self._positions.items()
        )
