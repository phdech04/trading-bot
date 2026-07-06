#!/usr/bin/env python
"""EMERGENCY KILL SWITCH: cancel all open orders, close all positions.

Connects to IBKR, flattens the account the bot trades, and exits. Works even
if the bot itself is wedged or not running. (If the bot IS running, Ctrl-C /
SIGTERM on it triggers the same flatten logic.)

Usage:
    uv run python scripts/kill.py [--force]   # --force skips the confirmation
"""

from __future__ import annotations

import argparse
import logging
import sys

from trading_bot.config import ConfigError, load_settings
from trading_bot.execution.ibkr_broker import IBKRBroker
from trading_bot.execution.kill_switch import flatten_all
from trading_bot.ibkr import IBKRConnectionError, IBKRModeMismatchError, connect_ibkr
from trading_bot.logging_config import setup_logging

logger = logging.getLogger("kill")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--force", action="store_true", help="skip confirmation prompt")
    args = parser.parse_args()

    setup_logging()
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    if not args.force:
        try:
            answer = input(
                f"This will CANCEL all orders and CLOSE all positions "
                f"({settings.env.trading_mode} account). Type KILL to confirm: "
            )
        except EOFError:
            answer = ""
        if answer.strip() != "KILL":
            print("Aborted -- nothing was done.")
            sys.exit(2)

    try:
        ib = connect_ibkr(settings)
    except (IBKRConnectionError, IBKRModeMismatchError) as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    try:
        broker = IBKRBroker(ib, settings)
        flatten_all(broker)
        remaining = broker.get_positions()
        if remaining:
            logger.error("Positions still open (check IBKR!): %s", remaining)
            sys.exit(1)
        print("Flat: all orders cancelled, all positions closed.")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
