"""Webhook alerts (Slack/Telegram/Discord-style). Optional.

Set ALERT_WEBHOOK_URL in .env to any endpoint that accepts a JSON POST with
a `text` field (Slack incoming webhooks do). Leave it empty to disable.
Alerts are fire-and-forget: a failed alert is logged, never raised -- the
bot must keep trading even if Slack is down.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def send_alert(webhook_url: str, message: str) -> None:
    if not webhook_url:
        return  # alerts not configured -- silently a no-op
    try:
        response = requests.post(webhook_url, json={"text": message}, timeout=10)
        response.raise_for_status()
        logger.info("Alert sent: %s", message)
    except requests.RequestException as exc:
        logger.error("Alert delivery failed (%s): %s", exc, message)
