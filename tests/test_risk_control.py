import pytest
from risk import risk_control as rc


def teardown_module(module):
    # Reset configuration after tests
    rc.configure({})


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
