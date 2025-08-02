from risk import risk_control as rc


def teardown_module(module):
    # Reset configuration after tests
    rc.configure({})


def test_deposit_cap_pct_applied():
    rc.configure({"deposit_cap_pct": 0.1}, deposit_size=1000)
    assert rc._limits.deposit_cap == 100.0
