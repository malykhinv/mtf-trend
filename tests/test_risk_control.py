import logging
from decimal import Decimal

import pytest

import risk.risk_control as rc


def test_maybe_reset_daily_loss_uses_utc(caplog):
    prev_last_reset_ts = rc._state.last_reset_ts
    prev_daily_loss = rc._state.daily_loss
    try:
        rc._state.last_reset_ts = 0
        rc._state.daily_loss = Decimal("5")
        now = 24 * 3600 + 1

        with caplog.at_level(logging.INFO):
            rc._maybe_reset_daily_loss(now)

        assert rc._state.daily_loss == Decimal("0")
        assert rc._state.last_reset_ts == now
        assert "+00:00" in caplog.text
    finally:
        rc._state.last_reset_ts = prev_last_reset_ts
        rc._state.daily_loss = prev_daily_loss

