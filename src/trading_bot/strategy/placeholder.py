"""!!! NOT A TRADING STRATEGY !!!

BuyAndHoldPlaceholder exists only to prove the pipeline works:
data -> signal -> risk -> execution -> logging, in all three modes.
It "decides" the same thing every bar: equal-weight the configured symbols.
Replace it with a real strategy later; nothing else in the bot changes.
"""

from __future__ import annotations

from trading_bot.models import MarketSnapshot, Signal
from trading_bot.strategy.base import Strategy


class BuyAndHoldPlaceholder(Strategy):
    def __init__(self, symbols: list[str], total_exposure: float = 0.6) -> None:
        """`total_exposure` = fraction of equity to deploy across all symbols,
        leaving the rest as a cash buffer (fees, price moves between bars)."""
        self._symbols = symbols
        self._total_exposure = total_exposure

    def generate_signal(self, snapshot: MarketSnapshot) -> Signal:
        # Only target symbols we actually have prices for right now.
        tradable = [s for s in self._symbols if s in snapshot.latest_prices]
        weight = self._total_exposure / len(tradable) if tradable else 0.0
        return Signal(
            timestamp=snapshot.timestamp,
            target_weights=dict.fromkeys(tradable, weight),
            reason="placeholder: equal-weight buy & hold (NOT a real strategy)",
        )
