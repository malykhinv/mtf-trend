from __future__ import annotations

import csv
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data.exchanges.ccxt_types import ExchangeOrderFill
from domain.enums.timeframe import Timeframe
from research_tools.anomaly_micro_live import (
    AnomalyMicroLiveRunner,
    LiveAnomalyConfig,
    LiveOrderPositionIntegrityError,
    LivePosition,
    LiveSignal,
    TelegramConfig,
    _position_symbol_key,
)


class FakeTickerSource:
    source_id = "fake_ticker_source"

    def fetch_snapshots(self, symbols: tuple[str, ...]) -> list[object]:
        return []


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self._next_message_id = 1

    def _message_id(self) -> int:
        message_id = self._next_message_id
        self._next_message_id += 1
        return message_id

    def send_sync(self, *, channel: str, text: str, reply_to_message_id: int | None = None) -> int:
        self.sent.append({"method": "send_sync", "channel": channel, "text": text, "reply_to_message_id": reply_to_message_id})
        return self._message_id()

    def send_photo_sync(
        self,
        *,
        channel: str,
        photo_path: Path,
        caption: str,
        reply_to_message_id: int | None = None,
    ) -> int:
        self.sent.append(
            {
                "method": "send_photo_sync",
                "channel": channel,
                "photo_path": str(photo_path),
                "caption": caption,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return self._message_id()

    def send(self, *, channel: str, key: str, text: str, symbol: str = "__telegram__", reply_to_message_id: int | None = None) -> None:
        self.sent.append(
            {
                "method": "send",
                "channel": channel,
                "key": key,
                "text": text,
                "symbol": symbol,
                "reply_to_message_id": reply_to_message_id,
            }
        )

    def send_critical_sync(
        self,
        *,
        channel: str,
        key: str,
        text: str,
        symbol: str = "__telegram__",
        reply_to_message_id: int | None = None,
    ) -> int:
        self.sent.append(
            {
                "method": "send_critical_sync",
                "channel": channel,
                "key": key,
                "text": text,
                "symbol": symbol,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return self._message_id()

    def edit_sync(self, *, channel: str, message_id: int, text: str) -> int:
        self.sent.append({"method": "edit_sync", "channel": channel, "message_id": message_id, "text": text})
        return message_id


class NoStartThread:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.started = False

    def start(self) -> None:
        self.started = True


class FakeExchange:
    def __init__(self) -> None:
        self.symbol = "TEST/USDT:USDT"
        self.last_price = 100.0
        self.entry_fill_price = 100.0
        self.free_balance = 1_000.0
        self.position_amount = 0.0
        self.stop_visible = True
        self.limit_visible = True
        self.fail_limit_order = False
        self.stop_orders: list[dict[str, object]] = []
        self.open_orders: list[dict[str, object]] = []
        self.cancelled_orders: list[str] = []
        self.created_market_orders: list[dict[str, object]] = []
        self.created_limit_orders: list[dict[str, object]] = []
        self.created_stop_orders: list[dict[str, object]] = []
        self.market_order_sequence = 1
        self.limit_order_sequence = 1
        self.stop_order_sequence = 1
        self.position_amount_reads: deque[float] = deque()
        self.ohlcv_frames: deque[pd.DataFrame] = deque()
        self.stop_fill_price: float | None = None
        self.retry_logger = None

    def set_retry_logger(self, logger: object) -> None:
        self.retry_logger = logger

    def fetch_last_price(self, symbol: str) -> float:
        self._assert_symbol(symbol)
        return self.last_price

    def fetch_usdt_free_balance(self) -> float:
        return self.free_balance

    def get_market_id(self, symbol: str) -> str:
        self._assert_symbol(symbol)
        return "TESTUSDT"

    def market_id(self, symbol: str) -> str:
        return self.get_market_id(symbol)

    def fetch_symbol_position_amount(self, symbol: str) -> float:
        self._assert_symbol(symbol)
        if self.position_amount_reads:
            value = float(self.position_amount_reads.popleft())
            self.position_amount = value
            return value
        return float(self.position_amount)

    def create_market_order_with_fill(
        self,
        symbol: str,
        side: str,
        amount: float,
        *,
        reduce_only: bool,
        client_order_id: str | None = None,
    ) -> ExchangeOrderFill:
        self._assert_symbol(symbol)
        order_id = f"market-{self.market_order_sequence}"
        self.market_order_sequence += 1
        average_price = self.entry_fill_price if side.lower() == "buy" else self.last_price
        filled_amount = float(amount)
        if side.lower() == "buy" and not reduce_only:
            self.position_amount += filled_amount
        elif side.lower() == "sell" and reduce_only:
            filled_amount = min(filled_amount, max(self.position_amount, 0.0))
            self.position_amount = max(self.position_amount - filled_amount, 0.0)
        else:
            raise AssertionError(f"unexpected market order side/reduce_only: {side} {reduce_only}")
        record = {
            "order_id": order_id,
            "side": side,
            "amount": amount,
            "filled_amount": filled_amount,
            "average_price": average_price,
            "reduce_only": reduce_only,
            "client_order_id": client_order_id or "",
        }
        self.created_market_orders.append(record)
        return ExchangeOrderFill(
            order_id=order_id,
            status="closed",
            timestamp_ms=1_800_000_000_000 + self.market_order_sequence,
            average_price=average_price,
            filled_amount=filled_amount,
            cost=average_price * filled_amount,
            fee_cost=0.0,
            fee_currency="USDT",
        )

    def create_stop_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        *,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        self._assert_symbol(symbol)
        order_id = f"stop-{self.stop_order_sequence}"
        self.stop_order_sequence += 1
        order = {
            "id": order_id,
            "clientOrderId": client_order_id or "",
            "clientOrderID": client_order_id or "",
            "clientAlgoId": client_order_id or "",
            "side": side,
            "type": "STOP_MARKET",
            "status": "open",
            "reduceOnly": True,
            "amount": float(amount),
            "origQty": str(float(amount)),
            "stopPrice": str(float(stop_price)),
            "info": {
                "algoId": order_id,
                "orderId": order_id,
                "clientAlgoId": client_order_id or "",
                "clientOrderId": client_order_id or "",
                "side": side.upper(),
                "type": "STOP_MARKET",
                "status": "NEW",
                "reduceOnly": "true",
                "origQty": str(float(amount)),
                "stopPrice": str(float(stop_price)),
            },
        }
        self.created_stop_orders.append(order)
        if self.stop_visible:
            self.stop_orders.append(order)
        return dict(order)

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        *,
        reduce_only: bool,
        client_order_id: str | None = None,
    ) -> dict[str, object]:
        self._assert_symbol(symbol)
        if self.fail_limit_order:
            raise RuntimeError("injected limit order failure")
        order_id = f"limit-{self.limit_order_sequence}"
        self.limit_order_sequence += 1
        order = {
            "id": order_id,
            "clientOrderId": client_order_id or "",
            "side": side,
            "type": "LIMIT",
            "status": "open",
            "reduceOnly": reduce_only,
            "amount": float(amount),
            "origQty": str(float(amount)),
            "price": str(float(price)),
        }
        self.created_limit_orders.append(order)
        if self.limit_visible:
            self.open_orders.append(order)
        return dict(order)

    def fetch_open_orders(self, symbol: str) -> list[dict[str, object]]:
        self._assert_symbol(symbol)
        return [dict(order) for order in self.open_orders]

    def fetch_open_stop_orders(self, symbol: str) -> list[dict[str, object]]:
        self._assert_symbol(symbol)
        return [dict(order) for order in self.stop_orders]

    def fetch_stop_order_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, object]:
        self._assert_symbol(symbol)
        for order in self.stop_orders:
            if order.get("clientOrderId") == client_order_id or order.get("clientAlgoId") == client_order_id:
                return dict(order)
        raise RuntimeError("Order does not exist")

    def fetch_order_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, object]:
        self._assert_symbol(symbol)
        for order in self.open_orders:
            if order.get("clientOrderId") == client_order_id:
                return dict(order)
        raise RuntimeError("Order does not exist")

    def cancel_stop_order(self, symbol: str, order_id: str) -> dict[str, object]:
        self._assert_symbol(symbol)
        self.cancelled_orders.append(order_id)
        self.stop_orders = [order for order in self.stop_orders if str(order.get("id")) != str(order_id)]
        return {"id": order_id, "status": "canceled"}

    def cancel_order(self, symbol: str, order_id: str) -> dict[str, object]:
        self._assert_symbol(symbol)
        self.cancelled_orders.append(order_id)
        self.open_orders = [order for order in self.open_orders if str(order.get("id")) != str(order_id)]
        return {"id": order_id, "status": "canceled"}

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, start_timestamp_ms: int, end_timestamp_ms: int) -> pd.DataFrame:
        self._assert_symbol(symbol)
        if self.ohlcv_frames:
            return self.ohlcv_frames.popleft().copy()
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def fetch_order_fill(self, symbol: str, order_id: str) -> ExchangeOrderFill:
        self._assert_symbol(symbol)
        price = self.stop_fill_price
        for order in self.created_limit_orders:
            if str(order.get("id")) == str(order_id):
                return ExchangeOrderFill(
                    order_id=order_id,
                    status="closed",
                    timestamp_ms=1_800_000_001_000,
                    average_price=float(order["price"]),
                    filled_amount=float(order["amount"]),
                    cost=float(order["price"]) * float(order["amount"]),
                    fee_cost=0.0,
                    fee_currency="USDT",
                )
        if price is None:
            for order in self.created_stop_orders:
                if str(order.get("id")) == str(order_id):
                    price = float(order["stopPrice"])
                    break
        if price is None:
            raise RuntimeError(f"unknown stop fill order id: {order_id}")
        return ExchangeOrderFill(
            order_id=order_id,
            status="closed",
            timestamp_ms=1_800_000_001_000,
            average_price=float(price),
            filled_amount=max(float(self.position_amount), 0.0),
            cost=float(price) * max(float(self.position_amount), 0.0),
            fee_cost=0.0,
            fee_currency="USDT",
        )

    def _assert_symbol(self, symbol: str) -> None:
        if symbol != self.symbol:
            raise AssertionError(f"unexpected symbol: {symbol}")


def make_signal(*, now_ms: int) -> LiveSignal:
    return LiveSignal(
        category_id="runner_balanced",
        category_label="runner balanced",
        category_priority=4,
        symbol="TEST/USDT:USDT",
        levels_timeframe=Timeframe.M5,
        entry_timeframe=Timeframe.S30,
        setup_source="unit_test",
        setup_elapsed_fraction=1.0,
        setup_closed_entry_candles=4,
        decision_timestamp_ms=now_ms - 40_000,
        start_timestamp_ms=now_ms - 10 * 60_000,
        session="unit",
        entry_price=100.0,
        stop_price=95.0,
        tp1_price=104.0,
        tp1_basis_price=95.0,
        tp1_basis_risk=5.0,
        tp1_r=0.75,
        tp1_fraction=1.0,
        box_high=104.0,
        box_low=95.0,
        initial_risk=5.0,
        initial_risk_pct=0.05,
        quote_ratio_start=5.0,
        trade_ratio_start=5.0,
        price_retention=0.8,
        hold_count=3,
        verticality_score=0.5,
        oi_change_pct_3x5m=None,
        previous_live_scan_closed_timestamp_ms=now_ms - 70_000,
        first_unscanned_decision_timestamp_ms=now_ms - 65_000,
        live_scan_gap_ltf_candles=1,
    )


def make_config(results_dir: Path, *, danger_local_guard: bool = False) -> LiveAnomalyConfig:
    return LiveAnomalyConfig(
        results_dir=results_dir,
        symbols=("TEST/USDT:USDT",),
        confirm_real_orders=True,
        timeframe_pairs=((Timeframe.M5, Timeframe.S30),),
        position_notional_usdt=100.0,
        max_open_positions=1,
        max_signal_age_ms=60_000,
        max_entry_price_drift_pct=0.01,
        min_executable_rr_to_signal_tp1=0.5,
        danger_local_entry_position_guard_enabled=danger_local_guard,
        ticker_radar_enabled=False,
        live_ws_ticker_enabled=False,
        live_ws_aggtrade_enabled=False,
        warm_watch_enabled=False,
        symbol_context_snapshot_enabled=False,
        live_ohlcv_cache_enabled=False,
        live_ohlcv_cache_write_enabled=False,
        delayed_replay_enabled=False,
        scan_sleep_seconds=0.0,
        network_sleep_seconds=0.0,
        telegram_cooldown_seconds=0.0,
    )


def make_runner(results_dir: Path, exchange: FakeExchange, *, danger_local_guard: bool = False) -> AnomalyMicroLiveRunner:
    runner = AnomalyMicroLiveRunner(
        config=make_config(results_dir, danger_local_guard=danger_local_guard),
        telegram=TelegramConfig("", "", "", ""),
        exchange_client=exchange,  # type: ignore[arg-type]
        ticker_snapshot_source=FakeTickerSource(),
        logger=lambda _message: None,
    )
    runner.telegram = FakeTelegram()  # type: ignore[assignment]
    runner._render_open_chart = lambda _position: None  # type: ignore[method-assign]
    runner._render_close_chart = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    runner._fetch_chart_frame = (  # type: ignore[method-assign]
        lambda symbol, timeframe, start_timestamp_ms, end_timestamp_ms: exchange.fetch_ohlcv(
            symbol,
            timeframe,
            start_timestamp_ms,
            end_timestamp_ms,
        )
    )
    return runner


def read_events(root: Path) -> list[dict[str, object]]:
    events_path = next((root / "live_anomaly_runs").glob("*/live_events.csv"))
    with events_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {
            "event": row["event"],
            "symbol": row["symbol"],
            "details": json.loads(row["details_json"]),
        }
        for row in rows
    ]


def read_ledger_rows(root: Path) -> list[dict[str, str]]:
    ledger_path = next((root / "live_anomaly_runs").glob("*/live_positions.csv"))
    with ledger_path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def one_row_ohlcv(*, high: float, low: float, close: float, timestamp: int = 1_800_000_001_000) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": timestamp,
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1.0,
            }
        ]
    )


class LiveOrderLifecycleTests(unittest.TestCase):
    def test_maybe_open_position_uses_live_order_path_and_verifies_initial_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exchange = FakeExchange()
            runner = make_runner(root, exchange)
            signal = make_signal(now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000))

            with patch("research_tools.anomaly_micro_live.threading.Thread", NoStartThread):
                runner._maybe_open_position(signal)

            symbol_key = _position_symbol_key(signal.symbol)
            self.assertIn(symbol_key, runner._open_positions)
            position = runner._open_positions[symbol_key]
            self.assertEqual(position.entry_order_id, "market-1")
            self.assertEqual(position.stop_order_id, "stop-1")
            self.assertEqual(position.tp1_order_id, "limit-1")
            self.assertAlmostEqual(position.entry_price, 100.0)
            self.assertAlmostEqual(position.stop_price, 95.0)
            self.assertAlmostEqual(position.tp1_price, 104.0)
            self.assertAlmostEqual(position.position_delta_amount, 1.0)
            self.assertEqual(exchange.created_market_orders[0]["side"], "buy")
            self.assertFalse(exchange.created_market_orders[0]["reduce_only"])
            self.assertEqual(exchange.created_stop_orders[0]["side"], "sell")
            self.assertEqual(exchange.created_stop_orders[0]["type"], "STOP_MARKET")
            self.assertTrue(exchange.created_stop_orders[0]["reduceOnly"])
            self.assertEqual(exchange.created_limit_orders[0]["side"], "sell")
            self.assertEqual(exchange.created_limit_orders[0]["type"], "LIMIT")
            self.assertTrue(exchange.created_limit_orders[0]["reduceOnly"])

            events = read_events(root)
            event_names = [row["event"] for row in events]
            self.assertIn("position_stop_order_verified", event_names)
            self.assertIn("position_tp1_limit_order_verified", event_names)
            self.assertIn("position_opened", event_names)
            ledger_rows = read_ledger_rows(root)
            self.assertEqual(len(ledger_rows), 1)
            self.assertEqual(ledger_rows[0]["status"], "open")
            self.assertEqual(ledger_rows[0]["entry_position_guard_source"], "pre_entry_exchange_position_fetch")

    def test_stop_verification_failure_after_entry_closes_unprotected_exchange_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exchange = FakeExchange()
            exchange.stop_visible = False
            runner = make_runner(root, exchange)
            signal = make_signal(now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000))

            with patch("research_tools.anomaly_micro_live.time.sleep", lambda _seconds: None):
                with self.assertRaises(LiveOrderPositionIntegrityError):
                    runner._maybe_open_position(signal)

            self.assertAlmostEqual(exchange.position_amount, 0.0)
            self.assertEqual(len(exchange.created_market_orders), 2)
            self.assertEqual(exchange.created_market_orders[0]["side"], "buy")
            self.assertEqual(exchange.created_market_orders[1]["side"], "sell")
            self.assertTrue(exchange.created_market_orders[1]["reduce_only"])
            self.assertEqual(runner._open_positions, {})

            events = read_events(root)
            event_names = [row["event"] for row in events]
            self.assertIn("position_stop_order_visibility_retry", event_names)
            self.assertIn("unprotected_entry_reduce_only_exit_filled", event_names)
            self.assertNotIn("position_opened", event_names)

    def test_tp1_limit_failure_after_initial_stop_closes_exposure_and_cancels_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exchange = FakeExchange()
            exchange.fail_limit_order = True
            runner = make_runner(root, exchange)
            signal = make_signal(now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000))

            with self.assertRaises(LiveOrderPositionIntegrityError):
                runner._maybe_open_position(signal)

            self.assertAlmostEqual(exchange.position_amount, 0.0)
            self.assertEqual(len(exchange.created_market_orders), 2)
            self.assertEqual(exchange.created_market_orders[1]["side"], "sell")
            self.assertTrue(exchange.created_market_orders[1]["reduce_only"])
            self.assertIn("stop-1", exchange.cancelled_orders)
            self.assertEqual(exchange.stop_orders, [])
            self.assertEqual(runner._open_positions, {})

            events = read_events(root)
            event_names = [row["event"] for row in events]
            self.assertIn("position_stop_order_verified", event_names)
            self.assertIn("unprotected_entry_reduce_only_exit_filled", event_names)
            self.assertIn("position_initial_stop_cancelled_after_tp1_failure", event_names)
            self.assertNotIn("position_opened", event_names)

    def test_monitor_records_verified_stop_fill_and_closes_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exchange = FakeExchange()
            runner = make_runner(root, exchange)
            signal = make_signal(now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000))
            position = make_live_position(signal=signal, amount=1.0)
            runner._open_positions[_position_symbol_key(signal.symbol)] = position
            exchange.open_orders.append(make_limit_order(position))
            exchange.position_amount_reads = deque([1.0, 0.0])
            exchange.ohlcv_frames.append(one_row_ohlcv(high=101.0, low=94.5, close=96.0))
            exchange.stop_fill_price = 94.8

            with patch("research_tools.anomaly_micro_live.time.sleep", lambda _seconds: None):
                with patch("research_tools.anomaly_micro_live.time.time", lambda: 1_800_000_100.0):
                    runner._monitor_position(position)

            self.assertEqual(runner._open_positions, {})
            self.assertEqual(runner._closed_positions_total, 1)
            events = read_events(root)
            event_names = [row["event"] for row in events]
            self.assertIn("stop_exit_filled", event_names)
            self.assertIn("position_closed", event_names)
            close_events = [row for row in events if row["event"] == "position_closed"]
            self.assertTrue(close_events)
            self.assertTrue(str(close_events[-1]["details"]["reason"]).startswith("стоп"))

    def test_monitor_tp1_full_exit_closes_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exchange = FakeExchange()
            runner = make_runner(root, exchange)
            signal = make_signal(now_ms=int(pd.Timestamp.utcnow().timestamp() * 1000))
            position = make_live_position(signal=signal, amount=1.0)
            runner._open_positions[_position_symbol_key(signal.symbol)] = position
            exchange.position_amount = 1.0
            tp1_order = make_limit_order(position)
            exchange.created_limit_orders.append(tp1_order)
            exchange.stop_orders.append(
                {
                    "id": position.stop_order_id,
                    "clientOrderId": "existing-stop-client-id",
                    "side": "sell",
                    "type": "STOP_MARKET",
                    "status": "open",
                    "reduceOnly": True,
                    "amount": 1.0,
                    "stopPrice": str(position.stop_price),
                }
            )
            exchange.position_amount_reads = deque([1.0, 0.0])
            exchange.ohlcv_frames.append(one_row_ohlcv(high=110.2, low=100.2, close=106.0, timestamp=1_800_000_001_000))
            exchange.ohlcv_frames.append(one_row_ohlcv(high=101.0, low=99.5, close=100.1, timestamp=1_800_000_031_000))
            exchange.stop_fill_price = 100.0

            with patch("research_tools.anomaly_micro_live.time.sleep", lambda _seconds: None):
                with patch("research_tools.anomaly_micro_live.time.time", lambda: 1_800_000_100.0):
                    runner._monitor_position(position)

            self.assertEqual(runner._open_positions, {})
            self.assertTrue(position.tp1_done)
            self.assertEqual(runner._closed_positions_total, 1)

            events = read_events(root)
            event_names = [row["event"] for row in events]
            self.assertIn("tp1_limit_exit_filled", event_names)
            self.assertNotIn("position_stop_order_replaced", event_names)
            self.assertNotIn("tp1_and_stop_to_be", event_names)
            self.assertNotIn("stop_exit_filled", event_names)
            self.assertIn("position_closed", event_names)


def make_live_position(*, signal: LiveSignal, amount: float) -> LivePosition:
    return LivePosition(
        position_id="TEST_USDT_USDT_position_1",
        signal=signal,
        amount=amount,
        notional_usdt=100.0,
        risk_usdt=5.0,
        entry_order_id="entry-live-1",
        stop_order_id="stop-live-1",
        opened_at_utc="2026-05-16T00:00:00+00:00",
        opened_at_ms=1_800_000_000_000,
        entry_price=100.0,
        stop_price=95.0,
        tp1_price=104.0,
        tp1_order_id="limit-live-1",
        tp1_client_order_id="limit-live-client-1",
        tp1_order_amount=amount,
        initial_risk=5.0,
        first_executable_entry_timestamp_ms=signal.decision_timestamp_ms,
        entry_lag_ms=5_000,
        entry_lag_ltf_candles=1,
        entered_late_vs_first_executable=False,
        entry_order_submit_lag_ms=5_000,
        entry_order_submit_lag_ltf_candles=1,
        previous_live_scan_closed_timestamp_ms=signal.previous_live_scan_closed_timestamp_ms,
        first_unscanned_decision_timestamp_ms=signal.first_unscanned_decision_timestamp_ms,
        live_scan_gap_ltf_candles=signal.live_scan_gap_ltf_candles,
        entry_fill_timestamp_ms=1_800_000_000_000,
        entry_order_submitted_at_ms=1_800_000_000_000,
        entry_order_status="closed",
        source_scan_mode="precise_direct",
        danger_cold_coverage_source=False,
        entry_position_guard_source="pre_entry_exchange_position_fetch",
        entry_filled_amount=amount,
        entry_cost_usdt=100.0,
        entry_fee_usdt=0.0,
        pre_position_amount=0.0,
        post_position_amount=amount,
        position_delta_amount=amount,
        remaining_amount=amount,
        current_stop_price=95.0,
    )


def make_limit_order(position: LivePosition) -> dict[str, object]:
    return {
        "id": position.tp1_order_id,
        "clientOrderId": position.tp1_client_order_id,
        "side": "sell",
        "type": "LIMIT",
        "status": "open",
        "reduceOnly": True,
        "amount": float(position.tp1_order_amount),
        "origQty": str(float(position.tp1_order_amount)),
        "price": str(float(position.tp1_price)),
    }


if __name__ == "__main__":
    unittest.main()
