"""Rolling session optimizer for large-runner research artifacts.

This module does not resimulate candles, levels or exits. It reads an existing
large-runner trade ledger, builds decision-time rule masks, selects robust rules
on trailing 30/60d windows per session, and applies them to the next OOS day.
All optimizer thresholds are code constants; the CLI only selects the discovery days folder.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence
import itertools
import math
import time

import numpy as np
import pandas as pd


MINUTE_MS = 60_000
DAY_MS = 86_400_000
OPTIMIZER_WINDOWS = (30, 60)
OPTIMIZER_SESSIONS = ("asia", "europe", "us", "overlap")
OPTIMIZER_SOURCE_FILE = "large_runner_trade_grid.csv"
OPTIMIZER_RISK_PER_TRADE_PCT = 0.03
OPTIMIZER_MIN_WIN_RATE = 0.50
OPTIMIZER_MIN_POSITIVE_DAY_RATE = 0.70
OPTIMIZER_MIN_TRADES_PER_DAY = 0.70
OPTIMIZER_MAX_DRAWDOWN_PCT = 0.15
OPTIMIZER_MIN_TOP_TRADE_INDEPENDENCE_PCT = 40.0
OPTIMIZER_MIN_TOP_SYMBOL_INDEPENDENCE_PCT = 0.0
OPTIMIZER_MAX_RULE_DEPTH = 3
OPTIMIZER_BEAM_WIDTH = 50
OPTIMIZER_MAX_RULES_PER_SESSION = 5
OPTIMIZER_REJECTED_RULES_PER_GROUP = 25
OPTIMIZER_FLUCTUATION_STRESS_MIN_FOLDS = 4


@dataclass(frozen=True, slots=True)
class RollingSessionOptimizerConfig:
    input_dir: Path
    output_dir: Path | None = None
    windows: tuple[int, ...] = OPTIMIZER_WINDOWS
    sessions: tuple[str, ...] = OPTIMIZER_SESSIONS
    source_file: str = OPTIMIZER_SOURCE_FILE
    risk_per_trade_pct: float = OPTIMIZER_RISK_PER_TRADE_PCT
    min_win_rate: float = OPTIMIZER_MIN_WIN_RATE
    min_positive_day_rate: float = OPTIMIZER_MIN_POSITIVE_DAY_RATE
    min_trades_per_day: float = OPTIMIZER_MIN_TRADES_PER_DAY
    max_drawdown_pct: float = OPTIMIZER_MAX_DRAWDOWN_PCT
    min_top_trade_independence_pct: float = OPTIMIZER_MIN_TOP_TRADE_INDEPENDENCE_PCT
    min_top_symbol_independence_pct: float = OPTIMIZER_MIN_TOP_SYMBOL_INDEPENDENCE_PCT
    max_rule_depth: int = OPTIMIZER_MAX_RULE_DEPTH
    beam_width: int = OPTIMIZER_BEAM_WIDTH
    max_rules_per_session: int = OPTIMIZER_MAX_RULES_PER_SESSION
    rejected_rules_per_group: int = OPTIMIZER_REJECTED_RULES_PER_GROUP
    fluctuation_stress_min_folds: int = OPTIMIZER_FLUCTUATION_STRESS_MIN_FOLDS

    def __post_init__(self) -> None:
        if not self.input_dir:
            raise ValueError("input_dir is required")
        if not self.windows:
            raise ValueError("at least one rolling window is required")
        if any(int(window) <= 0 for window in self.windows):
            raise ValueError("windows must be positive")
        if float(self.risk_per_trade_pct) <= 0.0:
            raise ValueError("risk_per_trade_pct must be > 0")
        if int(self.max_rule_depth) <= 0:
            raise ValueError("max_rule_depth must be > 0")
        if int(self.beam_width) <= 0:
            raise ValueError("beam_width must be > 0")


@dataclass(frozen=True, slots=True)
class RuleAtom:
    atom_id: str
    label: str
    column: str
    mask: np.ndarray


@dataclass(frozen=True, slots=True)
class CandidateRule:
    rule_id: str
    conditions: tuple[str, ...]
    atom_ids: tuple[str, ...]
    columns: tuple[str, ...]
    mask: np.ndarray


def run_rolling_session_optimizer(config: RollingSessionOptimizerConfig) -> Path:
    started = time.monotonic()
    input_dir = Path(config.input_dir)
    output_dir = Path(config.output_dir) if config.output_dir is not None else input_dir / "rolling_session_optimizer"
    output_dir.mkdir(parents=True, exist_ok=True)

    ledger_path = input_dir / config.source_file
    if not ledger_path.exists():
        raise FileNotFoundError(f"trade ledger not found: {ledger_path}")
    ledger = pd.read_csv(ledger_path)
    ledger = _prepare_ledger(ledger)
    atoms = _build_atoms(ledger)
    if not atoms:
        raise ValueError("no usable decision-time rule atoms were built from the ledger")

    context = _LedgerContext(ledger, atoms, config)
    selected_rows: list[dict[str, object]] = []
    rejected_rows: list[dict[str, object]] = []
    oos_rows: list[dict[str, object]] = []

    max_window = max(int(window) for window in config.windows)
    all_days = sorted(int(day) for day in np.unique(context.day_ord))
    if not all_days:
        raise ValueError("ledger has no valid entry days")
    first_oos_day = min(all_days) + max_window
    oos_days = [day for day in all_days if day >= first_oos_day]

    for day_index, test_day in enumerate(oos_days, start=1):
        if day_index == 1 or day_index % 10 == 0 or day_index == len(oos_days):
            pct = 100.0 * day_index / max(len(oos_days), 1)
            print(
                f"rolling session optimizer: day {day_index}/{len(oos_days)} ({pct:5.1f}%) { _date_from_ord(test_day) }",
                flush=True,
            )
        for session in config.sessions:
            session_mask = context.session_mask(session)
            test_mask = session_mask & (context.day_ord == test_day)
            if not bool(np.any(test_mask)):
                continue
            daily_selected: list[dict[str, object]] = []
            for window in config.windows:
                train_start = test_day - int(window)
                train_mask = session_mask & (context.day_ord >= train_start) & (context.day_ord < test_day)
                if not bool(np.any(train_mask)):
                    continue
                selected, rejected = _select_rules_for_window(context, train_mask, session=session, test_day=test_day, window_days=int(window))
                selected_rows.extend(selected)
                rejected_rows.extend(rejected)
                daily_selected.extend(selected)
            if daily_selected:
                oos_rows.extend(_apply_selected_rules_oos(context, daily_selected, test_mask, test_day=test_day, session=session))

    selected_frame = pd.DataFrame(selected_rows)
    rejected_frame = pd.DataFrame(rejected_rows)
    oos_frame = pd.DataFrame(oos_rows)
    daily_frame = _daily_summary(oos_frame)
    health_frame = _rule_health(selected_frame, oos_frame)
    stress_frame = _fluctuation_stress(selected_frame, context)
    drift_frame = _selection_drift(selected_frame)
    window_dynamics_frame = _window_dynamics(selected_frame)
    run_config = pd.DataFrame(
        [
            {
                **asdict(config),
                "input_dir": str(config.input_dir),
                "output_dir": str(output_dir),
                "windows": ",".join(str(window) for window in config.windows),
                "sessions": ",".join(config.sessions),
                "ledger_rows": int(len(ledger)),
                "atoms": int(len(atoms)),
                "oos_days": int(len(oos_days)),
                "optimizer_model": "rolling_session_optimizer_v3_stress_and_window_drift",
                "selection_model": "walk_forward_train_past_window_trade_next_day_by_session",
                "stress_model": "selected_rule_fold_fluctuation_stress_no_future_data",
                "drift_model": "session_window_top_rule_and_selected_set_jaccard",
                "gates": (
                    f"wr>={config.min_win_rate:.3f};positive_days>={config.min_positive_day_rate:.3f};"
                    f"trades_per_day>={config.min_trades_per_day:.3f};max_dd_pct<={config.max_drawdown_pct:.3f};"
                    f"top_trade_independence_pct>={config.min_top_trade_independence_pct:.1f}"
                ),
                "runtime_seconds": round(time.monotonic() - started, 3),
            }
        ]
    )

    _write_csv(output_dir / "rolling_session_selected_rules.csv", selected_frame)
    _write_csv(output_dir / "rolling_session_rejected_rules.csv", rejected_frame)
    _write_csv(output_dir / "rolling_session_oos_trades.csv", oos_frame)
    _write_csv(output_dir / "rolling_session_daily_summary.csv", daily_frame)
    _write_csv(output_dir / "rolling_session_rule_health.csv", health_frame)
    _write_csv(output_dir / "rolling_session_fluctuation_stress.csv", stress_frame)
    _write_csv(output_dir / "rolling_session_selection_drift.csv", drift_frame)
    _write_csv(output_dir / "rolling_session_window_dynamics.csv", window_dynamics_frame)
    _write_csv(output_dir / "rolling_session_run_config.csv", run_config)
    return output_dir


class _LedgerContext:
    def __init__(self, ledger: pd.DataFrame, atoms: list[RuleAtom], config: RollingSessionOptimizerConfig) -> None:
        self.ledger = ledger.reset_index(drop=True).copy()
        self.atoms = atoms
        self.config = config
        self.net_r = pd.to_numeric(self.ledger["net_r"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        self.timestamp = pd.to_numeric(self.ledger["entry_timestamp_ms"], errors="coerce").fillna(0).to_numpy(dtype="int64")
        self.day_ord = pd.to_numeric(self.ledger["entry_day_ord"], errors="coerce").fillna(0).to_numpy(dtype="int64")
        self.symbol_code = pd.factorize(self.ledger["symbol"].astype(str), sort=False)[0].astype("int64")
        self.session = self.ledger["session_primary"].astype(str).to_numpy(dtype=object)
        self.trade_key = self.ledger["rolling_trade_key"].astype(str).to_numpy(dtype=object)

    def session_mask(self, session: str) -> np.ndarray:
        if session == "overlap":
            if "session_overlap" in self.ledger.columns:
                return _bool_series(self.ledger, "session_overlap").to_numpy(dtype=bool)
            return np.zeros(len(self.ledger), dtype=bool)
        return self.session == str(session)


def _prepare_ledger(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("trade ledger is empty")
    work = frame.copy()
    if "status" in work.columns:
        work = work.loc[work["status"].astype(str).eq("closed")].copy()
    if work.empty:
        raise ValueError("trade ledger has no closed trades")
    if "entry_timestamp_ms" not in work.columns:
        if "seed_open_ms" in work.columns:
            work["entry_timestamp_ms"] = work["seed_open_ms"]
        else:
            raise ValueError("ledger needs entry_timestamp_ms or seed_open_ms")
    work["entry_timestamp_ms"] = pd.to_numeric(work["entry_timestamp_ms"], errors="coerce")
    work = work.dropna(subset=["entry_timestamp_ms"]).copy()
    work["entry_timestamp_ms"] = work["entry_timestamp_ms"].astype("int64")
    if "net_r" not in work.columns:
        if "net_return" in work.columns and "initial_risk_pct" in work.columns:
            risk = pd.to_numeric(work["initial_risk_pct"], errors="coerce").replace(0.0, np.nan)
            work["net_r"] = pd.to_numeric(work["net_return"], errors="coerce") / risk
        elif "gross_r" in work.columns:
            work["net_r"] = pd.to_numeric(work["gross_r"], errors="coerce")
        else:
            raise ValueError("ledger needs net_r or net_return+initial_risk_pct")
    work["net_r"] = pd.to_numeric(work["net_r"], errors="coerce")
    work = work.dropna(subset=["net_r"]).copy()
    if "symbol" not in work.columns:
        work["symbol"] = "UNKNOWN"
    if "session_primary" not in work.columns:
        work["session_primary"] = work["entry_timestamp_ms"].map(lambda value: _session_features(int(value))["session_primary"])
    if "session_overlap" not in work.columns:
        work["session_overlap"] = work["entry_timestamp_ms"].map(lambda value: _session_features(int(value))["session_overlap"])
    ts = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True)
    work["entry_date"] = ts.dt.strftime("%Y-%m-%d")
    # Integer day ordinal in UTC; avoids repeated datetime work in rolling loops.
    work["entry_day_ord"] = (work["entry_timestamp_ms"] // DAY_MS).astype("int64")
    if "exit_policy_id" not in work.columns:
        work["exit_policy_id"] = "unknown_exit"
    if "arm_id" not in work.columns:
        work["arm_id"] = "unknown_arm"
    work["rolling_trade_key"] = (
        work["symbol"].astype(str)
        + "|"
        + work["entry_timestamp_ms"].astype(str)
    )
    work.sort_values(["entry_timestamp_ms", "symbol", "arm_id", "exit_policy_id"], inplace=True)
    work.reset_index(drop=True, inplace=True)
    return work


def _build_atoms(ledger: pd.DataFrame) -> list[RuleAtom]:
    atoms: list[RuleAtom] = []

    def add_atom(column: str, label: str, mask: np.ndarray) -> None:
        if mask.dtype != bool:
            mask_bool = mask.astype(bool)
        else:
            mask_bool = mask
        support = int(mask_bool.sum())
        if support <= 0 or support >= len(ledger):
            return
        atom_id = _safe_id(f"{column}:{label}")
        atoms.append(RuleAtom(atom_id=atom_id, label=f"{column} {label}", column=column, mask=mask_bool))

    categorical_columns = [
        "arm_id",
        "exit_policy_id",
        "setup_selection_model",
        "level_attack_best_timeframe",
        "level_attack_best_tier",
        "h1_level_attack_tier",
        "h4_level_attack_tier",
        "d1_level_attack_tier",
        "h1_entry_mode_vs_nearest_level",
        "h4_entry_mode_vs_nearest_level",
        "d1_entry_mode_vs_nearest_level",
    ]
    for column in categorical_columns:
        if column not in ledger.columns:
            continue
        values = ledger[column].fillna("missing").astype(str)
        counts = values.value_counts(dropna=False)
        for value, count in counts.items():
            if value in {"", "missing", "none", "nan"}:
                continue
            if int(count) < 5:
                continue
            add_atom(column, f"=={value}", values.eq(value).to_numpy(dtype=bool))

    boolean_columns = [
        "level_attack_any_tf_candidate",
        "level_attack_any_loose_candidate",
        "level_attack_any_medium_candidate",
        "level_attack_any_strict_candidate",
        "level_attack_any_attack_zone70",
        "level_attack_any_entry_before_level_break",
        "level_attack_any_entry_in_level_crossing",
        "level_attack_any_flow_beats_prior",
        "h1_level_attack_candidate",
        "h1_level_attack_medium_candidate",
        "h1_level_attack_strict_candidate",
        "h1_entry_before_level_break",
        "h1_entry_in_level_crossing",
        "h1_attack_flow_beats_prior_spikes",
        "h4_level_attack_candidate",
        "h4_level_attack_medium_candidate",
        "h4_level_attack_strict_candidate",
        "h4_entry_before_level_break",
        "h4_entry_in_level_crossing",
        "h4_attack_flow_beats_prior_spikes",
        "d1_level_attack_candidate",
        "d1_level_attack_medium_candidate",
        "d1_level_attack_strict_candidate",
        "d1_entry_before_level_break",
        "d1_entry_in_level_crossing",
        "d1_attack_flow_beats_prior_spikes",
        "large_runner_5m_prefilter_passed",
    ]
    for column in boolean_columns:
        if column in ledger.columns:
            add_atom(column, "is_true", _bool_series(ledger, column).to_numpy(dtype=bool))

    multi_columns = ["large_runner_nature_trade_rule_ids", "large_runner_nature_ids", "level_attack_candidate_timeframes"]
    for column in multi_columns:
        if column not in ledger.columns:
            continue
        series = ledger[column].fillna("").astype(str)
        tokens: dict[str, int] = {}
        for text in series.tolist():
            for token in _split_tokens(text):
                tokens[token] = tokens.get(token, 0) + 1
        for token, count in sorted(tokens.items(), key=lambda item: (-item[1], item[0])):
            if count < 5:
                continue
            add_atom(column, f"contains {token}", series.map(lambda text, tok=token: tok in _split_tokens(text)).to_numpy(dtype=bool))

    threshold_specs: dict[str, list[tuple[str, float]]] = {
        "early_return_pct": [(">=", 0.02), (">=", 0.05), (">=", 0.10), ("<=", 0.00), ("<=", -0.03), ("<=", -0.05)],
        "pre60_return_pct": [("<=", 0.00), ("<=", -0.03), ("<=", -0.05), ("<=", -0.08), (">=", 0.03), (">=", 0.05)],
        "pre60_min_path_pct": [("<=", -0.05), ("<=", -0.08), ("<=", -0.12)],
        "pre60_range_pct": [(">=", 0.03), (">=", 0.05), (">=", 0.08)],
        "early_quote_ratio_24h_scaled": [(">=", 1.0), (">=", 2.0), (">=", 3.0), (">=", 5.0), (">=", 10.0)],
        "early_trade_ratio_24h_scaled": [(">=", 1.0), (">=", 2.0), (">=", 3.0), (">=", 5.0), (">=", 10.0)],
        "m1_taker_buy_quote_share": [("<=", 0.45), ("<=", 0.50), ("<=", 0.55), (">=", 0.55), (">=", 0.60), (">=", 0.65)],
        "m1_quote_top1_share": [("<=", 0.30), ("<=", 0.40), ("<=", 0.50), (">=", 0.60)],
        "m1_trade_top1_share": [("<=", 0.30), ("<=", 0.40), ("<=", 0.50), (">=", 0.60)],
        "m1_last2_quote_share": [("<=", 0.30), ("<=", 0.35), ("<=", 0.45), (">=", 0.55)],
        "m1_last2_trade_share": [("<=", 0.30), ("<=", 0.35), ("<=", 0.45), (">=", 0.55)],
        "oi_change_pre60_pct": [(">=", 0.0), (">=", 0.01), (">=", 0.03), ("<=", -0.01), ("<=", -0.03)],
        "oi_change_early_pct": [(">=", 0.0), (">=", 0.005), (">=", 0.01), ("<=", -0.005)],
        "h1_progress_to_level_from_pullback": [(">=", 0.45), (">=", 0.55), (">=", 0.70), (">=", 0.85)],
        "h4_progress_to_level_from_pullback": [(">=", 0.45), (">=", 0.55), (">=", 0.70), (">=", 0.85)],
        "d1_progress_to_level_from_pullback": [(">=", 0.45), (">=", 0.55), (">=", 0.70), (">=", 0.85)],
        "h1_nearest_level_distance_R_from_entry": [("<=", 0.5), ("<=", 1.0), ("<=", 1.5), ("<=", 2.0), (">=", 0.0)],
        "h4_nearest_level_distance_R_from_entry": [("<=", 0.5), ("<=", 1.0), ("<=", 1.5), ("<=", 2.0), (">=", 0.0)],
        "d1_nearest_level_distance_R_from_entry": [("<=", 0.5), ("<=", 1.0), ("<=", 1.5), ("<=", 2.0), (">=", 0.0)],
        "h1_attack_quote_vs_prior_spike_max": [(">=", 0.8), (">=", 1.0), (">=", 1.5), (">=", 2.0)],
        "h1_attack_trades_vs_prior_spike_max": [(">=", 0.8), (">=", 1.0), (">=", 1.5), (">=", 2.0)],
        "h4_attack_quote_vs_prior_spike_max": [(">=", 0.8), (">=", 1.0), (">=", 1.5), (">=", 2.0)],
        "h4_attack_trades_vs_prior_spike_max": [(">=", 0.8), (">=", 1.0), (">=", 1.5), (">=", 2.0)],
        "d1_attack_quote_vs_prior_spike_max": [(">=", 0.8), (">=", 1.0), (">=", 1.5), (">=", 2.0)],
        "d1_attack_trades_vs_prior_spike_max": [(">=", 0.8), (">=", 1.0), (">=", 1.5), (">=", 2.0)],
    }
    for column, specs in threshold_specs.items():
        if column not in ledger.columns:
            continue
        values = pd.to_numeric(ledger[column], errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(values)
        if int(finite.sum()) < 5:
            continue
        for op, threshold in specs:
            if op == ">=":
                mask = finite & (values >= float(threshold))
            elif op == "<=":
                mask = finite & (values <= float(threshold))
            else:
                continue
            add_atom(column, f"{op}{threshold:g}", mask)
    # Stable deterministic order.
    atoms.sort(key=lambda atom: atom.atom_id)
    return atoms


def _select_rules_for_window(
    context: _LedgerContext,
    train_mask: np.ndarray,
    *,
    session: str,
    test_day: int,
    window_days: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    config = context.config
    min_trades = int(math.ceil(float(window_days) * float(config.min_trades_per_day)))
    single_candidates: list[tuple[float, CandidateRule, dict[str, object]]] = []
    for atom in context.atoms:
        rule = CandidateRule(
            rule_id=atom.atom_id,
            conditions=(atom.label,),
            atom_ids=(atom.atom_id,),
            columns=(atom.column,),
            mask=atom.mask,
        )
        metrics = _metrics(context, train_mask & rule.mask, window_days=window_days, risk_per_trade_pct=config.risk_per_trade_pct)
        if int(metrics.get("trades", 0)) >= max(5, min_trades // 3):
            single_candidates.append((_soft_score(metrics, config), rule, metrics))
    single_candidates.sort(key=lambda item: item[0], reverse=True)
    beam_rules = [item[1] for item in single_candidates[: int(config.beam_width)]]
    all_candidates: list[tuple[float, CandidateRule, dict[str, object]]] = list(single_candidates[: max(int(config.beam_width) * 2, int(config.beam_width))])

    current_beam = beam_rules
    seen_rule_ids = {rule.rule_id for rule in current_beam}
    max_depth = min(int(config.max_rule_depth), 4)
    for depth in range(2, max_depth + 1):
        next_candidates: list[tuple[float, CandidateRule, dict[str, object]]] = []
        base_rules = current_beam[: int(config.beam_width)]
        atom_pool = [item[1] for item in single_candidates[: int(config.beam_width)]]
        for base in base_rules:
            for atom_rule in atom_pool:
                if atom_rule.atom_ids[0] in base.atom_ids:
                    continue
                # Avoid narrow contradictory rules on the same source column.
                if atom_rule.columns[0] in base.columns:
                    continue
                atom_ids = tuple(sorted((*base.atom_ids, atom_rule.atom_ids[0])))
                rule_id = "&".join(atom_ids)
                if rule_id in seen_rule_ids:
                    continue
                seen_rule_ids.add(rule_id)
                mask = base.mask & atom_rule.mask
                if int((train_mask & mask).sum()) < max(5, min_trades // 3):
                    continue
                conditions_by_id = {aid: label for aid, label in zip(base.atom_ids, base.conditions)}
                conditions_by_id[atom_rule.atom_ids[0]] = atom_rule.conditions[0]
                columns_by_id = {aid: column for aid, column in zip(base.atom_ids, base.columns)}
                columns_by_id[atom_rule.atom_ids[0]] = atom_rule.columns[0]
                rule = CandidateRule(
                    rule_id=rule_id,
                    conditions=tuple(conditions_by_id[aid] for aid in atom_ids),
                    atom_ids=atom_ids,
                    columns=tuple(columns_by_id[aid] for aid in atom_ids),
                    mask=mask,
                )
                metrics = _metrics(context, train_mask & rule.mask, window_days=window_days, risk_per_trade_pct=config.risk_per_trade_pct)
                score = _soft_score(metrics, config)
                next_candidates.append((score, rule, metrics))
        next_candidates.sort(key=lambda item: item[0], reverse=True)
        all_candidates.extend(next_candidates[: max(int(config.beam_width) * 2, int(config.beam_width))])
        current_beam = [item[1] for item in next_candidates[: int(config.beam_width)]]
        if not current_beam:
            break

    selected: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    all_candidates.sort(key=lambda item: item[0], reverse=True)
    selected_rule_ids: set[str] = set()
    rejected_count = 0
    for score, rule, metrics in all_candidates:
        gate_result, reject_reason = _passes_gates(metrics, config)
        row = {
            "date": _date_from_ord(test_day),
            "test_day_ord": int(test_day),
            "session": session,
            "window_days": int(window_days),
            "rule_id": rule.rule_id,
            "conditions": " AND ".join(rule.conditions),
            "condition_count": int(len(rule.conditions)),
            "train_score": float(score),
            **metrics,
        }
        if gate_result and rule.rule_id not in selected_rule_ids and len(selected) < int(config.max_rules_per_session):
            selected_rule_ids.add(rule.rule_id)
            selected.append(row)
        elif rejected_count < int(config.rejected_rules_per_group):
            rejected.append({**row, "reject_reason": reject_reason})
            rejected_count += 1
        if len(selected) >= int(config.max_rules_per_session) and rejected_count >= int(config.rejected_rules_per_group):
            break
    return selected, rejected


def _metrics(context: _LedgerContext, mask: np.ndarray, *, window_days: int, risk_per_trade_pct: float) -> dict[str, object]:
    idx = np.flatnonzero(mask)
    n = int(idx.size)
    if n == 0:
        return _empty_metrics(window_days)
    r = context.net_r[idx]
    sum_r = float(np.sum(r))
    win_rate = float(np.mean(r > 0.0))
    avg_r = float(np.mean(r))
    median_r = float(np.median(r))
    trades_per_day = float(n / max(float(window_days), 1.0))
    days = context.day_ord[idx]
    unique_days, inverse = np.unique(days, return_inverse=True)
    day_sums = np.bincount(inverse, weights=r)
    positive_days = int(np.sum(day_sums > 0.0))
    active_days = int(unique_days.size)
    positive_day_rate = float(positive_days / active_days) if active_days else 0.0
    ordered_idx = idx[np.argsort(context.timestamp[idx], kind="mergesort")]
    equity = np.cumsum(context.net_r[ordered_idx])
    if equity.size:
        peak = np.maximum.accumulate(np.maximum(equity, 0.0))
        max_drawdown_r = float(np.max(peak - equity))
    else:
        max_drawdown_r = 0.0
    max_drawdown_pct = float(max_drawdown_r * risk_per_trade_pct)
    top_trade_independence_pct = _break_even_top_removal_pct(r)
    top_symbol_independence_pct = _break_even_symbol_removal_pct(r, context.symbol_code[idx])
    return {
        "window_days": int(window_days),
        "trades": n,
        "trades_per_day": trades_per_day,
        "win_rate": win_rate,
        "positive_days": positive_days,
        "active_days": active_days,
        "positive_day_rate": positive_day_rate,
        "sum_r": sum_r,
        "avg_r": avg_r,
        "median_r": median_r,
        "max_drawdown_r": max_drawdown_r,
        "max_drawdown_pct": max_drawdown_pct,
        "top_trade_independence_pct": top_trade_independence_pct,
        "top_symbol_independence_pct": top_symbol_independence_pct,
    }


def _empty_metrics(window_days: int) -> dict[str, object]:
    return {
        "window_days": int(window_days),
        "trades": 0,
        "trades_per_day": 0.0,
        "win_rate": 0.0,
        "positive_days": 0,
        "active_days": 0,
        "positive_day_rate": 0.0,
        "sum_r": 0.0,
        "avg_r": 0.0,
        "median_r": 0.0,
        "max_drawdown_r": 0.0,
        "max_drawdown_pct": 0.0,
        "top_trade_independence_pct": 0.0,
        "top_symbol_independence_pct": 0.0,
    }


def _soft_score(metrics: Mapping[str, object], config: RollingSessionOptimizerConfig) -> float:
    trades = float(metrics.get("trades", 0.0))
    if trades <= 0.0:
        return -1e9
    wr = float(metrics.get("win_rate", 0.0))
    pos = float(metrics.get("positive_day_rate", 0.0))
    top = float(metrics.get("top_trade_independence_pct", 0.0)) / 100.0
    tpd = min(float(metrics.get("trades_per_day", 0.0)) / max(float(config.min_trades_per_day), 1e-9), 2.0)
    dd_pct = float(metrics.get("max_drawdown_pct", 0.0))
    dd_penalty = dd_pct / max(float(config.max_drawdown_pct), 1e-9)
    median_r = float(metrics.get("median_r", 0.0))
    sum_r = float(metrics.get("sum_r", 0.0))
    return float(2.0 * wr + 2.0 * pos + 1.5 * top + 0.75 * tpd + 0.5 * median_r + 0.02 * sum_r - 1.5 * dd_penalty)


def _passes_gates(metrics: Mapping[str, object], config: RollingSessionOptimizerConfig) -> tuple[bool, str]:
    effective_window_days = int(metrics.get("window_days", min(config.windows)))
    if int(metrics.get("trades", 0)) < int(math.ceil(max(effective_window_days, 1) * config.min_trades_per_day)):
        return False, "too_few_trades"
    if float(metrics.get("trades_per_day", 0.0)) < float(config.min_trades_per_day):
        return False, "trades_per_day_below_gate"
    if float(metrics.get("win_rate", 0.0)) < float(config.min_win_rate):
        return False, "win_rate_below_gate"
    if float(metrics.get("positive_day_rate", 0.0)) < float(config.min_positive_day_rate):
        return False, "positive_day_rate_below_gate"
    if float(metrics.get("max_drawdown_pct", 0.0)) > float(config.max_drawdown_pct):
        return False, "drawdown_above_gate"
    if float(metrics.get("top_trade_independence_pct", 0.0)) < float(config.min_top_trade_independence_pct):
        return False, "top_trade_independence_below_gate"
    if float(config.min_top_symbol_independence_pct) > 0.0 and float(metrics.get("top_symbol_independence_pct", 0.0)) < float(config.min_top_symbol_independence_pct):
        return False, "top_symbol_independence_below_gate"
    return True, "passed"


def _apply_selected_rules_oos(
    context: _LedgerContext,
    selected_rules: Sequence[Mapping[str, object]],
    test_mask: np.ndarray,
    *,
    test_day: int,
    session: str,
) -> list[dict[str, object]]:
    if not selected_rules:
        return []
    rule_by_id = {str(row["rule_id"]): row for row in selected_rules}
    selected_rule_masks: list[tuple[Mapping[str, object], np.ndarray]] = []
    atom_lookup = {atom.atom_id: atom for atom in context.atoms}
    for row in selected_rules:
        atom_ids = str(row["rule_id"]).split("&")
        mask = np.ones(len(context.ledger), dtype=bool)
        valid = True
        for atom_id in atom_ids:
            atom = atom_lookup.get(atom_id)
            if atom is None:
                valid = False
                break
            mask &= atom.mask
        if valid:
            selected_rule_masks.append((row, test_mask & mask))
    best_by_trade: dict[str, tuple[Mapping[str, object], int]] = {}
    for row, mask in selected_rule_masks:
        idxs = np.flatnonzero(mask)
        for idx in idxs.tolist():
            key = str(context.trade_key[idx])
            previous = best_by_trade.get(key)
            if previous is None or float(row.get("train_score", -1e9)) > float(previous[0].get("train_score", -1e9)):
                best_by_trade[key] = (row, int(idx))
    rows: list[dict[str, object]] = []
    for _, (rule, idx) in sorted(best_by_trade.items(), key=lambda item: int(context.timestamp[item[1][1]])):
        ledger_row = context.ledger.iloc[int(idx)]
        rows.append(
            {
                "date": _date_from_ord(test_day),
                "test_day_ord": int(test_day),
                "session": session,
                "rule_id": str(rule.get("rule_id", "")),
                "conditions": str(rule.get("conditions", "")),
                "window_days": int(rule.get("window_days", 0)),
                "train_score": float(rule.get("train_score", float("nan"))),
                "train_trades": int(rule.get("trades", 0)),
                "train_win_rate": float(rule.get("win_rate", float("nan"))),
                "train_positive_day_rate": float(rule.get("positive_day_rate", float("nan"))),
                "train_top_trade_independence_pct": float(rule.get("top_trade_independence_pct", float("nan"))),
                "symbol": ledger_row.get("symbol", ""),
                "entry_timestamp_ms": int(ledger_row.get("entry_timestamp_ms", 0)),
                "entry_utc": _timestamp_to_utc(int(ledger_row.get("entry_timestamp_ms", 0))),
                "arm_id": ledger_row.get("arm_id", ""),
                "exit_policy_id": ledger_row.get("exit_policy_id", ""),
                "net_r": float(ledger_row.get("net_r", 0.0)),
                "net_return": ledger_row.get("net_return", float("nan")),
            }
        )
    return rows


def _daily_summary(oos: pd.DataFrame) -> pd.DataFrame:
    columns = ["date", "session", "trades", "win_rate", "sum_r", "avg_r", "median_r", "positive", "unique_symbols", "rules_used"]
    if oos.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for (date, session), group in oos.groupby(["date", "session"], dropna=False):
        r = pd.to_numeric(group["net_r"], errors="coerce")
        rows.append(
            {
                "date": date,
                "session": session,
                "trades": int(len(group)),
                "win_rate": float(r.gt(0.0).mean()) if len(group) else float("nan"),
                "sum_r": float(r.sum()),
                "avg_r": float(r.mean()) if len(group) else float("nan"),
                "median_r": float(r.median()) if len(group) else float("nan"),
                "positive": bool(float(r.sum()) > 0.0),
                "unique_symbols": int(group["symbol"].astype(str).nunique()) if "symbol" in group.columns else 0,
                "rules_used": int(group["rule_id"].astype(str).nunique()) if "rule_id" in group.columns else 0,
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(["date", "session"]).reset_index(drop=True)


def _rule_health(selected: pd.DataFrame, oos: pd.DataFrame) -> pd.DataFrame:
    columns = ["rule_id", "conditions", "selected_count", "oos_trades", "oos_sum_r", "oos_win_rate", "oos_positive_days"]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    selected_by_rule = selected.groupby("rule_id", dropna=False)
    for rule_id, group in selected_by_rule:
        trades = oos.loc[oos["rule_id"].astype(str).eq(str(rule_id))].copy() if not oos.empty and "rule_id" in oos.columns else pd.DataFrame()
        r = pd.to_numeric(trades.get("net_r", pd.Series(dtype=float)), errors="coerce")
        if not trades.empty and "date" in trades.columns:
            day_sum = r.groupby(trades["date"]).sum()
            positive_days = int(day_sum.gt(0.0).sum())
        else:
            positive_days = 0
        rows.append(
            {
                "rule_id": str(rule_id),
                "conditions": str(group.iloc[0].get("conditions", "")),
                "selected_count": int(len(group)),
                "oos_trades": int(len(trades)),
                "oos_sum_r": float(r.sum()) if len(r) else 0.0,
                "oos_win_rate": float(r.gt(0.0).mean()) if len(r) else float("nan"),
                "oos_positive_days": positive_days,
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("oos_sum_r", ascending=False).reset_index(drop=True)



def _fluctuation_stress(selected: pd.DataFrame, context: _LedgerContext) -> pd.DataFrame:
    columns = [
        "date",
        "test_day_ord",
        "session",
        "window_days",
        "rule_id",
        "conditions",
        "folds_tested",
        "folds_passed",
        "fluctuation_pass_rate",
        "fluctuation_all_folds_pass",
        "fluctuation_min_trades",
        "fluctuation_min_win_rate",
        "fluctuation_min_positive_day_rate",
        "fluctuation_max_drawdown_pct",
        "fluctuation_worst_sum_r",
        "fluctuation_median_sum_r",
        "fluctuation_sum_r_std",
        "fluctuation_reject_reason",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for _, row in selected.iterrows():
        session = str(row.get("session", ""))
        test_day = int(row.get("test_day_ord", 0))
        window_days = int(row.get("window_days", 0))
        train_start = test_day - window_days
        session_mask = context.session_mask(session)
        rule_mask = _rule_mask_from_id(context, str(row.get("rule_id", "")))
        train_mask = session_mask & (context.day_ord >= train_start) & (context.day_ord < test_day) & rule_mask
        folds = _stress_fold_masks(context, train_mask)
        fold_metrics: list[dict[str, object]] = []
        pass_count = 0
        reject_reasons: list[str] = []
        for fold_name, fold_mask, fold_days in folds:
            if not bool(np.any(fold_mask)) or fold_days <= 0:
                continue
            metrics = _metrics(
                context,
                fold_mask,
                window_days=int(fold_days),
                risk_per_trade_pct=context.config.risk_per_trade_pct,
            )
            passed, reason = _passes_gates(metrics, context.config)
            pass_count += int(passed)
            if not passed:
                reject_reasons.append(f"{fold_name}:{reason}")
            fold_metrics.append(metrics)
        if fold_metrics:
            sum_rs = np.array([float(item.get("sum_r", 0.0)) for item in fold_metrics], dtype=float)
            trades = np.array([float(item.get("trades", 0.0)) for item in fold_metrics], dtype=float)
            win_rates = np.array([float(item.get("win_rate", 0.0)) for item in fold_metrics], dtype=float)
            pos_rates = np.array([float(item.get("positive_day_rate", 0.0)) for item in fold_metrics], dtype=float)
            drawdowns = np.array([float(item.get("max_drawdown_pct", 0.0)) for item in fold_metrics], dtype=float)
            folds_tested = int(len(fold_metrics))
            pass_rate = float(pass_count / max(folds_tested, 1))
            enough_folds = folds_tested >= int(context.config.fluctuation_stress_min_folds)
            all_pass = bool(enough_folds and pass_count == folds_tested)
            reject_reason = "passed" if all_pass else ("too_few_folds" if not enough_folds else ";".join(reject_reasons[:5]))
            rows.append(
                {
                    "date": row.get("date", ""),
                    "test_day_ord": test_day,
                    "session": session,
                    "window_days": window_days,
                    "rule_id": str(row.get("rule_id", "")),
                    "conditions": str(row.get("conditions", "")),
                    "folds_tested": folds_tested,
                    "folds_passed": int(pass_count),
                    "fluctuation_pass_rate": pass_rate,
                    "fluctuation_all_folds_pass": all_pass,
                    "fluctuation_min_trades": int(np.nanmin(trades)) if trades.size else 0,
                    "fluctuation_min_win_rate": float(np.nanmin(win_rates)) if win_rates.size else float("nan"),
                    "fluctuation_min_positive_day_rate": float(np.nanmin(pos_rates)) if pos_rates.size else float("nan"),
                    "fluctuation_max_drawdown_pct": float(np.nanmax(drawdowns)) if drawdowns.size else float("nan"),
                    "fluctuation_worst_sum_r": float(np.nanmin(sum_rs)) if sum_rs.size else float("nan"),
                    "fluctuation_median_sum_r": float(np.nanmedian(sum_rs)) if sum_rs.size else float("nan"),
                    "fluctuation_sum_r_std": float(np.nanstd(sum_rs)) if sum_rs.size else float("nan"),
                    "fluctuation_reject_reason": reject_reason,
                }
            )
        else:
            rows.append(
                {
                    "date": row.get("date", ""),
                    "test_day_ord": test_day,
                    "session": session,
                    "window_days": window_days,
                    "rule_id": str(row.get("rule_id", "")),
                    "conditions": str(row.get("conditions", "")),
                    "folds_tested": 0,
                    "folds_passed": 0,
                    "fluctuation_pass_rate": 0.0,
                    "fluctuation_all_folds_pass": False,
                    "fluctuation_min_trades": 0,
                    "fluctuation_min_win_rate": 0.0,
                    "fluctuation_min_positive_day_rate": 0.0,
                    "fluctuation_max_drawdown_pct": 0.0,
                    "fluctuation_worst_sum_r": 0.0,
                    "fluctuation_median_sum_r": 0.0,
                    "fluctuation_sum_r_std": 0.0,
                    "fluctuation_reject_reason": "no_nonempty_folds",
                }
            )
    return pd.DataFrame(rows, columns=columns).sort_values(["date", "session", "window_days", "fluctuation_pass_rate"], ascending=[True, True, True, False]).reset_index(drop=True)


def _stress_fold_masks(context: _LedgerContext, train_mask: np.ndarray) -> list[tuple[str, np.ndarray, int]]:
    days = np.unique(context.day_ord[train_mask])
    days = days[days > 0]
    if days.size == 0:
        return []
    folds: list[tuple[str, np.ndarray, int]] = []

    def add_fold(name: str, selected_days: np.ndarray) -> None:
        if selected_days.size == 0:
            return
        mask = train_mask & np.isin(context.day_ord, selected_days)
        folds.append((name, mask, int(selected_days.size)))

    midpoint = int(math.ceil(days.size / 2.0))
    add_fold("first_half", days[:midpoint])
    add_fold("second_half", days[midpoint:])
    add_fold("odd_days", days[(days % 2) == 1])
    add_fold("even_days", days[(days % 2) == 0])
    for modulo in range(3):
        add_fold(f"day_mod3_{modulo}", days[(days % 3) == modulo])
    return folds


def _rule_mask_from_id(context: _LedgerContext, rule_id: str) -> np.ndarray:
    atom_lookup = {atom.atom_id: atom for atom in context.atoms}
    mask = np.ones(len(context.ledger), dtype=bool)
    if not rule_id:
        return np.zeros(len(context.ledger), dtype=bool)
    for atom_id in str(rule_id).split("&"):
        atom = atom_lookup.get(atom_id)
        if atom is None:
            return np.zeros(len(context.ledger), dtype=bool)
        mask &= atom.mask
    return mask


def _selection_drift(selected: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "test_day_ord",
        "session",
        "window_days",
        "selected_rules",
        "top_rule_id",
        "top_conditions",
        "top_score",
        "previous_top_rule_id",
        "top_rule_changed",
        "selected_set_jaccard_vs_previous",
        "top_condition_jaccard_vs_previous",
        "drift_state",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    frame = selected.copy()
    frame["test_day_ord"] = pd.to_numeric(frame["test_day_ord"], errors="coerce").fillna(0).astype("int64")
    frame["window_days"] = pd.to_numeric(frame["window_days"], errors="coerce").fillna(0).astype("int64")
    for (session, window), group in frame.groupby(["session", "window_days"], dropna=False):
        previous_rules: set[str] | None = None
        previous_top = ""
        previous_conditions = ""
        for test_day, day_group in group.sort_values(["test_day_ord", "train_score"], ascending=[True, False]).groupby("test_day_ord", sort=True):
            ranked = day_group.sort_values("train_score", ascending=False)
            top = ranked.iloc[0]
            rules = set(ranked["rule_id"].astype(str).tolist())
            top_rule = str(top.get("rule_id", ""))
            conditions = str(top.get("conditions", ""))
            if previous_rules is None:
                selected_jaccard = float("nan")
                condition_jaccard = float("nan")
                changed = False
                drift_state = "first_selection"
            else:
                selected_jaccard = _set_jaccard(rules, previous_rules)
                condition_jaccard = _condition_jaccard(conditions, previous_conditions)
                changed = top_rule != previous_top
                if not changed and selected_jaccard >= 0.80:
                    drift_state = "stable"
                elif not changed:
                    drift_state = "same_top_rule_changed_supporting_rules"
                elif selected_jaccard >= 0.50:
                    drift_state = "partial_rotation"
                else:
                    drift_state = "jitter"
            rows.append(
                {
                    "date": str(top.get("date", _date_from_ord(int(test_day)))),
                    "test_day_ord": int(test_day),
                    "session": str(session),
                    "window_days": int(window),
                    "selected_rules": int(len(rules)),
                    "top_rule_id": top_rule,
                    "top_conditions": conditions,
                    "top_score": float(top.get("train_score", float("nan"))),
                    "previous_top_rule_id": previous_top,
                    "top_rule_changed": bool(changed),
                    "selected_set_jaccard_vs_previous": selected_jaccard,
                    "top_condition_jaccard_vs_previous": condition_jaccard,
                    "drift_state": drift_state,
                }
            )
            previous_rules = rules
            previous_top = top_rule
            previous_conditions = conditions
    return pd.DataFrame(rows, columns=columns).sort_values(["date", "session", "window_days"]).reset_index(drop=True)


def _window_dynamics(selected: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "test_day_ord",
        "session",
        "window_pair",
        "short_window_top_rule_id",
        "long_window_top_rule_id",
        "short_window_top_conditions",
        "long_window_top_conditions",
        "top_rule_same_between_windows",
        "selected_set_jaccard_between_windows",
        "top_condition_jaccard_between_windows",
        "window_agreement_state",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    frame = selected.copy()
    frame["test_day_ord"] = pd.to_numeric(frame["test_day_ord"], errors="coerce").fillna(0).astype("int64")
    frame["window_days"] = pd.to_numeric(frame["window_days"], errors="coerce").fillna(0).astype("int64")
    rows: list[dict[str, object]] = []
    for (test_day, session), group in frame.groupby(["test_day_ord", "session"], dropna=False):
        by_window: dict[int, pd.DataFrame] = {int(window): part.copy() for window, part in group.groupby("window_days", dropna=False)}
        windows = sorted(by_window)
        for left, right in zip(windows, windows[1:]):
            short = by_window[left].sort_values("train_score", ascending=False)
            long = by_window[right].sort_values("train_score", ascending=False)
            if short.empty or long.empty:
                continue
            short_top = short.iloc[0]
            long_top = long.iloc[0]
            short_rules = set(short["rule_id"].astype(str).tolist())
            long_rules = set(long["rule_id"].astype(str).tolist())
            same_top = str(short_top.get("rule_id", "")) == str(long_top.get("rule_id", ""))
            selected_jaccard = _set_jaccard(short_rules, long_rules)
            condition_jaccard = _condition_jaccard(str(short_top.get("conditions", "")), str(long_top.get("conditions", "")))
            if same_top:
                state = "same_top_rule"
            elif selected_jaccard >= 0.50 or condition_jaccard >= 0.50:
                state = "related_rules"
            else:
                state = "window_disagreement"
            rows.append(
                {
                    "date": str(short_top.get("date", _date_from_ord(int(test_day)))),
                    "test_day_ord": int(test_day),
                    "session": str(session),
                    "window_pair": f"{left}d_vs_{right}d",
                    "short_window_top_rule_id": str(short_top.get("rule_id", "")),
                    "long_window_top_rule_id": str(long_top.get("rule_id", "")),
                    "short_window_top_conditions": str(short_top.get("conditions", "")),
                    "long_window_top_conditions": str(long_top.get("conditions", "")),
                    "top_rule_same_between_windows": bool(same_top),
                    "selected_set_jaccard_between_windows": selected_jaccard,
                    "top_condition_jaccard_between_windows": condition_jaccard,
                    "window_agreement_state": state,
                }
            )
    return pd.DataFrame(rows, columns=columns).sort_values(["date", "session", "window_pair"]).reset_index(drop=True)


def _set_jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return float(len(left & right) / len(union))


def _condition_jaccard(left: str, right: str) -> float:
    return _set_jaccard(_condition_tokens(left), _condition_tokens(right))


def _condition_tokens(value: str) -> set[str]:
    return {token.strip() for token in str(value).split(" AND ") if token.strip()}

def _break_even_top_removal_pct(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    n = int(finite.size)
    if n == 0:
        return 0.0
    total = float(np.sum(finite))
    if total <= 0.0:
        return 0.0
    ordered = np.sort(finite)[::-1]
    removed = np.cumsum(ordered)
    remaining = total - removed
    hit = np.flatnonzero(remaining <= 0.0)
    if hit.size == 0:
        return 100.0
    return float(100.0 * (int(hit[0]) + 1) / n)


def _break_even_symbol_removal_pct(values: np.ndarray, symbol_codes: np.ndarray) -> float:
    finite = np.isfinite(values)
    if not bool(np.any(finite)):
        return 0.0
    values = values[finite]
    symbol_codes = symbol_codes[finite]
    total = float(np.sum(values))
    if total <= 0.0:
        return 0.0
    unique_codes, inverse = np.unique(symbol_codes, return_inverse=True)
    by_symbol = np.bincount(inverse, weights=values)
    n = int(by_symbol.size)
    if n == 0:
        return 0.0
    ordered = np.sort(by_symbol)[::-1]
    remaining = total - np.cumsum(ordered)
    hit = np.flatnonzero(remaining <= 0.0)
    if hit.size == 0:
        return 100.0
    return float(100.0 * (int(hit[0]) + 1) / n)


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    series = frame[column]
    if series.dtype == bool:
        return series.fillna(False).astype(bool)
    text = series.fillna(False).astype(str).str.lower()
    return text.isin({"true", "1", "yes", "y"})


def _split_tokens(value: str) -> set[str]:
    normalized = str(value).replace(",", "|").replace(";", "|")
    return {token.strip() for token in normalized.split("|") if token.strip() and token.strip().lower() not in {"none", "nan"}}


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)).strip("_").lower()[:140]


def _timestamp_to_utc(timestamp_ms: int | float) -> str:
    if not math.isfinite(float(timestamp_ms)):
        return ""
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, UTC).isoformat()


def _date_from_ord(day_ord: int) -> str:
    return datetime.fromtimestamp(int(day_ord) * DAY_MS / 1000, UTC).strftime("%Y-%m-%d")


def _session_features(timestamp_ms: int) -> dict[str, object]:
    ts = pd.to_datetime(int(timestamp_ms), unit="ms", utc=True)
    minute_of_day = int(ts.hour) * 60 + int(ts.minute)
    sessions = {
        "asia": (0, 8 * 60),
        "europe": (7 * 60, 16 * 60),
        "us": (13 * 60, 22 * 60),
    }
    active = {name: start <= minute_of_day < end for name, (start, end) in sessions.items()}
    active_names = [name for name, enabled in active.items() if enabled]
    return {
        "session_primary": active_names[-1] if active_names else "off_session",
        "session_overlap": bool(sum(1 for value in active.values() if value) >= 2),
    }


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
