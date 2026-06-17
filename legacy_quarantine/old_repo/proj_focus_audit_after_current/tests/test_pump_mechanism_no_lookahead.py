import unittest

import pandas as pd

from research_tools import pump_mechanism_stability_research as pm


class PumpMechanismNoLookaheadAuditTests(unittest.TestCase):
    def test_protocol_audit_fails_when_rule_train_window_touches_test_day(self) -> None:
        audit = pm._build_protocol_audit(
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            taxonomy=pd.DataFrame(),
            rule_universe=pd.DataFrame([
                {
                    "test_day_ord": 10,
                    "train_end_day_ord": 10,
                    "uses_outcome_columns": False,
                    "uses_pnl": False,
                    "uses_short_entry": False,
                    "uses_final_holdout_tuning": False,
                    "data_access_model": pm.DATA_ACCESS_MODEL,
                }
            ]),
            negative_space=pd.DataFrame(),
            plateau_basins=pd.DataFrame(),
            daily_selection=pd.DataFrame(),
            daily_oos=pd.DataFrame(),
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        )

        row = audit.loc[audit["audit_name"] == "rule_universe_thresholds_fit_only_on_prior_train_days"].iloc[0]
        self.assertEqual(row["audit_status"], "fail")
        self.assertEqual(int(row["failing_rows"]), 1)

    def test_protocol_audit_passes_clean_train_only_rule_flags(self) -> None:
        audit = pm._build_protocol_audit(
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            taxonomy=pd.DataFrame(),
            rule_universe=pd.DataFrame([
                {
                    "test_day_ord": 10,
                    "train_end_day_ord": 9,
                    "uses_outcome_columns": False,
                    "uses_pnl": False,
                    "uses_short_entry": False,
                    "uses_final_holdout_tuning": False,
                    "data_access_model": pm.DATA_ACCESS_MODEL,
                }
            ]),
            negative_space=pd.DataFrame(),
            plateau_basins=pd.DataFrame(),
            daily_selection=pd.DataFrame(),
            daily_oos=pd.DataFrame(),
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        )

        row = audit.loc[audit["audit_name"] == "rule_universe_thresholds_fit_only_on_prior_train_days"].iloc[0]
        self.assertEqual(row["audit_status"], "pass")
        self.assertEqual(int(row["failing_rows"]), 0)


if __name__ == "__main__":
    unittest.main()
