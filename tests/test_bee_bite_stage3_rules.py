import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "strategy" / "bee_bite" / "stage3_rules.py"
_SPEC = spec_from_file_location("bee_bite_stage3_rules_for_tests", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

is_move_pct_smaller_than_range_pct = _MODULE.is_move_pct_smaller_than_range_pct
resolve_below_range_span = _MODULE.resolve_below_range_span
resolve_half_hold_price = _MODULE.resolve_half_hold_price


class BeeBiteStage3RulesTest(unittest.TestCase):
    def test_resolve_half_hold_price_returns_midpoint(self) -> None:
        self.assertEqual(resolve_half_hold_price(high_pump=140.0, low_before_pump=100.0), 120.0)
        self.assertIsNone(resolve_half_hold_price(high_pump=100.0, low_before_pump=100.0))

    def test_move_pct_must_be_smaller_than_range_pct(self) -> None:
        self.assertTrue(
            is_move_pct_smaller_than_range_pct(
                move_size=4.0,
                reclaim_boundary=100.0,
                range_high=110.0,
            )
        )
        self.assertFalse(
            is_move_pct_smaller_than_range_pct(
                move_size=11.0,
                reclaim_boundary=100.0,
                range_high=110.0,
            )
        )

    def test_resolve_below_range_span_uses_tracked_subrange(self) -> None:
        self.assertEqual(resolve_below_range_span(lowest_break=95.0, below_range_high=99.0), 4.0)
        self.assertIsNone(resolve_below_range_span(lowest_break=None, below_range_high=99.0))


if __name__ == "__main__":
    unittest.main()
