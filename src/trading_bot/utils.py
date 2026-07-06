"""Small shared helpers: retry with backoff, client order IDs."""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def retry_with_backoff(
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[Exception], ...] = (ConnectionError, TimeoutError, OSError),
) -> Callable[[F], F]:
    """Decorator: retry a flaky call with exponential backoff + jitter.

    Waits base_delay, 2x, 4x, ... (capped at max_delay) between attempts.
    The random jitter (0-25%) prevents many retries landing at the exact
    same moment and hammering a rate-limited API in lockstep.

    Only exceptions listed in `retry_on` are retried -- a genuine bug
    (e.g. TypeError) should fail immediately, not be retried 4 times.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as exc:
                    if attempt == max_attempts:
                        logger.error(
                            "%s failed after %d attempts: %s", func.__name__, max_attempts, exc
                        )
                        raise
                    sleep_for = min(delay, max_delay) * (1 + random.random() * 0.25)
                    logger.warning(
                        "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        sleep_for,
                    )
                    time.sleep(sleep_for)
                    delay *= 2
            raise AssertionError("unreachable")  # loop always returns or raises

        return wrapper  # type: ignore[return-value]

    return decorator


def make_client_order_id(symbol: str, side: str, timestamp: datetime) -> str:
    """Deterministic order ID: same trading intent -> same ID.

    The ID encodes (bar timestamp, symbol, side). The risk manager emits at
    most one order per symbol per bar, so this is unique -- and because it is
    deterministic, a crashed-and-restarted bot regenerates the SAME id for the
    same intent, letting the broker adapter detect and skip the duplicate.
    """
    return f"{timestamp:%Y%m%d-%H%M%S}-{symbol}-{side}"
