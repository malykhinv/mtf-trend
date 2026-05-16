"""Real Binance live-order smoke test for the protected position lifecycle.

This module deliberately exercises the same exchange boundary used by live trading:
market entry fill -> reduce-only STOP_MARKET create -> stop visibility lookup ->
optional protected hold -> cancel stop -> reduce-only market cleanup.

It is not a strategy backtest and it does not infer fills from candles.
"""

from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from data.exchanges.ccxt_futures_client import CcxtFuturesClient
from data.exchanges.ccxt_types import ExchangeOrderFill
from domain.exceptions import ExchangeOrderNotFound


@dataclass(frozen=True, slots=True)
class LiveOrderSmokeConfig:
    symbol: str
    notional_usdt: float
    max_notional_usdt: float
    stop_distance_pct: float
    output_dir: Path
    confirm_real_order_smoke: bool
    leave_protected_position_open: bool = False
    verification_attempts: int = 5
    verification_sleep_seconds: float = 0.5
    max_position_amount_slippage_ratio: float = 0.05


@dataclass(frozen=True, slots=True)
class LiveOrderSmokeResult:
    status: str
    symbol: str
    entry_order_id: str | None
    stop_order_id: str | None
    entry_fill_price: float | None
    entry_filled_amount: float | None
    stop_price: float | None
    cleanup_close_order_id: str | None
    output_dir: str


class LiveOrderSmokeRunner:
    """Runs one minimal real-order protected-position smoke on Binance USD-M."""

    def __init__(self, *, config: LiveOrderSmokeConfig, exchange_client: CcxtFuturesClient) -> None:
        self.config = config
        self.exchange = exchange_client
        self.output_dir = config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.output_dir / "live_order_smoke_events.csv"
        self.summary_path = self.output_dir / "live_order_smoke_summary.json"
        self._client_id_prefix = self._build_client_id_prefix(config.symbol)
        self._entry_order_id: str | None = None
        self._entry_fill: ExchangeOrderFill | None = None
        self._stop_order_id: str | None = None
        self._stop_price: float | None = None
        self._cleanup_close_order_id: str | None = None
        self._write_header_if_needed()

    def run(self) -> int:
        if not self.config.confirm_real_order_smoke:
            self._event("preflight_failed", status="aborted", reason="missing --confirm-real-order-smoke")
            self._write_summary(status="aborted_missing_confirmation")
            print("smoke: отказ · нужен --confirm-real-order-smoke", flush=True)
            return 2
        if not math.isfinite(self.config.notional_usdt) or self.config.notional_usdt <= 0.0:
            self._event("preflight_failed", status="aborted", reason="invalid notional_usdt", notional_usdt=self.config.notional_usdt)
            self._write_summary(status="aborted_invalid_notional")
            return 2
        if self.config.notional_usdt > self.config.max_notional_usdt:
            self._event(
                "preflight_failed",
                status="aborted",
                reason="notional exceeds configured max",
                notional_usdt=self.config.notional_usdt,
                max_notional_usdt=self.config.max_notional_usdt,
            )
            self._write_summary(status="aborted_notional_over_max")
            print(
                f"smoke: отказ · notional {self.config.notional_usdt:.4f} > max {self.config.max_notional_usdt:.4f}",
                flush=True,
            )
            return 2
        if not math.isfinite(self.config.stop_distance_pct) or self.config.stop_distance_pct <= 0.0:
            self._event("preflight_failed", status="aborted", reason="invalid stop_distance_pct", stop_distance_pct=self.config.stop_distance_pct)
            self._write_summary(status="aborted_invalid_stop_distance")
            return 2

        status = "failed"
        code = 1
        try:
            self._preflight_zero_position_and_no_orders()
            self._open_market_entry()
            self._create_and_verify_stop()
            if self.config.leave_protected_position_open:
                status = "protected_position_left_open"
                code = 0
                self._event("protected_position_left_open", status="ok", stop_order_id=self._stop_order_id)
                print(
                    f"smoke: stop verified · position left open · symbol={self.config.symbol} stop_order_id={self._stop_order_id}",
                    flush=True,
                )
                return code
            self._cleanup_after_success()
            status = "ok"
            code = 0
            print("smoke: ok · entry, stop verification, cancel, reduce-only cleanup completed", flush=True)
            return code
        except Exception as exc:
            self._event("smoke_failed", status="error", error_type=type(exc).__name__, error=str(exc))
            print(f"smoke: ошибка · {type(exc).__name__}: {exc}", flush=True)
            try:
                self._emergency_cleanup()
            except Exception as cleanup_exc:  # noqa: BLE001 - cleanup failure must be visible and must not hide original error.
                self._event(
                    "emergency_cleanup_failed",
                    status="error",
                    error_type=type(cleanup_exc).__name__,
                    error=str(cleanup_exc),
                )
                print(
                    f"smoke: emergency cleanup failed · {type(cleanup_exc).__name__}: {cleanup_exc}",
                    flush=True,
                )
                status = "failed_cleanup_failed"
                code = 3
            else:
                status = "failed_cleanup_attempted"
                code = 3
            return code
        finally:
            self._write_summary(status=status)

    def _preflight_zero_position_and_no_orders(self) -> None:
        position_amount = self.exchange.fetch_symbol_position_amount(self.config.symbol)
        open_orders = self.exchange.fetch_open_orders(self.config.symbol)
        stop_orders = self.exchange.fetch_open_stop_orders(self.config.symbol)
        self._event(
            "preflight_snapshot",
            status="ok",
            exchange_position_amount=position_amount,
            open_orders_seen=len(open_orders),
            algo_open_orders_seen=len(stop_orders),
        )
        if abs(position_amount) > 0.0:
            raise RuntimeError(f"preflight position is not flat: symbol={self.config.symbol} amount={position_amount}")
        if open_orders or stop_orders:
            order_ids = [_resolve_order_id(row) for row in [*open_orders, *stop_orders] if isinstance(row, dict)]
            raise RuntimeError(
                f"preflight open orders exist: symbol={self.config.symbol} "
                f"ordinary_open_orders={len(open_orders)} algo_open_orders={len(stop_orders)} order_ids={order_ids}"
            )

    def _open_market_entry(self) -> None:
        last_price = self.exchange.fetch_last_price(self.config.symbol)
        amount = self.config.notional_usdt / last_price
        entry_client_id = f"{self._client_id_prefix}_e"
        self._event(
            "entry_submit",
            status="started",
            side="buy",
            notional_usdt=self.config.notional_usdt,
            last_price=last_price,
            amount=amount,
            client_order_id=entry_client_id,
        )
        fill = self.exchange.create_market_order_with_fill(
            self.config.symbol,
            "buy",
            amount,
            reduce_only=False,
            client_order_id=entry_client_id,
        )
        self._entry_fill = fill
        self._entry_order_id = fill.order_id
        self._event(
            "entry_fill_verified",
            status="ok",
            order_id=fill.order_id,
            fill_status=fill.status,
            average_price=fill.average_price,
            filled_amount=fill.filled_amount,
            cost=fill.cost,
            fee_cost=fill.fee_cost,
            fee_currency=fill.fee_currency,
        )
        position_amount = self.exchange.fetch_symbol_position_amount(self.config.symbol)
        self._event("post_entry_position_snapshot", status="ok", exchange_position_amount=position_amount)
        if position_amount <= 0.0:
            raise RuntimeError(f"entry fill verified but long exchange position not visible: amount={position_amount}")

    def _create_and_verify_stop(self) -> None:
        if self._entry_fill is None:
            raise RuntimeError("entry fill missing before stop creation")
        position_amount = self.exchange.fetch_symbol_position_amount(self.config.symbol)
        if position_amount <= 0.0:
            raise RuntimeError(f"cannot create stop without positive exchange position: amount={position_amount}")
        stop_price = self._entry_fill.average_price * (1.0 - self.config.stop_distance_pct)
        stop_client_id = f"{self._client_id_prefix}_s"
        self._stop_price = stop_price
        self._event(
            "stop_submit",
            status="started",
            side="sell",
            amount=position_amount,
            stop_price=stop_price,
            client_order_id=stop_client_id,
        )
        stop_order = self.exchange.create_stop_market_order(
            self.config.symbol,
            "sell",
            position_amount,
            stop_price,
            client_order_id=stop_client_id,
        )
        stop_order_id = _resolve_order_id(stop_order)
        if not stop_order_id:
            raise RuntimeError(f"stop create returned no order id: payload_keys={sorted(stop_order)}")
        self._stop_order_id = stop_order_id
        self._event(
            "stop_created",
            status="ok",
            order_id=stop_order_id,
            client_order_id=stop_client_id,
            raw_status=_order_text_field(stop_order, "status") or "",
            raw_type=_order_text_field(stop_order, "type") or "",
        )
        verified = self._verify_stop_order(
            order_id=stop_order_id,
            client_order_id=stop_client_id,
            expected_amount=position_amount,
            expected_stop_price=stop_price,
        )
        self._event(
            "stop_verified",
            status="ok",
            order_id=stop_order_id,
            source=verified["source"],
            verification_attempt=verified["attempt"],
            open_orders_seen=verified["open_orders_seen"],
            algo_open_orders_seen=verified["algo_open_orders_seen"],
        )

    def _verify_stop_order(
        self,
        *,
        order_id: str,
        client_order_id: str,
        expected_amount: float,
        expected_stop_price: float,
    ) -> dict[str, object]:
        last_lookup_error = ""
        ordinary_open_orders: list[dict[str, object]] = []
        conditional_stop_orders: list[dict[str, object]] = []
        for attempt in range(1, self.config.verification_attempts + 1):
            conditional_stop_orders = self.exchange.fetch_open_stop_orders(self.config.symbol)
            order = next((row for row in conditional_stop_orders if _order_matches_order_id(row, order_id)), None)
            source = "open_algo_orders_order_id" if order is not None else ""
            if order is None:
                order = next((row for row in conditional_stop_orders if _order_matches_client_order_id(row, client_order_id)), None)
                source = "open_algo_orders_client_order_id" if order is not None else ""
            if order is None:
                try:
                    fetched_stop = self.exchange.fetch_stop_order_by_client_order_id(self.config.symbol, client_order_id)
                except ExchangeOrderNotFound as exc:
                    last_lookup_error = f"ExchangeOrderNotFound: {exc}"
                except Exception as exc:  # noqa: BLE001 - exact class is recorded for exchange-boundary diagnosis.
                    last_lookup_error = f"{type(exc).__name__}: {exc}"
                else:
                    if _order_matches_order_id(fetched_stop, order_id) or _order_matches_client_order_id(fetched_stop, client_order_id):
                        order = fetched_stop
                        source = "algo_client_order_id_lookup"
            if order is None:
                ordinary_open_orders = self.exchange.fetch_open_orders(self.config.symbol)
                order = next((row for row in ordinary_open_orders if _order_matches_order_id(row, order_id)), None)
                source = "legacy_open_orders_order_id" if order is not None else ""
                if order is None:
                    order = next((row for row in ordinary_open_orders if _order_matches_client_order_id(row, client_order_id)), None)
                    source = "legacy_open_orders_client_order_id" if order is not None else ""
                if order is None:
                    try:
                        fetched = self.exchange.fetch_order_by_client_order_id(self.config.symbol, client_order_id)
                    except ExchangeOrderNotFound as exc:
                        if not last_lookup_error:
                            last_lookup_error = f"ExchangeOrderNotFound: {exc}"
                    except Exception as exc:  # noqa: BLE001 - exact class is recorded for exchange-boundary diagnosis.
                        if not last_lookup_error:
                            last_lookup_error = f"{type(exc).__name__}: {exc}"
                    else:
                        if _order_matches_order_id(fetched, order_id) or _order_matches_client_order_id(fetched, client_order_id):
                            order = fetched
                            source = "legacy_client_order_id_lookup"
            if order is not None:
                self._assert_verified_stop_order(
                    order,
                    order_id=order_id,
                    source=source,
                    expected_amount=expected_amount,
                    expected_stop_price=expected_stop_price,
                )
                return {
                    "source": source,
                    "attempt": attempt,
                    "open_orders_seen": len(ordinary_open_orders),
                    "algo_open_orders_seen": len(conditional_stop_orders),
                }
            self._event(
                "stop_visibility_retry",
                status="retry",
                order_id=order_id,
                client_order_id=client_order_id,
                verification_attempt=attempt,
                ordinary_open_orders_seen=len(ordinary_open_orders),
                algo_open_orders_seen=len(conditional_stop_orders),
                client_lookup_error=last_lookup_error,
            )
            if attempt < self.config.verification_attempts:
                time.sleep(self.config.verification_sleep_seconds)
        raise RuntimeError(
            f"stop order not visible after {self.config.verification_attempts} checks: "
            f"symbol={self.config.symbol} order_id={order_id} client_order_id={client_order_id} "
            f"ordinary_open_orders_seen={len(ordinary_open_orders)} "
            f"algo_open_orders_seen={len(conditional_stop_orders)} "
            f"client_lookup_error={last_lookup_error or 'none'}"
        )

    def _assert_verified_stop_order(
        self,
        order: dict[str, object],
        *,
        order_id: str,
        source: str,
        expected_amount: float,
        expected_stop_price: float,
    ) -> None:
        terminal = _order_terminal_status(order)
        if terminal is not None:
            raise RuntimeError(f"stop order terminal immediately: order_id={order_id} status={terminal} source={source}")
        side = _order_text_field(order, "side")
        if side is None or side.lower() != "sell":
            raise RuntimeError(f"stop side mismatch: order_id={order_id} side={side!r} source={source}")
        order_type = _order_text_field(order, "type")
        if order_type is None or "stop" not in order_type.lower():
            raise RuntimeError(f"stop type mismatch: order_id={order_id} type={order_type!r} source={source}")
        reduce_only = _order_bool_field(order, "reduceOnly")
        if reduce_only is not True:
            raise RuntimeError(f"stop reduceOnly mismatch: order_id={order_id} reduceOnly={reduce_only!r} source={source}")
        amount = _order_float_field(order, "amount", "origQty")
        if amount is None:
            raise RuntimeError(f"stop amount missing: order_id={order_id} source={source}")
        amount_delta_ratio = abs(amount - expected_amount) / expected_amount if expected_amount > 0.0 else float("inf")
        if not math.isfinite(amount_delta_ratio) or amount_delta_ratio > self.config.max_position_amount_slippage_ratio:
            raise RuntimeError(
                f"stop amount mismatch: order_id={order_id} amount={amount} expected={expected_amount} source={source}"
            )
        stop_price = _order_float_field(order, "stopPrice")
        if stop_price is None:
            raise RuntimeError(f"stopPrice missing: order_id={order_id} source={source}")
        price_delta_ratio = abs(stop_price - expected_stop_price) / expected_stop_price if expected_stop_price > 0.0 else float("inf")
        if not math.isfinite(price_delta_ratio) or price_delta_ratio > 1e-4:
            raise RuntimeError(
                f"stopPrice mismatch: order_id={order_id} stopPrice={stop_price} expected={expected_stop_price} source={source}"
            )

    def _cleanup_after_success(self) -> None:
        self._cancel_known_stop_order()
        self._close_current_position_reduce_only(reason="success_cleanup")
        self._assert_final_flat_and_no_smoke_orders()

    def _emergency_cleanup(self) -> None:
        self._cancel_known_stop_order()
        self._cancel_smoke_open_orders()
        self._close_current_position_reduce_only(reason="emergency_cleanup")
        self._assert_final_flat_and_no_smoke_orders()

    def _cancel_known_stop_order(self) -> None:
        if not self._stop_order_id:
            return
        try:
            payload = self.exchange.cancel_stop_order(self.config.symbol, self._stop_order_id)
        except Exception as exc:  # noqa: BLE001 - cancel failure is recorded and cleanup continues to position close.
            self._event("stop_cancel_failed", status="error", order_id=self._stop_order_id, error_type=type(exc).__name__, error=str(exc))
            return
        self._event(
            "stop_cancelled",
            status="ok",
            order_id=self._stop_order_id,
            raw_status=_order_text_field(payload, "status") or "",
        )

    def _cancel_smoke_open_orders(self) -> None:
        open_orders = self.exchange.fetch_open_orders(self.config.symbol)
        stop_orders = self.exchange.fetch_open_stop_orders(self.config.symbol)
        cancelled = 0
        for order in open_orders:
            if not _order_has_client_prefix(order, self._client_id_prefix):
                continue
            order_id = _resolve_order_id(order)
            if not order_id:
                continue
            try:
                self.exchange.cancel_order(self.config.symbol, order_id)
            except Exception as exc:  # noqa: BLE001
                self._event("smoke_open_order_cancel_failed", status="error", order_id=order_id, order_source="ordinary", error_type=type(exc).__name__, error=str(exc))
            else:
                cancelled += 1
                self._event("smoke_open_order_cancelled", status="ok", order_id=order_id, order_source="ordinary")
        for order in stop_orders:
            if not _order_has_client_prefix(order, self._client_id_prefix):
                continue
            order_id = _resolve_order_id(order)
            if not order_id:
                continue
            try:
                self.exchange.cancel_stop_order(self.config.symbol, order_id)
            except Exception as exc:  # noqa: BLE001
                self._event("smoke_open_order_cancel_failed", status="error", order_id=order_id, order_source="conditional_stop", error_type=type(exc).__name__, error=str(exc))
            else:
                cancelled += 1
                self._event("smoke_open_order_cancelled", status="ok", order_id=order_id, order_source="conditional_stop")
        self._event(
            "smoke_open_order_cancel_sweep",
            status="ok",
            cancelled=cancelled,
            ordinary_open_orders_seen=len(open_orders),
            algo_open_orders_seen=len(stop_orders),
        )

    def _close_current_position_reduce_only(self, *, reason: str) -> None:
        position_amount = self.exchange.fetch_symbol_position_amount(self.config.symbol)
        self._event("pre_close_position_snapshot", status="ok", reason=reason, exchange_position_amount=position_amount)
        if abs(position_amount) <= 0.0:
            return
        side = "sell" if position_amount > 0.0 else "buy"
        amount = abs(position_amount)
        close_client_id = f"{self._client_id_prefix}_c"
        fill = self.exchange.create_market_order_with_fill(
            self.config.symbol,
            side,
            amount,
            reduce_only=True,
            client_order_id=close_client_id,
        )
        self._cleanup_close_order_id = fill.order_id
        self._event(
            "cleanup_reduce_only_fill_verified",
            status="ok",
            reason=reason,
            order_id=fill.order_id,
            side=side,
            average_price=fill.average_price,
            filled_amount=fill.filled_amount,
        )

    def _assert_final_flat_and_no_smoke_orders(self) -> None:
        position_amount = self.exchange.fetch_symbol_position_amount(self.config.symbol)
        open_orders = self.exchange.fetch_open_orders(self.config.symbol)
        stop_orders = self.exchange.fetch_open_stop_orders(self.config.symbol)
        smoke_open_orders = [order for order in open_orders if _order_has_client_prefix(order, self._client_id_prefix)]
        smoke_stop_orders = [order for order in stop_orders if _order_has_client_prefix(order, self._client_id_prefix)]
        self._event(
            "final_exchange_snapshot",
            status="ok",
            exchange_position_amount=position_amount,
            open_orders_seen=len(open_orders),
            algo_open_orders_seen=len(stop_orders),
            smoke_open_orders_seen=len(smoke_open_orders),
            smoke_algo_open_orders_seen=len(smoke_stop_orders),
        )
        if abs(position_amount) > 0.0:
            raise RuntimeError(f"final position is not flat: symbol={self.config.symbol} amount={position_amount}")
        if smoke_open_orders or smoke_stop_orders:
            order_ids = [_resolve_order_id(order) for order in [*smoke_open_orders, *smoke_stop_orders]]
            raise RuntimeError(f"smoke open/conditional orders remain: symbol={self.config.symbol} order_ids={order_ids}")

    def _event(self, event: str, *, status: str, **details: object) -> None:
        payload = {
            "emitted_at_utc": datetime.now(UTC).isoformat(),
            "event": event,
            "symbol": self.config.symbol,
            "stage": event.split("_", 1)[0],
            "status": status,
            "details_json": json.dumps(details, ensure_ascii=False, sort_keys=True),
        }
        with self.events_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(payload))
            writer.writerow(payload)

    def _write_header_if_needed(self) -> None:
        if self.events_path.exists() and self.events_path.stat().st_size > 0:
            return
        with self.events_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["emitted_at_utc", "event", "symbol", "stage", "status", "details_json"],
            )
            writer.writeheader()

    def _write_summary(self, *, status: str) -> None:
        result = LiveOrderSmokeResult(
            status=status,
            symbol=self.config.symbol,
            entry_order_id=self._entry_order_id,
            stop_order_id=self._stop_order_id,
            entry_fill_price=self._entry_fill.average_price if self._entry_fill else None,
            entry_filled_amount=self._entry_fill.filled_amount if self._entry_fill else None,
            stop_price=self._stop_price,
            cleanup_close_order_id=self._cleanup_close_order_id,
            output_dir=str(self.output_dir),
        )
        self.summary_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _build_client_id_prefix(symbol: str) -> str:
        safe_symbol = "".join(ch for ch in symbol.upper() if ch.isalnum())[:10] or "SYMBOL"
        suffix = int(time.time() * 1000) % 10_000_000
        return f"smk_{safe_symbol}_{suffix}"


def _resolve_order_id(order: dict[str, object]) -> str | None:
    value = order.get("id")
    if value is None:
        info = _order_info(order)
        value = info.get("algoId") or info.get("orderId") or info.get("clientAlgoId")
    if value is None:
        return None
    order_id = str(value).strip()
    return order_id or None


def _order_info(order: dict[str, object]) -> dict[str, object]:
    info = order.get("info")
    return info if isinstance(info, dict) else {}


def _order_text_field(order: dict[str, object], key: str) -> str | None:
    value = order.get(key)
    if value is None or value == "":
        value = _order_info(order).get(key)
    if value is None or value == "":
        return None
    return str(value)


def _order_float_field(order: dict[str, object], *keys: str) -> float | None:
    info = _order_info(order)
    for key in keys:
        for source in (order, info):
            value = source.get(key)
            if value is None or value == "":
                continue
            try:
                parsed = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed):
                return parsed
    return None


def _order_bool_field(order: dict[str, object], key: str) -> bool | None:
    value = order.get(key)
    if value is None or value == "":
        value = _order_info(order).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    return None


def _order_matches_order_id(order: dict[str, object], order_id: str) -> bool:
    return (_resolve_order_id(order) or "") == str(order_id).strip()


def _order_matches_client_order_id(order: dict[str, object], client_order_id: str) -> bool:
    expected = str(client_order_id).strip()
    if not expected:
        return False
    info = _order_info(order)
    for key in ("clientOrderId", "client_order_id", "origClientOrderId", "newClientOrderId", "clientAlgoId"):
        for source in (order, info):
            value = source.get(key)
            if value is not None and str(value).strip() == expected:
                return True
    return False


def _order_has_client_prefix(order: dict[str, object], client_id_prefix: str) -> bool:
    info = _order_info(order)
    for key in ("clientOrderId", "client_order_id", "origClientOrderId", "newClientOrderId", "clientAlgoId"):
        for source in (order, info):
            value = source.get(key)
            if value is not None and str(value).strip().startswith(client_id_prefix):
                return True
    return False


def _order_terminal_status(order: dict[str, object]) -> str | None:
    status = _order_text_field(order, "status")
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized in {"closed", "canceled", "cancelled", "expired", "rejected", "triggered", "finished"}:
        return status
    return None
