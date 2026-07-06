"""IBKRDataProvider: historical bars + a live bar stream, via ib_async.

Streaming approach: `reqHistoricalData(..., keepUpToDate=True)` gives us an
auto-updating list of bars for ANY bar size -- IBKR keeps appending to it as
new bars form. A bar is only final once a bar with a LATER timestamp exists,
so we emit snapshots exactly when a bar completes. That mirrors what the
backtest replayer does with historical bars, which is the whole parity trick.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime

import pandas as pd
from ib_async import IB, Stock, util

from trading_bot.config import Settings
from trading_bot.data.base import DataProvider
from trading_bot.models import MarketSnapshot, Timeframe
from trading_bot.utils import retry_with_backoff

logger = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]


class IBKRDataProvider(DataProvider):
    def __init__(self, ib: IB, settings: Settings) -> None:
        self._ib = ib
        self._settings = settings
        self._contracts: dict[str, Stock] = {}

    def _contract(self, symbol: str) -> Stock:
        """US stock/ETF contract, qualified once and cached.

        'SMART' = IBKR's order router across US exchanges; USD pins down
        ambiguous listings.
        """
        if symbol not in self._contracts:
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            self._contracts[symbol] = contract
        return self._contracts[symbol]

    @staticmethod
    def _to_df(bars: list) -> pd.DataFrame:
        df = util.df(bars)
        if df is None or df.empty:
            return pd.DataFrame(columns=OHLCV)
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")[OHLCV]

    @retry_with_backoff()
    def get_historical_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> dict[str, pd.DataFrame]:
        # IBKR wants a *duration* ending at endDateTime, not a start date.
        days = max((end - start).days, 1)
        duration = f"{days // 365 + 1} Y" if days > 365 else f"{days} D"

        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            bars = self._ib.reqHistoricalData(
                self._contract(symbol),
                endDateTime=end,
                durationStr=duration,
                barSizeSetting=timeframe.ibkr_bar_size,
                whatToShow="TRADES",
                useRTH=True,  # regular trading hours only
                formatDate=1,
            )
            df = self._to_df(bars)
            result[symbol] = df.loc[str(start) : str(end)]
            logger.info("Fetched %d %s bars for %s", len(result[symbol]), timeframe.value, symbol)
        return result

    def stream_or_poll_latest(
        self,
        symbols: list[str],
        timeframe: Timeframe,
    ) -> Iterator[MarketSnapshot]:
        # Subscribe once per symbol. keepUpToDate=True means IBKR streams
        # updates into these BarDataList objects from now on.
        subscriptions = {}
        for symbol in symbols:
            subscriptions[symbol] = self._ib.reqHistoricalData(
                self._contract(symbol),
                endDateTime="",  # '' = now, required when keepUpToDate=True
                durationStr=self._seed_duration(timeframe),
                barSizeSetting=timeframe.ibkr_bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=True,
            )

        # Completed history per symbol. The LAST bar in each subscription is
        # still forming (IBKR updates it in place), so we exclude it.
        history: dict[str, pd.DataFrame] = {}
        for symbol, bars in subscriptions.items():
            df = self._to_df(bars)
            history[symbol] = df.iloc[:-1] if len(df) > 1 else df

        # Emit one snapshot immediately from the seeded history, so a bot on
        # a daily timeframe can act at startup instead of waiting until the
        # next market close for its first bar.
        yield self._snapshot(history)

        while True:
            # Block until *something* changes (or timeout). The timeout keeps
            # the loop responsive to Ctrl-C even when the market is quiet.
            self._ib.waitOnUpdate(timeout=self._settings.poll_interval_seconds)
            if not self._ib.isConnected():
                raise ConnectionError(
                    "Lost connection to IB Gateway/TWS. Restart it and relaunch the bot."
                )

            new_bar_completed = False
            for symbol, bars in subscriptions.items():
                df = self._to_df(bars)
                completed = df.iloc[:-1] if len(df) > 1 else df.iloc[:0]
                last_known = history[symbol].index.max() if len(history[symbol]) else None
                if last_known is None:
                    fresh = completed
                else:
                    fresh = completed.loc[completed.index > last_known]
                if not fresh.empty:
                    history[symbol] = pd.concat([history[symbol], fresh])
                    new_bar_completed = True

            if new_bar_completed:
                yield self._snapshot(history)

    def _seed_duration(self, timeframe: Timeframe) -> str:
        """How much history to preload so strategies have context on bar one."""
        return {
            Timeframe.MIN_1: "2 D",
            Timeframe.MIN_5: "1 W",
            Timeframe.MIN_15: "2 W",
            Timeframe.HOUR_1: "1 M",
            Timeframe.DAY_1: "1 Y",
        }[timeframe]

    @staticmethod
    def _snapshot(history: dict[str, pd.DataFrame]) -> MarketSnapshot:
        available = {s: df for s, df in history.items() if not df.empty}
        latest_prices = {s: float(df["close"].iloc[-1]) for s, df in available.items()}
        timestamp = max(df.index.max() for df in available.values()).to_pydatetime()
        return MarketSnapshot(
            timestamp=timestamp,
            bars=dict(available),
            latest_prices=latest_prices,
        )
