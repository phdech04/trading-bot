"""Backtest wiring: ReplayDataProvider + SimBroker around the SAME engine.

There is no separate 'backtest loop' -- run_backtest() just injects the
simulated adapters into TradingEngine and summarizes the portfolio afterwards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from trading_bot.config import Settings
from trading_bot.data.replay import ReplayDataProvider
from trading_bot.engine.core import TradingEngine
from trading_bot.execution.sim_broker import SimBroker
from trading_bot.models import Fill
from trading_bot.portfolio import Portfolio
from trading_bot.risk.risk_manager import RiskManager
from trading_bot.strategy.base import Strategy
from trading_bot.strategy.placeholder import BuyAndHoldPlaceholder

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    initial_cash: float
    final_equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_commission: float
    n_trades: int
    n_bars: int
    fills: list[Fill]

    @property
    def total_return_pct(self) -> float:
        return (self.final_equity / self.initial_cash - 1) * 100

    def summary(self) -> str:
        return (
            "\n===== BACKTEST SUMMARY =====\n"
            f"Initial cash:      {self.initial_cash:>14,.2f}\n"
            f"Final equity:      {self.final_equity:>14,.2f}\n"
            f"Total return:      {self.total_return_pct:>13.2f}%\n"
            f"Realized PnL:      {self.realized_pnl:>14,.2f}\n"
            f"Unrealized PnL:    {self.unrealized_pnl:>14,.2f}\n"
            f"Commissions paid:  {self.total_commission:>14,.2f}\n"
            f"Trades executed:   {self.n_trades:>14d}\n"
            f"Bars processed:    {self.n_bars:>14d}\n"
            "============================"
        )


def run_backtest(
    settings: Settings,
    bars: dict[str, pd.DataFrame],
    strategy: Strategy | None = None,
) -> BacktestResult:
    """Run `strategy` (default: the placeholder) over historical `bars`."""
    strategy = strategy or BuyAndHoldPlaceholder(settings.symbols)

    portfolio = Portfolio(settings.backtest.initial_cash)
    broker = SimBroker(portfolio, settings.fees)
    data = ReplayDataProvider(bars)
    risk = RiskManager(settings.risk)

    # dry_run makes no sense against the sim (nothing would ever fill and the
    # whole point is to exercise the execution path safely), so force it off.
    sim_settings = settings.model_copy(update={"dry_run": False})

    engine = TradingEngine(
        data=data, strategy=strategy, risk=risk, broker=broker, settings=sim_settings
    )
    engine.run()

    # Value the book at each symbol's last known close.
    final_prices = {sym: float(df["close"].iloc[-1]) for sym, df in bars.items() if not df.empty}
    n_bars = len({ts for df in bars.values() for ts in df.index})

    return BacktestResult(
        initial_cash=settings.backtest.initial_cash,
        final_equity=portfolio.equity(final_prices),
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=portfolio.unrealized_pnl(final_prices),
        total_commission=portfolio.total_commission,
        n_trades=len(portfolio.fills),
        n_bars=n_bars,
        fills=portfolio.fills,
    )
