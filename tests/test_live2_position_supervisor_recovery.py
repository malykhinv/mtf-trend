import pytest

from research_tools.anomaly_live2.execution import Live2ProtectedPosition
from research_tools.anomaly_live2.position_supervisor import Live2PositionSupervisor, _tp1_reduce_only_close_amount


class _ReadyExecution:
    ready = True


def test_stop_close_recovery_matches_binance_child_reduce_only_fill() -> None:
    supervisor = Live2PositionSupervisor(exchange_client=object(), execution_engine=_ReadyExecution())  # type: ignore[arg-type]
    position = Live2ProtectedPosition(
        position_id="AAA_USDT_USDT_100_1",
        symbol="AAA/USDT:USDT",
        opened_at_ms=100,
        entry_order_id="1",
        entry_client_order_id="l2e_AAA_USDT_USDT_100",
        entry_fill_price=1.0,
        amount=10.0,
        stop_price=0.99,
        stop_order_id="conditional-stop-order-id",
        stop_client_order_id="l2sr_AAA_USDT_USDT_200",
        pre_position_amount=0.0,
        post_position_amount=10.0,
        category_id="test",
        signal_entry_price=1.0,
        initial_risk_pct=0.01,
        tp1_price=1.02,
        initial_amount=10.0,
        remaining_amount=5.0,
        tp1_client_order_id="l2tp1_AAA_USDT_USDT_150",
        tp1_closed_amount=5.0,
        tp1_fill_price=1.02,
        realized_pnl_usdt=0.1,
        last_supervised_ms=200,
    )
    supervisor.set_user_data_status_provider(
        lambda: {
            "recent_order_events": [
                {
                    "symbol": "AAAUSDT",
                    "client_order_id": "l2sr____USDT_USDT_200",
                    "order_id": "child-market-order-id",
                    "execution_type": "TRADE",
                    "order_status": "FILLED",
                    "side": "SELL",
                    "order_type": "MARKET",
                    "reduce_only": True,
                    "last_filled_quantity": 5.0,
                    "last_filled_price": 1.01,
                    "average_price": 1.01,
                    "realized_profit": 0.05,
                    "transaction_time_ms": 250,
                    "event_time_ms": 250,
                }
            ]
        }
    )

    recovered = supervisor._recover_stop_close_from_user_data(position)

    assert recovered["realized_pnl_status"] == "recovered_from_user_data_order_trade_update"
    assert recovered["realized_pnl_reason"] == "matched_stop_client_order_id_or_order_id_or_child_reduce_only_fill"
    assert recovered["realized_pnl_usdt"] == pytest.approx(0.05)
    assert recovered["exit_fill_price"] == pytest.approx(1.01)
    assert recovered["exit_filled_amount"] == pytest.approx(5.0)


def test_tp1_full_close_amount_uses_protected_remaining_when_exchange_amount_is_dust_rounded() -> None:
    position = Live2ProtectedPosition(
        position_id="LITE_USDT_USDT_100_1",
        symbol="LITE/USDT:USDT",
        opened_at_ms=100,
        entry_order_id="1",
        entry_client_order_id="l2e_LITE_USDT_USDT_100",
        entry_fill_price=853.64,
        amount=0.01,
        stop_price=827.23,
        stop_order_id="stop-order-id",
        stop_client_order_id="l2s_LITE_USDT_USDT_100",
        pre_position_amount=0.0,
        post_position_amount=0.01,
        category_id="test",
        signal_entry_price=853.16,
        initial_risk_pct=0.03,
        tp1_price=872.60,
        initial_amount=0.01,
        remaining_amount=0.01,
    )

    close_amount = _tp1_reduce_only_close_amount(
        position=position,
        exchange_amount=0.009999999,
        close_fraction=1.0,
    )

    assert close_amount == pytest.approx(0.01)


def test_tp1_partial_close_amount_still_uses_exchange_amount_fraction() -> None:
    position = Live2ProtectedPosition(
        position_id="AAA_USDT_USDT_100_1",
        symbol="AAA/USDT:USDT",
        opened_at_ms=100,
        entry_order_id="1",
        entry_client_order_id="l2e_AAA_USDT_USDT_100",
        entry_fill_price=1.0,
        amount=10.0,
        stop_price=0.99,
        stop_order_id="stop-order-id",
        stop_client_order_id="l2s_AAA_USDT_USDT_100",
        pre_position_amount=0.0,
        post_position_amount=10.0,
        category_id="test",
        signal_entry_price=1.0,
        initial_risk_pct=0.01,
        tp1_price=1.02,
        initial_amount=10.0,
        remaining_amount=10.0,
    )

    close_amount = _tp1_reduce_only_close_amount(
        position=position,
        exchange_amount=9.8,
        close_fraction=0.5,
    )

    assert close_amount == pytest.approx(4.9)
