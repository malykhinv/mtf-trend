"""Human-readable terminal status grid for anomaly live2.

The grid intentionally mirrors the compact v1 operator log style: short
Russian section titles, four fixed-width cells per row, and explicit quality
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
    user_data_stream_status: Mapping[str, object],
    runtime_gate_status: Mapping[str, object],
    artifact_writer_status: Mapping[str, object],
    session_top_snapshot: Mapping[str, object] | None = None,
) -> str:
    """Format one live2 operator heartbeat in the compact v1 grid style."""

    ws_health = _dict(market_data_status.get("ws_health"))
    aggtrade_ws = _dict(market_data_status.get("aggtrade_ws"))
    mark_price_ws = _dict(market_data_status.get("mark_price_ws"))
    open_interest = _dict(market_data_status.get("open_interest"))
    prior_context = _dict(market_data_status.get("prior_context"))
    rolling_context_maintenance = _dict(market_data_status.get("rolling_context_maintenance"))
    universe = _dict(market_data_status.get("universe"))
    startup_warmup = _dict(market_data_status.get("startup_warmup"))
    readiness = _dict(runtime_gate_status.get("readiness"))
    supervisor_status = _dict(execution_status.get("position_supervisor"))
    user_stream = _dict(user_data_stream_status)

    shards_total = _int(ws_health.get("shards_total"))
    shards_connected = _int(ws_health.get("shards_connected"))
    reconnects = _int(ws_health.get("reconnect_attempts"))
    disconnects = _int(ws_health.get("disconnect_count"))
    planned_rotations = _int(ws_health.get("planned_rotation_count"))
    payload_errors = _int(ws_health.get("payload_errors"))
    aggtrade_rows_applied = _int(aggtrade_ws.get("rows_applied"))
    mark_rows_applied = _int(mark_price_ws.get("rows_applied"))
    mark_ready_symbols = _int(mark_counts.get("ok"))
    mark_stale_symbols = _int(mark_counts.get("stale"))
    oi_ready_symbols = _int(open_interest.get("ready_symbols"))
    oi_active_symbols = _int(open_interest.get("active_target_symbols"))
    oi_errors = _int(open_interest.get("total_errors"))
    oi_stale_symbols = _int(open_interest_counts.get("stale"))
    prior_ready_symbols = _int(prior_context.get("ready_symbols"))
    prior_active_symbols = _int(prior_context.get("active_target_symbols"))
    prior_errors = _int(prior_context.get("total_errors"))
    prior_stale_symbols = _int(prior_context_counts.get("stale"))
    prior_gap_tolerated = _int(prior_context.get("total_ws_5m_gap_tolerated"))
    prior_gap_above_tolerance = _int(prior_context.get("total_ws_5m_gap_above_tolerance_tolerated"))
    prior_gap_rejected = _int(prior_context.get("total_ws_5m_gap_rejected"))
    rolling_1m_status = str(rolling_context_maintenance.get("status") or "-")
    rolling_1m_targets = _int(rolling_context_maintenance.get("active_target_symbols"))
    rolling_1m_success = _int(rolling_context_maintenance.get("total_success"))
    rolling_1m_errors = _int(rolling_context_maintenance.get("total_errors"))
    rolling_1m_loaded = _int(rolling_context_maintenance.get("last_loaded_candles"))
    aggtrade_pre_first_payload_failures = _int(ws_health.get("aggtrade_pre_first_payload_failures"))
    coverage_ready = bool(market_data_status.get("stream_coverage_ready"))
    entry_stream_ready = bool(market_data_status.get("entry_stream_ready"))
    market_gate_ready = bool(market_data_status.get("market_data_ready_for_entries"))
    artifact_ready = bool(artifact_writer_status.get("ready"))
    entries_allowed = bool(readiness.get("new_entries_allowed"))
    trading_allowed_ratio = _trading_allowed_ratio(runtime_gate_status)

    connected_ratio = _connection_ratio(ws_health)
    data_status = "Поток" if coverage_ready else "Нет потока"
    if coverage_ready and not market_gate_ready:
        data_status = "Прогрев"
    if entry_stream_ready and not coverage_ready:
        data_status = "Entry"
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
    actionable_symbol_counts = _dict(market_data_status.get("actionable_symbol_counts"))
    current_actionable_symbols = _int(actionable_symbol_counts.get("current"))
    seen_actionable_symbols = _int(actionable_symbol_counts.get("session_seen"))
    if seen_actionable_symbols <= 0:
        seen_actionable_symbols = _int(actionable_symbol_counts.get("seen"))
    if current_actionable_symbols <= 0 and seen_actionable_symbols <= 0:
        current_actionable_symbols = _int(state_counts.get("actionable"))

    total_decisions = _int(decision_status.get("total_decisions"))
    selected_count = _int(decision_status.get("selected_count"))
    total_rejected = _int(decision_status.get("total_rejected"))
    deadline_missed = _int(decision_status.get("total_deadline_missed"))
    deadline_expired_backlog = _int(decision_status.get("total_deadline_expired_backlog"))
    data_not_ready = _int(decision_status.get("total_data_not_ready"))
    data_dependency_not_ready = _int(decision_status.get("total_data_dependency_not_ready"))
    pre_live_skipped = _int(decision_status.get("total_pre_live_bucket_skipped"))

    max_positions = _int(execution_status.get("max_open_positions"))
    max_positions_label = "all" if bool(execution_status.get("max_open_positions_unlimited")) or max_positions == 0 else str(max_positions)
    open_positions = _int(execution_status.get("open_protected_positions"))
    protected_positions = execution_status.get("protected_positions")
    protected_count = len(protected_positions) if isinstance(protected_positions, list) else open_positions
    session_positions_total = _int(execution_status.get("total_positions_protected"))
    total_orders = _int(execution_status.get("total_orders_submitted"))
    protected_total = _int(execution_status.get("total_positions_protected"))
    integrity_errors = _int(execution_status.get("total_integrity_errors")) + _int(supervisor_status.get("total_integrity_errors"))
    tp1_count = _int(supervisor_status.get("total_tp1_closes"))
    early_exit_count = _int(supervisor_status.get("total_early_exit_closes"))
    final_count = _int(supervisor_status.get("total_final_closes"))
    stop_count = _int(supervisor_status.get("total_stop_closes"))
    be_count = _int(supervisor_status.get("total_be_closes"))
    realized_pnl = _float_or_none(supervisor_status.get("total_realized_pnl_usdt"))
    user_stream_ready = bool(user_stream.get("ready"))
    user_stream_events = _int(user_stream.get("messages_received"))

    runtime_reason = _compact_gate_reason(str(runtime_gate_status.get("reason") or ""))
    queue_size = _int(artifact_writer_status.get("queue_size"))
    queue_max = _int(artifact_writer_status.get("queue_max_size"))
    loop_overruns = _int(runtime_gate_status.get("decision_loop_overrun_count"))
    loop_max_ms = _int(runtime_gate_status.get("decision_loop_max_elapsed_ms"))
    clean_windows = _int(market_data_status.get("clean_windows"))
    active_total = seen_actionable_symbols
    context_gap_summary = f"{prior_gap_tolerated}/{prior_gap_above_tolerance}/{prior_gap_rejected}"

    rows = [
        _section_title("Соединение"),
        _format_status_line(
            _format_status_cell(
                "Стабильность",
                _format_marked_quality_value(
                    _format_percent(connected_ratio, signed=False, precision=0),
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
                _format_marked_quality_value(data_status, "good" if entry_stream_ready else "warn"),
            ),
            _format_status_cell("Переподключения", reconnects),
        ),
        _separator_line(),
        _section_title("Задержки"),
        _format_status_line(
            _format_status_cell(
                "Цикл",
                _format_millis(max(0, int(cycle_seconds * 1000))) if math.isfinite(cycle_seconds) else "-",
            ),
            _format_status_cell(
                "Цикл максимум",
                _format_marked_quality_value(
                    _format_millis(loop_max_ms),
                    _quality_level_from_seconds(loop_max_ms / 1000.0 if loop_max_ms > 0 else 0.0, good_max=0.20, warn_max=0.75),
                ),
            ),
            _format_status_cell(
                "Перегрузки",
                _format_marked_quality_value(loop_overruns, _quality_level_from_count(loop_overruns, good_max=0, warn_max=2)),
            ),
            _format_status_cell(
                "Опоздания",
                _format_marked_quality_value(deadline_missed, _quality_level_from_count(deadline_missed, good_max=0, warn_max=2)),
            ),
        ),
        _format_status_line(
            _format_status_cell("Хвост", deadline_expired_backlog),
            _format_status_cell("Данные поздно", data_not_ready),
            _format_status_cell("Зависимости", data_dependency_not_ready),
            _format_status_cell("Чистые окна", clean_windows),
        ),
        _separator_line(),
        _section_title("Контекст"),
        _format_status_line(
            _format_status_cell("Цена", f"{mark_ready_symbols}/{selected_symbols or total_symbols}"),
            _format_status_cell("Цена устарела", mark_stale_symbols),
            _format_status_cell("ОИ", f"{oi_ready_symbols}/{oi_active_symbols or selected_symbols}"),
            _format_status_cell("ОИ устарел", oi_stale_symbols),
        ),
        _format_status_line(
            _format_status_cell("ОИ ошибки", _format_marked_quality_value(oi_errors, _quality_level_from_count(oi_errors, good_max=0, warn_max=3))),
            _format_status_cell("Контекст", f"{prior_ready_symbols}/{prior_active_symbols or selected_symbols}"),
            _format_status_cell("Контекст устарел", prior_stale_symbols),
            _format_status_cell("Разрывы контекста", context_gap_summary),
        ),
        _format_status_line(
            _format_status_cell("1m maint", rolling_1m_status),
            _format_status_cell("1m цели", rolling_1m_targets),
            _format_status_cell("1m ok/err", f"{rolling_1m_success}/{rolling_1m_errors}"),
            _format_status_cell("1m свечи", rolling_1m_loaded),
        ),
        _separator_line(),
        _section_title("Рынок"),
        _format_status_line(
            _format_status_cell("Время", _format_live_runtime(runtime_seconds)),
            _format_status_cell("Символы", selected_symbols),
            _format_status_cell("Аномалии", total_decisions),
            _format_status_cell("Активные", f"{current_actionable_symbols}/{active_total}"),
        ),
        *_format_session_top_block(session_top_snapshot),
        _separator_line(),
        _section_title(f"Торговля {_format_percent(trading_allowed_ratio, signed=False, precision=0)}"),
        _format_status_line(
            _format_status_cell("Позиции", f"{open_positions}/{max_positions_label}"),
            _format_status_cell("Сделки", session_positions_total),
            _format_status_cell("Closed", final_count),
            _format_status_cell("Early", early_exit_count),
        ),
        _format_status_line(
            _format_status_cell("PNL", _format_usdt(realized_pnl) if realized_pnl is not None else "-"),
            _format_status_cell("SL", stop_count),
            _format_status_cell("BE", be_count),
            _format_status_cell("TP", tp1_count),
        ),
    ]
    return "\n".join(rows)


def _format_session_top_block(session_top_snapshot: Mapping[str, object] | None) -> list[str]:
    if not session_top_snapshot:
        return []
    label_base = str(session_top_snapshot.get("session_label") or "Топы")
    window_label = str(session_top_snapshot.get("top_window_label") or "")
    phase = str(session_top_snapshot.get("session_phase") or "")
    label = f"{label_base} · {window_label}" if window_label else label_base
    if phase == "overlap" and "+" not in label:
        label = f"{label} · наложение"
    elif phase == "transition" and "→" not in label:
        label = f"{label} · переход"
    items_raw = session_top_snapshot.get("items")
    items = items_raw if isinstance(items_raw, list) else []
    cells: list[str] = []
    for item in items[:4]:
        if not isinstance(item, Mapping):
            continue
        symbol = _compact_symbol(str(item.get("symbol") or ""))
        growth = _float_or_none(item.get("growth_fraction"))
        if not symbol or growth is None:
            continue
        cells.append(_format_session_top_cell(f"{symbol} {_format_percent(growth, signed=False, precision=1)}"))
    if cells:
        while len(cells) < 4:
            cells.append(_format_session_top_cell(""))
        return [_separator_line(), _section_title(label), _format_status_line(*cells[:4])]
    reason = str(session_top_snapshot.get("reason") or "")
    if reason in {"no_positive_growth_since_session_metric_baseline"}:
        value = "нет роста"
    elif reason in {"no_usable_ticker_price_snapshots_since_session_metric_start"}:
        value = "нет данных"
    else:
        value = "нет данных"
    return [
        _separator_line(),
        _section_title(label),
        _format_status_line(
            _format_session_top_cell(value),
            _format_session_top_cell(""),
            _format_session_top_cell(""),
            _format_session_top_cell(""),
        ),
    ]


def _format_session_top_cell(text: str, *, width: int = 24) -> str:
    cleaned = str(text).strip()
    if len(cleaned) > width:
        cleaned = cleaned[: max(0, width - 1)] + "~"
    return f"{cleaned:<{width}}"


def _compact_symbol(symbol: str) -> str:
    text = str(symbol or "").strip()
    if not text:
        return ""
    if "/" in text:
        return text.split("/", 1)[0]
    if "_" in text:
        return text.split("_", 1)[0]
    return text


def _format_status_cell(label: str, value: object, *, width: int = 24) -> str:
    text = f"{label} {_format_live_status_value(value)}"
    if len(text) > width:
        text = text[: max(0, width - 1)] + "~"
    return f"{text:<{width}}"


def _format_status_line(*cells: str) -> str:
    return "  ".join(cells).rstrip()


def _section_title(title: str) -> str:
    return f"◆ {title}"


def _separator_line() -> str:
    return "_" * 99


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
    if seconds < 1.0:
        return _format_millis(int(seconds * 1000))
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


def _format_usdt(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "-"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value):.2f}"


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


def _trading_allowed_ratio(runtime_gate_status: Mapping[str, object]) -> float | None:
    seconds = _dict(runtime_gate_status.get("session_seconds")) or _dict(runtime_gate_status.get("seconds"))
    allowed = _float_or_none(seconds.get("allowed_seconds"))
    blocked = _float_or_none(seconds.get("blocked_seconds"))
    if allowed is None or blocked is None:
        return None
    total = allowed + blocked
    if total <= 0:
        return 1.0 if _dict(runtime_gate_status.get("readiness")).get("new_entries_allowed") else 0.0
    return max(0.0, min(1.0, allowed / total))


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


def _float_or_none(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


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
