"""DataProvider ABC -- the adapter boundary for market data.

The engine only ever talks to this interface. Swapping
ReplayDataProvider (backtest) for IBKRDataProvider (paper/live) is the ONLY
data-side difference between modes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime

import pandas as pd

from trading_bot.models import MarketSnapshot, Timeframe


class DataProvider(ABC):
    @abstractmethod
    def get_historical_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> dict[str, pd.DataFrame]:
        """Return OHLCV history per symbol: columns [open, high, low, close,
        volume], indexed by bar timestamp, restricted to [start, end]."""

    @abstractmethod
    def stream_or_poll_latest(
        self,
        symbols: list[str],
        timeframe: Timeframe,
    ) -> Iterator[MarketSnapshot]:
        """Yield one MarketSnapshot per completed bar, in time order.

        This generator contract is the backtest/live parity trick:
          - the replayer yields snapshots built from historical bars,
          - the IBKR provider yields snapshots as real bars complete,
        and the engine loop cannot tell the difference.
        """
