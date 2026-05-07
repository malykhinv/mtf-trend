"""Focused review for known runner dates from anomaly-continuation lab output."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True, slots=True)
class RunnerTarget:
    symbol: str
    local_date: str


def parse_runner_target(raw: str) -> RunnerTarget:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("target must be SYMBOL=YYYY-MM-DD")
    symbol, local_date = raw.split("=", 1)
    symbol = symbol.strip()
    local_date = local_date.strip()
    if not symbol or not local_date:
        raise argparse.ArgumentTypeError("target must be SYMBOL=YYYY-MM-DD")
    if "/" not in symbol:
        symbol = f"{symbol}/USDT:USDT"
    return RunnerTarget(symbol=symbol, local_date=local_date)


def build_runner_review(
    lab_frame: pd.DataFrame,
    *,
    targets: list[RunnerTarget],
    local_utc_offset_hours: int = 2,
    top_n: int = 8,
) -> pd.DataFrame:
    if lab_frame.empty:
        return pd.DataFrame()
    required = {"symbol", "timestamp_utc", "decision_timestamp_utc", "future_ret_high_after_decision"}
    missing = required.difference(lab_frame.columns)
    if missing:
        raise ValueError(f"lab_frame missing required columns: {sorted(missing)}")

    frame = lab_frame.copy()
    timestamp = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    decision_timestamp = pd.to_datetime(frame["decision_timestamp_utc"], utc=True, errors="coerce")
    frame["target_local_date"] = (timestamp + pd.Timedelta(hours=local_utc_offset_hours)).dt.strftime("%Y-%m-%d")
    frame["timestamp_local"] = (timestamp + pd.Timedelta(hours=local_utc_offset_hours)).dt.strftime("%Y-%m-%dT%H:%M:%S")
    frame["decision_timestamp_local"] = (
        decision_timestamp + pd.Timedelta(hours=local_utc_offset_hours)
    ).dt.strftime("%Y-%m-%dT%H:%M:%S")

    review_rows: list[pd.DataFrame] = []
    for target in targets:
        subset = frame.loc[
            frame["symbol"].eq(target.symbol)
            & frame["target_local_date"].eq(target.local_date)
        ].copy()
        if subset.empty:
            review_rows.append(
                pd.DataFrame(
                    [
                        {
                            "target_symbol": target.symbol,
                            "target_local_date": target.local_date,
                            "status": "no_candidates",
                        }
                    ]
                )
            )
            continue
        subset.sort_values(
            ["future_ret_high_after_decision", "hold_count_next_n_candles", "start_verticality_score"],
            ascending=[False, False, False],
            inplace=True,
        )
        subset = subset.head(top_n).copy()
        subset.drop(columns=["target_local_date"], inplace=True)
        subset.insert(0, "target_symbol", target.symbol)
        subset.insert(1, "target_local_date", target.local_date)
        subset.insert(2, "status", "ranked_by_future_for_review")
        subset.insert(3, "future_rank_within_target_date", range(1, len(subset) + 1))
        review_rows.append(subset)
    if not review_rows:
        return pd.DataFrame()
    result = pd.concat(review_rows, ignore_index=True, sort=False)
    preferred_columns = [
        "target_symbol",
        "target_local_date",
        "status",
        "future_rank_within_target_date",
        "symbol",
        "timestamp_utc",
        "timestamp_local",
        "decision_timestamp_utc",
        "decision_timestamp_local",
        "confirmation_candles",
        "start_open",
        "start_high",
        "start_low",
        "start_close",
        "decision_close",
        "start_trade_count",
        "start_trade_ratio",
        "start_quote_ratio",
        "hold_count_next_n_candles",
        "hold_ratio_next_n_candles",
        "next_n_trade_decay",
        "next_n_quote_decay",
        "price_retention_next_n",
        "midpoint_lost_next_n",
        "new_high_count_next_n",
        "start_verticality_score",
        "start_verticality_path_efficiency",
        "start_verticality_slope_pct_per_candle",
        "start_verticality_max_retrace_fraction",
        "future_ret_high_after_decision",
        "future_dd_low_after_decision",
        "outcome_label",
    ]
    columns = [column for column in preferred_columns if column in result.columns]
    return result.loc[:, columns]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--target", action="append", type=parse_runner_target, required=True)
    parser.add_argument("--local-utc-offset-hours", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    lab_frame = pd.read_csv(args.lab_csv)
    review = build_runner_review(
        lab_frame,
        targets=args.target,
        local_utc_offset_hours=args.local_utc_offset_hours,
        top_n=args.top_n,
    )
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(args.output_csv, index=False)
    print(f"wrote runner review to {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
