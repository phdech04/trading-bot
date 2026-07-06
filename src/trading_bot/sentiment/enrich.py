"""SentimentEnricher: glues news -> cache -> LLM -> MarketSnapshot.

The engine calls `enrich(snapshot)` each bar. The enricher decides whether a
refresh is due (refresh_minutes), fetches headlines, reuses cached scores
where possible, scores the rest in ONE LLM call, and returns a copy of the
snapshot with `sentiment` filled in. Strategies stay pure: they just read
`snapshot.sentiment` like any other data.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta

from trading_bot.data.news import NewsProvider
from trading_bot.models import MarketSnapshot
from trading_bot.sentiment.base import SentimentProvider, SentimentResult, neutral
from trading_bot.sentiment.cache import SentimentCache

logger = logging.getLogger(__name__)


class SentimentEnricher:
    def __init__(
        self,
        news: NewsProvider,
        provider: SentimentProvider,
        cache: SentimentCache,
        lookback_hours: int = 24,
        refresh_minutes: int = 60,
    ) -> None:
        self._news = news
        self._provider = provider
        self._cache = cache
        self._lookback_hours = lookback_hours
        self._refresh = timedelta(minutes=refresh_minutes)
        self._last_scores: dict[str, SentimentResult] = {}
        self._last_refresh: datetime | None = None

    def enrich(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        # Snapshot time (not wall-clock) drives the cadence, so the same code
        # is well-defined in backtests too, should sentiment ever be replayed.
        due = (
            self._last_refresh is None
            or snapshot.timestamp - self._last_refresh >= self._refresh
        )
        if due:
            self._last_scores = self._refresh_scores(list(snapshot.latest_prices))
            self._last_refresh = snapshot.timestamp

        # dataclasses.replace: copy the frozen snapshot with one field changed.
        return dataclasses.replace(snapshot, sentiment=dict(self._last_scores))

    def _refresh_scores(self, symbols: list[str]) -> dict[str, SentimentResult]:
        headlines = self._news.get_headlines(symbols, self._lookback_hours)

        scores: dict[str, SentimentResult] = {}
        to_score: dict[str, list[str]] = {}
        for symbol in symbols:
            items = headlines.get(symbol, [])
            if not items:
                scores[symbol] = neutral(symbol, "no recent headlines")
                continue
            cached = self._cache.get(symbol, [item.id for item in items])
            if cached is not None:
                logger.info("Sentiment cache hit for %s", symbol)
                scores[symbol] = cached
            else:
                to_score[symbol] = [item.headline for item in items]

        if to_score:
            fresh = self._provider.score(to_score)
            for symbol, result in fresh.items():
                scores[symbol] = result
                self._cache.put(symbol, [i.id for i in headlines[symbol]], result)
                logger.info(
                    "SENTIMENT %s: %.2f (%s) -- %s",
                    symbol,
                    result.score,
                    result.label,
                    result.rationale,
                )
        return scores
