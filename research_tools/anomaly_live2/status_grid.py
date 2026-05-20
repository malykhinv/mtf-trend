"""Human-readable terminal status grid for anomaly live2.

The grid intentionally mirrors the compact v1 operator log style: short
Russian section titles, three fixed-width cells per row, and explicit quality
marks. It is console-only; artifacts remain the source of truth.
"""

from __future__ import annotations

import math
from typing import Mapping


def format_live2_status_grid(
    *,
    runtime_seconds: float,
    cycle_seconds: float,
    state_counts: Mapping[str, int],
    ticker_counts: Mapping[str, int],
    aggtrade_counts: Mapping[str, int],
    mark_counts: Mapping[str, int],
    open_interest_counts: Mapping[str, int],
    prior_context_counts: Mapping[str, int],
    candle_counts: Mapping[str, int],
    market_data_status: Mapping[str, object],
    decision_status: Mapping[str, object],
    execution_status: Mapping[str, object],
    runtime_gate_status: Mapping[str, object],
    artifact_writer_status: Mapping[str, object],
) -> str:
    """Format one live2 operator heartbeat in the compact v1 grid style."""

    ws_health = _dict(market_data_status.get("ws_health"))
    aggtrade_ws = _dict(market_data_status.get("aggtrade_ws"))
    mark_price_ws = _dict(market_data_status.get("mark_price_ws"))
    open_interest = _dict(market_data_status.get("open_interest"))
    prior_context = _dict(market_data_status.get("prior_context"))
    universe = _dict(market_data_status.get("universe"))
    startup_warmup = _dict(market_data_status.get("startup_warmup"))
    readiness = _dict(runtime_gate_status.get("readiness"))
    supervisor_status = _dict(execution_status.get("position_supervisor"))

    shards_total = _int(ws_health.get("shards_total"))
    shards_connected = _int(ws_health.get("shards_connected"))
    reconnects = _int(ws_health.get("reconnect_attempts"))
    disconnects = _int(ws_health.get("disconnect_count"))
    planned_rotations = _int(ws_health.get("planned_rotation_count"))
    payload_errors = _int(ws_health.get("payload_errors"))
    aggtrade_rows_applied = _int(aggtrade_ws.get("rows_applied"))
    mark_rows_applied = _int(mark_price_ws.get("rows_applied"))
    mark_ready_symbols = _int(mark_counts.get("ok"))
    oi_ready_symbols = _int(open_interest.get("ready_symbols"))
    oi_active_symbols = _int(open_interest.get("active_target_symbols"))
    oi_errors = _int(open_interest.get("total_errors"))
    oi_stale_symbols = _int(open_interest_counts.get("stale"))
    prior_ready_symbols = _int(prior_context.get("ready_symbols"))
    prior_active_symbols = _int(prior_context.get("active_target_symbols"))
    prior_errors = _int(prior_context.get("total_errors"))
    prior_stale_symbols = _int(prior_context_counts.get("stale"))
    aggtrade_pre_first_payload_failures = _int(ws_health.get("aggtrade_pre_first_payload_failures"))
    coverage_ready = bool(market_data_status.get("stream_coverage_ready"))
    market_gate_ready = bool(market_data_status.get("market_data_ready_for_entries"))
    artifact_ready = bool(artifact_writer_status.get("ready"))
    entries_allowed = bool(readiness.get("new_entries_allowed"))

    connected_ratio = _connection_ratio(ws_health)
    data_status = "Поток" if coverage_ready else "Нет потока"
    if coverage_ready and not market_gate_ready:
        data_status = "Прогрев"
    if disconnects > 0 or reconnects > 0:
        data_status = "Reconnect"

    selected_symbols = _int(universe.get("selected_symbols"))
    warmed_symbols = _int(startup_warmup.get("symbols_warmed"))
    warmup_requested = _int(startup_warmup.get("symbols_requested"))
    live_ready_candles = _int(candle_counts.get("live_ready"))
    warmup_only_candles = _int(candle_counts.get("startup_warmup_only"))
    ready_candles = live_ready_candles + warmup_only_candles
    total_symbols = max(_sum_counts(state_counts), selected_symbols, ready_candles)
    watched_symbols = _int(state_counts.get("watching")) + _int(state_counts.get("actionable"))
    actionable_symbols = _int(state_counts.get("actionable"))

    total_decisions = _int(decision_status.get("total_decisions"))
    selected_count = _int(decision_status.get("selected_count"))
    total_rejected = _int(decision_status.get("total_rejected"))
    deadline_missed = _int(decision_status.get("total_deadline_missed"))
    data_not_ready = _int(decision_status.get("total_data_not_ready"))
    data_dependency_not_ready = _int(decision_status.get("total_data_dependency_not_ready"))
    pre_live_skipped = _int(decision_status.get("total_pre_live_bucket_skipped"))

    max_positions = _int(execution_status.get("max_open_positions"))
    open_positions = _int(execution_status.get("open_protected_positions"))
    protected_positions = execution_status.get("protected_positions")
    protected_count = len(protected_positions) if isinstance(protected_positions, list) else open_positions
    total_orders = _int(execution_status.get("total_orders_submitted"))
    protected_total = _int(execution_status.get("total_positions_protected"))
    integrity_errors = _int(execution_status.get("total_integrity_errors")) + _int(supervisor_status.get("total_integrity_errors"))
    tp1_count = _int(supervisor_status.get("total_tp1_closes"))
    final_count = _int(supervisor_status.get("total_final_closes"))

    runtime_reason = _compact_gate_reason(str(runtime_gate_status.get("reason") or ""))
    queue_size = _int(artifact_writer_status.get("queue_size"))
    queue_max = _int(artifact_writer_status.get("queue_max_size"))
    loop_overruns = _int(runtime_gate_status.get("decision_loop_overrun_count"))
    loop_max_ms = _int(runtime_gate_status.get("decision_loop_max_elapsed_ms"))

    rows = [
        "Соединение",
        _format_status_line(
            _format_status_cell(
                "Стабильность",
                _format_marked_quality_value(
                    _format_percent(connected_ratio, signed=False, precision=1),
                    _quality_level_from_ratio(connected_ratio, good_min=0.98, warn_min=0.95),
                ),
            ),
            _format_status_cell(
                "Пульс",
                _format_marked_quality_value(
                    _format_live_pulse(cycle_seconds),
                    _quality_level_from_seconds(cycle_seconds, good_max=0.20, warn_max=0.75),
                ),
            ),
            _format_status_cell(
                "Данные",
                _format_marked_quality_value(data_status, "good" if coverage_ready else "warn"),
            ),
        ),
        _format_status_line(
            _format_status_cell("Шарды", f"{shards_connected}/{shards_total}"),
            _format_status_cell(
                "Сделки",
                _format_marked_quality_value(
                    aggtrade_rows_applied,
                    "good" if aggtrade_rows_applied > 0 else "bad" if shards_total > 0 else "warn",
                ),
            ),
            _format_status_cell(
                "До payload",
                _format_marked_quality_value(
                    aggtrade_pre_first_payload_failures,
                    _quality_level_from_count(aggtrade_pre_first_payload_failures, good_max=0, warn_max=2),
                ),
            ),
        ),
        _format_status_line(
            _format_status_cell("Mark", _format_marked_quality_value(mark_rows_applied, "good" if mark_rows_applied > 0 else "warn")),
            _format_status_cell("Mark sym", mark_ready_symbols),
            _format_status_cell("Ротации", planned_rotations),
        ),
        _format_status_line(
            _format_status_cell("OI", _format_marked_quality_value(f"{oi_ready_symbols}/{oi_active_symbols}", "good" if not oi_active_symbols or oi_ready_symbols >= oi_active_symbols else "warn")),
            _format_status_cell("OI stale", oi_stale_symbols),
            _format_status_cell("OI err", _format_marked_quality_value(oi_errors, _quality_level_from_count(oi_errors, good_max=0, warn_max=3))),
        ),
        _format_status_line(
            _format_status_cell("24h ctx", _format_marked_quality_value(f"{prior_ready_symbols}/{prior_active_symbols}", "good" if not prior_active_symbols or prior_ready_symbols >= prior_active_symbols else "warn")),
            _format_status_cell("Ctx stale", prior_stale_symbols),
            _format_status_cell("Ctx err", _format_marked_quality_value(prior_errors, _quality_level_from_count(prior_errors, good_max=0, warn_max=3))),
        ),
        _format_status_line(
            _format_status_cell("Переподкл", reconnects),
            _format_status_cell("Разрывы", disconnects),
            _format_status_cell("Ошибки", payload_errors),
        ),
        "",
        "Рынок",
        _format_status_line(
            _format_status_cell("Время", _format_live_runtime(runtime_seconds)),
            _format_status_cell("Вселенная", selected_symbols),
            _format_status_cell("Свечи", f"{live_ready_candles}/{total_symbols}"),
        ),
        _format_status_line(
            _format_status_cell("Активные", f"{actionable_symbols}/{watched_symbols}"),
            _format_status_cell("Прогрев", f"{warmed_symbols}/{warmup_requested}" if warmup_requested else "-"),
            _format_status_cell("Только REST", warmup_only_candles),
        ),
        "",
        "Торговля",
        _format_status_line(
            _format_status_cell("PNL", "-"),
            _format_status_cell("Позиции", f"{open_positions}/{max_positions}"),
            _format_status_cell("Защита", protected_count),
        ),
        _format_status_line(
            _format_status_cell("Ордера", total_orders),
            _format_status_cell("TP1", tp1_count),
            _format_status_cell("Закрыто", final_count),
        ),
        "",
        "Контроль",
        _format_status_line(
            _format_status_cell(
                "Входы",
                _format_marked_quality_value("Вкл" if entries_allowed else "Выкл", "good" if entries_allowed else "warn"),
            ),
            _format_status_cell("Причина", runtime_reason),
            _format_status_cell(
                "Аудит",
                _format_marked_quality_value(
                    f"{queue_size}/{queue_max}",
                    "good" if artifact_ready and queue_size <= max(1, queue_max // 4) else "warn" if artifact_ready else "bad",
                ),
            ),
        ),
        _format_status_line(
            _format_status_cell(
                "Дедлайн",
                _format_marked_quality_value(deadline_missed, _quality_level_from_count(deadline_missed, good_max=0, warn_max=2)),
            ),
            _format_status_cell("Данные", data_not_ready + data_dependency_not_ready),
            _format_status_cell("Отказы", total_rejected),
        ),
        _format_status_line(
            _format_status_cell("Deps", data_dependency_not_ready),
            _format_status_cell("До live", pre_live_skipped),
            _format_status_cell("Выбрано", selected_count),
        ),
        _format_status_line(
            _format_status_cell(
                "Цикл max",
                _format_marked_quality_value(
                    _format_millis(loop_max_ms),
                    _quality_level_from_seconds(loop_max_ms / 1000.0 if loop_max_ms > 0 else 0.0, good_max=0.20, warn_max=0.75),
                ),
            ),
            _format_status_cell(
                "Перегруз",
                _format_marked_quality_value(loop_overruns, _quality_level_from_count(loop_overruns, good_max=0, warn_max=2)),
            ),
            _format_status_cell(
                "Риск",
                _format_marked_quality_value(integrity_errors, _quality_level_from_count(integrity_errors, good_max=0, warn_max=0)),
            ),
        ),
    ]
    return "\n".join(rows)


def _format_status_cell(label: str, value: object, *, width: int = 26) -> str:
    text = f"{label} {_format_live_status_value(value)}"
    if len(text) > width:
        text = text[: max(0, width - 1)] + "~"
    return f"{text:<{width}}"


def _format_status_line(*cells: str) -> str:
    return "  ".join(cells).rstrip()


def _format_live_status_value(value: object) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            return "-"
        if abs(value) >= 100.0:
            return f"{value:.0f}"
        if abs(value) >= 10.0:
            return f"{value:.1f}"
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _quality_mark(level: str) -> str:
    if level == "good":
        return "✓"
    if level == "warn":
        return "!"
    if level == "bad":
        return "×"
    return "?"


def _format_marked_quality_value(value: object, level: str) -> str:
    return f"{_quality_mark(level)} {_format_live_status_value(value)}"


def _quality_level_from_ratio(value: float | None, *, good_min: float, warn_min: float) -> str:
    if value is None or not math.isfinite(value):
        return "unknown"
    if value >= good_min:
        return "good"
    if value >= warn_min:
        return "warn"
    return "bad"


def _quality_level_from_seconds(value: float | None, *, good_max: float, warn_max: float) -> str:
    if value is None or not math.isfinite(value) or value < 0.0:
        return "unknown"
    if value <= good_max:
        return "good"
    if value <= warn_max:
        return "warn"
    return "bad"


def _quality_level_from_count(value: int | float | None, *, good_max: int, warn_max: int) -> str:
    if value is None:
        return "unknown"
    finite = float(value)
    if not math.isfinite(finite):
        return "unknown"
    if finite <= good_max:
        return "good"
    if finite <= warn_max:
        return "warn"
    return "bad"


def _format_live_runtime(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0.0:
        return "-"
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}ч {minutes:02d}м {secs:02d}с"
    if minutes > 0:
        return f"{minutes}м {secs:02d}с"
    return f"{secs}с"


def _format_live_pulse(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0.0:
        return "-"
    if seconds >= 60.0:
        return _format_live_runtime(seconds)
    return f"{float(seconds):.1f}с"


def _format_millis(milliseconds: int) -> str:
    if milliseconds <= 0:
        return "0мс"
    if milliseconds < 1000:
        return f"{milliseconds}мс"
    return _format_live_pulse(milliseconds / 1000.0)


def _format_percent(value: float | None, *, signed: bool, precision: int = 1) -> str:
    if value is None or not math.isfinite(value):
        return "-"
    pct = value * 100.0
    sign = "+" if signed and pct > 0 else ""
    return f"{sign}{pct:.{precision}f}%"


def _connection_ratio(ws_health: Mapping[str, object]) -> float | None:
    ticker_ready = bool(ws_health.get("ticker_ready"))
    aggtrade_ready = bool(ws_health.get("aggtrade_ready"))
    shards_total = _int(ws_health.get("shards_total"))
    shards_connected = _int(ws_health.get("shards_connected"))
    if shards_total > 0:
        return (float(shards_connected) + (1.0 if ticker_ready else 0.0)) / float(shards_total + 1)
    if ticker_ready and aggtrade_ready:
        return 1.0
    if ticker_ready:
        return 0.5
    return 0.0


def _compact_gate_reason(reason: str) -> str:
    text = str(reason or "").strip()
    if not text or text == "all_gates_ready":
        return "ok"
    replacements = {
        "stream_coverage_not_ready": "нет потока",
        "decision_latency_degraded": "лаг",
        "artifact_writer_not_ready": "аудит",
        "exchange_boundary_not_ready": "биржа",
        "position_supervisor_not_ready": "сопров",
        "execution_not_ready": "ордера",
    }
    parts = [part for part in text.split("+") if part]
    compact = [replacements.get(part, part) for part in parts]
    return "+".join(compact) if compact else text


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _sum_counts(counts: Mapping[str, int]) -> int:
    total = 0
    for value in counts.values():
        total += _int(value)
    return total
