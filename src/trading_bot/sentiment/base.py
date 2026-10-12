"""SentimentProvider ABC + SentimentResult.

Sentiment is a signal INPUT: the engine may attach per-symbol scores to the
MarketSnapshot, and a strategy may (or may not) look at them. Nothing else
in the bot knows or cares which LLM produced the numbers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

Label = Literal["bearish", "neutral", "bullish"]


@dataclass(frozen=True)
class SentimentResult:
    symbol: str
    score: float  # -1 (max bearish) .. +1 (max bullish)
    label: Label
    rationale: str  # one line: WHY the model thinks so (for the logs)
    # False = this is a fallback (LLM failed / no data), not a real assessment.
    # Fallbacks are never cached, so a transient outage isn't frozen in.
    ok: bool = True


def neutral(symbol: str, rationale: str) -> SentimentResult:
    """The safe fallback whenever scoring is impossible or unreliable."""
    return SentimentResult(symbol=symbol, score=0.0, label="neutral", rationale=rationale, ok=False)


class SentimentProvider(ABC):
    @abstractmethod
    def score(self, headlines_by_symbol: dict[str, list[str]]) -> dict[str, SentimentResult]:
        """Score a batch of headlines, grouped per symbol.

        (The grouping is what lets one LLM call rate many symbols and still
        attribute each score to the right ticker.)

        MUST always return an entry for every requested symbol -- degrade to
        `neutral(...)` rather than raising, so a flaky LLM can never take
        down the trading loop.
        """
