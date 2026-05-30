"""Closed-hour top-growth audit for live2.

This is research/audit plumbing, not a signal source. It runs in bounded chunks
from the heartbeat path and writes closed 1h exchange-candle artifacts so missed
pumps are visible without using partial ticker snapshots as a fallback.
"""

from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from domain.enums.timeframe import Timeframe

HOUR_MS = 60 * 60 * 1000

TOP_GROWTH_COLUMNS = (
    "period_start_utc",
    "period_end_utc",
    "rank",
    "symbol",
    "growth_pct",
    "growth_fraction",
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
    "threshold_pct",
    "timeframe",
    "source",
)

TOP_GROWTH_STATUS_COLUMNS = (
    "period_start_utc",
    "period_end_utc",
    "snapshot_utc",
    "symbol",
    "status",
    "reason",
    "growth_pct",
    "growth_fraction",
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "number_of_trades",
    "taker_buy_quote_volume",
    "candle_timestamp_ms",
    "threshold_pct",
    "timeframe",
    "source",
)

TOP_GROWTH_INDEX_COLUMNS = (
    "snapshot_utc",
    "period_start_utc",
    "period_end_utc",
    "period_start_ms",
    "period_end_ms",
    "symbols_total",
    "processed_count",
    "remaining_count",
    "completion_status",
    "top_count",
    "ok_count",
    "below_threshold_count",
    "failed_count",
    "threshold_pct",
    "limit",
    "top_file",
    "status_file",
    "source",
)


@dataclass(frozen=True, slots=True)
class Live2TopGrowthAuditConfig:
    enabled: bool = True
    min_return_pct: float = 0.10
    limit: int = 5
    symbols_per_cycle: int = 1
    max_cycle_seconds: float = 0.75
    fetch_spacing_seconds: float = 0.02

    def __post_init__(self) -> None:
        if self.min_return_pct <= 0.0 or not math.isfinite(self.min_return_pct):
            raise ValueError("top_growth_min_return_pct must be finite and > 0")
        if self.limit <= 0:
            raise ValueError("top_growth_limit must be > 0")
        if self.symbols_per_cycle <= 0:
            raise ValueError("top_growth_symbols_per_cycle must be > 0")
        if self.max_cycle_seconds <= 0.0 or not math.isfinite(self.max_cycle_seconds):
            raise ValueError("top_growth_max_cycle_seconds must be finite and > 0")
        if self.fetch_spacing_seconds < 0.0 or not math.isfinite(self.fetch_spacing_seconds):
            raise ValueError("top_growth_fetch_spacing_seconds must be finite and >= 0")


@dataclass(frozen=True, slots=True)
class TopGrowthSnapshotConfig:
    output_dir: Path
    symbols: tuple[str, ...]
    period_start_ms: int | None = None
    min_return_pct: float = 0.10
    limit: int = 5
    fetch_spacing_seconds: float = 0.05


@dataclass(slots=True)
class Live2TopGrowthAuditTask:
    period_start_ms: int
    period_end_ms: int
    snapshot_utc: str
    symbols: tuple[str, ...]
    cursor: int = 0
    status_rows: list[dict[str, object]] = field(default_factory=list)
    candidates: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Live2TopGrowthAuditStats:
    enabled: bool
    status: str
    reason: str
    period_start_ms: int | None = None
    period_end_ms: int | None = None
    processed_count: int = 0
    remaining_count: int = 0
    symbols_total: int = 0
    top_count: int = 0
    cycle_seconds: float = 0.0
    top_file: str = ""
    status_file: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "reason": self.reason,
            "period_start_ms": self.period_start_ms,
            "period_end_ms": self.period_end_ms,
            "processed_count": self.processed_count,
            "remaining_count": self.remaining_count,
            "symbols_total": self.symbols_total,
            "top_count": self.top_count,
            "cycle_seconds": round(self.cycle_seconds, 3),
            "top_file": self.top_file,
            "status_file": self.status_file,
        }


class Live2TopGrowthAudit:
    def __init__(
        self,
        *,
        output_dir: Path,
        exchange_client: Any,
        config: Live2TopGrowthAuditConfig,
    ) -> None:
        self.output_dir = output_dir
        self.exchange = exchange_client
        self.config = config
        self.top_growth_dir = output_dir / "top_growth"
        self.index_path = self.top_growth_dir / "top_growth_index.csv"
        self._task: Live2TopGrowthAuditTask | None = None
        self._completed_periods: set[int] = set()
        self._last_stats = Live2TopGrowthAuditStats(
            enabled=config.enabled,
            status="not_started",
            reason="not_started",
        )
        self.top_growth_dir.mkdir(parents=True, exist_ok=True)
        _ensure_csv(self.index_path, TOP_GROWTH_INDEX_COLUMNS)

    def process_due(self, *, symbols: tuple[str, ...], now_ms: int) -> Live2TopGrowthAuditStats:
        started = time.perf_counter()
        if not self.config.enabled:
            self._last_stats = Live2TopGrowthAuditStats(enabled=False, status="disabled", reason="disabled")
            return self._last_stats
        if self.exchange is None or not hasattr(self.exchange, "fetch_ohlcv"):
            self._last_stats = Live2TopGrowthAuditStats(
                enabled=True,
                status="disabled",
                reason="exchange_client_has_no_fetch_ohlcv",
            )
            return self._last_stats
        if self._task is None:
            period_start_ms = _previous_closed_hour_start_ms(now_ms)
            if period_start_ms in self._completed_periods:
                self._last_stats = Live2TopGrowthAuditStats(
                    enabled=True,
                    status="idle",
                    reason="closed_hour_already_audited",
                    period_start_ms=period_start_ms,
                    period_end_ms=period_start_ms + HOUR_MS,
                )
                return self._last_stats
            unique_symbols = tuple(dict.fromkeys(symbols))
            if not unique_symbols:
                self._last_stats = Live2TopGrowthAuditStats(enabled=True, status="idle", reason="empty_symbol_universe")
                return self._last_stats
            self._task = Live2TopGrowthAuditTask(
                period_start_ms=period_start_ms,
                period_end_ms=period_start_ms + HOUR_MS,
                snapshot_utc=datetime.now(UTC).isoformat(),
                symbols=unique_symbols,
            )

        task = self._task
        processed_this_cycle = 0
        while task.cursor < len(task.symbols) and processed_this_cycle < self.config.symbols_per_cycle:
            if time.perf_counter() - started >= self.config.max_cycle_seconds and processed_this_cycle > 0:
                break
            symbol = task.symbols[task.cursor]
            task.cursor += 1
            status_row = _load_top_growth_symbol_row(
                exchange=self.exchange,
                symbol=symbol,
                period_start_ms=task.period_start_ms,
                period_end_ms=task.period_end_ms,
                snapshot_utc=task.snapshot_utc,
                threshold_fraction=self.config.min_return_pct,
            )
            task.status_rows.append(status_row)
            candidate = _candidate_from_status_row(status_row, threshold_fraction=self.config.min_return_pct)
            if candidate is not None:
                task.candidates.append(candidate)
            processed_this_cycle += 1
            if self.config.fetch_spacing_seconds > 0.0:
                time.sleep(self.config.fetch_spacing_seconds)

        if task.cursor < len(task.symbols):
            top_rows = _rank_candidates(task.candidates, limit=self.config.limit)
            top_path, status_path = self._write_snapshot(task=task, top_rows=top_rows, completion_status="processing")
            self._last_stats = Live2TopGrowthAuditStats(
                enabled=True,
                status="processing",
                reason="bounded_chunk_processed_partial_artifacts_written",
                period_start_ms=task.period_start_ms,
                period_end_ms=task.period_end_ms,
                processed_count=task.cursor,
                remaining_count=len(task.symbols) - task.cursor,
                symbols_total=len(task.symbols),
                top_count=len(top_rows),
                cycle_seconds=time.perf_counter() - started,
                top_file=str(top_path.relative_to(self.output_dir)),
                status_file=str(status_path.relative_to(self.output_dir)),
            )
            return self._last_stats

        top_rows = _rank_candidates(task.candidates, limit=self.config.limit)
        top_path, status_path = self._write_snapshot(task=task, top_rows=top_rows, completion_status="completed")
        self._completed_periods.add(task.period_start_ms)
        self._task = None
        self._last_stats = Live2TopGrowthAuditStats(
            enabled=True,
            status="completed",
            reason="closed_hour_top_growth_artifacts_written",
            period_start_ms=task.period_start_ms,
            period_end_ms=task.period_end_ms,
            processed_count=len(task.symbols),
            remaining_count=0,
            symbols_total=len(task.symbols),
            top_count=len(top_rows),
            cycle_seconds=time.perf_counter() - started,
            top_file=str(top_path.relative_to(self.output_dir)),
            status_file=str(status_path.relative_to(self.output_dir)),
        )
        return self._last_stats

    def status(self) -> dict[str, object]:
        return self._last_stats.as_dict()

    def flush_partial(self, *, reason: str = "interrupted_shutdown") -> Live2TopGrowthAuditStats | None:
        task = self._task
        if task is None or not task.status_rows:
            return None
        top_rows = _rank_candidates(task.candidates, limit=self.config.limit)
        top_path, status_path = self._write_snapshot(task=task, top_rows=top_rows, completion_status="partial")
        self._last_stats = Live2TopGrowthAuditStats(
            enabled=True,
            status="partial",
            reason=reason,
            period_start_ms=task.period_start_ms,
            period_end_ms=task.period_end_ms,
            processed_count=task.cursor,
            remaining_count=max(0, len(task.symbols) - task.cursor),
            symbols_total=len(task.symbols),
            top_count=len(top_rows),
            top_file=str(top_path.relative_to(self.output_dir)),
            status_file=str(status_path.relative_to(self.output_dir)),
        )
        return self._last_stats

    def _write_snapshot(
        self,
        *,
        task: Live2TopGrowthAuditTask,
        top_rows: list[dict[str, object]],
        completion_status: str,
    ) -> tuple[Path, Path]:
        stamp = datetime.fromtimestamp(task.period_start_ms / 1000, UTC).strftime("%Y%m%d_%H0000_UTC")
        top_path = self.top_growth_dir / f"top_growth_{stamp}.csv"
        status_path = self.top_growth_dir / f"top_growth_status_{stamp}.csv"
        period_start_utc = _iso_ms(task.period_start_ms)
        period_end_utc = _iso_ms(task.period_end_ms)
        top_payload = [
            {
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                **row,
            }
            for row in top_rows
        ]
        status_payload = [
            {
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                **row,
            }
            for row in task.status_rows
        ]
        _write_csv(top_path, TOP_GROWTH_COLUMNS, top_payload)
        _write_csv(status_path, TOP_GROWTH_STATUS_COLUMNS, status_payload)
        ok_count = sum(1 for row in task.status_rows if row.get("status") == "ok")
        below_count = sum(1 for row in task.status_rows if row.get("status") == "below_threshold")
        failed_count = len(task.status_rows) - ok_count - below_count
        _append_csv(
            self.index_path,
            TOP_GROWTH_INDEX_COLUMNS,
            {
                "snapshot_utc": task.snapshot_utc,
                "period_start_utc": period_start_utc,
                "period_end_utc": period_end_utc,
                "period_start_ms": task.period_start_ms,
                "period_end_ms": task.period_end_ms,
                "symbols_total": len(task.symbols),
                "processed_count": len(task.status_rows),
                "remaining_count": max(0, len(task.symbols) - len(task.status_rows)),
                "completion_status": completion_status,
                "top_count": len(top_rows),
                "ok_count": ok_count,
                "below_threshold_count": below_count,
                "failed_count": failed_count,
                "threshold_pct": self.config.min_return_pct * 100.0,
                "limit": self.config.limit,
                "top_file": str(top_path.relative_to(self.output_dir)),
                "status_file": str(status_path.relative_to(self.output_dir)),
                "source": "live2_incremental_closed_1h_exchange_candles",
            },
        )
        return top_path, status_path


class TopGrowthSnapshotRunner:
    def __init__(
        self,
        *,
        config: TopGrowthSnapshotConfig,
        exchange_client: Any,
        logger: Any = print,
    ) -> None:
        self.config = config
        self.exchange = exchange_client
        self.logger = logger

    def run(self) -> int:
        if self.config.limit <= 0:
            raise ValueError("top_growth_limit must be > 0")
        if self.config.min_return_pct <= 0.0 or not math.isfinite(self.config.min_return_pct):
            raise ValueError("top_growth_min_return_pct must be finite and > 0")
        if self.config.fetch_spacing_seconds < 0.0 or not math.isfinite(self.config.fetch_spacing_seconds):
            raise ValueError("top_growth_fetch_spacing_seconds must be finite and >= 0")
        symbols = tuple(dict.fromkeys(self.config.symbols or tuple(self.exchange.list_usdt_swap_symbols())))
        if not symbols:
            raise ValueError("no symbols for top-growth snapshot")
        now_ms = int(time.time() * 1000)
        period_start_ms = self.config.period_start_ms
        if period_start_ms is None:
            period_start_ms = _previous_closed_hour_start_ms(now_ms)
        period_end_ms = period_start_ms + HOUR_MS
        if period_end_ms > now_ms:
            raise ValueError("top-growth period must be a fully closed 1h interval")
        audit = Live2TopGrowthAudit(
            output_dir=self.config.output_dir,
            exchange_client=self.exchange,
            config=Live2TopGrowthAuditConfig(
                enabled=True,
                min_return_pct=self.config.min_return_pct,
                limit=self.config.limit,
                symbols_per_cycle=max(1, len(symbols)),
                max_cycle_seconds=max(1.0, float(len(symbols)) * max(0.01, self.config.fetch_spacing_seconds + 0.5)),
                fetch_spacing_seconds=self.config.fetch_spacing_seconds,
            ),
        )
        stats = audit.process_due(symbols=symbols, now_ms=period_end_ms)
        # Force the requested period for explicit --period-start-utc without exposing live audit internals.
        if self.config.period_start_ms is not None:
            audit = Live2TopGrowthAudit(
                output_dir=self.config.output_dir,
                exchange_client=self.exchange,
                config=Live2TopGrowthAuditConfig(
                    enabled=True,
                    min_return_pct=self.config.min_return_pct,
                    limit=self.config.limit,
                    symbols_per_cycle=max(1, len(symbols)),
                    max_cycle_seconds=max(1.0, float(len(symbols)) * max(0.01, self.config.fetch_spacing_seconds + 0.5)),
                    fetch_spacing_seconds=self.config.fetch_spacing_seconds,
                ),
            )
            audit._task = Live2TopGrowthAuditTask(
                period_start_ms=period_start_ms,
                period_end_ms=period_end_ms,
                snapshot_utc=datetime.now(UTC).isoformat(),
                symbols=symbols,
            )
            stats = audit.process_due(symbols=symbols, now_ms=period_end_ms)
        self.logger(
            "top-growth: "
            f"{_iso_ms(period_start_ms)} · top {stats.top_count} · "
            f"artifacts {self.config.output_dir / 'top_growth'}"
        )
        return 0 if stats.status == "completed" else 1


def _load_top_growth_symbol_row(
    *,
    exchange: Any,
    symbol: str,
    period_start_ms: int,
    period_end_ms: int,
    snapshot_utc: str,
    threshold_fraction: float,
) -> dict[str, object]:
    base = {
        "snapshot_utc": snapshot_utc,
        "symbol": symbol,
        "status": "failed",
        "reason": "unknown",
        "growth_pct": "",
        "growth_fraction": "",
        "open": "",
        "high": "",
        "low": "",
        "close": "",
        "quote_volume": "",
        "number_of_trades": "",
        "taker_buy_quote_volume": "",
        "candle_timestamp_ms": "",
        "threshold_pct": threshold_fraction * 100.0,
        "timeframe": Timeframe.H1.value,
        "source": "exchange_1h_closed_candle",
    }
    if hasattr(exchange, "fetch_binance_klines"):
        try:
            raw_row = _fetch_exact_binance_1h_kline(
                exchange=exchange,
                symbol=symbol,
                period_start_ms=period_start_ms,
                period_end_ms=period_end_ms,
            )
        except Exception as exc:
            return {**base, "reason": f"fetch_binance_klines_failed:{type(exc).__name__}:{str(exc)[:160]}"}
        if raw_row is None:
            return {**base, "reason": "empty_binance_1h_klines"}
        return _status_row_from_prices(
            base=base,
            open_time_ms=period_start_ms,
            open_price=_float_or_none(raw_row[1] if len(raw_row) > 1 else None),
            high_price=_float_or_none(raw_row[2] if len(raw_row) > 2 else None),
            low_price=_float_or_none(raw_row[3] if len(raw_row) > 3 else None),
            close_price=_float_or_none(raw_row[4] if len(raw_row) > 4 else None),
            quote_volume=_float_or_none(raw_row[7] if len(raw_row) > 7 else None),
            number_of_trades=_float_or_none(raw_row[8] if len(raw_row) > 8 else None),
            taker_buy_quote_volume=_float_or_none(raw_row[10] if len(raw_row) > 10 else None),
            threshold_fraction=threshold_fraction,
        )

    try:
        frame = exchange.fetch_ohlcv(symbol, Timeframe.H1, period_start_ms, period_end_ms - 1)
    except Exception as exc:
        return {**base, "reason": f"fetch_ohlcv_failed:{type(exc).__name__}:{str(exc)[:160]}"}
    if getattr(frame, "empty", True):
        return {**base, "reason": "empty_ohlcv"}
    missing = [column for column in ("timestamp", "open", "high", "low", "close") if column not in frame.columns]
    if missing:
        return {**base, "reason": "missing_columns:" + ",".join(missing)}
    exact = frame.loc[frame["timestamp"].astype("int64") == int(period_start_ms)]
    if exact.empty:
        timestamps = frame["timestamp"].dropna().astype("int64")
        reason = "no_exact_hour_candle"
        if not timestamps.empty:
            reason += f":first={int(timestamps.min())}:last={int(timestamps.max())}"
        return {**base, "reason": reason}
    row = exact.sort_values("timestamp").iloc[-1]
    return _status_row_from_prices(
        base=base,
        open_time_ms=period_start_ms,
        open_price=_float_or_none(row.get("open")),
        high_price=_float_or_none(row.get("high")),
        low_price=_float_or_none(row.get("low")),
        close_price=_float_or_none(row.get("close")),
        quote_volume=_float_or_none(row.get("quote_volume")),
        number_of_trades=_float_or_none(row.get("number_of_trades")),
        taker_buy_quote_volume=_float_or_none(row.get("taker_buy_quote_volume")),
        threshold_fraction=threshold_fraction,
    )


def _fetch_exact_binance_1h_kline(
    *,
    exchange: Any,
    symbol: str,
    period_start_ms: int,
    period_end_ms: int,
) -> list[object] | None:
    rows = exchange.fetch_binance_klines(
        symbol=symbol,
        timeframe=Timeframe.H1,
        start_timestamp_ms=int(period_start_ms),
        end_timestamp_ms=int(period_end_ms) - 1,
        limit=2,
    )
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        try:
            open_time_ms = int(float(row[0]))
        except (TypeError, ValueError):
            continue
        if open_time_ms == int(period_start_ms):
            return list(row)
    return None


def _status_row_from_prices(
    *,
    base: dict[str, object],
    open_time_ms: int,
    open_price: float | None,
    high_price: float | None,
    low_price: float | None,
    close_price: float | None,
    quote_volume: float | None,
    number_of_trades: float | None,
    taker_buy_quote_volume: float | None,
    threshold_fraction: float,
) -> dict[str, object]:
    if open_price is None or close_price is None or high_price is None or low_price is None or open_price <= 0.0:
        return {
            **base,
            "reason": "invalid_open_close",
            "open": _blank_or_value(open_price),
            "high": _blank_or_value(high_price),
            "low": _blank_or_value(low_price),
            "close": _blank_or_value(close_price),
            "candle_timestamp_ms": open_time_ms,
        }
    growth_fraction = (close_price - open_price) / open_price
    status = "ok" if growth_fraction >= threshold_fraction else "below_threshold"
    return {
        **base,
        "status": status,
        "reason": status,
        "growth_pct": growth_fraction * 100.0,
        "growth_fraction": growth_fraction,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "quote_volume": _blank_or_value(quote_volume),
        "number_of_trades": _blank_or_value(number_of_trades),
        "taker_buy_quote_volume": _blank_or_value(taker_buy_quote_volume),
        "candle_timestamp_ms": open_time_ms,
    }


def _candidate_from_status_row(row: dict[str, object], *, threshold_fraction: float) -> dict[str, object] | None:
    if row.get("status") != "ok":
        return None
    growth_fraction = _float_or_none(row.get("growth_fraction"))
    if growth_fraction is None or growth_fraction < threshold_fraction:
        return None
    return {
        "symbol": row.get("symbol", ""),
        "growth_pct": row.get("growth_pct", ""),
        "growth_fraction": row.get("growth_fraction", ""),
        "open": row.get("open", ""),
        "high": row.get("high", ""),
        "low": row.get("low", ""),
        "close": row.get("close", ""),
        "quote_volume": row.get("quote_volume", ""),
        "number_of_trades": row.get("number_of_trades", ""),
        "taker_buy_quote_volume": row.get("taker_buy_quote_volume", ""),
        "threshold_pct": threshold_fraction * 100.0,
        "timeframe": Timeframe.H1.value,
        "source": "exchange_1h_closed_candle",
    }


def _rank_candidates(candidates: list[dict[str, object]], *, limit: int) -> list[dict[str, object]]:
    candidates.sort(
        key=lambda row: (
            _float_or_none(row.get("growth_fraction")) or -math.inf,
            _float_or_none(row.get("quote_volume")) or -math.inf,
        ),
        reverse=True,
    )
    return [{"rank": rank, **row} for rank, row in enumerate(candidates[:limit], start=1)]


def _previous_closed_hour_start_ms(now_ms: int) -> int:
    return ((int(now_ms) // HOUR_MS) - 1) * HOUR_MS


def parse_top_growth_period_start_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc.minute != 0 or parsed_utc.second != 0 or parsed_utc.microsecond != 0:
        raise ValueError("period-start-utc must point to the start of a closed 1h candle")
    return int(parsed_utc.timestamp() * 1000)


def _iso_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _float_or_none(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _blank_or_value(value: object) -> object:
    return "" if value is None else value


def _ensure_csv(path: Path, fieldnames: tuple[str, ...]) -> None:
    if path.exists():
        return
    _write_csv(path, fieldnames, [])


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _append_csv(path: Path, fieldnames: tuple[str, ...], row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)
