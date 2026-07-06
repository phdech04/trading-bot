#!/usr/bin/env python
"""Paper trading against IBKR (via a running IB Gateway / TWS).

Dry-run is the DEFAULT: intended orders are logged, nothing is sent.
Pass --execute to actually place paper orders.

Usage:
    uv run python scripts/run_paper.py [--execute] [--config config/config.yaml]
"""

from __future__ import annotations

import argparse
import logging
import sys

from trading_bot.config import ConfigError, load_settings
from trading_bot.engine.live_engine import run_trading
from trading_bot.ibkr import IBKRConnectionError, IBKRModeMismatchError
from trading_bot.logging_config import setup_logging

logger = logging.getLogger("run_paper")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually place paper orders (default: dry-run, log only)",
    )
    args = parser.parse_args()

    setup_logging()
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    if settings.env.trading_mode != "paper":
        logger.critical(
            "TRADING_MODE=%s but this is the PAPER script. "
            "Set TRADING_MODE=paper in .env (or use run_live.py, carefully).",
            settings.env.trading_mode,
        )
        sys.exit(2)

    # The script flag decides dry-run explicitly, overriding config.yaml,
    # so what happens never depends on a file you forgot you edited.
    settings = settings.model_copy(update={"dry_run": not args.execute})

    try:
        run_trading(settings)
    except (IBKRConnectionError, IBKRModeMismatchError) as exc:
        logger.critical("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
