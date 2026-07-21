"""End-to-end engine test: two bidders through auction + funnel."""
import unittest

from game.engine.economics import KeywordSpec, TeamBid
from game.engine.resolve import resolve_keyword


class ResolveTests(unittest.TestCase):
    def test_quality_can_beat_a_bigger_budget(self):
        spec = KeywordSpec(label="k", search_volume=1000, ctr_curve=[0.3, 0.15],
                           conversion_rate=0.1, order_value=50.0, reserve_price=0.5, ad_slots=2)
        # "Deep pockets" A (bid 4, quality 1) vs. relevant B (bid 3, quality 3).
        rows = {r.team_id: r for r in resolve_keyword(spec, [
            TeamBid("A", 4.0, 1.0),
            TeamBid("B", 3.0, 3.0),
        ])}
        # B wins position 1 at a lower CPC thanks to quality.
        self.assertEqual(rows["B"].position, 1)
        self.assertEqual(rows["A"].position, 2)
        # B wins the top slot but pays less than A's max bid, thanks to quality.
        self.assertLess(rows["B"].actual_cpc, 4.0)
        # ROAS is populated when there is spend.
        self.assertIsNotNone(rows["B"].roas)
        self.assertGreater(rows["B"].revenue, 0)


if __name__ == "__main__":
    unittest.main()
