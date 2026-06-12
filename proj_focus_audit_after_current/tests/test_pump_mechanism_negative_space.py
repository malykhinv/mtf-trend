import unittest

import pandas as pd

from research_tools import pump_mechanism_stability_research as pm


class PumpMechanismNegativeSpaceAuditTests(unittest.TestCase):
    def test_protocol_audit_requires_failed_neighbors_for_selected_basins(self) -> None:
        daily_selection = pd.DataFrame([
            {
                "basin_id": "basin_a",
                "test_day_ord": 20,
                "train_end_day_ord": 19,
                "data_access_model": pm.DATA_ACCESS_MODEL,
            }
        ])
        negative_space = pd.DataFrame([
            {
                "basin_id": "basin_a",
                "neighbor_passes_min_sample": True,
                "neighbor_fail_reason": "pass",
                "data_access_model": pm.DATA_ACCESS_MODEL,
            }
        ])

        audit = pm._build_protocol_audit(
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            taxonomy=pd.DataFrame(),
            rule_universe=pd.DataFrame(),
            negative_space=negative_space,
            plateau_basins=pd.DataFrame(),
            daily_selection=daily_selection,
            daily_oos=pd.DataFrame(),
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        )

        row = audit.loc[
            audit["audit_name"] == "selected_basins_have_full_negative_space_rows_including_rejected_neighbors"
        ].iloc[0]
        self.assertEqual(row["audit_status"], "fail")
        self.assertEqual(int(row["failing_rows"]), 1)

    def test_protocol_audit_accepts_rejected_neighbor_rows(self) -> None:
        daily_selection = pd.DataFrame([
            {
                "basin_id": "basin_a",
                "test_day_ord": 20,
                "train_end_day_ord": 19,
                "data_access_model": pm.DATA_ACCESS_MODEL,
            }
        ])
        negative_space = pd.DataFrame([
            {
                "basin_id": "basin_a",
                "neighbor_passes_min_sample": True,
                "neighbor_fail_reason": "pass",
                "data_access_model": pm.DATA_ACCESS_MODEL,
            },
            {
                "basin_id": "basin_a",
                "neighbor_passes_min_sample": False,
                "neighbor_fail_reason": "low_events",
                "data_access_model": pm.DATA_ACCESS_MODEL,
            },
        ])

        audit = pm._build_protocol_audit(
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            taxonomy=pd.DataFrame(),
            rule_universe=pd.DataFrame(),
            negative_space=negative_space,
            plateau_basins=pd.DataFrame(),
            daily_selection=daily_selection,
            daily_oos=pd.DataFrame(),
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        )

        row = audit.loc[
            audit["audit_name"] == "selected_basins_have_full_negative_space_rows_including_rejected_neighbors"
        ].iloc[0]
        self.assertEqual(row["audit_status"], "pass")
        self.assertEqual(int(row["failing_rows"]), 0)


if __name__ == "__main__":
    unittest.main()
