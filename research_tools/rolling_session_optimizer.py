"""Professional rolling session optimizer for large-runner research artifacts.

The optimizer is intentionally ledger-only: it never reloads candles, rescans
levels, or resimulates exits.  It reads the existing large-runner trade ledger,
selects rules from past windows only, applies them to the next OOS day, and
writes enough audit artifacts to see whether adaptation is stable or just noise.

P540 methodology:
* session buckets are separated: asia_only, asia_europe_overlap, europe_only,
  europe_us_overlap, us_only, off_session;
* 15/30/60d windows have different roles: recency / main / robustness;
* rules are assigned states: core, strong, tactical, challenger, cooldown,
  rejected;
* selected trades are weighted by state and protected against clones;
* stress folds, window agreement, OOS memory and cooldowns are first-class
  artifacts.
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

OPTIMIZER_WINDOWS = (15, 30, 60)
OPTIMIZER_RECENCY_WINDOW = 15
OPTIMIZER_MAIN_WINDOW = 30
OPTIMIZER_ROBUST_WINDOW = 60
OPTIMIZER_SESSION_BUCKETS = (
    "asia_only",
    "asia_europe_overlap",
    "europe_only",
    "europe_us_overlap",
    "us_only",
    "off_session",
)
OPTIMIZER_SOURCE_FILE = "large_runner_trade_grid.csv"
OPTIMIZER_RISK_PER_TRADE_PCT = 0.03

# Rule-level trade minima. Portfolio frequency is controlled separately by the
# number of selected rules and OOS caps; requiring 0.7 trades/day per single rule
# would turn the selector into a museum exhibit.
MIN_TRADES_BY_WINDOW = {15: 5, 30: 12, 60: 20}
SOFT_MIN_TRADES_BY_WINDOW = {15: 3, 30: 8, 60: 12}

# Main gates.
MAIN_MIN_WIN_RATE = 0.50
MAIN_MIN_POSITIVE_DAY_RATE = 0.70
MAIN_MAX_DRAWDOWN_PCT = 0.15
MAIN_MIN_TOP_TRADE_INDEPENDENCE_PCT = 40.0
MAIN_MIN_TOP_SYMBOL_INDEPENDENCE_PCT = 30.0

# Recency gates: 15d proves the rule is alive now, not necessarily perfect.
RECENCY_ALIVE_MIN_WIN_RATE = 0.45
RECENCY_ALIVE_MIN_POSITIVE_DAY_RATE = 0.55
RECENCY_ALIVE_MAX_DRAWDOWN_PCT = 0.20
RECENCY_STRONG_MIN_WIN_RATE = 0.55
RECENCY_STRONG_MIN_POSITIVE_DAY_RATE = 0.70

# Robustness gates: 60d must not be dead.
ROBUST_MIN_WIN_RATE = 0.50
ROBUST_MIN_POSITIVE_DAY_RATE = 0.65
ROBUST_MAX_DRAWDOWN_PCT = 0.15
ROBUST_MIN_TOP_TRADE_INDEPENDENCE_PCT = 30.0
ROBUST_MIN_TOP_SYMBOL_INDEPENDENCE_PCT = 20.0
ROBUST_NOT_DEAD_MIN_WIN_RATE = 0.42
ROBUST_NOT_DEAD_MIN_POSITIVE_DAY_RATE = 0.55
ROBUST_NOT_DEAD_MAX_DRAWDOWN_PCT = 0.25
ROBUST_NOT_DEAD_MIN_TOP_TRADE_INDEPENDENCE_PCT = 10.0

OPTIMIZER_MAX_RULE_DEPTH = 3
OPTIMIZER_BEAM_WIDTH = 50
OPTIMIZER_REJECTED_RULES_PER_GROUP = 30
OPTIMIZER_MAX_RULES_PER_SESSION = 5
OPTIMIZER_MAX_TRADES_PER_SYMBOL_SESSION_DAY = 1
OPTIMIZER_MAX_TRADES_PER_RULE_SESSION_DAY = 2
OPTIMIZER_MAX_TRADES_PER_SESSION_DAY = 5

STATE_RISK_WEIGHTS = {
    "core": 1.00,
    "strong": 0.60,
    "tactical": 0.30,
    "challenger": 0.00,
    "cooldown": 0.00,
    "rejected": 0.00,
}
STATE_PRIORITY = {"core": 4, "strong": 3, "tactical": 2, "challenger": 1, "cooldown": 0, "rejected": -1}

STRESS_MIN_PASS_RATE = 0.70
STRESS_WORST_FOLD_SUM_R_MIN = -2.0
STRESS_MIN_FOLD_WIN_RATE = 0.35
STRESS_MIN_FOLDS = 4

# OOS memory/cooldown.
OOS_BAD_LAST_SELECTED_DAY_SUM_R = -3.0
OOS_BAD_LAST_TRADE_WR = 0.30
OOS_BAD_LAST_TRADE_MIN_COUNT = 5
OOS_COOLDOWN_DAYS = 7
OOS_CONSECUTIVE_NEGATIVE_DAYS_FOR_COOLDOWN = 3
OOS_CONSECUTIVE_NEGATIVE_COOLDOWN_DAYS = 5


@dataclass(frozen=True, slots=True)
class RollingSessionOptimizerConfig:
    input_dir: Path
    output_dir: Path | None = None
    windows: tuple[int, ...] = OPTIMIZER_WINDOWS
    sessions: tuple[str, ...] = OPTIMIZER_SESSION_BUCKETS
    source_file: str = OPTIMIZER_SOURCE_FILE
    risk_per_trade_pct: float = OPTIMIZER_RISK_PER_TRADE_PCT
    max_rule_depth: int = OPTIMIZER_MAX_RULE_DEPTH
    beam_width: int = OPTIMIZER_BEAM_WIDTH
    max_rules_per_session: int = OPTIMIZER_MAX_RULES_PER_SESSION
    rejected_rules_per_group: int = OPTIMIZER_REJECTED_RULES_PER_GROUP

    def __post_init__(self) -> None:
        if not self.input_dir:
            raise ValueError("input_dir is required")
        if tuple(self.windows) != OPTIMIZER_WINDOWS:
            raise ValueError("P540 uses fixed 15/30/60 windows")
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


@dataclass(slots=True)
class OosMemoryState:
    selected_day_rs: list[float]
    trade_rs: list[float]
    cooldown_until_day: int = 0
    cooldown_reason: str = ""


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
    challenger_rows: list[dict[str, object]] = []
    rejected_rows: list[dict[str, object]] = []
    oos_rows: list[dict[str, object]] = []
    window_health_rows: list[dict[str, object]] = []
    stress_rows: list[dict[str, object]] = []
    oos_memory_rows: list[dict[str, object]] = []
    cooldown_rows: list[dict[str, object]] = []

    memory: dict[str, OosMemoryState] = {}
    max_window = max(int(window) for window in config.windows)
    all_days = sorted(int(day) for day in np.unique(context.day_ord) if int(day) > 0)
    if not all_days:
        raise ValueError("ledger has no valid entry days")
    first_oos_day = min(all_days) + max_window
    oos_days = [day for day in all_days if day >= first_oos_day]

    for day_index, test_day in enumerate(oos_days, start=1):
        if day_index == 1 or day_index % 10 == 0 or day_index == len(oos_days):
            pct = 100.0 * day_index / max(len(oos_days), 1)
            print(
                f"rolling session optimizer: day {day_index}/{len(oos_days)} ({pct:5.1f}%) {_date_from_ord(test_day)}",
                flush=True,
            )
        for session in config.sessions:
            session_mask = context.session_mask(session)
            test_mask = session_mask & (context.day_ord == test_day)
            if not bool(np.any(test_mask)):
                continue
            result = _select_rules_for_session_day(
                context,
                session=session,
                test_day=test_day,
                session_mask=session_mask,
                memory=memory,
            )
            selected_rows.extend(result["selected"])
            challenger_rows.extend(result["challenger"])
            rejected_rows.extend(result["rejected"])
            window_health_rows.extend(result["window_health"])
            stress_rows.extend(result["stress"])
            cooldown_rows.extend(result["cooldowns"])
            selected_for_trade = [row for row in result["selected"] if str(row.get("selection_state")) in {"core", "strong", "tactical"}]
            day_oos = _apply_selected_rules_oos(context, selected_for_trade, test_mask, test_day=test_day, session=session)
            oos_rows.extend(day_oos)
            _update_oos_memory(
                memory,
                selected_for_trade,
                day_oos,
                test_day=test_day,
                session=session,
                memory_rows=oos_memory_rows,
            )

    selected_frame = pd.DataFrame(selected_rows)
    challenger_frame = pd.DataFrame(challenger_rows)
    rejected_frame = pd.DataFrame(rejected_rows)
    oos_frame = pd.DataFrame(oos_rows)
    window_health_frame = pd.DataFrame(window_health_rows)
    stress_frame = pd.DataFrame(stress_rows)
    oos_memory_frame = pd.DataFrame(oos_memory_rows)
    cooldown_frame = pd.DataFrame(cooldown_rows)
    daily_frame = _daily_summary(oos_frame)
    health_frame = _rule_health(selected_frame, oos_frame)
    drift_frame = _selection_drift(selected_frame)
    window_dynamics_frame = _window_dynamics(window_health_frame, selected_frame)
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
                "optimizer_model": "rolling_session_optimizer_v4_professional_15_30_60_tiered_oos_memory",
                "selection_model": "30d_candidates_15d_recency_60d_robustness_session_buckets_no_future_data",
                "session_model": "asia_only_asia_europe_overlap_europe_only_europe_us_overlap_us_only_off_session",
                "state_model": "core_strong_tactical_challenger_cooldown_rejected_weighted_oos",
                "stress_model": "first_second_half_odd_even_mod3_week_folds_no_future_data",
                "anti_clone_model": "max_one_symbol_session_max_two_rule_session_max_five_session_day",
                "runtime_seconds": round(time.monotonic() - started, 3),
            }
        ]
    )

    _write_csv(output_dir / "rolling_session_selected_rules.csv", selected_frame)
    _write_csv(output_dir / "rolling_session_challenger_rules.csv", challenger_frame)
    _write_csv(output_dir / "rolling_session_rejected_rules.csv", rejected_frame)
    _write_csv(output_dir / "rolling_session_oos_trades.csv", oos_frame)
    _write_csv(output_dir / "rolling_session_daily_summary.csv", daily_frame)
    _write_csv(output_dir / "rolling_session_rule_health.csv", health_frame)
    _write_csv(output_dir / "rolling_session_window_health.csv", window_health_frame)
    _write_csv(output_dir / "rolling_session_fluctuation_stress.csv", stress_frame)
    _write_csv(output_dir / "rolling_session_selection_drift.csv", drift_frame)
    _write_csv(output_dir / "rolling_session_window_dynamics.csv", window_dynamics_frame)
    _write_csv(output_dir / "rolling_session_oos_memory.csv", oos_memory_frame)
    _write_csv(output_dir / "rolling_session_cooldowns.csv", cooldown_frame)
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
        self.symbols = self.ledger["symbol"].astype(str).to_numpy(dtype=object)
        self.symbol_code = pd.factorize(self.ledger["symbol"].astype(str), sort=False)[0].astype("int64")
        self.session_bucket = self.ledger["session_bucket"].astype(str).to_numpy(dtype=object)
        self.trade_key = self.ledger["rolling_trade_key"].astype(str).to_numpy(dtype=object)
        self.atom_lookup = {atom.atom_id: atom for atom in atoms}

    def session_mask(self, session: str) -> np.ndarray:
        return self.session_bucket == str(session)


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
    if "exit_policy_id" not in work.columns:
        work["exit_policy_id"] = "unknown_exit"
    if "arm_id" not in work.columns:
        work["arm_id"] = "unknown_arm"
    if "setup_selection_model" not in work.columns:
        work["setup_selection_model"] = "unknown_setup"
    session_rows = work["entry_timestamp_ms"].map(lambda value: _session_features(int(value)))
    if "session_primary" not in work.columns:
        work["session_primary"] = session_rows.map(lambda value: value["session_primary"])
    if "session_overlap" not in work.columns:
        work["session_overlap"] = session_rows.map(lambda value: value["session_overlap"])
    work["session_bucket"] = session_rows.map(lambda value: value["session_bucket"])
    work["hour_utc"] = session_rows.map(lambda value: value["hour_utc"])
    work["weekday"] = session_rows.map(lambda value: value["weekday"])
    ts = pd.to_datetime(work["entry_timestamp_ms"], unit="ms", utc=True)
    work["entry_date"] = ts.dt.strftime("%Y-%m-%d")
    work["entry_day_ord"] = (work["entry_timestamp_ms"] // DAY_MS).astype("int64")
    work["rolling_trade_key"] = work["symbol"].astype(str) + "|" + work["entry_timestamp_ms"].astype(str)
    work.sort_values(["entry_timestamp_ms", "symbol", "arm_id", "exit_policy_id"], inplace=True)
    work.reset_index(drop=True, inplace=True)
    return work


def _build_atoms(ledger: pd.DataFrame) -> list[RuleAtom]:
    atoms: list[RuleAtom] = []

    def add_atom(column: str, label: str, mask: np.ndarray) -> None:
        mask_bool = mask.astype(bool) if mask.dtype != bool else mask
        support = int(mask_bool.sum())
        if support <= 0 or support >= len(ledger):
            return
        atom_id = _safe_id(f"{column}:{label}")
        atoms.append(RuleAtom(atom_id=atom_id, label=f"{column} {label}", column=column, mask=mask_bool))

    categorical_columns = [
        "arm_id",
        "exit_policy_id",
        "setup_selection_model",
        "session_bucket",
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
        split_cache = [tuple(_split_tokens(text)) for text in series.tolist()]
        for tokens_in_row in split_cache:
            for token in tokens_in_row:
                tokens[token] = tokens.get(token, 0) + 1
        for token, count in sorted(tokens.items(), key=lambda item: (-item[1], item[0])):
            if count < 5:
                continue
            add_atom(column, f"contains {token}", np.array([token in row_tokens for row_tokens in split_cache], dtype=bool))

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
    atoms.sort(key=lambda atom: atom.atom_id)
    return atoms


def _select_rules_for_session_day(
    context: _LedgerContext,
    *,
    session: str,
    test_day: int,
    session_mask: np.ndarray,
    memory: dict[str, OosMemoryState],
) -> dict[str, list[dict[str, object]]]:
    config = context.config
    main_window = OPTIMIZER_MAIN_WINDOW
    main_train_mask = session_mask & (context.day_ord >= test_day - main_window) & (context.day_ord < test_day)
    if not bool(np.any(main_train_mask)):
        return {"selected": [], "challenger": [], "rejected": [], "window_health": [], "stress": [], "cooldowns": []}

    candidate_rules = _beam_candidate_rules(context, main_train_mask, window_days=main_window)
    evaluated: list[tuple[float, str, CandidateRule, dict[str, object], dict[int, dict[str, object]], dict[str, object], str]] = []
    rejected: list[dict[str, object]] = []
    window_health_rows: list[dict[str, object]] = []
    stress_rows: list[dict[str, object]] = []
    cooldown_rows: list[dict[str, object]] = []

    for rule in candidate_rules:
        window_metrics: dict[int, dict[str, object]] = {}
        for window in OPTIMIZER_WINDOWS:
            train_mask = session_mask & (context.day_ord >= test_day - int(window)) & (context.day_ord < test_day)
            metrics = _metrics(context, train_mask & rule.mask, window_days=int(window), risk_per_trade_pct=config.risk_per_trade_pct)
            window_metrics[int(window)] = metrics
            window_health_rows.append(
                {
                    "date": _date_from_ord(test_day),
                    "test_day_ord": int(test_day),
                    "session": session,
                    "window_days": int(window),
                    "rule_id": rule.rule_id,
                    "conditions": " | ".join(rule.conditions),
                    **_prefix_metrics(metrics, "window"),
                }
            )
        stress = _stress_metrics(context, session_mask & (context.day_ord >= test_day - main_window) & (context.day_ord < test_day), rule, window_days=main_window)
        stress_rows.append(
            {
                "date": _date_from_ord(test_day),
                "test_day_ord": int(test_day),
                "session": session,
                "rule_id": rule.rule_id,
                "conditions": " | ".join(rule.conditions),
                **stress,
            }
        )
        memory_key = _memory_key(session, rule.rule_id)
        memory_state = memory.get(memory_key, OosMemoryState([], []))
        state, reject_reason, cooldown_row = _selection_state(rule, window_metrics, stress, memory_state, test_day=test_day, session=session)
        if cooldown_row:
            cooldown_rows.append(cooldown_row)
        score = _final_score(window_metrics, stress, memory_state, state)
        row_base = {
            "date": _date_from_ord(test_day),
            "test_day_ord": int(test_day),
            "session": session,
            "rule_id": rule.rule_id,
            "conditions": " | ".join(rule.conditions),
            "selection_state": state,
            "risk_weight": STATE_RISK_WEIGHTS.get(state, 0.0),
            "final_score": score,
            "reject_reason": reject_reason,
            **_prefix_metrics(window_metrics[15], "w15"),
            **_prefix_metrics(window_metrics[30], "w30"),
            **_prefix_metrics(window_metrics[60], "w60"),
            **{f"stress_{key}": value for key, value in stress.items()},
            **_memory_summary(memory_state),
        }
        if state in {"core", "strong", "tactical"}:
            evaluated.append((score, state, rule, row_base, window_metrics, stress, reject_reason))
        elif state == "challenger":
            rejected.append(row_base)
        else:
            rejected.append(row_base)

    evaluated.sort(key=lambda item: (STATE_PRIORITY.get(item[1], 0), item[0]), reverse=True)
    selected_rows: list[dict[str, object]] = []
    used_rule_ids: set[str] = set()
    for score, state, rule, row_base, _, _, _ in evaluated:
        if len(selected_rows) >= int(config.max_rules_per_session):
            break
        if _is_redundant_rule(rule.rule_id, used_rule_ids):
            continue
        selected_rows.append(row_base)
        used_rule_ids.add(rule.rule_id)

    selected_ids = {str(row["rule_id"]) for row in selected_rows}
    challenger_rows: list[dict[str, object]] = []
    rejected_rows: list[dict[str, object]] = []
    for row in rejected:
        if row["selection_state"] == "challenger":
            challenger_rows.append(row)
        else:
            rejected_rows.append(row)
    for score, state, rule, row_base, _, _, _ in evaluated:
        if rule.rule_id not in selected_ids:
            challenger = dict(row_base)
            challenger["selection_state"] = "challenger"
            challenger["risk_weight"] = 0.0
            challenger["reject_reason"] = "not_in_top_session_portfolio_or_redundant"
            challenger_rows.append(challenger)
    rejected_rows = sorted(rejected_rows, key=lambda row: float(row.get("final_score", 0.0)), reverse=True)[: int(config.rejected_rules_per_group)]
    challenger_rows = sorted(challenger_rows, key=lambda row: float(row.get("final_score", 0.0)), reverse=True)[: int(config.rejected_rules_per_group)]

    return {
        "selected": selected_rows,
        "challenger": challenger_rows,
        "rejected": rejected_rows,
        "window_health": window_health_rows,
        "stress": stress_rows,
        "cooldowns": cooldown_rows,
    }


def _beam_candidate_rules(context: _LedgerContext, train_mask: np.ndarray, *, window_days: int) -> list[CandidateRule]:
    config = context.config
    min_trades = SOFT_MIN_TRADES_BY_WINDOW.get(int(window_days), 8)
    single_candidates: list[tuple[float, CandidateRule, dict[str, object]]] = []
    for atom in context.atoms:
        rule = CandidateRule(atom.atom_id, (atom.label,), (atom.atom_id,), (atom.column,), atom.mask)
        metrics = _metrics(context, train_mask & rule.mask, window_days=window_days, risk_per_trade_pct=config.risk_per_trade_pct)
        if int(metrics.get("trades", 0)) >= min_trades:
            single_candidates.append((_soft_score(metrics), rule, metrics))
    single_candidates.sort(key=lambda item: item[0], reverse=True)
    all_rules: dict[str, CandidateRule] = {item[1].rule_id: item[1] for item in single_candidates[: int(config.beam_width) * 2]}
    current_beam = [item[1] for item in single_candidates[: int(config.beam_width)]]
    atom_pool = [item[1] for item in single_candidates[: int(config.beam_width)]]
    seen = set(all_rules)
    max_depth = min(int(config.max_rule_depth), OPTIMIZER_MAX_RULE_DEPTH)
    for _depth in range(2, max_depth + 1):
        next_candidates: list[tuple[float, CandidateRule, dict[str, object]]] = []
        for base in current_beam:
            for atom_rule in atom_pool:
                atom_id = atom_rule.atom_ids[0]
                if atom_id in base.atom_ids:
                    continue
                if atom_rule.columns[0] in base.columns:
                    continue
                atom_ids = tuple(sorted((*base.atom_ids, atom_id)))
                rule_id = "&".join(atom_ids)
                if rule_id in seen:
                    continue
                seen.add(rule_id)
                mask = base.mask & atom_rule.mask
                if int((train_mask & mask).sum()) < min_trades:
                    continue
                conditions_by_id = {aid: label for aid, label in zip(base.atom_ids, base.conditions)}
                columns_by_id = {aid: column for aid, column in zip(base.atom_ids, base.columns)}
                conditions_by_id[atom_id] = atom_rule.conditions[0]
                columns_by_id[atom_id] = atom_rule.columns[0]
                rule = CandidateRule(
                    rule_id=rule_id,
                    conditions=tuple(conditions_by_id[aid] for aid in atom_ids),
                    atom_ids=atom_ids,
                    columns=tuple(columns_by_id[aid] for aid in atom_ids),
                    mask=mask,
                )
                metrics = _metrics(context, train_mask & rule.mask, window_days=window_days, risk_per_trade_pct=config.risk_per_trade_pct)
                score = _soft_score(metrics) - 0.03 * (len(rule.atom_ids) - 1)
                next_candidates.append((score, rule, metrics))
        next_candidates.sort(key=lambda item: item[0], reverse=True)
        current_beam = [item[1] for item in next_candidates[: int(config.beam_width)]]
        for _, rule, _ in next_candidates[: int(config.beam_width) * 2]:
            all_rules.setdefault(rule.rule_id, rule)
        if not current_beam:
            break
    return list(all_rules.values())


def _selection_state(
    rule: CandidateRule,
    window_metrics: Mapping[int, Mapping[str, object]],
    stress: Mapping[str, object],
    memory_state: OosMemoryState,
    *,
    test_day: int,
    session: str,
) -> tuple[str, str, dict[str, object] | None]:
    if int(memory_state.cooldown_until_day) > int(test_day):
        row = {
            "date": _date_from_ord(test_day),
            "test_day_ord": int(test_day),
            "session": session,
            "rule_id": rule.rule_id,
            "conditions": " | ".join(rule.conditions),
            "cooldown_until_day": int(memory_state.cooldown_until_day),
            "cooldown_until_date": _date_from_ord(int(memory_state.cooldown_until_day)),
            "cooldown_reason": memory_state.cooldown_reason,
        }
        return "cooldown", memory_state.cooldown_reason or "cooldown_active", row
    m15 = window_metrics.get(15, {})
    m30 = window_metrics.get(30, {})
    m60 = window_metrics.get(60, {})
    main_strong = _main_strong(m30)
    recency_alive = _recency_alive(m15)
    recency_strong = _recency_strong(m15)
    robust_strong = _robust_strong(m60)
    robust_not_dead = _robust_not_dead(m60)
    stress_ok = _bool(stress.get("stress_pass"))
    memory_ok = _memory_ok(memory_state)
    if main_strong and recency_alive and robust_strong and stress_ok and memory_ok:
        return "core", "selected_core", None
    if main_strong and recency_alive and robust_not_dead and stress_ok and memory_ok:
        return "strong", "selected_strong", None
    if recency_strong and _not_dead(m30) and _not_catastrophic(m60) and stress_ok and memory_ok:
        return "tactical", "selected_tactical_recency", None
    if (main_strong or recency_strong) and _not_catastrophic(m60) and memory_ok:
        return "challenger", "candidate_but_not_tradeable_yet", None
    reasons = []
    if not main_strong:
        reasons.append("main30_not_strong")
    if not recency_alive:
        reasons.append("recency15_not_alive")
    if not robust_not_dead:
        reasons.append("robust60_dead")
    if not stress_ok:
        reasons.append("stress_failed")
    if not memory_ok:
        reasons.append("oos_memory_bad")
    return "rejected", "+".join(reasons) or "rejected", None


def _main_strong(m: Mapping[str, object]) -> bool:
    return bool(
        int(m.get("trades", 0)) >= MIN_TRADES_BY_WINDOW[30]
        and float(m.get("sum_r", 0.0)) > 0.0
        and float(m.get("win_rate", 0.0)) >= MAIN_MIN_WIN_RATE
        and float(m.get("positive_active_day_rate", 0.0)) >= MAIN_MIN_POSITIVE_DAY_RATE
        and float(m.get("max_drawdown_pct", 1.0)) <= MAIN_MAX_DRAWDOWN_PCT
        and float(m.get("top_trade_independence_pct", 0.0)) >= MAIN_MIN_TOP_TRADE_INDEPENDENCE_PCT
        and float(m.get("top_symbol_independence_pct", 0.0)) >= MAIN_MIN_TOP_SYMBOL_INDEPENDENCE_PCT
    )


def _recency_alive(m: Mapping[str, object]) -> bool:
    return bool(
        int(m.get("trades", 0)) >= MIN_TRADES_BY_WINDOW[15]
        and float(m.get("sum_r", 0.0)) > 0.0
        and float(m.get("win_rate", 0.0)) >= RECENCY_ALIVE_MIN_WIN_RATE
        and float(m.get("positive_active_day_rate", 0.0)) >= RECENCY_ALIVE_MIN_POSITIVE_DAY_RATE
        and float(m.get("max_drawdown_pct", 1.0)) <= RECENCY_ALIVE_MAX_DRAWDOWN_PCT
    )


def _recency_strong(m: Mapping[str, object]) -> bool:
    return bool(_recency_alive(m) and float(m.get("win_rate", 0.0)) >= RECENCY_STRONG_MIN_WIN_RATE and float(m.get("positive_active_day_rate", 0.0)) >= RECENCY_STRONG_MIN_POSITIVE_DAY_RATE)


def _robust_strong(m: Mapping[str, object]) -> bool:
    return bool(
        int(m.get("trades", 0)) >= MIN_TRADES_BY_WINDOW[60]
        and float(m.get("sum_r", 0.0)) > 0.0
        and float(m.get("win_rate", 0.0)) >= ROBUST_MIN_WIN_RATE
        and float(m.get("positive_active_day_rate", 0.0)) >= ROBUST_MIN_POSITIVE_DAY_RATE
        and float(m.get("max_drawdown_pct", 1.0)) <= ROBUST_MAX_DRAWDOWN_PCT
        and float(m.get("top_trade_independence_pct", 0.0)) >= ROBUST_MIN_TOP_TRADE_INDEPENDENCE_PCT
        and float(m.get("top_symbol_independence_pct", 0.0)) >= ROBUST_MIN_TOP_SYMBOL_INDEPENDENCE_PCT
    )


def _robust_not_dead(m: Mapping[str, object]) -> bool:
    return bool(
        int(m.get("trades", 0)) >= SOFT_MIN_TRADES_BY_WINDOW[60]
        and float(m.get("sum_r", 0.0)) >= 0.0
        and float(m.get("win_rate", 0.0)) >= ROBUST_NOT_DEAD_MIN_WIN_RATE
        and float(m.get("positive_active_day_rate", 0.0)) >= ROBUST_NOT_DEAD_MIN_POSITIVE_DAY_RATE
        and float(m.get("max_drawdown_pct", 1.0)) <= ROBUST_NOT_DEAD_MAX_DRAWDOWN_PCT
        and float(m.get("top_trade_independence_pct", 0.0)) >= ROBUST_NOT_DEAD_MIN_TOP_TRADE_INDEPENDENCE_PCT
    )


def _not_dead(m: Mapping[str, object]) -> bool:
    return bool(
        int(m.get("trades", 0)) >= SOFT_MIN_TRADES_BY_WINDOW[30]
        and float(m.get("sum_r", 0.0)) >= 0.0
        and float(m.get("win_rate", 0.0)) >= 0.42
        and float(m.get("positive_active_day_rate", 0.0)) >= 0.55
        and float(m.get("max_drawdown_pct", 1.0)) <= 0.25
    )


def _not_catastrophic(m: Mapping[str, object]) -> bool:
    return bool(float(m.get("sum_r", 0.0)) >= -2.0 and float(m.get("max_drawdown_pct", 1.0)) <= 0.35)


def _memory_ok(memory_state: OosMemoryState) -> bool:
    last_days = memory_state.selected_day_rs[-5:]
    if len(last_days) >= 5 and sum(last_days) < OOS_BAD_LAST_SELECTED_DAY_SUM_R:
        return False
    if len(last_days) >= OOS_CONSECUTIVE_NEGATIVE_DAYS_FOR_COOLDOWN and all(value < 0.0 for value in last_days[-OOS_CONSECUTIVE_NEGATIVE_DAYS_FOR_COOLDOWN:]):
        return False
    trades = memory_state.trade_rs[-10:]
    if len(trades) >= OOS_BAD_LAST_TRADE_MIN_COUNT:
        wr = sum(1 for value in trades if value > 0.0) / len(trades)
        if wr < OOS_BAD_LAST_TRADE_WR:
            return False
    return True


def _final_score(
    window_metrics: Mapping[int, Mapping[str, object]],
    stress: Mapping[str, object],
    memory_state: OosMemoryState,
    state: str,
) -> float:
    m15 = window_metrics.get(15, {})
    m30 = window_metrics.get(30, {})
    m60 = window_metrics.get(60, {})
    base = (
        1.4 * _health_score(m30)
        + 0.8 * _health_score(m15)
        + 0.8 * _health_score(m60)
    )
    stress_mult = max(0.0, min(1.2, float(stress.get("stress_pass_rate", 0.0)) + 0.25))
    memory_mult = 1.0 if _memory_ok(memory_state) else 0.2
    state_mult = {"core": 1.25, "strong": 1.0, "tactical": 0.75, "challenger": 0.5}.get(state, 0.1)
    return float(base * stress_mult * memory_mult * state_mult)


def _health_score(m: Mapping[str, object]) -> float:
    trades = float(m.get("trades", 0.0))
    return float(
        1.8 * float(m.get("win_rate", 0.0))
        + 1.6 * float(m.get("positive_active_day_rate", 0.0))
        + 0.015 * min(100.0, float(m.get("top_trade_independence_pct", 0.0)))
        + 0.010 * min(100.0, float(m.get("top_symbol_independence_pct", 0.0)))
        + 0.25 * math.tanh(trades / 20.0)
        + 0.10 * float(m.get("median_r", 0.0))
        + 0.08 * float(m.get("sum_r", 0.0))
        - 2.0 * float(m.get("max_drawdown_pct", 0.0))
    )


def _soft_score(metrics: Mapping[str, object]) -> float:
    return _health_score(metrics)


def _metrics(context: _LedgerContext, mask: np.ndarray, *, window_days: int, risk_per_trade_pct: float) -> dict[str, object]:
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return _empty_metrics(window_days)
    r = context.net_r[idx]
    days = context.day_ord[idx]
    symbols = context.symbol_code[idx]
    day_sums = _group_sum_int(days, r)
    active_days = len(day_sums)
    equity = np.cumsum(r * float(risk_per_trade_pct))
    running_max = np.maximum.accumulate(np.insert(equity, 0, 0.0))[1:]
    drawdown = running_max - equity
    max_dd = float(np.max(drawdown)) if drawdown.size else 0.0
    symbol_sums = _group_sum_int(symbols, r)
    return {
        "window_days": int(window_days),
        "trades": int(idx.size),
        "active_days": int(active_days),
        "trades_per_day": float(idx.size / max(int(window_days), 1)),
        "win_rate": float(np.mean(r > 0.0)),
        "avg_r": float(np.mean(r)),
        "median_r": float(np.median(r)),
        "sum_r": float(np.sum(r)),
        "positive_active_days": int(sum(1 for value in day_sums.values() if value > 0.0)),
        "positive_active_day_rate": float(sum(1 for value in day_sums.values() if value > 0.0) / max(active_days, 1)),
        "positive_calendar_day_rate": float(sum(1 for value in day_sums.values() if value > 0.0) / max(int(window_days), 1)),
        "max_drawdown_pct": max_dd,
        "top_trade_independence_pct": _top_trade_independence_pct(r),
        "top_symbol_independence_pct": _top_symbol_independence_pct(symbol_sums),
        "symbols": int(len(symbol_sums)),
    }


def _empty_metrics(window_days: int) -> dict[str, object]:
    return {
        "window_days": int(window_days),
        "trades": 0,
        "active_days": 0,
        "trades_per_day": 0.0,
        "win_rate": 0.0,
        "avg_r": 0.0,
        "median_r": 0.0,
        "sum_r": 0.0,
        "positive_active_days": 0,
        "positive_active_day_rate": 0.0,
        "positive_calendar_day_rate": 0.0,
        "max_drawdown_pct": 0.0,
        "top_trade_independence_pct": 0.0,
        "top_symbol_independence_pct": 0.0,
        "symbols": 0,
    }


def _top_trade_independence_pct(values: np.ndarray) -> float:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0
    total = float(np.sum(finite))
    if total <= 0.0:
        return 0.0
    ordered = np.sort(finite)[::-1]
    remaining = total
    for index, value in enumerate(ordered, start=1):
        remaining -= float(value)
        if remaining <= 0.0:
            return float(100.0 * index / len(ordered))
    return 100.0


def _top_symbol_independence_pct(symbol_sums: Mapping[int, float]) -> float:
    if not symbol_sums:
        return 0.0
    values = np.array(list(symbol_sums.values()), dtype=float)
    total = float(np.sum(values))
    if total <= 0.0:
        return 0.0
    ordered = np.sort(values)[::-1]
    remaining = total
    for index, value in enumerate(ordered, start=1):
        remaining -= float(value)
        if remaining <= 0.0:
            return float(100.0 * index / len(ordered))
    return 100.0


def _group_sum_int(keys: np.ndarray, values: np.ndarray) -> dict[int, float]:
    result: dict[int, float] = {}
    for key, value in zip(keys.tolist(), values.tolist()):
        result[int(key)] = result.get(int(key), 0.0) + float(value)
    return result


def _stress_metrics(context: _LedgerContext, train_mask: np.ndarray, rule: CandidateRule, *, window_days: int) -> dict[str, object]:
    folds = _stress_fold_masks(context, train_mask)
    rows: list[dict[str, object]] = []
    for fold_name, fold_mask, fold_days in folds:
        metrics = _metrics(context, fold_mask & rule.mask, window_days=max(int(fold_days), 1), risk_per_trade_pct=context.config.risk_per_trade_pct)
        passed = bool(
            int(metrics.get("trades", 0)) >= max(2, min(5, MIN_TRADES_BY_WINDOW[15]))
            and float(metrics.get("sum_r", 0.0)) >= STRESS_WORST_FOLD_SUM_R_MIN
            and float(metrics.get("win_rate", 0.0)) >= STRESS_MIN_FOLD_WIN_RATE
        )
        rows.append({"fold": fold_name, "fold_days": int(fold_days), "passed": passed, **metrics})
    nonempty = [row for row in rows if int(row.get("trades", 0)) > 0]
    if not nonempty:
        return {
            "stress_folds_tested": 0,
            "stress_folds_passed": 0,
            "stress_pass_rate": 0.0,
            "stress_pass": False,
            "stress_min_win_rate": 0.0,
            "stress_min_positive_day_rate": 0.0,
            "stress_worst_sum_r": 0.0,
            "stress_sum_r_std": 0.0,
            "stress_reject_reason": "no_nonempty_folds",
        }
    pass_count = sum(1 for row in nonempty if bool(row.get("passed")))
    pass_rate = pass_count / max(len(nonempty), 1)
    win_rates = np.array([float(row.get("win_rate", 0.0)) for row in nonempty], dtype=float)
    pos_rates = np.array([float(row.get("positive_active_day_rate", 0.0)) for row in nonempty], dtype=float)
    sum_rs = np.array([float(row.get("sum_r", 0.0)) for row in nonempty], dtype=float)
    reject = "ok"
    if len(nonempty) < STRESS_MIN_FOLDS:
        reject = "not_enough_nonempty_folds"
    elif pass_rate < STRESS_MIN_PASS_RATE:
        reject = "low_stress_pass_rate"
    elif float(np.min(sum_rs)) < STRESS_WORST_FOLD_SUM_R_MIN:
        reject = "worst_fold_too_negative"
    elif float(np.min(win_rates)) < STRESS_MIN_FOLD_WIN_RATE:
        reject = "min_fold_wr_too_low"
    return {
        "stress_folds_tested": int(len(nonempty)),
        "stress_folds_passed": int(pass_count),
        "stress_pass_rate": float(pass_rate),
        "stress_pass": bool(reject == "ok"),
        "stress_min_win_rate": float(np.min(win_rates)),
        "stress_min_positive_day_rate": float(np.min(pos_rates)),
        "stress_worst_sum_r": float(np.min(sum_rs)),
        "stress_sum_r_std": float(np.std(sum_rs)),
        "stress_reject_reason": reject,
    }


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
    # Calendar week-like folds inside train. This catches “first half carried it”
    # and week-specific spikes without relying on future data.
    week_keys = days // 7
    for week in sorted(set(week_keys.tolist())):
        add_fold(f"week_fold_{int(week)}", days[week_keys == week])
    return folds


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
    candidates: list[tuple[float, int, Mapping[str, object]]] = []
    for rule_row in sorted(selected_rules, key=lambda row: float(row.get("final_score", 0.0)), reverse=True):
        mask = test_mask & _rule_mask_from_id(context, str(rule_row.get("rule_id", "")))
        for idx in np.flatnonzero(mask):
            candidates.append((float(rule_row.get("final_score", 0.0)), int(idx), rule_row))
    candidates.sort(key=lambda item: (-item[0], int(context.timestamp[item[1]])))
    used_symbols: set[str] = set()
    rule_counts: dict[str, int] = {}
    emitted: list[dict[str, object]] = []
    used_trade_keys: set[str] = set()
    for _, idx, rule_row in candidates:
        if len(emitted) >= OPTIMIZER_MAX_TRADES_PER_SESSION_DAY:
            break
        symbol = str(context.symbols[idx])
        trade_key = str(context.trade_key[idx])
        rule_id = str(rule_row.get("rule_id", ""))
        if trade_key in used_trade_keys:
            continue
        if symbol in used_symbols:
            continue
        if rule_counts.get(rule_id, 0) >= OPTIMIZER_MAX_TRADES_PER_RULE_SESSION_DAY:
            continue
        risk_weight = float(rule_row.get("risk_weight", 0.0))
        if risk_weight <= 0.0:
            continue
        net_r = float(context.net_r[idx])
        source = context.ledger.iloc[idx]
        emitted.append(
            {
                "date": _date_from_ord(test_day),
                "test_day_ord": int(test_day),
                "session": session,
                "rule_id": rule_id,
                "conditions": str(rule_row.get("conditions", "")),
                "selection_state": str(rule_row.get("selection_state", "")),
                "risk_weight": risk_weight,
                "final_score": float(rule_row.get("final_score", 0.0)),
                "symbol": symbol,
                "entry_timestamp_ms": int(context.timestamp[idx]),
                "entry_utc": _timestamp_to_utc(int(context.timestamp[idx])),
                "net_r": net_r,
                "weighted_r": net_r * risk_weight,
                "arm_id": source.get("arm_id", ""),
                "exit_policy_id": source.get("exit_policy_id", ""),
                "session_bucket": source.get("session_bucket", session),
            }
        )
        used_symbols.add(symbol)
        used_trade_keys.add(trade_key)
        rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1
    return emitted


def _update_oos_memory(
    memory: dict[str, OosMemoryState],
    selected_rules: Sequence[Mapping[str, object]],
    day_oos: Sequence[Mapping[str, object]],
    *,
    test_day: int,
    session: str,
    memory_rows: list[dict[str, object]],
) -> None:
    by_rule: dict[str, list[float]] = {}
    for row in day_oos:
        by_rule.setdefault(str(row.get("rule_id", "")), []).append(float(row.get("net_r", 0.0)))
    for rule in selected_rules:
        rule_id = str(rule.get("rule_id", ""))
        key = _memory_key(session, rule_id)
        state = memory.setdefault(key, OosMemoryState([], []))
        day_values = by_rule.get(rule_id, [])
        day_sum = float(sum(day_values))
        state.selected_day_rs.append(day_sum)
        state.selected_day_rs[:] = state.selected_day_rs[-20:]
        state.trade_rs.extend(day_values)
        state.trade_rs[:] = state.trade_rs[-50:]
        cooldown_reason = ""
        if len(state.selected_day_rs) >= 5 and sum(state.selected_day_rs[-5:]) < OOS_BAD_LAST_SELECTED_DAY_SUM_R:
            state.cooldown_until_day = max(state.cooldown_until_day, int(test_day) + OOS_COOLDOWN_DAYS)
            cooldown_reason = "last_5_selected_days_sum_r_below_limit"
        elif len(state.selected_day_rs) >= OOS_CONSECUTIVE_NEGATIVE_DAYS_FOR_COOLDOWN and all(value < 0.0 for value in state.selected_day_rs[-OOS_CONSECUTIVE_NEGATIVE_DAYS_FOR_COOLDOWN:]):
            state.cooldown_until_day = max(state.cooldown_until_day, int(test_day) + OOS_CONSECUTIVE_NEGATIVE_COOLDOWN_DAYS)
            cooldown_reason = "three_selected_days_negative"
        elif len(state.trade_rs[-10:]) >= OOS_BAD_LAST_TRADE_MIN_COUNT:
            last_trades = state.trade_rs[-10:]
            wr = sum(1 for value in last_trades if value > 0.0) / len(last_trades)
            if wr < OOS_BAD_LAST_TRADE_WR:
                state.cooldown_until_day = max(state.cooldown_until_day, int(test_day) + OOS_COOLDOWN_DAYS)
                cooldown_reason = "last_10_oos_trade_wr_below_limit"
        if cooldown_reason:
            state.cooldown_reason = cooldown_reason
        memory_rows.append(
            {
                "date": _date_from_ord(test_day),
                "test_day_ord": int(test_day),
                "session": session,
                "rule_id": rule_id,
                "selected_day_r": day_sum,
                "selected_day_trades": int(len(day_values)),
                "last_5_selected_days_sum_r": float(sum(state.selected_day_rs[-5:])),
                "last_10_trade_wr": _win_rate(state.trade_rs[-10:]),
                "cooldown_until_day": int(state.cooldown_until_day),
                "cooldown_until_date": _date_from_ord(int(state.cooldown_until_day)) if state.cooldown_until_day else "",
                "cooldown_reason": state.cooldown_reason,
            }
        )


def _memory_summary(state: OosMemoryState) -> dict[str, object]:
    return {
        "memory_selected_days": int(len(state.selected_day_rs)),
        "memory_last_5_selected_days_sum_r": float(sum(state.selected_day_rs[-5:])),
        "memory_oos_trades": int(len(state.trade_rs)),
        "memory_last_10_trade_wr": _win_rate(state.trade_rs[-10:]),
        "memory_cooldown_until_day": int(state.cooldown_until_day),
        "memory_cooldown_reason": state.cooldown_reason,
    }


def _memory_key(session: str, rule_id: str) -> str:
    return f"{session}|{rule_id}"


def _rule_mask_from_id(context: _LedgerContext, rule_id: str) -> np.ndarray:
    mask = np.ones(len(context.ledger), dtype=bool)
    if not rule_id:
        return np.zeros(len(context.ledger), dtype=bool)
    for atom_id in str(rule_id).split("&"):
        atom = context.atom_lookup.get(atom_id)
        if atom is None:
            return np.zeros(len(context.ledger), dtype=bool)
        mask &= atom.mask
    return mask


def _is_redundant_rule(rule_id: str, used_rule_ids: set[str]) -> bool:
    # Avoid selecting a strict superset and subset together in the same session.
    parts = set(str(rule_id).split("&"))
    for existing in used_rule_ids:
        other = set(str(existing).split("&"))
        if parts.issubset(other) or other.issubset(parts):
            return True
    return False


def _daily_summary(oos: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "session",
        "trades",
        "win_rate",
        "sum_r",
        "weighted_sum_r",
        "median_r",
        "symbols",
        "positive",
    ]
    if oos.empty:
        return pd.DataFrame(columns=columns)
    frame = oos.copy()
    rows: list[dict[str, object]] = []
    for (date, session), group in frame.groupby(["date", "session"], dropna=False):
        net_r = pd.to_numeric(group["net_r"], errors="coerce").fillna(0.0)
        weighted_r = pd.to_numeric(group.get("weighted_r", pd.Series(index=group.index)), errors="coerce").fillna(0.0)
        rows.append(
            {
                "date": str(date),
                "session": str(session),
                "trades": int(len(group)),
                "win_rate": float(net_r.gt(0.0).mean()) if len(group) else 0.0,
                "sum_r": float(net_r.sum()),
                "weighted_sum_r": float(weighted_r.sum()),
                "median_r": float(net_r.median()) if len(group) else 0.0,
                "symbols": int(group["symbol"].astype(str).nunique()) if "symbol" in group.columns else 0,
                "positive": bool(weighted_r.sum() > 0.0),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(["date", "session"]).reset_index(drop=True)


def _rule_health(selected: pd.DataFrame, oos: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "session",
        "rule_id",
        "conditions",
        "selected_days",
        "core_days",
        "strong_days",
        "tactical_days",
        "oos_trades",
        "oos_sum_r",
        "oos_weighted_sum_r",
        "oos_wr",
        "last_selected_date",
    ]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    oos_frame = oos.copy() if not oos.empty else pd.DataFrame()
    for (session, rule_id), group in selected.groupby(["session", "rule_id"], dropna=False):
        oos_group = oos_frame.loc[(oos_frame["session"].astype(str) == str(session)) & (oos_frame["rule_id"].astype(str) == str(rule_id))] if not oos_frame.empty else pd.DataFrame()
        net_r = pd.to_numeric(oos_group.get("net_r", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        weighted_r = pd.to_numeric(oos_group.get("weighted_r", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        states = group["selection_state"].astype(str)
        rows.append(
            {
                "session": str(session),
                "rule_id": str(rule_id),
                "conditions": str(group.iloc[-1].get("conditions", "")),
                "selected_days": int(len(group)),
                "core_days": int(states.eq("core").sum()),
                "strong_days": int(states.eq("strong").sum()),
                "tactical_days": int(states.eq("tactical").sum()),
                "oos_trades": int(len(oos_group)),
                "oos_sum_r": float(net_r.sum()) if len(net_r) else 0.0,
                "oos_weighted_sum_r": float(weighted_r.sum()) if len(weighted_r) else 0.0,
                "oos_wr": float(net_r.gt(0.0).mean()) if len(net_r) else 0.0,
                "last_selected_date": str(group["date"].max()),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values(["session", "oos_weighted_sum_r"], ascending=[True, False]).reset_index(drop=True)


def _selection_drift(selected: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "test_day_ord",
        "session",
        "selected_rules",
        "top_rule_id",
        "top_conditions",
        "top_state",
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
    for session, group in frame.groupby("session", dropna=False):
        previous_rules: set[str] | None = None
        previous_top = ""
        previous_conditions = ""
        for test_day, day_group in group.sort_values(["test_day_ord", "final_score"], ascending=[True, False]).groupby("test_day_ord", sort=True):
            ranked = day_group.sort_values("final_score", ascending=False)
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
                elif selected_jaccard >= 0.50 or condition_jaccard >= 0.50:
                    drift_state = "partial_rotation"
                else:
                    drift_state = "jitter"
            rows.append(
                {
                    "date": str(top.get("date", _date_from_ord(int(test_day)))),
                    "test_day_ord": int(test_day),
                    "session": str(session),
                    "selected_rules": int(len(rules)),
                    "top_rule_id": top_rule,
                    "top_conditions": conditions,
                    "top_state": str(top.get("selection_state", "")),
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
    return pd.DataFrame(rows, columns=columns).sort_values(["date", "session"]).reset_index(drop=True)


def _window_dynamics(window_health: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "test_day_ord",
        "session",
        "window_pair",
        "short_top_rule_id",
        "long_top_rule_id",
        "short_top_conditions",
        "long_top_conditions",
        "top_rule_same_between_windows",
        "top_condition_jaccard_between_windows",
        "selected_rule_present_in_both_windows",
        "window_agreement_state",
    ]
    if window_health.empty:
        return pd.DataFrame(columns=columns)
    selected_ids = set(selected["rule_id"].astype(str).tolist()) if not selected.empty and "rule_id" in selected.columns else set()
    frame = window_health.copy()
    frame["test_day_ord"] = pd.to_numeric(frame["test_day_ord"], errors="coerce").fillna(0).astype("int64")
    frame["window_days"] = pd.to_numeric(frame["window_days"], errors="coerce").fillna(0).astype("int64")
    rows: list[dict[str, object]] = []
    for (test_day, session), group in frame.groupby(["test_day_ord", "session"], dropna=False):
        by_window = {int(window): part.sort_values("window_sum_r", ascending=False).copy() for window, part in group.groupby("window_days", dropna=False)}
        for left, right in ((15, 30), (30, 60), (15, 60)):
            if left not in by_window or right not in by_window or by_window[left].empty or by_window[right].empty:
                continue
            short_top = by_window[left].iloc[0]
            long_top = by_window[right].iloc[0]
            same = str(short_top.get("rule_id", "")) == str(long_top.get("rule_id", ""))
            cond_j = _condition_jaccard(str(short_top.get("conditions", "")), str(long_top.get("conditions", "")))
            present_both = bool(str(short_top.get("rule_id", "")) in selected_ids and str(long_top.get("rule_id", "")) in selected_ids)
            if same:
                state = "same_top_rule"
            elif cond_j >= 0.50:
                state = "related_conditions"
            else:
                state = "window_disagreement"
            rows.append(
                {
                    "date": str(short_top.get("date", _date_from_ord(int(test_day)))),
                    "test_day_ord": int(test_day),
                    "session": str(session),
                    "window_pair": f"{left}d_vs_{right}d",
                    "short_top_rule_id": str(short_top.get("rule_id", "")),
                    "long_top_rule_id": str(long_top.get("rule_id", "")),
                    "short_top_conditions": str(short_top.get("conditions", "")),
                    "long_top_conditions": str(long_top.get("conditions", "")),
                    "top_rule_same_between_windows": bool(same),
                    "top_condition_jaccard_between_windows": cond_j,
                    "selected_rule_present_in_both_windows": present_both,
                    "window_agreement_state": state,
                }
            )
    return pd.DataFrame(rows, columns=columns).sort_values(["date", "session", "window_pair"]).reset_index(drop=True)


def _prefix_metrics(metrics: Mapping[str, object], prefix: str) -> dict[str, object]:
    return {f"{prefix}_{key}": value for key, value in metrics.items()}


def _session_features(timestamp_ms: int) -> dict[str, object]:
    ts = pd.to_datetime(int(timestamp_ms), unit="ms", utc=True)
    hour = int(ts.hour)
    minute = hour * 60 + int(ts.minute)
    asia = 0 <= minute < 8 * 60
    europe = 7 * 60 <= minute < 16 * 60
    us = 13 * 60 <= minute < 22 * 60
    if asia and europe:
        bucket = "asia_europe_overlap"
    elif europe and us:
        bucket = "europe_us_overlap"
    elif asia:
        bucket = "asia_only"
    elif europe:
        bucket = "europe_only"
    elif us:
        bucket = "us_only"
    else:
        bucket = "off_session"
    active = [name for name, value in (("asia", asia), ("europe", europe), ("us", us)) if value]
    return {
        "hour_utc": hour,
        "weekday": int(ts.weekday()),
        "session_asia": bool(asia),
        "session_europe": bool(europe),
        "session_us": bool(us),
        "session_overlap": bool(len(active) >= 2),
        "session_primary": active[-1] if active else "off_session",
        "session_bucket": bucket,
    }


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(False).astype(bool)
    return values.astype(str).str.lower().isin({"1", "true", "yes", "y", "on"})


def _split_tokens(value: object) -> list[str]:
    text = str(value or "")
    if not text:
        return []
    return [part.strip() for part in text.replace(",", "|").replace(";", "|").split("|") if part.strip()]


def _safe_id(value: str) -> str:
    result = []
    for char in value.lower():
        if char.isalnum():
            result.append(char)
        else:
            result.append("_")
    text = "".join(result).strip("_")
    while "__" in text:
        text = text.replace("__", "_")
    return text[:160]


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _timestamp_to_utc(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC).isoformat()


def _date_from_ord(day_ord: int) -> str:
    if int(day_ord) <= 0:
        return ""
    return datetime.fromtimestamp(int(day_ord) * DAY_MS / 1000, tz=UTC).date().isoformat()


def _win_rate(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(1 for value in values if float(value) > 0.0) / len(values))


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _set_jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return float(len(left & right) / len(union))


def _condition_jaccard(left: str, right: str) -> float:
    left_set = {part.strip() for part in str(left).split("|") if part.strip()}
    right_set = {part.strip() for part in str(right).split("|") if part.strip()}
    return _set_jaccard(left_set, right_set)
