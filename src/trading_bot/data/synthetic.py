"""Deterministic synthetic OHLCV data.

Lets the backtest and the test suite run END TO END with no IBKR connection
and no internet. The prices are a seeded geometric random walk -- statistically
market-ish, but meaningless. Never draw strategy conclusions from them.
"""

from __future__ import annotations

import zlib
from datetime import datetime

import numpy as np
import pandas as pd

from trading_bot.models import Timeframe


def generate_bars(
    symbols: list[str],
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """Generate per-symbol OHLCV DataFrames. Same inputs -> same data, always.

    Each symbol's random stream is seeded from (seed + crc32 of its name), so
    adding a symbol never changes the data of the others.
    """
    index = pd.date_range(start=start, end=end, freq=timeframe.pandas_freq)
    result: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        rng = np.random.default_rng(seed + zlib.crc32(symbol.encode()))

        # Start price in a plausible 40..440 range, derived from the symbol name.
        start_price = 40.0 + (zlib.crc32(symbol.encode()) % 400)

        # Geometric random walk: tiny positive drift, ~1.5% per-bar volatility.
        returns = rng.normal(loc=0.0002, scale=0.015, size=len(index))
        close = start_price * np.exp(np.cumsum(returns))

        # Build open/high/low around the close path with small random spreads.
        open_ = np.empty_like(close)
        open_[0] = start_price
        open_[1:] = close[:-1] * (1 + rng.normal(0, 0.002, size=len(index) - 1))
        spread = np.abs(rng.normal(0, 0.004, size=len(index)))
        high = np.maximum(open_, close) * (1 + spread)
        low = np.minimum(open_, close) * (1 - spread)
        volume = rng.integers(100_000, 5_000_000, size=len(index))

        result[symbol] = pd.DataFrame(
            {
                "open": open_.round(2),
                "high": high.round(2),
                "low": low.round(2),
                "close": close.round(2),
                "volume": volume,
            },
            index=index,
        )
        result[symbol].index.name = "date"

    return result
