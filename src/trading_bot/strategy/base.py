"""Strategy ABC -- the ONE method every strategy implements.

The contract that guarantees backtest/live parity:

    generate_signal(snapshot) -> Signal

MUST be a pure function of the snapshot: no network calls, no order
placement, no file I/O, no clocks, no hidden state that data didn't provide.
If a strategy only sees MarketSnapshots, it literally cannot behave
differently in a backtest than it does live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from trading_bot.models import MarketSnapshot, Signal


class Strategy(ABC):
    @abstractmethod
    def generate_signal(self, snapshot: MarketSnapshot) -> Signal:
        """Data in, target portfolio out. Nothing else."""
