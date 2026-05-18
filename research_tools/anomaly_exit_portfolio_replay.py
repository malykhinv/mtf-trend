"""Portfolio-level exit replay for saved anomaly_lab trade artifacts.

This tool intentionally works from existing CSV artifacts. It does not rerun
signal generation and does not change live trading logic.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_MODELS = ("current", "tp1_25_rest_1p5r", "tp1_50_rest_1p5r", "full_tp1")
DEFAULT_TF_PRIORITY = ("1m/15s", "1m/5s", "5m/30s")
KEY_COLUMNS = ["symbol", "setup_timeframe", "entry_timeframe", "decision_timestamp_ms"]


@dataclass(frozen=True, slots=True)
class ExitModel:
    name: str
    tp1_fraction: float
    rest_target_r: float | None
    fallback: str


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run no-overlap portfolio replay on saved anomaly_lab trades.",
    )
    parser.add_argument("--lab-dir", type=Path, required=True, help="Path to .output/results/anomaly_lab")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for replay CSV artifacts")
    parser.add_argument(
        "--families",
        default="live_priority",
        help="Comma-separated pump_category_family values to include, e.g. live_priority or live_priority,discovery.",
    )
    parser.add_argument(
        "--context-parity",
        default="",
        help="Optional context_parity_status filter after joining anomaly_context_parity_report.csv, e.g. ok.",
    )
    parser.add_argument("--max-positions", type=int, default=1, help="Maximum concurrent portfolio positions.")
    parser.add_argument(
        "--same-symbol-overlap",
        choices=("reject", "allow"),
        default="reject",
        help="Reject a new trade while the same symbol already has an open replay position.",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help=(
            "Comma-separated exit models. Supported: current, full_tp1, "
            "tp1_25_rest_1p5r, tp1_50_rest_1p5r, tp1_75_rest_1p5r, "
            "and generic tp1_<pct>_rest_<r>r, optionally ending _be."
        ),
    )
    parser.add_argument(
        "--tf-priority",
        default=",".join(DEFAULT_TF_PRIORITY),
        help="Comma-separated TF priority for same-timestamp conflicts.",
    )
    return parser.parse_args(argv)


def _tf_pair_from_dir_name(name: str) -> str:
    parts = name.split("_", 1)
    if len(parts) != 2:
        return name
    return f"{parts[0]}/{parts[1]}"


def _tf_dir_from_pair(pair: str) -> str:
    return pair.replace("/", "_")


def load_lab_trades(lab_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    runs_path = lab_dir / "anomaly_lab_timeframe_runs.csv"
    if runs_path.exists():
        runs = pd.read_csv(runs_path)
        pairs = [
            (str(row["setup_timeframe"]), str(row["entry_timeframe"]))
            for _, row in runs.iterrows()
            if str(row.get("setup_timeframe", "")) and str(row.get("entry_timeframe", ""))
        ]
    else:
        pairs = []
        for child in sorted(lab_dir.iterdir()):
            if child.is_dir() and (child / "anomaly_trades.csv").exists():
                setup, entry = _tf_pair_from_dir_name(child.name).split("/", 1)
                pairs.append((setup, entry))
    for setup_tf, entry_tf in pairs:
        tf_pair = f"{setup_tf}/{entry_tf}"
        run_dir = lab_dir / _tf_dir_from_pair(tf_pair)
        trades_path = run_dir / "anomaly_trades.csv"
        if not trades_path.exists():
            continue
        frame = pd.read_csv(trades_path)
        frame["tf_set"] = tf_pair
        frame["source_trade_file"] = str(trades_path)
        parity_path = run_dir / "anomaly_context_parity_report.csv"
        if parity_path.exists():
            parity = pd.read_csv(parity_path)
            available = [column for column in KEY_COLUMNS + ["context_parity_status"] if column in parity.columns]
            if set(KEY_COLUMNS).issubset(available):
                parity = parity.loc[:, available].drop_duplicates(KEY_COLUMNS)
                frame = frame.merge(parity, on=KEY_COLUMNS, how="left")
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No anomaly_trades.csv files found under {lab_dir}")
    result = pd.concat(frames, ignore_index=True)
    for column in (
        "decision_timestamp_ms",
        "entry_timestamp_ms",
        "exit_timestamp_ms",
        "net_return",
        "entry_price",
        "tp1_fill_price",
        "initial_risk_pct",
        "mfe_pct",
        "fee_rate",
        "pump_category_rank",
    ):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def parse_exit_model(raw: str) -> ExitModel:
    name = raw.strip()
    if name == "current":
        return ExitModel(name=name, tp1_fraction=0.5, rest_target_r=None, fallback="current")
    if name in {"full_tp1", "tp1_100", "tp1_100_rest_1r"}:
        return ExitModel(name=name, tp1_fraction=1.0, rest_target_r=1.0, fallback="current")
    if not name.startswith("tp1_") or "_rest_" not in name:
        raise ValueError(f"Unsupported model: {raw}")
    prefix, rest = name.split("_rest_", 1)
    pct_text = prefix.removeprefix("tp1_")
    try:
        tp1_fraction = float(pct_text) / 100.0
    except ValueError as exc:
        raise ValueError(f"Invalid TP1 fraction in model: {raw}") from exc
    fallback = "be_after_tp1" if rest.endswith("_be") else "current"
    rest = rest.removesuffix("_be")
    rest = rest.replace("p", ".")
    if not rest.endswith("r"):
        raise ValueError(f"Invalid rest target in model: {raw}")
    target_r = float(rest[:-1])
    if not 0.0 <= tp1_fraction <= 1.0:
        raise ValueError(f"TP1 fraction must be 0..100 in model: {raw}")
    return ExitModel(name=name, tp1_fraction=tp1_fraction, rest_target_r=target_r, fallback=fallback)


def compute_model_return(trades: pd.DataFrame, model: ExitModel) -> pd.Series:
    current = trades["net_return"].astype(float)
    if model.name == "current":
        return current
    fee = trades.get("fee_rate", pd.Series(0.0004, index=trades.index)).fillna(0.0004).astype(float)
    entry = trades.get("entry_price", pd.Series(np.nan, index=trades.index)).astype(float)
    tp1_fill = trades.get("tp1_fill_price", pd.Series(np.nan, index=trades.index)).astype(float)
    initial_risk_pct = trades.get("initial_risk_pct", pd.Series(np.nan, index=trades.index)).astype(float)
    mfe_pct = trades.get("mfe_pct", pd.Series(np.nan, index=trades.index)).astype(float)
    tp1_hit = trades.get("tp1_hit", pd.Series(False, index=trades.index)).astype(str).str.lower().isin(
        {"true", "1", "yes"}
    )
    valid_tp1 = tp1_hit & tp1_fill.notna() & entry.gt(0.0)
    tp1_leg = (tp1_fill / entry - 1.0) - 2.0 * fee
    current_runner_leg = pd.Series(
        np.where(valid_tp1, (current - 0.5 * tp1_leg) / 0.5, current),
        index=trades.index,
    )
    if model.rest_target_r is None:
        rest_leg = current_runner_leg
    else:
        target_hit = mfe_pct.gt(float(model.rest_target_r) * initial_risk_pct) & initial_risk_pct.notna()
        target_leg = float(model.rest_target_r) * initial_risk_pct - 2.0 * fee
        if model.fallback == "be_after_tp1":
            fallback_leg = -2.0 * fee
        else:
            fallback_leg = current_runner_leg
        rest_leg = pd.Series(np.where(target_hit, target_leg, fallback_leg), index=trades.index)
    value = pd.Series(
        np.where(valid_tp1, float(model.tp1_fraction) * tp1_leg + (1.0 - float(model.tp1_fraction)) * rest_leg, current),
        index=trades.index,
    )
    return value.astype(float)


def filter_trades(trades: pd.DataFrame, *, families: set[str], context_parity: str) -> pd.DataFrame:
    result = trades.copy()
    result = result.loc[result.get("status", "").astype(str).eq("closed")].copy()
    if families:
        result = result.loc[result.get("pump_category_family", "").astype(str).isin(families)].copy()
    if context_parity:
        if "context_parity_status" not in result.columns:
            result = result.iloc[0:0].copy()
        else:
            result = result.loc[result["context_parity_status"].astype(str).eq(context_parity)].copy()
    result.dropna(subset=["entry_timestamp_ms", "exit_timestamp_ms"], inplace=True)
    return result


def replay_portfolio(
    trades: pd.DataFrame,
    *,
    model: ExitModel,
    max_positions: int,
    same_symbol_overlap: str,
    tf_priority: dict[str, int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = trades.copy()
    work["model_net_return"] = compute_model_return(work, model)
    work["tf_priority"] = work["tf_set"].map(tf_priority).fillna(10_000).astype(int)
    if "pump_category_rank" not in work.columns:
        work["pump_category_rank"] = 10_000
    work["pump_category_rank"] = work["pump_category_rank"].fillna(10_000)
    work.sort_values(
        ["entry_timestamp_ms", "pump_category_rank", "tf_priority", "symbol", "decision_timestamp_ms"],
        inplace=True,
    )
    accepted_rows: list[dict[str, object]] = []
    reviewed_rows: list[dict[str, object]] = []
    open_positions: list[dict[str, object]] = []
    for _, row in work.iterrows():
        entry_ts = int(row["entry_timestamp_ms"])
        exit_ts = int(row["exit_timestamp_ms"])
        symbol = str(row.get("symbol", ""))
        open_positions = [pos for pos in open_positions if int(pos["exit_timestamp_ms"]) > entry_ts]
        reject_reason = ""
        if len(open_positions) >= int(max_positions):
            reject_reason = "max_positions_active"
        elif same_symbol_overlap == "reject" and any(str(pos["symbol"]) == symbol for pos in open_positions):
            reject_reason = "same_symbol_position_active"
        accepted = reject_reason == ""
        replay_row = row.to_dict()
        replay_row["replay_model"] = model.name
        replay_row["replay_accepted"] = accepted
        replay_row["replay_reject_reason"] = reject_reason
        replay_row["replay_open_positions_before"] = len(open_positions)
        replay_row["replay_net_return"] = float(row["model_net_return"])
        reviewed_rows.append(replay_row)
        if accepted:
            open_positions.append({"symbol": symbol, "exit_timestamp_ms": exit_ts})
            accepted_rows.append(replay_row)
    return pd.DataFrame(accepted_rows), pd.DataFrame(reviewed_rows)


def summarize_returns(values: pd.Series) -> dict[str, object]:
    returns = pd.to_numeric(values, errors="coerce").dropna()
    if returns.empty:
        return {
            "trades": 0,
            "win_rate": math.nan,
            "avg_return": math.nan,
            "median_return": math.nan,
            "sum_return": 0.0,
            "profit_factor": math.nan,
            "top5_share": math.nan,
            "top15_share": math.nan,
            "break_even_top_n": 0,
            "break_even_top_pct": math.nan,
        }
    total = float(returns.sum())
    positive = float(returns.loc[returns > 0].sum())
    negative = float(returns.loc[returns < 0].sum())
    top = returns.sort_values(ascending=False)
    remaining = total
    cut_n = 0
    if total > 0:
        for value in top:
            cut_n += 1
            remaining -= float(value)
            if remaining <= 0:
                break
    return {
        "trades": int(len(returns)),
        "win_rate": float((returns > 0).mean()),
        "avg_return": float(returns.mean()),
        "median_return": float(returns.median()),
        "sum_return": total,
        "profit_factor": positive / abs(negative) if negative < 0 else math.inf,
        "top5_share": float(top.head(5).sum() / total) if total else math.nan,
        "top15_share": float(top.head(15).sum() / total) if total else math.nan,
        "break_even_top_n": int(cut_n),
        "break_even_top_pct": float(cut_n / len(returns)),
    }


def build_daily(accepted: pd.DataFrame) -> pd.DataFrame:
    if accepted.empty:
        return pd.DataFrame(columns=["date", "trades", "sum_return", "equity_return", "drawdown"])
    frame = accepted.copy()
    frame["entry_dt"] = pd.to_datetime(frame["entry_timestamp_utc"], utc=True, errors="coerce")
    frame["date"] = frame["entry_dt"].dt.date.astype(str)
    daily = frame.groupby("date", as_index=False).agg(
        trades=("replay_net_return", "size"),
        sum_return=("replay_net_return", "sum"),
    )
    daily["equity_return"] = daily["sum_return"].cumsum()
    daily["drawdown"] = daily["equity_return"] - daily["equity_return"].cummax()
    return daily


def write_outputs(
    *,
    output_dir: Path,
    model_results: list[tuple[ExitModel, pd.DataFrame, pd.DataFrame]],
    config: dict[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, object]] = []
    tail_rows: list[dict[str, object]] = []
    accepted_frames: list[pd.DataFrame] = []
    reviewed_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    for model, accepted, reviewed in model_results:
        metrics = summarize_returns(accepted.get("replay_net_return", pd.Series(dtype=float)))
        skipped = int((~reviewed.get("replay_accepted", pd.Series(dtype=bool)).astype(bool)).sum()) if not reviewed.empty else 0
        daily = build_daily(accepted)
        max_daily_drawdown = float(daily["drawdown"].min()) if not daily.empty else math.nan
        summary_rows.append(
            {
                "model": model.name,
                **config,
                **metrics,
                "skipped_trades": skipped,
                "max_daily_drawdown": max_daily_drawdown,
            }
        )
        if not accepted.empty:
            top = accepted.sort_values("replay_net_return", ascending=False).copy()
            top["top_rank"] = range(1, len(top) + 1)
            tail_rows.append(top.head(50))
            accepted_frames.append(accepted)
        if not reviewed.empty:
            reviewed_frames.append(reviewed)
        if not daily.empty:
            daily["model"] = model.name
            daily_frames.append(daily)
    pd.DataFrame(summary_rows).to_csv(output_dir / "portfolio_exit_replay_summary.csv", index=False)
    if accepted_frames:
        pd.concat(accepted_frames, ignore_index=True).to_csv(output_dir / "portfolio_exit_replay_trades.csv", index=False)
    else:
        pd.DataFrame().to_csv(output_dir / "portfolio_exit_replay_trades.csv", index=False)
    if reviewed_frames:
        pd.concat(reviewed_frames, ignore_index=True).to_csv(output_dir / "portfolio_exit_replay_reviewed.csv", index=False)
    else:
        pd.DataFrame().to_csv(output_dir / "portfolio_exit_replay_reviewed.csv", index=False)
    if daily_frames:
        pd.concat(daily_frames, ignore_index=True).to_csv(output_dir / "portfolio_exit_replay_daily.csv", index=False)
    else:
        pd.DataFrame().to_csv(output_dir / "portfolio_exit_replay_daily.csv", index=False)
    if tail_rows:
        pd.concat(tail_rows, ignore_index=True).to_csv(output_dir / "portfolio_exit_replay_top_tail.csv", index=False)
    else:
        pd.DataFrame().to_csv(output_dir / "portfolio_exit_replay_top_tail.csv", index=False)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    families = {item.strip() for item in str(args.families).split(",") if item.strip()}
    model_names = [item.strip() for item in str(args.models).split(",") if item.strip()]
    models = [parse_exit_model(name) for name in model_names]
    tf_priority = {
        item.strip(): index
        for index, item in enumerate(str(args.tf_priority).split(","))
        if item.strip()
    }
    trades = load_lab_trades(args.lab_dir)
    filtered = filter_trades(trades, families=families, context_parity=str(args.context_parity or ""))
    if filtered.empty:
        raise SystemExit("No trades left after filters")
    model_results: list[tuple[ExitModel, pd.DataFrame, pd.DataFrame]] = []
    for model in models:
        accepted, reviewed = replay_portfolio(
            filtered,
            model=model,
            max_positions=int(args.max_positions),
            same_symbol_overlap=str(args.same_symbol_overlap),
            tf_priority=tf_priority,
        )
        model_results.append((model, accepted, reviewed))
    write_outputs(
        output_dir=args.output_dir,
        model_results=model_results,
        config={
            "families": ",".join(sorted(families)),
            "context_parity": str(args.context_parity or ""),
            "max_positions": int(args.max_positions),
            "same_symbol_overlap": str(args.same_symbol_overlap),
            "tf_priority": ",".join(tf_priority.keys()),
        },
    )
    summary = pd.read_csv(args.output_dir / "portfolio_exit_replay_summary.csv")
    print(summary.to_string(index=False))
    print(f"wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
