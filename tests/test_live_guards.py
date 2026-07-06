"""Safety guards: paper/live mode verification + the typed LIVE confirmation."""

from __future__ import annotations

import pytest

from trading_bot.config import Settings
from trading_bot.engine.live_engine import require_live_confirmation
from trading_bot.ibkr import IBKRModeMismatchError, verify_mode


def with_env(settings: Settings, **updates) -> Settings:
    return settings.model_copy(update={"env": settings.env.model_copy(update=updates)})


# --- verify_mode: account/port must agree with TRADING_MODE -----------------


def test_paper_mode_with_paper_account_ok(settings: Settings) -> None:
    verify_mode(["DU1234567"], settings)  # must not raise


def test_paper_mode_with_live_account_refuses(settings: Settings) -> None:
    with pytest.raises(IBKRModeMismatchError, match="does not look like a paper account"):
        verify_mode(["U7654321"], settings)


def test_live_mode_with_paper_account_refuses(settings: Settings) -> None:
    live = with_env(settings, trading_mode="live", ibkr_port=7496)
    with pytest.raises(IBKRModeMismatchError, match="is a paper account"):
        verify_mode(["DU1234567"], live)


def test_live_mode_on_paper_port_refuses(settings: Settings) -> None:
    live = with_env(settings, trading_mode="live", ibkr_port=7497)  # paper port!
    with pytest.raises(IBKRModeMismatchError, match="PAPER port"):
        verify_mode(["U7654321"], live)


def test_paper_mode_on_live_port_refuses(settings: Settings) -> None:
    paper = with_env(settings, ibkr_port=4001)  # live gateway port
    with pytest.raises(IBKRModeMismatchError, match="LIVE port"):
        verify_mode(["DU1234567"], paper)


def test_unknown_configured_account_refuses(settings: Settings) -> None:
    cfg = with_env(settings, ibkr_account="DU9999999")
    with pytest.raises(IBKRModeMismatchError, match="not among the accounts"):
        verify_mode(["DU1234567"], cfg)


# --- require_live_confirmation: a human must type LIVE ----------------------


def test_exact_phrase_accepts(settings: Settings) -> None:
    assert require_live_confirmation(settings, input_fn=lambda _: "LIVE") is True


@pytest.mark.parametrize("answer", ["live", "yes", "", "LIVE!", "L I V E"])
def test_anything_else_refuses(settings: Settings, answer: str) -> None:
    assert require_live_confirmation(settings, input_fn=lambda _: answer) is False


def test_no_terminal_refuses(settings: Settings) -> None:
    def no_tty(_prompt: str) -> str:
        raise EOFError  # what input() does when stdin is not interactive

    assert require_live_confirmation(settings, input_fn=no_tty) is False
