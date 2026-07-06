"""News headlines for the sentiment module.

IBKR's news feeds need paid subscriptions, so the default source is Yahoo
Finance's free per-ticker RSS feed (no API key). The NewsProvider interface
keeps the source swappable -- a paid news API later is a drop-in replacement.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

import feedparser
import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsItem:
    id: str  # stable per article -- the sentiment cache keys on (symbol, id)
    symbol: str
    headline: str
    published: datetime
    source: str


class NewsProvider(ABC):
    @abstractmethod
    def get_headlines(self, symbols: list[str], lookback_hours: int) -> dict[str, list[NewsItem]]:
        """Recent headlines per symbol. MUST degrade gracefully: a symbol
        with no news (or a fetch error) gets an empty list, never an exception."""


class YahooRSSNews(NewsProvider):
    URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"

    def get_headlines(self, symbols: list[str], lookback_hours: int) -> dict[str, list[NewsItem]]:
        cutoff = datetime.now() - timedelta(hours=lookback_hours)
        result: dict[str, list[NewsItem]] = {}
        for symbol in symbols:
            try:
                result[symbol] = self._fetch_symbol(symbol, cutoff)
            except Exception as exc:
                # News is optional input -- log and carry on with no items.
                logger.warning("News fetch failed for %s: %s", symbol, exc)
                result[symbol] = []
        return result

    def _fetch_symbol(self, symbol: str, cutoff: datetime) -> list[NewsItem]:
        # Fetch with requests (bundled CA certs + a real timeout), then let
        # feedparser only PARSE. feedparser's own fetching has no timeout and
        # trips over missing system certificates on some Python installs.
        response = requests.get(
            self.URL.format(symbol=symbol),
            timeout=15,
            headers={"User-Agent": "trading-bot/0.1 (+https://github.com/)"},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            # bozo = feedparser's flag for "this XML was malformed".
            logger.warning("Malformed/empty RSS feed for %s: %s", symbol, feed.bozo_exception)
            return []

        items: list[NewsItem] = []
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            # published_parsed is a time.struct_time (UTC-ish); tolerate absence.
            parsed = entry.get("published_parsed")
            published = datetime(*parsed[:6]) if parsed else datetime.now()
            if published < cutoff:
                continue
            items.append(
                NewsItem(
                    id=entry.get("id") or entry.get("link") or title,
                    symbol=symbol,
                    headline=title,
                    published=published,
                    source="yahoo-rss",
                )
            )
        logger.info("Fetched %d headlines for %s", len(items), symbol)
        return items
