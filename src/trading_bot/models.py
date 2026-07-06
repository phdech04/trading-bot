"""Shared datatypes passed between the layers of the bot.

These are deliberately plain, frozen dataclasses: they carry data across the
adapter boundaries (data -> strategy -> risk -> execution) and nothing else.
Freezing them prevents one layer from accidentally mutating another's input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

import pandas as pd

if TYPE_CHECKING:
    # Only imported for type hints -- avoids a runtime circular import.
    from trading_bot.sentiment.base import SentimentResult


class Timeframe(StrEnum):
    """Bar sizes the bot understands.

    Each member knows how to express itself both as an IBKR "bar size" string
    (for API requests) and a pandas frequency string (for generating/reindexing
    data) so the rest of the code never hardcodes either format.
    """

    MIN_1 = "1min"
    MIN_5 = "5min"
    MIN_15 = "15min"
    HOUR_1 = "1h"
    DAY_1 = "1d"

    @property
    def ibkr_bar_size(self) -> str:
        return {
            Timeframe.MIN_1: "1 min",
            Timeframe.MIN_5: "5 mins",
            Timeframe.MIN_15: "15 mins",
            Timeframe.HOUR_1: "1 hour",
            Timeframe.DAY_1: "1 day",
        }[self]

    @property
    def pandas_freq(self) -> str:
        return {
            Timeframe.MIN_1: "1min",
            Timeframe.MIN_5: "5min",
            Timeframe.MIN_15: "15min",
            Timeframe.HOUR_1: "1h",
            Timeframe.DAY_1: "1B",  # business days -- markets are closed on weekends
        }[self]

    @property
    def seconds(self) -> int:
        return {
            Timeframe.MIN_1: 60,
            Timeframe.MIN_5: 300,
            Timeframe.MIN_15: 900,
            Timeframe.HOUR_1: 3600,
            Timeframe.DAY_1: 86400,
        }[self]


@dataclass(frozen=True)
class MarketSnapshot:
    """Everything a Strategy is allowed to see at one moment in time.

    `bars` contains OHLCV history strictly UP TO `timestamp` -- never beyond.
    In a backtest that rule is what prevents lookahead bias; live, it is
    simply all the data that exists. Because the snapshot looks identical in
    both cases, the same strategy code runs unchanged in every mode.
    """

    timestamp: datetime
    # symbol -> DataFrame with columns [open, high, low, close, volume],
    # indexed by bar timestamp.
    bars: dict[str, pd.DataFrame]
    # symbol -> most recent close price (convenience; derivable from `bars`).
    latest_prices: dict[str, float]
    # Optional enrichment added by the engine when the sentiment module is on.
    sentiment: dict[str, SentimentResult] | None = None


@dataclass(frozen=True)
class Signal:
    """Strategy output: the portfolio the strategy WANTS, not sized orders.

    Strategies express intent as target weights (fraction of account equity
    per symbol, 0..1). Turning weights into actual share quantities -- and
    capping them -- is the RiskManager's job. This split keeps strategies
    pure and makes every order pass through risk checks by construction.
    """

    timestamp: datetime
    target_weights: dict[str, float]  # symbol -> desired fraction of equity
    reason: str = ""  # human-readable note for the logs


@dataclass(frozen=True)
class Order:
    """A concrete, risk-approved instruction for the broker."""

    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    order_type: Literal["MKT"] = "MKT"  # scaffold only supports market orders
    # Unique per trading intent. Brokers use it to de-duplicate, so a retry
    # after a timeout can never place the same order twice (idempotency).
    client_order_id: str = ""


@dataclass(frozen=True)
class Fill:
    """A confirmed execution reported back by a broker."""

    order: Order
    filled_qty: int
    fill_price: float
    commission: float
    timestamp: datetime


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int  # positive = long; scaffold does not short
    avg_cost: float  # average price paid per share


@dataclass(frozen=True)
class AccountInfo:
    account_id: str
    equity: float  # cash + market value of positions
    cash: float
    buying_power: float
