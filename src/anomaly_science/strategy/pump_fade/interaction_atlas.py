from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.regimes import (
    load_causal_regime_atlas_config,
    run_causal_regime_atlas,
)
from anomaly_science.strategy.pump_fade.regimes import (
    PUMP_FADE_REGIME_AXES,
    PUMP_FADE_REGIME_INTERACTIONS,
)


@dataclass(frozen=True, slots=True)
class PumpFadeInteractionAtlasFamilyConfig:
    protocol_freeze_id: str
    atlas_config_path: str
    state_ordinals: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.protocol_freeze_id:
            raise ValueError("protocol_freeze_id is required")
        if self.state_ordinals != (1, 2):
            raise ValueError("registered interaction-atlas state ordinals must be (1, 2)")


def load_pump_fade_interaction_atlas_family_config(
    path: Path,
) -> PumpFadeInteractionAtlasFamilyConfig:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if "state_ordinals" in raw:
        raw["state_ordinals"] = tuple(raw["state_ordinals"])
    unknown = sorted(
        set(raw) - set(PumpFadeInteractionAtlasFamilyConfig.__dataclass_fields__)
    )
    if unknown:
        raise ValueError(f"unknown interaction-atlas family fields: {unknown}")
    return PumpFadeInteractionAtlasFamilyConfig(**raw)


def run_pump_fade_interaction_atlas_family(
    *,
    nature_path: Path,
    state_lattice_path: Path,
    out_dir: Path,
    config: PumpFadeInteractionAtlasFamilyConfig,
    repository_root: Path,
    allow_dirty_development: bool = False,
) -> Path:
    """Run identical registered single/interaction hypotheses at T0 and ordinals 1-2."""

    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"interaction-atlas family output must be absent or empty: {out_dir}")
    base = load_causal_regime_atlas_config(repository_root / config.atlas_config_path)
    if base.axes != PUMP_FADE_REGIME_AXES or base.interactions != PUMP_FADE_REGIME_INTERACTIONS:
        raise ValueError("interaction-atlas protocol differs from the strategy declaration")
    out_dir.mkdir(parents=True, exist_ok=True)
    variant_count = 1 + len(config.state_ordinals)
    per_variant_fdr_alpha = base.fdr_alpha / variant_count
    corrected_base = replace(base, fdr_alpha=per_variant_fdr_alpha)
    variants = [("t0", nature_path, corrected_base)] + [
        (
            f"ordinal_{ordinal}",
            state_lattice_path,
            replace(
                corrected_base,
                protocol_freeze_id=f"{config.protocol_freeze_id}:ordinal_{ordinal}",
                future_start_time_column="future_start_time_ms",
                label_column="y",
                label_available_column="label_available",
                row_filter_column="is_registered_state_lattice",
                population_exact_column="state_ordinal",
                population_exact_value=ordinal,
            ),
        )
        for ordinal in config.state_ordinals
    ]
    written: list[Path] = []
    for variant_name, input_path, variant_config in variants:
        if variant_name == "t0":
            variant_config = replace(
                variant_config,
                protocol_freeze_id=f"{config.protocol_freeze_id}:t0",
            )
        variant_dir = out_dir / variant_name
        run_causal_regime_atlas(
            input_path=input_path,
            out_dir=variant_dir,
            config=variant_config,
            allow_dirty_development=allow_dirty_development,
        )
        written.extend(path for path in variant_dir.rglob("*") if path.is_file())
    protocol_path = out_dir / "interaction_atlas_family_protocol.json"
    _write_json(
        protocol_path,
        {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_freeze_id": config.protocol_freeze_id,
            "nature_input": str(nature_path.resolve()),
            "nature_sha256": sha256_file(nature_path),
            "state_lattice_input": str(state_lattice_path.resolve()),
            "state_lattice_sha256": sha256_file(state_lattice_path),
            "registered_variants": [name for name, *_ in variants],
            "single_axis_count": len(PUMP_FADE_REGIME_AXES),
            "interaction_count": len(PUMP_FADE_REGIME_INTERACTIONS),
            "joint_fdr_within_each_variant": True,
            "family_fdr_alpha": base.fdr_alpha,
            "per_variant_fdr_alpha": per_variant_fdr_alpha,
            "cross_variant_correction": "Bonferroni over T0, ordinal 1, ordinal 2",
            "scientific_scope": "development single and interaction atlas; no probability or EV claim",
        },
    )
    written.append(protocol_path)
    manifest = build_manifest(
        run_id=(
            "pump-fade-interaction-atlas-family-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ),
        artifact_paths=tuple(dict.fromkeys(written)),
        root=out_dir,
    )
    write_manifest(out_dir / "interaction_atlas_family.manifest.json", manifest)
    return out_dir


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


__all__ = [
    "PumpFadeInteractionAtlasFamilyConfig",
    "load_pump_fade_interaction_atlas_family_config",
    "run_pump_fade_interaction_atlas_family",
]
