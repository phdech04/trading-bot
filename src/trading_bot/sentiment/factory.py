"""Builds the optional sentiment pipeline from Settings.

Returns None when sentiment is disabled -- the engine then simply skips
enrichment. Imports happen lazily inside the enabled branch so a bot with
sentiment turned off never even loads the LLM/news dependencies.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from trading_bot.config import Settings

if TYPE_CHECKING:
    from trading_bot.sentiment.enrich import SentimentEnricher

logger = logging.getLogger(__name__)


def create_enricher(settings: Settings) -> SentimentEnricher | None:
    if not settings.sentiment.enabled:
        logger.info("Sentiment module disabled (sentiment.enabled=false)")
        return None

    from trading_bot.data.news import YahooRSSNews
    from trading_bot.sentiment.cache import SentimentCache
    from trading_bot.sentiment.enrich import SentimentEnricher
    from trading_bot.sentiment.llm_sentiment import GeminiSentiment, OllamaSentiment

    provider_name = settings.env.sentiment_provider
    if provider_name == "gemini":
        if not settings.env.gemini_api_key:
            raise ValueError(
                "sentiment.enabled=true with SENTIMENT_PROVIDER=gemini, but "
                "GEMINI_API_KEY is empty. Add it to .env (or switch to ollama)."
            )
        provider = GeminiSentiment(
            api_key=settings.env.gemini_api_key,
            model=settings.sentiment.gemini_model,
        )
    else:  # "ollama" -- validated by config, no other values possible
        provider = OllamaSentiment(
            base_url=settings.sentiment.ollama_base_url,
            model=settings.sentiment.ollama_model,
        )

    logger.info("Sentiment enabled: provider=%s", provider_name)
    return SentimentEnricher(
        news=YahooRSSNews(),
        provider=provider,
        cache=SentimentCache("data/sentiment_cache.json"),
        lookback_hours=settings.sentiment.news_lookback_hours,
        refresh_minutes=settings.sentiment.refresh_minutes,
    )
