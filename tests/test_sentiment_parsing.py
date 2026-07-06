"""Sentiment: strict-JSON parsing, retry-once-then-neutral, cache, enricher."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from trading_bot.data.news import NewsItem, NewsProvider
from trading_bot.models import MarketSnapshot
from trading_bot.sentiment.base import SentimentResult
from trading_bot.sentiment.cache import SentimentCache
from trading_bot.sentiment.enrich import SentimentEnricher
from trading_bot.sentiment.llm_sentiment import LLMSentimentBase, parse_sentiment_json

GOOD_JSON = json.dumps(
    {
        "AAPL": {"score": 0.6, "label": "bullish", "rationale": "strong earnings"},
        "MSFT": {"score": -0.3, "label": "bearish", "rationale": "guidance cut"},
    }
)


# --- parse_sentiment_json ----------------------------------------------------


def test_parses_strict_json() -> None:
    results = parse_sentiment_json(GOOD_JSON, ["AAPL", "MSFT"])
    assert results["AAPL"].score == 0.6
    assert results["AAPL"].label == "bullish"
    assert results["MSFT"].label == "bearish"


def test_strips_markdown_fences() -> None:
    fenced = f"```json\n{GOOD_JSON}\n```"
    results = parse_sentiment_json(fenced, ["AAPL", "MSFT"])
    assert results["AAPL"].score == 0.6


def test_clamps_out_of_range_scores() -> None:
    raw = json.dumps({"AAPL": {"score": 7, "label": "bullish", "rationale": "x"}})
    assert parse_sentiment_json(raw, ["AAPL"])["AAPL"].score == 1.0


def test_bad_label_is_rederived_from_score() -> None:
    raw = json.dumps({"AAPL": {"score": -0.9, "label": "MOON", "rationale": "x"}})
    assert parse_sentiment_json(raw, ["AAPL"])["AAPL"].label == "bearish"


def test_missing_symbol_gets_neutral() -> None:
    results = parse_sentiment_json(GOOD_JSON, ["AAPL", "MSFT", "NVDA"])
    assert results["NVDA"].label == "neutral"
    assert results["NVDA"].score == 0.0


def test_garbage_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_sentiment_json("The market feels bullish today!", ["AAPL"])


# --- retry-once-then-neutral policy -----------------------------------------


class ScriptedLLM(LLMSentimentBase):
    """Returns canned replies in order; records how many calls were made."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def _call_llm(self, prompt: str) -> str:
        self.calls += 1
        return self.replies.pop(0)


def test_malformed_then_good_reply_recovers() -> None:
    llm = ScriptedLLM(["not json at all", GOOD_JSON])
    results = llm.score({"AAPL": ["headline"], "MSFT": ["headline"]})
    assert llm.calls == 2  # retried exactly once
    assert results["AAPL"].label == "bullish"


def test_two_malformed_replies_fall_back_to_neutral() -> None:
    llm = ScriptedLLM(["garbage", "more garbage"])
    results = llm.score({"AAPL": ["headline"]})
    assert llm.calls == 2  # retry once, then give up -- never a third call
    assert results["AAPL"].label == "neutral"
    assert "unavailable" in results["AAPL"].rationale


def test_no_headlines_skips_the_llm_entirely() -> None:
    llm = ScriptedLLM([])
    results = llm.score({"AAPL": []})
    assert llm.calls == 0
    assert results["AAPL"].label == "neutral"


# --- cache -------------------------------------------------------------------


def test_cache_roundtrip_and_new_article_miss(tmp_path: Path) -> None:
    cache = SentimentCache(tmp_path / "cache.json")
    result = SentimentResult("AAPL", 0.5, "bullish", "test")
    cache.put("AAPL", ["a1", "a2"], result)

    assert cache.get("AAPL", ["a1", "a2"]) == result  # same articles: hit
    assert cache.get("AAPL", ["a1", "a3"]) is None  # new article: miss
    assert cache.get("MSFT", ["a1"]) is None  # other symbol: miss

    # And it survives a restart (new instance, same file).
    assert SentimentCache(tmp_path / "cache.json").get("AAPL", ["a1", "a2"]) == result


# --- enricher ------------------------------------------------------------------


class FakeNews(NewsProvider):
    def get_headlines(self, symbols: list[str], lookback_hours: int):
        return {
            s: [NewsItem(id=f"{s}-1", symbol=s, headline=f"{s} rallies", published=datetime.now(), source="fake")]
            for s in symbols
        }


def _snapshot(ts: datetime) -> MarketSnapshot:
    return MarketSnapshot(timestamp=ts, bars={}, latest_prices={"AAPL": 100.0})


def test_enricher_attaches_sentiment_and_respects_refresh(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [json.dumps({"AAPL": {"score": 0.4, "label": "bullish", "rationale": "x"}})]
    )
    enricher = SentimentEnricher(
        news=FakeNews(),
        provider=llm,
        cache=SentimentCache(tmp_path / "cache.json"),
        refresh_minutes=60,
    )

    t0 = datetime(2024, 3, 4, 10, 0)
    enriched = enricher.enrich(_snapshot(t0))
    assert enriched.sentiment is not None
    assert enriched.sentiment["AAPL"].label == "bullish"
    assert llm.calls == 1

    # 30 minutes later: within refresh window -> reuse, no new LLM call
    # (ScriptedLLM would raise IndexError if called again -- replies is empty).
    again = enricher.enrich(_snapshot(t0 + timedelta(minutes=30)))
    assert again.sentiment["AAPL"].label == "bullish"
    assert llm.calls == 1
