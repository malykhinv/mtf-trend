import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.risk import RiskManager


def test_risk_per_trade_and_position_size(tmp_path):
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(fetch_balance, db_path=tmp_path / "state.db")
    assert rm.risk_per_trade() == 10.0
    assert rm.position_size(100, 95) == 2.0


def test_open_risk_limit(tmp_path):
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(fetch_balance, db_path=tmp_path / "state.db")
    # open trades until limit reached
    for _ in range(10):
        rm.open_trade(100, 90)
    assert not rm.can_open_trade()


def test_consecutive_loss_halt(tmp_path):
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(
        fetch_balance,
        max_consecutive_losses=2,
        daily_drawdown_pct=1,
        db_path=tmp_path / "state.db",
    )
    rm.open_trade(100, 90)
    balance = 990.0
    rm.close_trade(-10)
    assert rm.can_open_trade()
    rm.open_trade(100, 90)
    balance = 980.0
    rm.close_trade(-10)
    assert not rm.can_open_trade()


def test_daily_drawdown_stop(tmp_path):
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(
        fetch_balance,
        daily_drawdown_pct=0.05,
        max_consecutive_losses=10,
        db_path=tmp_path / "state.db",
    )
    rm.open_trade(100, 90)
    balance = 949.0  # >5% drawdown
    rm.close_trade(-51)
    assert not rm.can_open_trade()


def test_state_persistence(tmp_path):
    balance = 1000.0

    def fetch_balance():
        return balance

    db = tmp_path / "state.db"
    rm = RiskManager(fetch_balance, max_consecutive_losses=2, db_path=db)
    rm.open_trade(100, 90)
    balance = 990.0
    rm.close_trade(-10)
    assert rm.consecutive_losses == 1

    rm2 = RiskManager(fetch_balance, max_consecutive_losses=2, db_path=db)
    assert rm2.consecutive_losses == 1
    rm2.open_trade(100, 90)
    balance = 980.0
    rm2.close_trade(-10)
    assert rm2.trading_halted

    rm3 = RiskManager(fetch_balance, max_consecutive_losses=2, db_path=db)
    assert rm3.trading_halted


def test_max_open_trades_limit(tmp_path):
    balance = 1000.0

    def fetch_balance():
        return balance

    rm = RiskManager(fetch_balance, max_open_trades=2, db_path=tmp_path / "state.db")
    rm.open_trade(100, 90)
    rm.open_trade(100, 90)
    assert rm.open_trades == 2
    assert not rm.can_open_trade()
    with pytest.raises(ValueError):
        rm.open_trade(100, 90)
    rm.close_trade(10)
    assert rm.open_trades == 1
    assert rm.can_open_trade()
    rm.open_trade(100, 90)
    assert rm.open_trades == 2
