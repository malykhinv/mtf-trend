"""Research reporting helpers for post-pump absorption."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib
import pandas as pd

from domain.enums.timeframe import Timeframe

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


module_logger = logging.getLogger(__name__)

_FLOAT_COLUMNS_TO_FORMAT = {
    "best_profit_factor",
    "best_pnl_percent",
    "best_win_rate",
    "best_max_dd",
    "best_max_drawdown_pct",
    "best_ppa_median_entry_range_fraction",
    "best_ppa_median_aggression_ratio",
    "best_ppa_median_aggression_volume_mult",
    "best_ppa_median_mfe_r",
    "best_ppa_median_mae_r",
    "best_ppa_median_stop_distance_atr",
    "best_ppa_median_holding_bars",
    "win_rate",
    "pnl_total",
    "pnl_percent_total",
    "avg_pnl",
    "median_pnl",
    "median_entry_range_fraction",
    "median_aggression_ratio",
    "median_aggression_volume_mult",
    "median_stop_distance_atr",
    "median_mfe_r",
    "median_mae_r",
    "median_holding_bars",
    "range_mid_hit_rate",
    "range_high_hit_rate",
    "tp1_hit_rate",
    "tp2_hit_rate",
}

_SCALAR_TYPES = (str, int, float, bool)


@dataclass(frozen=True, slots=True)
class PostPumpAbsorptionResearchRun:
    timeframe: Timeframe
    strategy_results_dir: Path
    results: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: pd.DataFrame


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _infer_symbol_from_file_name(path: Path) -> str:
    base_name = path.stem
    if base_name.endswith("_trades"):
        base_name = base_name[: -len("_trades")]
    if base_name.endswith("_diagnostics"):
        base_name = base_name[: -len("_diagnostics")]
    return base_name.replace("_", "/")


def _load_trade_rows(diagnostics_dir: Path, timeframe: Timeframe) -> pd.DataFrame:
    trade_frames: list[pd.DataFrame] = []
    for path in sorted(diagnostics_dir.glob("*_trades.csv")):
        frame = _read_csv_or_empty(path)
        if frame.empty:
            continue
        if "symbol" not in frame.columns:
            frame["symbol"] = _infer_symbol_from_file_name(path)
        frame["timeframe"] = timeframe.value
        frame["source_path"] = str(path)
        trade_frames.append(frame)
    if not trade_frames:
        return pd.DataFrame()
    return pd.concat(trade_frames, ignore_index=True)


def _flatten_diagnostics_payload(path: Path, payload: dict[str, Any], timeframe: Timeframe) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": str(payload.get("symbol") or _infer_symbol_from_file_name(path)),
        "timeframe": timeframe.value,
        "trades_generated": int(payload.get("trades_generated", 0) or 0),
        "diagnostics_path": str(path),
    }
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict):
        for key, value in diagnostics.items():
            if isinstance(value, _SCALAR_TYPES) or value is None:
                row[key] = value
    return row


def _load_diagnostics_rows(diagnostics_dir: Path, timeframe: Timeframe) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(diagnostics_dir.glob("*_diagnostics.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        rows.append(_flatten_diagnostics_payload(path, payload, timeframe))
    return pd.DataFrame(rows)


def load_post_pump_absorption_research_run(
    *,
    timeframe: Timeframe,
    strategy_results_dir: Path,
    results_file_name: str = "results.csv",
) -> PostPumpAbsorptionResearchRun:
    diagnostics_dir = strategy_results_dir / "trade_plots" / "post_pump_absorption_diagnostics"
    return PostPumpAbsorptionResearchRun(
        timeframe=timeframe,
        strategy_results_dir=Path(strategy_results_dir),
        results=_read_csv_or_empty(strategy_results_dir / results_file_name),
        trades=_load_trade_rows(diagnostics_dir, timeframe),
        diagnostics=_load_diagnostics_rows(diagnostics_dir, timeframe),
    )


def _safe_numeric(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
        return float(value)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _safe_int(value: object) -> int:
    numeric = _safe_numeric(value)
    return int(numeric or 0)


def _coerce_numeric_columns(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    prepared = frame.copy()
    for column in columns:
        if column in prepared.columns:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
    return prepared


def _result_count(trades: pd.DataFrame, *, result_type: str) -> int:
    if trades.empty or "result_type" not in trades.columns:
        return 0
    return int((trades["result_type"].astype(str) == result_type).sum())


def _setup_count(trades: pd.DataFrame, *, setup_type: str) -> int:
    if trades.empty or "setup_type" not in trades.columns:
        return 0
    return int((trades["setup_type"].astype(str) == setup_type).sum())


def _hit_rate(trades: pd.DataFrame, column_name: str) -> float | None:
    if trades.empty or column_name not in trades.columns:
        return None
    values = pd.to_numeric(trades[column_name], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.mean())


def _median_metric(trades: pd.DataFrame, column_name: str) -> float | None:
    if trades.empty or column_name not in trades.columns:
        return None
    values = pd.to_numeric(trades[column_name], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.median())


def _build_timeframe_summary(runs: Sequence[PostPumpAbsorptionResearchRun]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for run in runs:
        best_row = run.results.iloc[0] if not run.results.empty else None
        diagnostics = run.diagnostics
        trades = run.trades

        row: dict[str, Any] = {
            "timeframe": run.timeframe.value,
            "strategy_results_dir": str(run.strategy_results_dir),
            "combinations_total": int(len(run.results)),
            "combinations_with_trades": int((run.results["trades_count"] > 0).sum()) if "trades_count" in run.results.columns else 0,
            "diagnostics_symbols": int(len(diagnostics)),
            "diagnostics_symbols_with_trades": int((pd.to_numeric(diagnostics["trades_generated"], errors="coerce").fillna(0) > 0).sum()) if "trades_generated" in diagnostics.columns else 0,
            "diagnostics_missing_taker_data": int(pd.to_numeric(diagnostics["missing_taker_data"], errors="coerce").fillna(0).sum()) if "missing_taker_data" in diagnostics.columns else 0,
            "trade_rows_count": int(len(trades)),
            "trade_symbols_count": int(trades["symbol"].nunique()) if "symbol" in trades.columns and not trades.empty else 0,
            "trade_lsb_count": _setup_count(trades, setup_type="LSB"),
            "trade_mbb_count": _setup_count(trades, setup_type="MBB"),
            "trade_sl_count": _result_count(trades, result_type="SL"),
            "trade_be_count": _result_count(trades, result_type="BE"),
            "trade_time_exit_profit_count": _result_count(trades, result_type="TIME_EXIT_PROFIT"),
            "trade_tp1_be_count": _result_count(trades, result_type="TP1_BE"),
            "trade_tp2_count": _result_count(trades, result_type="TP2"),
            "trade_range_mid_hit_rate": _hit_rate(trades, "range_mid_hit"),
            "trade_range_high_hit_rate": _hit_rate(trades, "range_high_hit"),
            "trade_tp1_hit_rate": _hit_rate(trades, "tp1_hit"),
            "trade_tp2_hit_rate": _hit_rate(trades, "tp2_hit"),
        }
        if best_row is not None:
            for source_column, target_column in (
                ("profit_factor", "best_profit_factor"),
                ("pnl_percent", "best_pnl_percent"),
                ("win_rate", "best_win_rate"),
                ("trades_count", "best_trades_count"),
                ("max_dd", "best_max_dd"),
                ("max_drawdown_pct", "best_max_drawdown_pct"),
                ("sl_count", "best_sl_count"),
                ("be_count", "best_be_count"),
                ("time_exit_profit_count", "best_time_exit_profit_count"),
                ("tp1_be_count", "best_tp1_be_count"),
                ("tp2_count", "best_tp2_count"),
                ("ppa_setup_lsb_count", "best_ppa_setup_lsb_count"),
                ("ppa_setup_mbb_count", "best_ppa_setup_mbb_count"),
                ("ppa_median_entry_range_fraction", "best_ppa_median_entry_range_fraction"),
                ("ppa_median_aggression_ratio", "best_ppa_median_aggression_ratio"),
                ("ppa_median_aggression_volume_mult", "best_ppa_median_aggression_volume_mult"),
                ("ppa_median_stop_distance_atr", "best_ppa_median_stop_distance_atr"),
                ("ppa_median_mfe_r", "best_ppa_median_mfe_r"),
                ("ppa_median_mae_r", "best_ppa_median_mae_r"),
                ("ppa_median_holding_bars", "best_ppa_median_holding_bars"),
                ("ppa_range_mid_hit_count", "best_ppa_range_mid_hit_count"),
                ("ppa_range_high_hit_count", "best_ppa_range_high_hit_count"),
                ("ppa_tp1_hit_count", "best_ppa_tp1_hit_count"),
                ("ppa_tp2_hit_count", "best_ppa_tp2_hit_count"),
            ):
                row[target_column] = best_row.get(source_column)
        rows.append(row)
    summary = pd.DataFrame(rows)
    numeric_columns = [
        column
        for column in summary.columns
        if column not in {"timeframe", "strategy_results_dir"}
    ]
    return _coerce_numeric_columns(summary, numeric_columns)


def _build_trade_summary_by_timeframe(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    prepared = _coerce_numeric_columns(
        trades,
        (
            "pnl",
            "pnl_percent",
            "entry_range_fraction",
            "aggression_ratio",
            "aggression_volume_mult",
            "stop_distance_atr",
            "mfe_r",
            "mae_r",
            "holding_bars",
            "range_mid_hit",
            "range_high_hit",
            "tp1_hit",
            "tp2_hit",
        ),
    )
    rows: list[dict[str, Any]] = []
    for timeframe, group in prepared.groupby("timeframe", sort=True):
        pnl = pd.to_numeric(group["pnl"], errors="coerce").fillna(0.0) if "pnl" in group.columns else pd.Series(dtype="float64")
        pnl_percent = pd.to_numeric(group["pnl_percent"], errors="coerce").fillna(0.0) if "pnl_percent" in group.columns else pd.Series(dtype="float64")
        rows.append(
            {
                "timeframe": timeframe,
                "trades_count": int(len(group)),
                "symbols_count": int(group["symbol"].nunique()) if "symbol" in group.columns else 0,
                "pnl_total": float(pnl.sum()),
                "pnl_percent_total": float(pnl_percent.sum()),
                "win_rate": float((pnl > 0).mean()) if not pnl.empty else None,
                "avg_pnl": float(pnl.mean()) if not pnl.empty else None,
                "median_pnl": float(pnl.median()) if not pnl.empty else None,
                "lsb_count": _setup_count(group, setup_type="LSB"),
                "mbb_count": _setup_count(group, setup_type="MBB"),
                "sl_count": _result_count(group, result_type="SL"),
                "be_count": _result_count(group, result_type="BE"),
                "time_exit_profit_count": _result_count(group, result_type="TIME_EXIT_PROFIT"),
                "tp1_be_count": _result_count(group, result_type="TP1_BE"),
                "tp2_count": _result_count(group, result_type="TP2"),
                "median_entry_range_fraction": _median_metric(group, "entry_range_fraction"),
                "median_aggression_ratio": _median_metric(group, "aggression_ratio"),
                "median_aggression_volume_mult": _median_metric(group, "aggression_volume_mult"),
                "median_stop_distance_atr": _median_metric(group, "stop_distance_atr"),
                "median_mfe_r": _median_metric(group, "mfe_r"),
                "median_mae_r": _median_metric(group, "mae_r"),
                "median_holding_bars": _median_metric(group, "holding_bars"),
                "range_mid_hit_rate": _hit_rate(group, "range_mid_hit"),
                "range_high_hit_rate": _hit_rate(group, "range_high_hit"),
                "tp1_hit_rate": _hit_rate(group, "tp1_hit"),
                "tp2_hit_rate": _hit_rate(group, "tp2_hit"),
            }
        )
    summary = pd.DataFrame(rows).sort_values("timeframe").reset_index(drop=True)
    numeric_columns = [column for column in summary.columns if column != "timeframe"]
    return _coerce_numeric_columns(summary, numeric_columns)


def _build_symbol_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "symbol" not in trades.columns:
        return pd.DataFrame()

    prepared = _coerce_numeric_columns(
        trades,
        ("pnl", "pnl_percent", "entry_range_fraction", "aggression_ratio", "range_mid_hit", "range_high_hit"),
    )
    rows: list[dict[str, Any]] = []
    for (timeframe, symbol), group in prepared.groupby(["timeframe", "symbol"], sort=True):
        pnl = pd.to_numeric(group["pnl"], errors="coerce").fillna(0.0) if "pnl" in group.columns else pd.Series(dtype="float64")
        pnl_percent = pd.to_numeric(group["pnl_percent"], errors="coerce").fillna(0.0) if "pnl_percent" in group.columns else pd.Series(dtype="float64")
        rows.append(
            {
                "timeframe": timeframe,
                "symbol": symbol,
                "trades_count": int(len(group)),
                "pnl_total": float(pnl.sum()),
                "pnl_percent_total": float(pnl_percent.sum()),
                "win_rate": float((pnl > 0).mean()) if not pnl.empty else None,
                "lsb_count": _setup_count(group, setup_type="LSB"),
                "mbb_count": _setup_count(group, setup_type="MBB"),
                "range_mid_hit_rate": _hit_rate(group, "range_mid_hit"),
                "range_high_hit_rate": _hit_rate(group, "range_high_hit"),
                "median_entry_range_fraction": _median_metric(group, "entry_range_fraction"),
                "median_aggression_ratio": _median_metric(group, "aggression_ratio"),
            }
        )
    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["timeframe", "pnl_total", "trades_count"], ascending=[True, False, False]).reset_index(drop=True)


def _build_diagnostics_summary_by_timeframe(diagnostics: pd.DataFrame) -> pd.DataFrame:
    if diagnostics.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    numeric_columns = [
        column
        for column in diagnostics.columns
        if column not in {"timeframe", "symbol", "diagnostics_path"}
    ]
    prepared = _coerce_numeric_columns(diagnostics, numeric_columns)
    for timeframe, group in prepared.groupby("timeframe", sort=True):
        row: dict[str, Any] = {
            "timeframe": timeframe,
            "symbols_count": int(len(group)),
        }
        for column in numeric_columns:
            if column not in group.columns:
                continue
            series = pd.to_numeric(group[column], errors="coerce").dropna()
            if series.empty:
                continue
            row[column] = float(series.sum())
        rows.append(row)
    return pd.DataFrame(rows).sort_values("timeframe").reset_index(drop=True)


def _build_context_frame(run_context: dict[str, Any] | None) -> pd.DataFrame:
    if not run_context:
        return pd.DataFrame()
    rows = [{"key": key, "value": json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value} for key, value in run_context.items()]
    return pd.DataFrame(rows)


def _format_markdown_value(column_name: str, value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return str(value)
    numeric = _safe_numeric(value)
    if numeric is not None:
        if column_name.endswith("_rate") or column_name in {"best_win_rate", "win_rate"}:
            return f"{numeric * 100:.2f}%"
        if column_name in _FLOAT_COLUMNS_TO_FORMAT:
            return f"{numeric:.4f}"
        return f"{numeric:.0f}" if float(numeric).is_integer() else f"{numeric:.4f}"
    return str(value)


def _frame_to_markdown(frame: pd.DataFrame, *, columns: Sequence[str] | None = None, limit: int | None = None) -> str:
    if frame.empty:
        return "_No data._"
    subset = frame.copy()
    if columns is not None:
        present_columns = [column for column in columns if column in subset.columns]
        if present_columns:
            subset = subset[present_columns]
    if limit is not None and limit >= 0:
        subset = subset.head(limit)
    headers = [str(column) for column in subset.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for _, row in subset.iterrows():
        lines.append(
            "| "
            + " | ".join(_format_markdown_value(str(column), row[column]) for column in subset.columns)
            + " |"
        )
    return "\n".join(lines)


def _save_placeholder_chart(path: Path, *, title: str, message: str) -> None:
    figure, axis = plt.subplots(figsize=(10, 4))
    axis.axis("off")
    axis.set_title(title)
    axis.text(0.5, 0.5, message, ha="center", va="center", fontsize=12)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _plot_bar_grid(
    frame: pd.DataFrame,
    *,
    metrics: Sequence[tuple[str, str]],
    path: Path,
    title: str,
) -> None:
    if frame.empty or "timeframe" not in frame.columns:
        _save_placeholder_chart(path, title=title, message="No data")
        return
    valid_metrics = [metric for metric in metrics if metric[0] in frame.columns]
    if not valid_metrics:
        _save_placeholder_chart(path, title=title, message="No metrics")
        return
    rows = 2
    cols = 3
    figure, axes = plt.subplots(rows, cols, figsize=(16, 8))
    axes_flat = list(axes.flatten())
    labels = frame["timeframe"].astype(str).tolist()
    colors = ["#31572c", "#4f772d", "#90a955", "#ecf39e", "#386641", "#a7c957"]
    for index, axis in enumerate(axes_flat):
        if index >= len(valid_metrics):
            axis.axis("off")
            continue
        column_name, label = valid_metrics[index]
        values = pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0).tolist()
        if column_name.endswith("_rate") or column_name in {"best_win_rate", "win_rate"}:
            values = [value * 100.0 for value in values]
            label = f"{label} (%)"
        axis.bar(labels, values, color=colors[index % len(colors)])
        axis.set_title(label)
        axis.grid(axis="y", alpha=0.25)
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _plot_stacked_counts(
    frame: pd.DataFrame,
    *,
    columns: Sequence[tuple[str, str, str]],
    path: Path,
    title: str,
) -> None:
    if frame.empty or "timeframe" not in frame.columns:
        _save_placeholder_chart(path, title=title, message="No data")
        return
    present_columns = [item for item in columns if item[0] in frame.columns]
    if not present_columns:
        _save_placeholder_chart(path, title=title, message="No metrics")
        return
    labels = frame["timeframe"].astype(str).tolist()
    bottoms = [0.0] * len(labels)
    figure, axis = plt.subplots(figsize=(12, 6))
    for column_name, legend_label, color in present_columns:
        values = pd.to_numeric(frame[column_name], errors="coerce").fillna(0.0).tolist()
        axis.bar(labels, values, bottom=bottoms, label=legend_label, color=color)
        bottoms = [bottom + value for bottom, value in zip(bottoms, values, strict=False)]
    axis.set_title(title)
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _write_research_report(
    *,
    output_dir: Path,
    run_context: dict[str, Any] | None,
    timeframe_summary: pd.DataFrame,
    trade_summary: pd.DataFrame,
    diagnostics_summary: pd.DataFrame,
    symbol_summary: pd.DataFrame,
    charts_dir: Path,
) -> Path:
    lines: list[str] = [
        "# Post Pump Absorption Research",
        "",
        "## Run Context",
        "",
        _frame_to_markdown(_build_context_frame(run_context)),
        "",
        "## Timeframe Summary",
        "",
        _frame_to_markdown(
            timeframe_summary,
            columns=(
                "timeframe",
                "best_profit_factor",
                "best_pnl_percent",
                "best_win_rate",
                "best_trades_count",
                "best_max_drawdown_pct",
                "trade_rows_count",
                "diagnostics_missing_taker_data",
            ),
        ),
        "",
        "## Trade Summary",
        "",
        _frame_to_markdown(
            trade_summary,
            columns=(
                "timeframe",
                "trades_count",
                "symbols_count",
                "pnl_total",
                "win_rate",
                "lsb_count",
                "mbb_count",
                "range_mid_hit_rate",
                "range_high_hit_rate",
                "tp2_hit_rate",
            ),
        ),
        "",
        "## Diagnostics Summary",
        "",
        _frame_to_markdown(
            diagnostics_summary,
            columns=(
                "timeframe",
                "symbols_count",
                "pumps_found",
                "range_candidates",
                "lower_zone_hits",
                "aggression_hits",
                "lsb_hits",
                "mbb_hits",
                "trades_generated",
                "reentries_generated",
                "missing_taker_data",
            ),
        ),
        "",
        "## Top Symbols",
        "",
    ]
    if symbol_summary.empty:
        lines.append("_No symbol-level trades._")
    else:
        for timeframe in symbol_summary["timeframe"].astype(str).drop_duplicates().tolist():
            lines.extend(
                [
                    f"### {timeframe}",
                    "",
                    _frame_to_markdown(
                        symbol_summary[symbol_summary["timeframe"].astype(str) == timeframe],
                        columns=(
                            "symbol",
                            "trades_count",
                            "pnl_total",
                            "win_rate",
                            "lsb_count",
                            "mbb_count",
                            "range_mid_hit_rate",
                            "range_high_hit_rate",
                        ),
                        limit=10,
                    ),
                    "",
                ]
            )
    lines.extend(
        [
            "## Artifacts",
            "",
            f"- summary csv: `{output_dir / 'summary_by_timeframe.csv'}`",
            f"- combined results: `{output_dir / 'combined_results.csv'}`",
            f"- all trades: `{output_dir / 'all_best_trades.csv'}`",
            f"- symbol summary: `{output_dir / 'symbol_summary.csv'}`",
            f"- diagnostics by symbol: `{output_dir / 'diagnostics_by_symbol.csv'}`",
            f"- diagnostics summary: `{output_dir / 'diagnostics_summary_by_timeframe.csv'}`",
            f"- charts dir: `{charts_dir}`",
            "",
        ]
    )
    report_path = output_dir / "research_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_post_pump_absorption_research_artifacts(
    *,
    output_dir: Path,
    runs: Sequence[PostPumpAbsorptionResearchRun],
    run_context: dict[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Path]:
    active_logger = logger or module_logger
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    combined_results_parts = [
        run.results.assign(timeframe=run.timeframe.value, strategy_results_dir=str(run.strategy_results_dir))
        for run in runs
        if not run.results.empty
    ]
    combined_trades_parts = [run.trades for run in runs if not run.trades.empty]
    combined_diagnostics_parts = [run.diagnostics for run in runs if not run.diagnostics.empty]

    combined_results = (
        pd.concat(combined_results_parts, ignore_index=True)
        if combined_results_parts
        else pd.DataFrame()
    )
    combined_trades = (
        pd.concat(combined_trades_parts, ignore_index=True)
        if combined_trades_parts
        else pd.DataFrame()
    )
    combined_diagnostics = (
        pd.concat(combined_diagnostics_parts, ignore_index=True)
        if combined_diagnostics_parts
        else pd.DataFrame()
    )

    timeframe_summary = _build_timeframe_summary(runs)
    trade_summary = _build_trade_summary_by_timeframe(combined_trades)
    symbol_summary = _build_symbol_summary(combined_trades)
    diagnostics_summary = _build_diagnostics_summary_by_timeframe(combined_diagnostics)

    combined_results_path = output_dir / "combined_results.csv"
    combined_results.to_csv(combined_results_path, index=False)

    timeframe_summary_path = output_dir / "summary_by_timeframe.csv"
    timeframe_summary.to_csv(timeframe_summary_path, index=False)

    timeframe_summary_json_path = output_dir / "summary_by_timeframe.json"
    timeframe_summary_json_path.write_text(
        json.dumps(timeframe_summary.to_dict(orient="records"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    trade_summary_path = output_dir / "trade_summary_by_timeframe.csv"
    trade_summary.to_csv(trade_summary_path, index=False)

    all_trades_path = output_dir / "all_best_trades.csv"
    combined_trades.to_csv(all_trades_path, index=False)

    symbol_summary_path = output_dir / "symbol_summary.csv"
    symbol_summary.to_csv(symbol_summary_path, index=False)

    diagnostics_by_symbol_path = output_dir / "diagnostics_by_symbol.csv"
    combined_diagnostics.to_csv(diagnostics_by_symbol_path, index=False)

    diagnostics_summary_path = output_dir / "diagnostics_summary_by_timeframe.csv"
    diagnostics_summary.to_csv(diagnostics_summary_path, index=False)

    run_context_path = output_dir / "research_context.json"
    run_context_path.write_text(json.dumps(run_context or {}, ensure_ascii=False, indent=2), encoding="utf-8")

    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    _plot_bar_grid(
        timeframe_summary,
        metrics=(
            ("best_profit_factor", "Best Profit Factor"),
            ("best_pnl_percent", "Best PnL"),
            ("best_win_rate", "Best Win Rate"),
            ("best_trades_count", "Best Trades Count"),
            ("best_max_drawdown_pct", "Best Max DD"),
            ("trade_rows_count", "Detailed Trades"),
        ),
        path=charts_dir / "performance_by_timeframe.png",
        title="Performance by Timeframe",
    )
    _plot_stacked_counts(
        trade_summary,
        columns=(
            ("sl_count", "SL", "#bc4749"),
            ("be_count", "BE", "#f2e8cf"),
            ("time_exit_profit_count", "Time Exit Profit", "#6a994e"),
            ("tp1_be_count", "TP1 BE", "#a7c957"),
            ("tp2_count", "TP2", "#386641"),
        ),
        path=charts_dir / "outcomes_by_timeframe.png",
        title="Outcome Mix by Timeframe",
    )
    _plot_stacked_counts(
        trade_summary,
        columns=(
            ("lsb_count", "LSB", "#386641"),
            ("mbb_count", "MBB", "#6a994e"),
        ),
        path=charts_dir / "setups_by_timeframe.png",
        title="Setup Mix by Timeframe",
    )
    _plot_bar_grid(
        trade_summary,
        metrics=(
            ("median_entry_range_fraction", "Median Entry Range Fraction"),
            ("median_aggression_ratio", "Median Aggression Ratio"),
            ("median_aggression_volume_mult", "Median Aggression Volume Mult"),
            ("median_stop_distance_atr", "Median Stop ATR"),
            ("median_mfe_r", "Median MFE R"),
            ("median_mae_r", "Median MAE R"),
        ),
        path=charts_dir / "quality_medians_by_timeframe.png",
        title="Quality Medians by Timeframe",
    )
    _plot_bar_grid(
        trade_summary,
        metrics=(
            ("range_mid_hit_rate", "Range Mid Hit Rate"),
            ("range_high_hit_rate", "Range High Hit Rate"),
            ("tp1_hit_rate", "TP1 Hit Rate"),
            ("tp2_hit_rate", "TP2 Hit Rate"),
            ("win_rate", "Trade Win Rate"),
            ("median_holding_bars", "Median Holding Bars"),
        ),
        path=charts_dir / "hit_rates_by_timeframe.png",
        title="Hit Rates by Timeframe",
    )

    report_path = _write_research_report(
        output_dir=output_dir,
        run_context=run_context,
        timeframe_summary=timeframe_summary,
        trade_summary=trade_summary,
        diagnostics_summary=diagnostics_summary,
        symbol_summary=symbol_summary,
        charts_dir=charts_dir,
    )

    active_logger.info(
        "post-pump research artifacts saved: output_dir=%s timeframes=%s",
        output_dir,
        ",".join(run.timeframe.value for run in runs),
    )
    return {
        "combined_results": combined_results_path,
        "timeframe_summary_csv": timeframe_summary_path,
        "timeframe_summary_json": timeframe_summary_json_path,
        "trade_summary": trade_summary_path,
        "all_trades": all_trades_path,
        "symbol_summary": symbol_summary_path,
        "diagnostics_by_symbol": diagnostics_by_symbol_path,
        "diagnostics_summary": diagnostics_summary_path,
        "run_context": run_context_path,
        "report": report_path,
        "charts_dir": charts_dir,
    }
