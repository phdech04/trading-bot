"""ReplayDataProvider: replays historical bars one snapshot at a time.

This is the backtest half of the parity trick. It walks forward through
history and, at every bar, hands the engine a MarketSnapshot containing ONLY
data up to that moment -- exactly what a live provider would know at that
time. The engine (and the strategy) cannot peek into the future.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pandas as pd

from trading_bot.data.base import DataProvider
from trading_bot.models import MarketSnapshot, Timeframe

logger = logging.getLogger(__name__)


class ReplayDataProvider(DataProvider):
    def __init__(self, bars: dict[str, pd.DataFrame]) -> None:
        """`bars` = full OHLCV history per symbol (from cache or synthetic)."""
        self._bars = bars

    def get_historical_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,  # noqa: ARG002 -- data is pre-loaded at one timeframe
        start: datetime,
        end: datetime,
    ) -> dict[str, pd.DataFrame]:
        return {sym: df.loc[start:end] for sym, df in self._bars.items() if sym in symbols}

    def stream_or_poll_latest(
        self,
        symbols: list[str],
        timeframe: Timeframe,  # noqa: ARG002
    ) -> Iterator[MarketSnapshot]:
        available = [s for s in symbols if s in self._bars]
        missing = set(symbols) - set(available)
        if missing:
            logger.warning("No replay data for symbols: %s", sorted(missing))

        # Walk the union of all bar timestamps in order. Symbols that don't
        # have a bar at a given time simply keep their last known price --
        # same as live trading when one symbol ticks and another doesn't.
        all_times = sorted({ts for s in available for ts in self._bars[s].index})

        for ts in all_times:
            snapshot_bars: dict[str, pd.DataFrame] = {}
            latest_prices: dict[str, float] = {}
            for sym in available:
                # .loc[:ts] = history up to AND including ts, never beyond.
                history = self._bars[sym].loc[:ts]
                if history.empty:
                    continue  # this symbol hasn't started trading yet
                snapshot_bars[sym] = history
                latest_prices[sym] = float(history["close"].iloc[-1])

            yield MarketSnapshot(
                timestamp=ts.to_pydatetime(),
                bars=snapshot_bars,
                latest_prices=latest_prices,
            )


# --- CSV cache helpers (shared by run_backtest.py and fetch_data.py) --------


def cache_path(data_dir: str | Path, symbol: str, timeframe: Timeframe) -> Path:
    return Path(data_dir) / f"{symbol}_{timeframe.value}.csv"


def save_bars_to_cache(
    bars: dict[str, pd.DataFrame], data_dir: str | Path, timeframe: Timeframe
) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    for symbol, df in bars.items():
        path = cache_path(data_dir, symbol, timeframe)
        df.to_csv(path)
        logger.info("Cached %d bars for %s -> %s", len(df), symbol, path)


def load_cached_bars(
    symbols: list[str], data_dir: str | Path, timeframe: Timeframe
) -> dict[str, pd.DataFrame] | None:
    """Load all requested symbols from the CSV cache.

    Returns None if ANY symbol is missing -- mixing cached and absent data
    would silently backtest a smaller universe than configured.
    """
    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = cache_path(data_dir, symbol, timeframe)
        if not path.exists():
            logger.info("Cache miss for %s (no %s)", symbol, path)
            return None
        result[symbol] = pd.read_csv(path, index_col=0, parse_dates=True)
    return result
