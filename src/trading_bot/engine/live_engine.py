"""Wiring for paper and live trading: IBKR adapters around the SAME engine.

Note what is NOT here: there is no paper loop and no live loop. Both modes
build the identical TradingEngine; the only difference between paper and
live is which Gateway/TWS system the socket reaches (checked by verify_mode).
"""

from __future__ import annotations

import logging

from trading_bot.config import Settings
from trading_bot.data.ibkr_data import IBKRDataProvider
from trading_bot.engine.core import TradingEngine
from trading_bot.execution.ibkr_broker import IBKRBroker
from trading_bot.ibkr import connect_ibkr
from trading_bot.risk.risk_manager import RiskManager
from trading_bot.sentiment.factory import create_enricher
from trading_bot.strategy.placeholder import BuyAndHoldPlaceholder

logger = logging.getLogger(__name__)

LIVE_CONFIRM_PHRASE = "LIVE"


def run_trading(settings: Settings) -> None:
    """Connect to IBKR, run the engine until stopped, always disconnect."""
    ib = connect_ibkr(settings)
    try:
        engine = TradingEngine(
            data=IBKRDataProvider(ib, settings),
            strategy=BuyAndHoldPlaceholder(settings.symbols),
            risk=RiskManager(settings.risk),
            broker=IBKRBroker(ib, settings),
            settings=settings,
            enricher=create_enricher(settings),
        )
        engine.run()
    finally:
        ib.disconnect()
        logger.info("Disconnected from IBKR")


def require_live_confirmation(settings: Settings, input_fn=input) -> bool:
    """The interactive guard for live trading. Returns True only when the
    user types the exact confirmation phrase.

    Kept as a plain function (with injectable input) so tests can prove the
    guard refuses without a terminal session.
    """
    print("=" * 60)
    print("!!! LIVE TRADING -- REAL MONEY !!!")
    print(f"    account:  {settings.env.ibkr_account or '(first managed account)'}")
    print(f"    port:     {settings.env.ibkr_port}")
    print(f"    symbols:  {settings.symbols}")
    print(f"    dry_run:  {settings.dry_run}")
    print("=" * 60)
    try:
        answer = input_fn(f"Type {LIVE_CONFIRM_PHRASE} (exactly) to continue: ")
    except EOFError:
        # No interactive terminal (piped/cron) -> refuse. Live trading may
        # only ever start with a human at the keyboard.
        return False
    return answer.strip() == LIVE_CONFIRM_PHRASE
