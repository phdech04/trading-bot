"""TradingEngine: THE shared loop.

Backtest, paper, and live all run this exact code. The only thing that
changes between modes is which DataProvider and Broker get injected:

    backtest:  ReplayDataProvider + SimBroker
    paper:     IBKRDataProvider   + IBKRBroker (paper port/account)
    live:      IBKRDataProvider   + IBKRBroker (live port/account)

Per bar: snapshot -> (optional sentiment enrichment) -> strategy signal ->
risk checks -> orders -> broker (or dry-run log). Every step is logged.
"""

from __future__ import annotations

import logging
import signal
from typing import Protocol

from trading_bot.config import Settings
from trading_bot.data.base import DataProvider
from trading_bot.execution.base import Broker
from trading_bot.execution.kill_switch import flatten_all
from trading_bot.models import MarketSnapshot
from trading_bot.risk.risk_manager import RiskManager
from trading_bot.strategy.base import Strategy

logger = logging.getLogger(__name__)


class KillSwitchRequested(Exception):
    """Raised from the SIGINT/SIGTERM handler to break out of the loop,
    even if the loop is blocked waiting for market data."""


class SnapshotEnricher(Protocol):
    """Anything that can add data (e.g. sentiment) to a snapshot.

    Implemented by sentiment.enrich.SentimentEnricher; the engine only
    depends on this narrow protocol, keeping sentiment fully optional.
    """

    def enrich(self, snapshot: MarketSnapshot) -> MarketSnapshot: ...


class TradingEngine:
    def __init__(
        self,
        data: DataProvider,
        strategy: Strategy,
        risk: RiskManager,
        broker: Broker,
        settings: Settings,
        enricher: SnapshotEnricher | None = None,
    ) -> None:
        self.data = data
        self.strategy = strategy
        self.risk = risk
        self.broker = broker
        self.settings = settings
        self.enricher = enricher
        self._halt = False

    # --- public controls -----------------------------------------------------

    def stop(self) -> None:
        """Ask the loop to halt after the current bar (used by tests/tools)."""
        self._halt = True

    @property
    def halted(self) -> bool:
        return self._halt

    # --- the loop --------------------------------------------------------------

    def run(self) -> None:
        old_handlers = self._install_signal_handlers()
        mode = "DRY-RUN" if self.settings.dry_run else "EXECUTE"
        logger.info(
            "Engine starting [%s]: symbols=%s timeframe=%s",
            mode,
            self.settings.symbols,
            self.settings.timeframe.value,
        )
        try:
            for snapshot in self.data.stream_or_poll_latest(
                self.settings.symbols, self.settings.timeframe
            ):
                if self._halt:
                    break
                self._process_bar(snapshot)
                if self._halt:  # a breach inside _process_bar also halts
                    break
        except (KeyboardInterrupt, KillSwitchRequested):
            logger.critical("Interrupt received -- engaging kill switch")
            self._fire_kill_switch()
        finally:
            self._restore_signal_handlers(old_handlers)
            logger.info("Engine stopped")

    def _process_bar(self, snapshot: MarketSnapshot) -> None:
        # The sim broker needs to know current time/prices to fill against.
        # Real brokers don't have this method -- the market plays that role.
        on_snapshot = getattr(self.broker, "on_snapshot", None)
        if on_snapshot is not None:
            on_snapshot(snapshot)

        snapshot = self._maybe_enrich(snapshot)

        signal_ = self.strategy.generate_signal(snapshot)
        logger.info(
            "SIGNAL @ %s: %s (%s)",
            signal_.timestamp,
            {s: round(w, 4) for s, w in signal_.target_weights.items()},
            signal_.reason,
        )

        account = self.broker.get_account()
        positions = self.broker.get_positions()
        orders = self.risk.evaluate(signal_, account, positions, snapshot.latest_prices)

        if self.risk.daily_loss_breached:
            logger.critical("Daily loss limit breached -- engaging kill switch")
            self._fire_kill_switch()
            return

        for order in orders:
            if self.settings.dry_run:
                logger.info(
                    "[DRY-RUN] would submit: %s %d %s (%s) id=%s",
                    order.side,
                    order.qty,
                    order.symbol,
                    order.order_type,
                    order.client_order_id,
                )
            else:
                fill = self.broker.submit_order(order)
                if fill is None:
                    logger.warning(
                        "Order not (yet) filled: %s %d %s id=%s",
                        order.side,
                        order.qty,
                        order.symbol,
                        order.client_order_id,
                    )

    # --- helpers ------------------------------------------------------------

    def _maybe_enrich(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        if self.enricher is None:
            return snapshot
        try:
            return self.enricher.enrich(snapshot)
        except Exception:
            # Sentiment is a nice-to-have input; it must NEVER kill the loop.
            logger.exception("Sentiment enrichment failed -- continuing without it")
            return snapshot

    def _fire_kill_switch(self) -> None:
        self._halt = True
        if self.settings.dry_run:
            # In dry-run we never sent orders, so there is nothing of ours to
            # unwind -- and flattening would touch REAL pre-existing positions.
            logger.critical("Dry-run: halting WITHOUT flattening (no orders were sent)")
        else:
            flatten_all(self.broker)

    def _install_signal_handlers(self) -> dict[int, object]:
        """Route Ctrl-C (SIGINT) and SIGTERM into the kill switch.

        The handler just raises; the actual flattening happens in run()'s
        except block, because doing real work inside a signal handler is
        fragile. Returns the previous handlers so they can be restored.
        """

        def handler(signum: int, frame: object) -> None:  # noqa: ARG001
            raise KillSwitchRequested(f"signal {signum}")

        old: dict[int, object] = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                old[sig] = signal.signal(sig, handler)
            except ValueError:
                # signal.signal only works in the main thread (e.g. not under
                # pytest-xdist workers). Fine: stop() still works.
                logger.debug("Not in main thread; skipping handler for %s", sig)
        return old

    def _restore_signal_handlers(self, old: dict[int, object]) -> None:
        for sig, handler in old.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]
