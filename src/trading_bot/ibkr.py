"""IBKR connection helper + paper/live safety guard.

IBKR has no API keys: the bot opens a socket to a running IB Gateway or
Trader Workstation (TWS), which is where you actually log in. Which SYSTEM
you reach (paper or live) is decided by the port and the account -- so this
module cross-checks both against TRADING_MODE before anything else runs.
"""

from __future__ import annotations

import logging

from ib_async import IB

from trading_bot.config import Settings

logger = logging.getLogger(__name__)

# Conventional API ports (configurable inside Gateway/TWS, so we also treat
# unknown ports as "can't tell" rather than failing hard).
PAPER_PORTS = {7497, 4002}  # TWS paper, Gateway paper
LIVE_PORTS = {7496, 4001}  # TWS live, Gateway live

CONNECT_HELP = """\
Could not connect to IB Gateway / TWS at {host}:{port} (client id {client_id}).

Checklist:
  1. Is IB Gateway or TWS running and logged in?
  2. Is the API enabled?  (Configure -> API -> Settings ->
     'Enable ActiveX and Socket Clients'; for paper TWS also uncheck
     'Read-Only API' if you want to place orders)
  3. Does IBKR_PORT in .env match the socket port in those settings?
       7497 = TWS paper      7496 = TWS live
       4002 = Gateway paper  4001 = Gateway live
  4. Is 127.0.0.1 allowed under 'Trusted IPs' (or 'Allow connections from
     localhost only' checked)?
"""


class IBKRConnectionError(Exception):
    """Gateway/TWS unreachable, or refused the connection."""


class IBKRModeMismatchError(Exception):
    """TRADING_MODE disagrees with the port/account we actually connected to."""


def verify_mode(accounts: list[str], settings: Settings) -> None:
    """Cross-check TRADING_MODE against the connected account + port.

    Pure function (no I/O) so it is easy to test. IBKR paper accounts start
    with 'DU' by convention; live accounts start with 'U'. Any mismatch in
    either direction aborts -- a mismatch ALWAYS means misconfiguration, and
    with real money involved we stop rather than guess.
    """
    mode = settings.env.trading_mode
    port = settings.env.ibkr_port

    if settings.env.ibkr_account and settings.env.ibkr_account not in accounts:
        raise IBKRModeMismatchError(
            f"IBKR_ACCOUNT={settings.env.ibkr_account} is not among the accounts this "
            f"login manages: {accounts}. Fix IBKR_ACCOUNT in .env."
        )

    account = settings.env.ibkr_account or (accounts[0] if accounts else "")
    account_is_paper = account.startswith("DU")

    if mode == "paper" and not account_is_paper:
        raise IBKRModeMismatchError(
            f"TRADING_MODE=paper but connected account '{account}' does not look like a "
            "paper account (paper accounts start with 'DU'). You may be connected to a "
            "LIVE system -- refusing to continue."
        )
    if mode == "live" and account_is_paper:
        raise IBKRModeMismatchError(
            f"TRADING_MODE=live but connected account '{account}' is a paper account. "
            "Either set TRADING_MODE=paper or log Gateway/TWS into your live account."
        )

    if port in PAPER_PORTS and mode == "live":
        raise IBKRModeMismatchError(
            f"TRADING_MODE=live but IBKR_PORT={port} is a conventional PAPER port."
        )
    if port in LIVE_PORTS and mode == "paper":
        raise IBKRModeMismatchError(
            f"TRADING_MODE=paper but IBKR_PORT={port} is a conventional LIVE port."
        )
    if port not in PAPER_PORTS | LIVE_PORTS:
        logger.warning(
            "IBKR_PORT=%d is not one of the conventional ports %s -- cannot sanity-check "
            "it against TRADING_MODE. Make sure it is really a %s port.",
            port,
            sorted(PAPER_PORTS | LIVE_PORTS),
            mode.upper(),
        )

    logger.info("Mode check OK: TRADING_MODE=%s, account=%s, port=%d", mode, account, port)


def connect_ibkr(settings: Settings, timeout: float = 10.0) -> IB:
    """Connect to Gateway/TWS, verify paper/live mode, return the live IB handle.

    Raises IBKRConnectionError with a step-by-step checklist when the
    connection fails (the most common beginner problem: Gateway isn't running).
    """
    env = settings.env
    ib = IB()
    try:
        ib.connect(env.ibkr_host, env.ibkr_port, clientId=env.ibkr_client_id, timeout=timeout)
    except (ConnectionError, OSError, TimeoutError) as exc:
        raise IBKRConnectionError(
            CONNECT_HELP.format(host=env.ibkr_host, port=env.ibkr_port, client_id=env.ibkr_client_id)
        ) from exc

    try:
        verify_mode(ib.managedAccounts(), settings)
    except IBKRModeMismatchError:
        ib.disconnect()  # do not leave a mismatched session open
        raise

    logger.info("Connected to IBKR at %s:%d", env.ibkr_host, env.ibkr_port)
    return ib
