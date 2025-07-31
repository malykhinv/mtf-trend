import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.risk import RiskManager


def test_risk_per_trade_and_position_size():
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(fetch_balance)
    assert rm.risk_per_trade() == 10.0
    assert rm.position_size(100, 95) == 2.0


def test_open_risk_limit():
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(fetch_balance)
    # open trades until limit reached
    for _ in range(10):
        rm.open_trade(100, 90)
    assert not rm.can_open_trade()


def test_consecutive_loss_halt():
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(fetch_balance, max_consecutive_losses=2, daily_drawdown_pct=1)
    rm.open_trade(100, 90)
    balance = 990.0
    rm.close_trade(-10)
    assert rm.can_open_trade()
    rm.open_trade(100, 90)
    balance = 980.0
    rm.close_trade(-10)
    assert not rm.can_open_trade()


def test_daily_drawdown_stop():
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(fetch_balance, daily_drawdown_pct=0.05, max_consecutive_losses=10)
    rm.open_trade(100, 90)
    balance = 949.0  # >5% drawdown
    rm.close_trade(-51)
    assert not rm.can_open_trade()
