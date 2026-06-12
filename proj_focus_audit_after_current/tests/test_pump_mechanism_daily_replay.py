import unittest

import pandas as pd

from research_tools import pump_mechanism_stability_research as pm


class PumpMechanismDailyReplayAuditTests(unittest.TestCase):
    def test_protocol_audit_fails_when_daily_oos_train_window_touches_test_day(self) -> None:
        daily_oos = pd.DataFrame([
            {
                "test_day_ord": 30,
                "train_end_day_ord": 30,
                "selection_uses_test_day_outcomes": False,
                "test_day_not_in_train_window": False,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": pm.DATA_ACCESS_MODEL,
            }
        ])

        audit = pm._build_protocol_audit(
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            taxonomy=pd.DataFrame(),
            rule_universe=pd.DataFrame(),
            negative_space=pd.DataFrame(),
            plateau_basins=pd.DataFrame(),
            daily_selection=pd.DataFrame(),
            daily_oos=daily_oos,
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        )

        split_row = audit.loc[audit["audit_name"] == "daily_selection_and_oos_train_windows_end_before_test_day"].iloc[0]
        eval_row = audit.loc[audit["audit_name"] == "daily_oos_is_evaluation_only_not_selection_or_pnl"].iloc[0]
        self.assertEqual(split_row["audit_status"], "fail")
        self.assertEqual(eval_row["audit_status"], "fail")

    def test_protocol_audit_accepts_clean_daily_oos_flags(self) -> None:
        daily_oos = pd.DataFrame([
            {
                "test_day_ord": 30,
                "train_end_day_ord": 29,
                "selection_uses_test_day_outcomes": False,
                "test_day_not_in_train_window": True,
                "uses_pnl": False,
                "uses_short_entry": False,
                "uses_final_holdout_tuning": False,
                "data_access_model": pm.DATA_ACCESS_MODEL,
            }
        ])

        audit = pm._build_protocol_audit(
            events=pd.DataFrame(),
            outcomes=pd.DataFrame(),
            taxonomy=pd.DataFrame(),
            rule_universe=pd.DataFrame(),
            negative_space=pd.DataFrame(),
            plateau_basins=pd.DataFrame(),
            daily_selection=pd.DataFrame(),
            daily_oos=daily_oos,
            window_health=pd.DataFrame(),
            selection_drift=pd.DataFrame(),
        )

        split_row = audit.loc[audit["audit_name"] == "daily_selection_and_oos_train_windows_end_before_test_day"].iloc[0]
        eval_row = audit.loc[audit["audit_name"] == "daily_oos_is_evaluation_only_not_selection_or_pnl"].iloc[0]
        self.assertEqual(split_row["audit_status"], "pass")
        self.assertEqual(eval_row["audit_status"], "pass")


if __name__ == "__main__":
    unittest.main()
