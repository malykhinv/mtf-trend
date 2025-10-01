"""CLI utility for optimizing anomaly thresholds from a workbook sample.

Run as ``python -m bot.utils.anomaly_threshold_optimizer`` to evaluate the
sample workbook and optionally persist optimized thresholds back to it.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from bot.domain.anomaly_bootstrapper import AnomalyLiveBootstrapper
from bot.domain.anomaly_optimizer import (
    AnomalySample,
    CandidateEvaluation,
    ParseDiagnostics,
    ThresholdCandidate,
    load_threshold_candidate,
    iter_candidate_thresholds,
    optimize_thresholds,
    parse_anomaly_samples,
    write_threshold_candidate,
)
from bot.utils.logger import setup_logging

LOGGER = setup_logging(__name__)
WORKBOOK_PATH = Path(__file__).resolve().parents[1] / "utils" / "sample" / "anomalies.xlsx"


def _log_candidate_metrics(evaluation: CandidateEvaluation) -> None:
    """Emit detailed information about the best candidate into the logs."""

    candidate = evaluation.candidate
    initial_deposit = candidate.initial_deposit
    long_return_pct = (
        (evaluation.long_final_equity / initial_deposit - 1) * 100
        if initial_deposit
        else 0.0
    )
    short_return_pct = (
        (evaluation.short_final_equity / initial_deposit - 1) * 100
        if initial_deposit
        else 0.0
    )

    LOGGER.info(
        "Best candidate score %.2f across %d trades (avg %.4f).",
        evaluation.score,
        evaluation.executed_trades,
        evaluation.average_trade_return_pct,
    )
    LOGGER.info(
        "Long equity %.2f (return %.2f%%); short equity %.2f (return %.2f%%).",
        evaluation.long_final_equity,
        long_return_pct,
        evaluation.short_final_equity,
        short_return_pct,
    )
    LOGGER.info("Selected thresholds:")
    for field, value in iter_candidate_thresholds(candidate):
        LOGGER.info("  %s: %.6f", field, value)


def _optimize(
    samples: Sequence[AnomalySample],
    base_candidate: ThresholdCandidate,
) -> CandidateEvaluation:
    grid_deltas = AnomalyLiveBootstrapper._compute_grid_deltas(base_candidate)
    return optimize_thresholds(samples, base_candidate, grid_deltas=grid_deltas)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for command line execution."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workbook",
        default=str(WORKBOOK_PATH),
        help="Path to the anomalies workbook (default: %(default)s)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist optimized thresholds back into the workbook",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    workbook_path = Path(args.workbook)
    LOGGER.info("Loading anomaly samples from %s", workbook_path)
    diagnostics = ParseDiagnostics()
    try:
        samples = parse_anomaly_samples(workbook_path, diagnostics=diagnostics)
    except FileNotFoundError:
        LOGGER.warning("Workbook %s not found; aborting", workbook_path)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Failed to load anomaly samples from %s: %s", workbook_path, exc)
        return 1

    if not samples:
        if diagnostics.total_skipped:
            LOGGER.warning(
                (
                    "Anomaly sample workbook %s does not contain any valid rows; "
                    "skipped %d rows. Details: %s"
                ),
                workbook_path,
                diagnostics.total_skipped,
                diagnostics.summarize(),
            )
        else:
            LOGGER.warning(
                "Anomaly sample workbook %s does not contain any rows; nothing to optimize",
                workbook_path,
            )
        return 0

    try:
        base_candidate = load_threshold_candidate(workbook_path)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Failed to load baseline thresholds from %s: %s", workbook_path, exc)
        return 1

    LOGGER.info("Optimizing thresholds for %d samples", len(samples))
    evaluation = _optimize(samples, base_candidate)
    _log_candidate_metrics(evaluation)

    if args.write:
        try:
            write_threshold_candidate(workbook_path, evaluation.candidate)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Failed to persist optimized thresholds to %s: %s", workbook_path, exc)
            return 1
        LOGGER.info("Persisted optimized thresholds to %s", workbook_path)

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
