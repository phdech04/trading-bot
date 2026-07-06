"""LLM-backed sentiment scorers: Gemini (hosted) and Ollama (local).

Both providers share the same prompt, the same defensive JSON parsing, and
the same retry-once-then-neutral policy -- only `_call_llm()` differs. Which
one runs is a one-line env change (SENTIMENT_PROVIDER=gemini|ollama).
"""

from __future__ import annotations

import json
import logging
from abc import abstractmethod

import requests

from trading_bot.sentiment.base import SentimentProvider, SentimentResult, neutral

logger = logging.getLogger(__name__)

VALID_LABELS = {"bearish", "neutral", "bullish"}

PROMPT_TEMPLATE = """\
You are a financial news sentiment rater for short-term US equity trading.

Rate the aggregate sentiment of the headlines below, per stock symbol.

Return STRICT JSON only. No prose, no explanations outside the JSON, and no
markdown code fences. Use exactly this schema (one entry per symbol):

{{"SYMBOL": {{"score": 0.0, "label": "neutral", "rationale": "one short line"}}}}

Rules:
- "score": a number in [-1, 1]; -1 = very bearish, 0 = neutral, 1 = very bullish.
- "label": exactly one of "bearish", "neutral", "bullish".
- "rationale": ONE short line explaining the score.
- Include EVERY symbol listed below, even if its headlines are unclear
  (then use score 0 and label "neutral").

Headlines by symbol:
{headlines}
"""


def build_prompt(headlines_by_symbol: dict[str, list[str]]) -> str:
    blocks = []
    for symbol, headlines in sorted(headlines_by_symbol.items()):
        lines = "\n".join(f"- {h}" for h in headlines) or "- (no headlines)"
        blocks.append(f"{symbol}:\n{lines}")
    return PROMPT_TEMPLATE.format(headlines="\n\n".join(blocks))


def parse_sentiment_json(raw: str, symbols: list[str]) -> dict[str, SentimentResult]:
    """Parse the model's reply defensively.

    Raises ValueError on anything unusable (caller retries once). Individual
    weirdness (score out of range, bad label, missing symbol) is repaired
    instead: clamp, re-derive, or fall back to neutral -- one flaky entry
    shouldn't discard an otherwise good batch.
    """
    text = raw.strip()
    # Models sometimes wrap JSON in ```json fences despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__}")

    results: dict[str, SentimentResult] = {}
    for symbol in symbols:
        entry = data.get(symbol)
        if not isinstance(entry, dict):
            results[symbol] = neutral(symbol, "model returned no entry for this symbol")
            continue
        try:
            score = max(-1.0, min(1.0, float(entry["score"])))
        except (KeyError, TypeError, ValueError):
            results[symbol] = neutral(symbol, "model returned an unusable score")
            continue
        label = entry.get("label")
        if label not in VALID_LABELS:
            # Derive the label from the score rather than trusting the model.
            label = "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral"
        results[symbol] = SentimentResult(
            symbol=symbol,
            score=score,
            label=label,
            rationale=str(entry.get("rationale", ""))[:200],
        )
    return results


class LLMSentimentBase(SentimentProvider):
    """Shared scoring flow; subclasses only implement the actual LLM call."""

    def score(self, headlines_by_symbol: dict[str, list[str]]) -> dict[str, SentimentResult]:
        symbols = sorted(headlines_by_symbol)
        if not any(headlines_by_symbol.values()):
            return {s: neutral(s, "no recent headlines") for s in symbols}

        prompt = build_prompt(headlines_by_symbol)
        last_error: Exception | None = None
        for attempt in (1, 2):  # spec: retry once on malformed output
            try:
                raw = self._call_llm(prompt)
                return parse_sentiment_json(raw, symbols)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Sentiment scoring attempt %d/2 failed (%s): %s",
                    attempt,
                    type(self).__name__,
                    exc,
                )
        logger.error("Sentiment unavailable, returning neutral for all symbols: %s", last_error)
        return {s: neutral(s, f"sentiment unavailable: {last_error}") for s in symbols}

    @abstractmethod
    def _call_llm(self, prompt: str) -> str:
        """Send the prompt, return the raw text reply."""


class GeminiSentiment(LLMSentimentBase):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        # Imported here (not module top) so the Ollama-only setup never
        # needs Google credentials/SDK working.
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def _call_llm(self, prompt: str) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                # Ask the API itself to enforce JSON output -- much more
                # reliable than prompt instructions alone.
                response_mime_type="application/json",
                temperature=0.0,  # scoring should be repeatable, not creative
            ),
        )
        return response.text or ""


class OllamaSentiment(LLMSentimentBase):
    """Local, keyless option: talks to `ollama serve` on your machine."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2") -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    def _call_llm(self, prompt: str) -> str:
        response = requests.post(
            f"{self._base_url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "format": "json",  # Ollama's JSON mode
                "options": {"temperature": 0.0},
            },
            timeout=120,  # small local models can be slow on first load
        )
        response.raise_for_status()
        return response.json().get("response", "")
