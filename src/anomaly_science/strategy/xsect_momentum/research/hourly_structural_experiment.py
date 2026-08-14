"""Frozen v2 1h structural-risk experiment on the wide IS quality plateau.

Protocol: docs/strategies/xsect_momentum_1h_structural_risk_protocol_v2.md
Run with the multi-regime daily panel and the frozen 2026-01-01 OOS boundary.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly_science.strategy.xsect_momentum.research import backtest as bt
from anomaly_science.strategy.xsect_momentum.research import panel as pn
from anomaly_science.strategy.xsect_momentum.research import score as sc
from anomaly_science.strategy.xsect_momentum.research.gates import (
    block_bootstrap_sharpe,
    evaluate_gates,
)
from anomaly_science.strategy.xsect_momentum.research.hourly_structural import (
    ExitVariant,
    HourlyBarStore,
    HourlyRunResult,
    StructuralAnchorSpec,
    run_hourly_backtest,
)
from anomaly_science.strategy.xsect_momentum.research.policy import PRIMARY
from anomaly_science.strategy.xsect_momentum.research.run_strategy import (
    _shuffle_within_date,
)


OUT = Path(".output/results/xsect_momentum")
RUN_OUT = OUT / "hourly_structural_runs_v2"
HOURLY_ROOT = Path(".output/market/binance_vision/um_futures/klines_1h")
IS_END = pd.Timestamp("2026-01-01", tz="UTC")
CONFIGS = [
    (universe_n, k, reb)
    for universe_n in (75, 100, 200)
    for k in (15, 20)
    for reb in (3, 7, 14)
]
VARIANTS: tuple[ExitVariant, ...] = (
    "symmetric_structural",
    "short_structural",
)
SHUFFLE_SEEDS = (11, 29, 47)
ANCHOR_SPEC = StructuralAnchorSpec(left_hours=6, right_hours=6, lookback_hours=168)
PRIMARY_KEY = (100, 20, 7)
SUMMARY_PATH = OUT / "hourly_structural_summary_v2.parquet"


def _summary_row(
    run_id: str,
    control: str,
    seed: int | None,
    k: int,
    rebalance_days: int,
    variant: ExitVariant,
    policy,
    result: HourlyRunResult,
) -> dict:
    gates = evaluate_gates(result)  # structural interface matches RunResult
    boot_med, boot_q05 = block_bootstrap_sharpe(
        result.daily_ret, block=rebalance_days, n=800
    )
    values = gates.values
    return {
        "run_id": run_id,
        "control": control,
        "seed": seed,
        "k": k,
        "rebalance_days": rebalance_days,
        "universe_n": policy.universe_n,
        "max_weight": policy.max_weight,
        "exit_variant": variant,
        "config_id": policy.config_id(),
        **values,
        "boot_med_sharpe": boot_med,
        "boot_q05_sharpe": boot_q05,
        **{
            f"ret_{year}": float(
                (1.0 + result.daily_ret[result.daily_ret.index.year == year]).prod() - 1.0
            )
            for year in (2023, 2024, 2025)
        },
        "mean_turnover": float(result.turnover.mean()),
        "gates": "".join("P" if gates.passes[g] else "." for g in sorted(gates.passes)),
        "all_gates_pass": gates.all_pass,
        **result.meta,
        "anchor_left_hours": ANCHOR_SPEC.left_hours,
        "anchor_right_hours": ANCHOR_SPEC.right_hours,
        "anchor_lookback_hours": ANCHOR_SPEC.lookback_hours,
        "is_start": result.daily_ret.index.min(),
        "is_end": result.daily_ret.index.max(),
        "hourly_file_count": len(list(HOURLY_ROOT.glob("*.parquet"))),
    }


def _write_run(run_id: str, result: HourlyRunResult) -> None:
    RUN_OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": result.daily_ret.index,
        "daily_return": result.daily_ret.to_numpy(),
        "equity": result.equity.reindex(result.daily_ret.index).to_numpy(),
    }).to_parquet(RUN_OUT / f"{run_id}_daily.parquet")
    result.trades.to_parquet(RUN_OUT / f"{run_id}_trades.parquet")


def _checkpoint(rows: list[dict]) -> None:
    pd.DataFrame(rows).to_parquet(SUMMARY_PATH)


def _run_one(
    rows: list[dict],
    panel: pd.DataFrame,
    close: pd.DataFrame,
    quote_volume: pd.DataFrame,
    universe_mask: pd.DataFrame,
    scores: pd.DataFrame,
    store: HourlyBarStore,
    universe_n: int,
    k: int,
    rebalance_days: int,
    variant: ExitVariant,
    control: str = "real",
    seed: int | None = None,
) -> HourlyRunResult:
    policy = replace(
        PRIMARY,
        top_k=k,
        n_short=k,
        rebalance_days=rebalance_days,
        universe_n=universe_n,
        max_weight=0.10,
        direction_mode="market_neutral",
        stop_loss_pct=0.0,
    )
    run_id = f"{control}_u{universe_n}_k{k}_reb{rebalance_days}_{variant}"
    if seed is not None:
        run_id += f"_seed{seed}"
    print(f"running {run_id} ...", flush=True)
    result = run_hourly_backtest(
        panel,
        close,
        quote_volume,
        universe_mask,
        scores,
        bt.sel_market_neutral,
        policy,
        store,
        variant,
        ANCHOR_SPEC,
        seed=0 if seed is None else seed,
    )
    _write_run(run_id, result)
    rows.append(
        _summary_row(
            run_id,
            control,
            seed,
            k,
            rebalance_days,
            variant,
            policy,
            result,
        )
    )
    _checkpoint(rows)
    print(
        f"  ret={rows[-1]['total_return']:+.3f} sharpe={rows[-1]['sharpe']:.3f} "
        f"DD={rows[-1]['g7_max_drawdown']:.3f} worst={rows[-1]['g8_worst_day']:.3f} "
        f"coverage={result.meta['anchor_coverage']}",
        flush=True,
    )
    return result


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RUN_OUT.mkdir(parents=True, exist_ok=True)
    panel = pn.load_panel(
        is_only=True,
        source_glob=str(Path(pn.KLINES_DAILY_PANEL)),
        is_end=IS_END,
    )
    close = pn.pivot(panel, "close")
    open_daily = pn.pivot(panel, "open")
    quote_volume = pn.pivot(panel, "quote_volume")
    cache: dict[int, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for universe_n in (75, 100, 200):
        universe_mask = pn.build_universe_mask(
            panel,
            quote_volume,
            universe_n,
            PRIMARY.liquidity_lb,
            PRIMARY.min_age_days,
        )
        quality_score = sc.quality_score(
            panel,
            replace(PRIMARY, universe_n=universe_n),
            universe_mask,
        )
        cache[universe_n] = (universe_mask, quality_score)
    store = HourlyBarStore(HOURLY_ROOT, IS_END)
    rows: list[dict] = (
        pd.read_parquet(SUMMARY_PATH).to_dict("records")
        if SUMMARY_PATH.exists()
        else []
    )

    def find_row(run_id: str) -> dict | None:
        return next((row for row in rows if row["run_id"] == run_id), None)

    # Phase 1: the hourly path must reproduce the corrected daily no-stop path.
    for universe_n, k, rebalance_days in CONFIGS:
        universe_mask, quality_score = cache[universe_n]
        run_id = f"real_u{universe_n}_k{k}_reb{rebalance_days}_no_stop"
        existing = find_row(run_id)
        if existing is not None and existing.get("daily_reconciliation_pass") == True:
            print(f"resuming after reconciled {run_id}", flush=True)
            continue
        if existing is not None:
            rows.remove(existing)
        hourly = _run_one(
            rows,
            panel,
            close,
            quote_volume,
            universe_mask,
            quality_score,
            store,
            universe_n,
            k,
            rebalance_days,
            "no_stop",
        )
        policy = replace(
            PRIMARY,
            top_k=k,
            n_short=k,
            rebalance_days=rebalance_days,
            universe_n=universe_n,
            max_weight=0.10,
            direction_mode="market_neutral",
            stop_loss_pct=0.0,
        )
        daily = bt.run_backtest(
            panel,
            close,
            open_daily,
            quote_volume,
            universe_mask,
            quality_score,
            bt.sel_market_neutral,
            policy,
            bidirectional=True,
        )
        daily_gates = evaluate_gates(daily)
        hourly_gates = evaluate_gates(hourly)
        equity_diff_bps = abs(hourly.meta["final_equity"] / daily.meta["final_equity"] - 1.0) * 1e4
        dd_diff_bps = abs(
            hourly_gates.values["g7_max_drawdown"] - daily_gates.values["g7_max_drawdown"]
        ) * 1e4
        current = find_row(run_id)
        if current is None:
            raise AssertionError(f"missing just-written summary row {run_id}")
        current["daily_reconciliation_equity_diff_bps"] = equity_diff_bps
        current["daily_reconciliation_dd_diff_bps"] = dd_diff_bps
        current["daily_reconciliation_pass"] = equity_diff_bps <= 10.0 and dd_diff_bps <= 1.0
        _checkpoint(rows)
        if not current["daily_reconciliation_pass"]:
            raise RuntimeError(
                f"hourly no-stop reconciliation failed for u={universe_n}, k={k}, reb={rebalance_days}: "
                f"equity {equity_diff_bps:.3f}bps, DD {dd_diff_bps:.3f}bps"
            )

    # Phase 2: frozen structural policies on the full neighbouring plateau.
    for universe_n, k, rebalance_days in CONFIGS:
        universe_mask, quality_score = cache[universe_n]
        for variant in VARIANTS:
            run_id = f"real_u{universe_n}_k{k}_reb{rebalance_days}_{variant}"
            if find_row(run_id) is not None:
                print(f"resuming after {run_id}", flush=True)
                continue
            _run_one(
                rows,
                panel,
                close,
                quote_volume,
                universe_mask,
                quality_score,
                store,
                universe_n,
                k,
                rebalance_days,
                variant,
            )

    # Phase 3: identical primary structural policy on three shuffled scores.
    universe_mask, quality_score = cache[PRIMARY_KEY[0]]
    for seed in SHUFFLE_SEEDS:
        run_id = (
            f"shuffled_u{PRIMARY_KEY[0]}_k{PRIMARY_KEY[1]}_reb{PRIMARY_KEY[2]}_"
            f"symmetric_structural_seed{seed}"
        )
        if find_row(run_id) is not None:
            print(f"resuming after {run_id}", flush=True)
            continue
        shuffled = _shuffle_within_date(quality_score, seed)
        _run_one(
            rows,
            panel,
            close,
            quote_volume,
            universe_mask,
            shuffled,
            store,
            PRIMARY_KEY[0],
            PRIMARY_KEY[1],
            PRIMARY_KEY[2],
            "symmetric_structural",
            control="shuffled",
            seed=seed,
        )

    summary = pd.DataFrame(rows)
    summary.to_parquet(SUMMARY_PATH)
    print(f"wrote {SUMMARY_PATH}", flush=True)


if __name__ == "__main__":
    main()
