"""Funnel tests. Pure Python — run without Django or a database."""
import unittest

from game.engine.economics import KeywordSpec
from game.engine.funnel import run_funnel


def spec(**kw):
    base = dict(label="k", search_volume=1000, ctr_curve=[0.3, 0.15],
                conversion_rate=0.1, order_value=50.0, reserve_price=0.5, ad_slots=2)
    base.update(kw)
    return KeywordSpec(**base)


class FunnelTests(unittest.TestCase):
    def test_not_shown_is_all_zero(self):
        out = run_funnel(spec(), position=None, actual_cpc=2.0, budget_remaining=None)
        self.assertEqual((out.impressions, out.clicks, out.spend, out.conversions, out.revenue),
                         (0, 0, 0.0, 0.0, 0.0))

    def test_position_one_funnel_math(self):
        # 1000 impressions x 0.30 CTR = 300 clicks; spend 300 x 1.34; conv 30; rev 1500.
        out = run_funnel(spec(), position=1, actual_cpc=1.34, budget_remaining=None)
        self.assertEqual(out.impressions, 1000)
        self.assertEqual(out.clicks, 300)
        self.assertAlmostEqual(out.spend, 402.0, places=2)
        self.assertAlmostEqual(out.conversions, 30.0, places=2)
        self.assertAlmostEqual(out.revenue, 1500.0, places=2)

    def test_budget_caps_clicks(self):
        # Budget of 100 at CPC 1.34 affords floor(74.6) = 74 clicks.
        out = run_funnel(spec(), position=1, actual_cpc=1.34, budget_remaining=100.0)
        self.assertEqual(out.clicks, 74)
        self.assertLessEqual(out.spend, 100.0)

    def test_lower_position_gets_fewer_clicks(self):
        out = run_funnel(spec(), position=2, actual_cpc=1.0, budget_remaining=None)
        self.assertEqual(out.clicks, 150)  # 1000 x 0.15


if __name__ == "__main__":
    unittest.main()
