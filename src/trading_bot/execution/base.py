"""Broker ABC -- the adapter boundary for order execution.

The engine only ever talks to this interface. Swapping SimBroker (backtest)
for IBKRBroker (paper/live) is the ONLY execution-side difference between
modes -- and IBKRBroker itself doesn't change between paper and live either;
only the port/account it connects to does.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from trading_bot.models import AccountInfo, Fill, Order, Position


class Broker(ABC):
    @abstractmethod
    def submit_order(self, order: Order) -> Fill | None:
        """Submit an order. Returns the Fill if known immediately, else None
        (e.g. an order still working at the exchange).

        Implementations MUST be idempotent on `order.client_order_id`:
        submitting the same ID twice must not create a second order.
        """

    @abstractmethod
    def get_positions(self) -> dict[str, Position]: ...

    @abstractmethod
    def get_account(self) -> AccountInfo: ...

    @abstractmethod
    def cancel_all(self) -> None:
        """Cancel every open order."""

    @abstractmethod
    def close_all_positions(self) -> None:
        """Flatten the book: market-close every open position."""
