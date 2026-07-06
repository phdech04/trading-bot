#!/usr/bin/env python
"""One-shot sanity check of the sentiment pipeline (news -> LLM -> scores).

Fetches real headlines for your configured symbols and scores them with the
provider selected by SENTIMENT_PROVIDER (.env). Needs GEMINI_API_KEY for
gemini, or a running `ollama serve` for ollama. Does NOT need IBKR.

Usage:
    uv run python scripts/check_sentiment.py
"""

from __future__ import annotations

import logging
import sys

from trading_bot.config import ConfigError, load_settings
from trading_bot.logging_config import setup_logging
from trading_bot.sentiment.factory import create_enricher

logger = logging.getLogger("check_sentiment")


def main() -> None:
    setup_logging()
    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    # Force-enable sentiment for this check, whatever config.yaml says.
    settings = settings.model_copy(
        update={"sentiment": settings.sentiment.model_copy(update={"enabled": True})}
    )
    try:
        enricher = create_enricher(settings)
    except ValueError as exc:  # e.g. missing GEMINI_API_KEY
        logger.critical("%s", exc)
        sys.exit(1)

    assert enricher is not None
    scores = enricher._refresh_scores(settings.symbols)  # noqa: SLF001 -- deliberate one-off probe

    print("\n===== SENTIMENT CHECK =====")
    for symbol, result in scores.items():
        print(f"{symbol:>6}: {result.score:+.2f} {result.label:<8} {result.rationale}")
    print("===========================")


if __name__ == "__main__":
    main()
