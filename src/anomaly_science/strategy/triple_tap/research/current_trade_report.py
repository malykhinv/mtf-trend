"""Current IS trade report split by setup family.

This report is explicit: no ML selection, no fallback. It evaluates the current
detector contract and the registered partial-runner policy in two views:

* combined portfolio: breakout and cap compete for the same slots/symbols;
* family-only diagnostics: each family simulated alone, not additive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomaly_science.strategy.triple_tap.research import TRADE_KEY
from anomaly_science.strategy.triple_tap.research.accounting import POLICY
from anomaly_science.strategy.triple_tap.research.audit import _calendar_stats, _ledger, _metrics
from anomaly_science.strategy.triple_tap.research.economics import DEPOSIT, RES

OUT_JSON = Path("research/triple_tap_current_trade_report.json")
OUT_MD = Path("research/triple_tap_current_trade_report.md")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if pd.isna(value):
        return None
    return value


def _candidate_frame() -> pd.DataFrame:
    policies = pd.read_parquet(RES / "execution_policies.parquet")
    setups = pd.read_parquet(RES / "setups_discovery.parquet")
    candidates = policies[
        (policies["entry_mode"] == "close")
        & (policies["exit_policy"] == POLICY)
        & policies["r_multiple"].notna()
    ].copy()
    candidates["model_score_r"] = 0.0
    enrich_cols = [
        "setup_family",
        "ignition_rise",
        "sleep_range_pct",
        "sleep_high_vs_level",
        "pre_pump_atr_pct",
        "last_tap_level_ratio",
        "prior_close_above_level_count",
        "pre_brk_vol_ramp",
        "rr",
        "span_h",
    ]
    return candidates.merge(
        setups[TRADE_KEY + enrich_cols],
        on=TRADE_KEY,
        validate="one_to_one",
    )


def _run_ledger(frame: pd.DataFrame, risk_pct: float = 0.02) -> tuple[pd.DataFrame, dict[str, int]]:
    return _ledger(
        frame,
        risk_pct=risk_pct,
        max_open=3,
        slip_pct=0.005,
        priority_by_score=False,
        max_notional_multiple=0.50,
    )


def _family_contribution(ledger: pd.DataFrame, frame: pd.DataFrame) -> list[dict[str, Any]]:
    enriched = ledger.merge(frame[TRADE_KEY + ["setup_family"]], on=TRADE_KEY, validate="one_to_one")
    rows: list[dict[str, Any]] = []
    for family, group in enriched.groupby("setup_family", observed=True):
        pnl = group["pnl"].to_numpy(float)
        gross_loss = -float(pnl[pnl < 0].sum())
        rows.append(
            {
                "setup_family": str(family),
                "trades": int(len(group)),
                "return_pct": float(100 * pnl.sum() / DEPOSIT),
                "win_rate_pct": float((pnl > 0).mean() * 100),
                "expectancy_r": float(group["net_r"].mean()),
                "profit_factor": float(pnl[pnl > 0].sum() / gross_loss) if gross_loss > 0 else None,
                "pnl": float(pnl.sum()),
            }
        )
    return rows


def _group_metrics(ledger: pd.DataFrame, frame: pd.DataFrame, keys: list[str]) -> list[dict[str, Any]]:
    extra_keys = [key for key in keys if key not in TRADE_KEY]
    enriched = ledger.merge(frame[TRADE_KEY + extra_keys], on=TRADE_KEY, validate="one_to_one")
    rows: list[dict[str, Any]] = []
    for values, group in enriched.groupby(keys, observed=True):
        if not isinstance(values, tuple):
            values = (values,)
        pnl = group["pnl"].to_numpy(float)
        gross_loss = -float(pnl[pnl < 0].sum())
        row = {key: str(value) for key, value in zip(keys, values)}
        row.update(
            trades=int(len(group)),
            return_pct=float(100 * pnl.sum() / DEPOSIT),
            win_rate_pct=float((pnl > 0).mean() * 100),
            expectancy_r=float(group["net_r"].mean()),
            profit_factor=float(pnl[pnl > 0].sum() / gross_loss) if gross_loss > 0 else None,
            pnl=float(pnl.sum()),
        )
        rows.append(row)
    return rows


def _family_only(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family, group in frame.groupby("setup_family", observed=True):
        ledger, rejects = _run_ledger(group)
        months = _calendar_stats(ledger, "M") if not ledger.empty else []
        rows.append(
            {
                "setup_family": str(family),
                **_metrics(ledger),
                "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
                "months": int(len(months)),
                "rejects": rejects,
                "diagnostic_only_not_additive": True,
            }
        )
    return rows


def _risk_stress(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for risk_pct in (0.02, 0.03, 0.04, 0.05):
        ledger, rejects = _run_ledger(frame, risk_pct=risk_pct)
        risk = ledger["risk_dollars"].to_numpy(float)
        rows.append(
            {
                "risk_pct": float(100 * risk_pct),
                **_metrics(ledger),
                "effective_risk_pct_p50": float(np.median(risk / DEPOSIT * 100)),
                "effective_risk_pct_p90": float(np.quantile(risk / DEPOSIT * 100, 0.90)),
                "rejects": rejects,
            }
        )
    return rows


def run(out_json: Path = OUT_JSON, out_md: Path = OUT_MD) -> dict[str, Any]:
    frame = _candidate_frame()
    ledger, rejects = _run_ledger(frame)
    months = _calendar_stats(ledger, "M")
    base = _metrics(ledger)
    report = {
        "scope": "IS current detector; no ML selection; combined family portfolio",
        "policy": POLICY,
        "population": {
            "portfolio_candidates": int(len(frame)),
            "portfolio_taken": int(len(ledger)),
            "portfolio_not_opened": int(sum(rejects.values())),
        },
        "combined_portfolio": {
            **base,
            "positive_months": int(sum(row["net_pnl"] > 0 for row in months)),
            "months": int(len(months)),
            "monthly_pnl": {row["period"]: row["net_pnl"] for row in months},
            "rejects": rejects,
        },
        "combined_family_contribution": _family_contribution(ledger, frame),
        "combined_by_family_tf": _group_metrics(ledger, frame, ["setup_family", "tf"]),
        "family_only_diagnostics_not_additive": _family_only(frame),
        "risk_stress_combined": _risk_stress(frame),
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(_jsonable(report), indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Triple-tap current trade report",
        "",
        "Scope: IS current detector, no ML selection, combined breakout+cap portfolio.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Candidates | {report['population']['portfolio_candidates']} |",
        f"| Taken trades | {base['trades']} |",
        f"| Not opened | {report['population']['portfolio_not_opened']} |",
        f"| Return | {base['return_pct']:+.1f}% |",
        f"| Max DD | {base['max_drawdown_pct']:.1f}% |",
        f"| Win rate | {base['win_rate_pct']:.1f}% |",
        f"| PF | {base['profit_factor']:.2f} |",
        "",
        "## Combined portfolio contribution by family",
        "",
        "| Family | Trades | Return | WR | PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in report["combined_family_contribution"]:
        lines.append(
            f"| {row['setup_family']} | {row['trades']} | {row['return_pct']:+.1f}% | "
            f"{row['win_rate_pct']:.1f}% | {row['profit_factor']:.2f} |"
        )
    lines += [
        "",
        "Family-only diagnostics are not additive; see JSON.",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote trade report -> {out_json} and {out_md}", flush=True)
    return report


if __name__ == "__main__":
    run()
