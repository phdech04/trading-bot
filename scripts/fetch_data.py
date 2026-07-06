#!/usr/bin/env python
"""Fetch historical bars from IBKR and cache them as CSVs in data/.

Requires IB Gateway / TWS running. After this, run_backtest.py uses the
cached real data instead of synthetic data.

Usage:
    uv run python scripts/fetch_data.py [--start 2024-01-02] [--end 2024-12-31]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, time

from trading_bot.config import ConfigError, load_settings
from trading_bot.data.ibkr_data import IBKRDataProvider
from trading_bot.data.replay import save_bars_to_cache
from trading_bot.ibkr import IBKRConnectionError, IBKRModeMismatchError, connect_ibkr
from trading_bot.logging_config import setup_logging

logger = logging.getLogger("fetch_data")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--start", help="YYYY-MM-DD (default: backtest.start from config)")
    parser.add_argument("--end", help="YYYY-MM-DD (default: backtest.end from config)")
    args = parser.parse_args()

    setup_logging()
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    start = (
        datetime.strptime(args.start, "%Y-%m-%d")
        if args.start
        else datetime.combine(settings.backtest.start, time.min)
    )
    end = (
        datetime.strptime(args.end, "%Y-%m-%d")
        if args.end
        else datetime.combine(settings.backtest.end, time.min)
    )

    try:
        ib = connect_ibkr(settings)
    except (IBKRConnectionError, IBKRModeMismatchError) as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    try:
        provider = IBKRDataProvider(ib, settings)
        bars = provider.get_historical_bars(settings.symbols, settings.timeframe, start, end)
        save_bars_to_cache(bars, "data", settings.timeframe)
        for symbol, df in bars.items():
            print(f"{symbol}: {len(df)} bars cached")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
