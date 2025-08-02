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


def test_configure_negative_values_raise():

    params = [
        "max_position_size",
        "max_daily_loss",
        "deposit_cap",
        "max_consecutive_losses",
        "max_open_positions",
        "deposit_cap_pct",
    ]
    for p in params:
        with pytest.raises(ValueError):
            rc.configure({p: -1}, deposit_size=1000)


def test_configure_deposit_cap_pct_above_one_raises():

    with pytest.raises(ValueError):
        rc.configure({"deposit_cap_pct": 1.5}, deposit_size=1000)


def test_configure_negative_deposit_size_raises():

    with pytest.raises(ValueError):
        rc.configure({}, deposit_size=-100)
