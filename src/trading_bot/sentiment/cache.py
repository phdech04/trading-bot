"""Sentiment cache: don't pay (or wait) to re-score articles we've seen.

Keyed by (symbol, article_id), as one flat JSON file. After scoring a
symbol's batch of articles we store that symbol-level result under EVERY
(symbol, article_id) involved. On the next refresh:

  - same articles  -> every id hits -> reuse the stored result, no LLM call;
  - any NEW article -> at least one id misses -> re-score the whole batch
    (the new article might change the overall picture).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from trading_bot.sentiment.base import SentimentResult

logger = logging.getLogger(__name__)


class SentimentCache:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._entries: dict[str, dict] = {}
        if self._path.exists():
            try:
                self._entries = json.loads(self._path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not read sentiment cache %s: %s", self._path, exc)

    @staticmethod
    def _key(symbol: str, article_id: str) -> str:
        return f"{symbol}::{article_id}"

    def get(self, symbol: str, article_ids: list[str]) -> SentimentResult | None:
        """Cache hit only if EVERY article id is known for this symbol."""
        if not article_ids:
            return None
        stored = [self._entries.get(self._key(symbol, aid)) for aid in article_ids]
        if any(entry is None for entry in stored):
            return None
        return SentimentResult(**stored[-1])  # all stored for the same batch; take one

    def put(self, symbol: str, article_ids: list[str], result: SentimentResult) -> None:
        for aid in article_ids:
            self._entries[self._key(symbol, aid)] = asdict(result)
        self._save()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._entries, indent=2))
        except OSError as exc:
            # A broken cache only costs re-scoring; never crash over it.
            logger.warning("Could not write sentiment cache %s: %s", self._path, exc)
