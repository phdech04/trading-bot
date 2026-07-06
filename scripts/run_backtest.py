#!/usr/bin/env python
"""Run the placeholder strategy through a full backtest.

Data source, in order of preference:
  1. cached IBKR bars in data/ (create them with scripts/fetch_data.py),
  2. deterministic synthetic data (no connection needed) -- with a loud warning.

Usage:
    uv run python scripts/run_backtest.py [--synthetic] [--config config/config.yaml]
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, time

from trading_bot.backtest.engine import run_backtest
from trading_bot.config import load_settings
from trading_bot.data.replay import load_cached_bars
from trading_bot.data.synthetic import generate_bars
from trading_bot.logging_config import setup_logging

logger = logging.getLogger("run_backtest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="skip the cache and use synthetic data",
    )
    args = parser.parse_args()

    setup_logging()
    settings = load_settings(args.config)

    bars = None
    if not args.synthetic:
        bars = load_cached_bars(settings.symbols, "data", settings.timeframe)

    if bars is None:
        logger.warning(
            "No cached IBKR data for all of %s -- using SYNTHETIC data. "
            "Results prove the pipeline works, nothing more. "
            "Run scripts/fetch_data.py (with IB Gateway/TWS running) for real data.",
            settings.symbols,
        )
        bars = generate_bars(
            settings.symbols,
            settings.timeframe,
            start=datetime.combine(settings.backtest.start, time.min),
            end=datetime.combine(settings.backtest.end, time.min),
        )

    result = run_backtest(settings, bars)
    print(result.summary())


if __name__ == "__main__":
    main()
