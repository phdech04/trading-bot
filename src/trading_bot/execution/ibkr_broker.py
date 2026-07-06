"""IBKRBroker: order execution through IB Gateway / TWS.

IMPORTANT: this same class serves paper AND live. There is deliberately no
`if paper:` anywhere in it -- which system your orders reach is decided
entirely by the port/account Gateway/TWS is logged into (checked at startup
by trading_bot.ibkr.verify_mode). Paper trading therefore exercises the
exact code path live trading will use.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ib_async import IB, MarketOrder, Stock

from trading_bot.config import Settings
from trading_bot.execution.base import Broker
from trading_bot.models import AccountInfo, Fill, Order, Position
from trading_bot.utils import retry_with_backoff

logger = logging.getLogger(__name__)

# Trade statuses that mean "this order is finished and will never fill (again)".
_DEAD_STATUSES = {"Cancelled", "ApiCancelled", "Inactive"}


class IBKRBroker(Broker):
    def __init__(self, ib: IB, settings: Settings, fill_timeout: float = 15.0) -> None:
        self._ib = ib
        self._settings = settings
        self._fill_timeout = fill_timeout
        accounts = ib.managedAccounts()
        self._account = settings.env.ibkr_account or (accounts[0] if accounts else "")
        self._contracts: dict[str, Stock] = {}

    def _contract(self, symbol: str) -> Stock:
        if symbol not in self._contracts:
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            self._contracts[symbol] = contract
        return self._contracts[symbol]

    # --- Broker interface -----------------------------------------------------

    def submit_order(self, order: Order) -> Fill | None:
        # Idempotency: IBKR echoes our client_order_id back as `orderRef` on
        # every trade this session knows about. If we already submitted this
        # exact intent (e.g. we retried after a timeout), skip it.
        if order.client_order_id:
            for trade in self._ib.trades():
                if (
                    trade.order.orderRef == order.client_order_id
                    and trade.orderStatus.status not in _DEAD_STATUSES
                ):
                    logger.warning(
                        "Order with id %s already exists (status %s) -- skipping duplicate",
                        order.client_order_id,
                        trade.orderStatus.status,
                    )
                    return None

        ib_order = MarketOrder(order.side, order.qty)
        ib_order.orderRef = order.client_order_id
        if self._account:
            ib_order.account = self._account

        trade = self._ib.placeOrder(self._contract(order.symbol), ib_order)
        logger.info(
            "Submitted %s %d %s id=%s", order.side, order.qty, order.symbol, order.client_order_id
        )

        # Market orders usually fill in seconds during market hours; wait a
        # short while so we can log the fill. If it doesn't fill in time we
        # return None -- the order keeps working at IBKR, and the idempotency
        # check above stops us from accidentally re-submitting it next bar.
        waited = 0.0
        while not trade.isDone() and waited < self._fill_timeout:
            self._ib.waitOnUpdate(timeout=1.0)
            waited += 1.0

        if trade.orderStatus.status == "Filled":
            commission = sum(
                f.commissionReport.commission for f in trade.fills if f.commissionReport
            )
            fill = Fill(
                order=order,
                filled_qty=int(trade.orderStatus.filled),
                fill_price=float(trade.orderStatus.avgFillPrice),
                commission=float(commission),
                timestamp=datetime.now(),
            )
            logger.info(
                "FILL %s %d %s @ %.4f (commission %.2f) id=%s",
                order.side,
                fill.filled_qty,
                order.symbol,
                fill.fill_price,
                fill.commission,
                order.client_order_id,
            )
            return fill

        logger.warning(
            "Order %s not filled after %.0fs (status: %s) -- it may still fill later",
            order.client_order_id,
            self._fill_timeout,
            trade.orderStatus.status,
        )
        return None

    @retry_with_backoff()
    def get_positions(self) -> dict[str, Position]:
        result: dict[str, Position] = {}
        for pos in self._ib.positions(self._account):
            if pos.contract.secType != "STK" or pos.position == 0:
                continue
            result[pos.contract.symbol] = Position(
                symbol=pos.contract.symbol,
                qty=int(pos.position),
                avg_cost=float(pos.avgCost),
            )
        return result

    @retry_with_backoff()
    def get_account(self) -> AccountInfo:
        summary = {row.tag: row.value for row in self._ib.accountSummary(self._account)}
        try:
            return AccountInfo(
                account_id=self._account,
                equity=float(summary["NetLiquidation"]),
                cash=float(summary["TotalCashValue"]),
                buying_power=float(summary["BuyingPower"]),
            )
        except KeyError as exc:
            raise ConnectionError(f"Account summary missing tag {exc} -- is IBKR healthy?") from exc

    def cancel_all(self) -> None:
        logger.warning("Cancelling ALL open orders (reqGlobalCancel)")
        self._ib.reqGlobalCancel()
        self._ib.waitOnUpdate(timeout=2.0)  # give cancellations a moment to land

    def close_all_positions(self) -> None:
        positions = self.get_positions()
        if not positions:
            logger.info("No positions to close")
            return
        for symbol, pos in positions.items():
            side = "SELL" if pos.qty > 0 else "BUY"  # BUY covers a short
            self.submit_order(
                Order(
                    symbol=symbol,
                    side=side,
                    qty=abs(pos.qty),
                    client_order_id=f"flatten-{symbol}-{datetime.now():%Y%m%d-%H%M%S}",
                )
            )
