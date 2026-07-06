"""Kill switch: flatten everything, then stop.

ONE function used everywhere it matters:
  - `scripts/kill.py` / `make kill` (manual emergency stop),
  - the engine's Ctrl-C / SIGTERM handler,
  - the daily-loss circuit breaker in the engine loop.
"""

from __future__ import annotations

import logging

from trading_bot.execution.base import Broker

logger = logging.getLogger(__name__)


def flatten_all(broker: Broker) -> None:
    """Cancel all open orders, then close all positions.

    Order matters: cancelling first prevents a resting order from filling
    WHILE we are busy closing positions (which would leave a new position
    behind after the "flatten").
    """
    logger.critical("KILL SWITCH: cancelling all open orders")
    broker.cancel_all()
    logger.critical("KILL SWITCH: closing all positions")
    broker.close_all_positions()
    logger.critical("KILL SWITCH: done -- book should be flat")
