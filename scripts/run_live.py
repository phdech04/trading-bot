#!/usr/bin/env python
"""LIVE trading -- real money. Heavily guarded, by design.

This script refuses to run unless ALL of these hold:
  1. TRADING_MODE=live is set explicitly in .env,
  2. you type LIVE (exactly) at the interactive prompt,
  3. the connected account/port actually ARE live (verified after connect).

Even then, orders are only sent with --execute; without it you get a live
dry-run (signals + intended orders logged, nothing submitted).

Usage:
    uv run python scripts/run_live.py [--execute] [--config config/config.yaml]
"""

from __future__ import annotations

import argparse
import logging
import sys

from trading_bot.config import ConfigError, load_settings
from trading_bot.engine.live_engine import require_live_confirmation, run_trading
from trading_bot.ibkr import IBKRConnectionError, IBKRModeMismatchError
from trading_bot.logging_config import setup_logging

logger = logging.getLogger("run_live")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually place LIVE orders (default: dry-run, log only)",
    )
    args = parser.parse_args()

    setup_logging()
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    # Guard 1: the env flag. Defaults to paper; you must have set it yourself.
    if settings.env.trading_mode != "live":
        logger.critical(
            "Refusing: TRADING_MODE=%s. Live trading requires TRADING_MODE=live in .env "
            "AND typing the confirmation phrase. If you want paper trading, use "
            "run_paper.py instead.",
            settings.env.trading_mode,
        )
        sys.exit(2)

    settings = settings.model_copy(update={"dry_run": not args.execute})

    # Guard 2: a human must type LIVE at a real terminal.
    if not require_live_confirmation(settings):
        logger.critical("Refusing: live confirmation not given.")
        sys.exit(2)

    # Guard 3 (account/port really live) runs inside connect_ibkr -> verify_mode.
    try:
        run_trading(settings)
    except (IBKRConnectionError, IBKRModeMismatchError) as exc:
        logger.critical("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
