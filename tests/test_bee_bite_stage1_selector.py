import unittest

import numpy as np

from data.liquidity.bee_bite_stage1_selector import (
    BeeBiteStage1Selector,
    _PreparedStage1Frame,
    _Stage1Candidate,
)


class BeeBiteStage1SelectorTest(unittest.TestCase):
    def test_stair_step_pump_keeps_early_peak_and_shortens_confirm_delay(self) -> None:
        selector = BeeBiteStage1Selector(
            sleep_window_bars=2,
            pump_window_bars=2,
            pump_start_lookback_bars=4,
            volume_window_bars=2,
            stair_confirm_delay_bars=4,
            min_confirm_delay_bars=15,
            max_confirm_delay_bars=30,
        )
        prepared = _PreparedStage1Frame(
            timestamps=np.arange(9, dtype=np.int64),
            opens=np.array([99.5, 100.0, 101.0, 110.0, 118.0, 118.5, 118.7, 119.0, 120.0], dtype=np.float64),
            highs=np.array([100.0, 100.5, 111.0, 120.0, 120.4, 120.2, 120.3, 120.5, 126.0], dtype=np.float64),
            lows=np.array([99.0, 99.5, 100.0, 109.0, 117.5, 118.0, 118.2, 118.5, 119.5], dtype=np.float64),
            closes=np.array([100.0, 100.2, 110.0, 119.0, 119.2, 119.0, 119.4, 119.6, 125.0], dtype=np.float64),
            quote_volume=np.array([1.0, 1.0, 12.0, 18.0, 6.0, 5.0, 5.0, 5.0, 20.0], dtype=np.float64),
            rolling_volume=np.array([1.0, 1.0, 12.0, 18.0, 24.0, 23.0, 22.0, 21.0, 25.0], dtype=np.float64),
            sleep_volume_mean=np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float64),
            sleep_window_range_pct=np.zeros(9, dtype=np.float64),
            cumulative_quote_volume=np.cumsum(np.array([1.0, 1.0, 12.0, 18.0, 6.0, 5.0, 5.0, 5.0, 20.0], dtype=np.float64)),
        )
        candidate = _Stage1Candidate(
            sleep_start_idx=0,
            sleep_end_idx=1,
            pump_start_idx=2,
            peak_idx=3,
            regime_end_idx=3,
            sleep_avg_volume_usdt=1.0,
            pump_base_price=100.0,
            pump_peak_price=120.0,
            hold_base_idx=2,
            hold_base_price=100.0,
            pump_percent=0.20,
            confirm_delay_bars=15,
        )

        finalized = selector._finalize_candidate_regime(prepared=prepared, candidate=candidate)

        self.assertEqual(finalized.peak_idx, 3)
        self.assertEqual(finalized.regime_end_idx, 7)
        self.assertEqual(finalized.confirm_delay_bars, 4)


if __name__ == "__main__":
    unittest.main()
