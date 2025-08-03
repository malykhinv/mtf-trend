import pytest
from risk import risk_control as rc
from decimal import Decimal
import time


def teardown_module(module):
    # Reset configuration after tests
    rc.configure({})
    rc._state = rc.RiskState()


def test_deposit_cap_pct_applied():
    rc.configure({"deposit_cap_pct": 0.1}, deposit_size=1000)
    assert rc._limits.deposit_cap == 100.0


def test_configure_handles_none_values():
    rc.configure(
        {
            "max_position_size": None,
            "max_daily_loss": None,
            "deposit_cap": None,
            "max_consecutive_losses": None,
            "max_open_positions": None,
            "deposit_cap_pct": None,
        },
        deposit_size=1000,
    )
    assert rc._limits.max_position_size.is_infinite()
    assert rc._limits.max_daily_loss.is_infinite()
    assert rc._limits.deposit_cap.is_infinite()
    assert rc._limits.max_consecutive_losses == float("inf")
    assert rc._limits.max_open_positions == float("inf")


def test_configure_invalid_values_ignored():

    params_checks = {
        "max_position_size": lambda: rc._limits.max_position_size.is_infinite(),
        "max_daily_loss": lambda: rc._limits.max_daily_loss.is_infinite(),
        "deposit_cap": lambda: rc._limits.deposit_cap.is_infinite(),
        "max_consecutive_losses": lambda: rc._limits.max_consecutive_losses == float("inf"),
        "max_open_positions": lambda: rc._limits.max_open_positions == float("inf"),
        "deposit_cap_pct": lambda: rc._limits.deposit_cap.is_infinite(),
    }
    for p, check in params_checks.items():
        rc.configure({p: -1}, deposit_size=1000)
        assert check()


def test_configure_nan_inf_values_ignored():
    rc.configure({"max_position_size": float("nan"), "max_daily_loss": float("inf")})
    assert rc._limits.max_position_size.is_infinite()
    assert rc._limits.max_daily_loss.is_infinite()


def test_configure_deposit_cap_pct_out_of_range_ignored():
    rc.configure({"deposit_cap_pct": 1.5}, deposit_size=1000)
    assert rc._limits.deposit_cap.is_infinite()


def test_configure_negative_deposit_size_ignored():
    rc.configure({"deposit_cap_pct": 0.1}, deposit_size=-100)
    assert rc._limits.deposit_cap.is_infinite()


@pytest.mark.asyncio
async def test_daily_loss_resets_after_24h_record_pnl(monkeypatch):
    rc._state.daily_loss = Decimal("5")
    now = time.time()
    rc._state.last_reset_ts = now
    monkeypatch.setattr(rc.time, "time", lambda: now + 24 * 3600 + 1)
    await rc.record_pnl(Decimal("0"))
    assert rc._state.daily_loss == Decimal("0")
    assert rc._state.last_reset_ts == now + 24 * 3600 + 1


@pytest.mark.asyncio
async def test_daily_loss_resets_after_24h_is_paused(monkeypatch):
    rc._state.daily_loss = Decimal("5")
    now = time.time()
    rc._state.last_reset_ts = now
    monkeypatch.setattr(rc.time, "time", lambda: now + 24 * 3600 + 1)
    await rc.is_paused()
    assert rc._state.daily_loss == Decimal("0")
    assert rc._state.last_reset_ts == now + 24 * 3600 + 1
