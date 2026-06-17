import unittest

import pandas as pd

from research_tools import pump_mechanism_stability_research as pm


class PumpMechanismStructuralCubeSelectionTests(unittest.TestCase):
    def test_structural_failure_cube_cells_get_first_selection_budget(self) -> None:
        train = pd.DataFrame(
            [
                {
                    "event_id": f"e{i}",
                    "symbol": f"S{i % 5}",
                    "day_ord": i // 3,
                    "date": "2026-01-01",
                    "mechanism_family": "generic_failed_acceptance_candidate",
                    "acceptance_regime": "immediate_rejection",
                    "structure_regime": "seed_low_break_after_pump",
                    "price_progress_regime": "wick_without_acceptance",
                    "session_bucket": "us_only",
                    "oi_regime": "oi_flat_or_below_threshold",
                    "flow_regime": "neutral_flow",
                }
                for i in range(pm.MIN_TRAIN_SCOPE_EVENTS + 5)
            ]
        )

        raw_scopes = pm._raw_train_rule_scopes(train)
        selected = pm._select_mechanism_intent_rule_cells(raw_scopes)
        selected_scope_names = [str(scope.get("scope_name")) for scope in selected]

        self.assertIn("structural_failure_cube", selected_scope_names)
        self.assertIn("structural_failure_cube_session", selected_scope_names)
        self.assertLess(
            selected_scope_names.index("structural_failure_cube_session"),
            selected_scope_names.index("mechanism_family+session_bucket"),
        )

    def test_structural_failure_cube_does_not_emit_missing_oi_main_scope(self) -> None:
        train = pd.DataFrame(
            [
                {
                    "event_id": f"e{i}",
                    "symbol": f"S{i % 5}",
                    "day_ord": i // 3,
                    "date": "2026-01-01",
                    "acceptance_regime": "immediate_rejection",
                    "structure_regime": "seed_low_break_after_pump",
                    "price_progress_regime": "wick_without_acceptance",
                    "session_bucket": "us_only",
                    "oi_regime": pm.NO_OI_REGIME,
                    "flow_regime": "neutral_flow",
                }
                for i in range(pm.MIN_TRAIN_SCOPE_EVENTS + 5)
            ]
        )

        scopes = pm._structural_failure_cube_scopes(train)
        scope_keys = [pm._scope_key(scope.get("conditions", {})) for scope in scopes]

        self.assertTrue(scopes)
        self.assertFalse(any(f"oi_regime={pm.NO_OI_REGIME}" in key for key in scope_keys))


if __name__ == "__main__":
    unittest.main()
