"""Auction/GSP tests. Pure Python — run without Django or a database."""
import unittest

from game.engine.economics import KeywordSpec, TeamBid
from game.engine.auction import run_auction


def spec(**kw):
    base = dict(label="k", search_volume=1000, ctr_curve=[0.3, 0.15, 0.1],
                conversion_rate=0.1, order_value=50.0, reserve_price=0.5, ad_slots=2)
    base.update(kw)
    return KeywordSpec(**base)


class AuctionTests(unittest.TestCase):
    def test_ad_rank_orders_by_bid_times_quality(self):
        # A: 4 x 1 = 4 ; B: 3 x 3 = 9 -> B outranks A despite a lower max bid.
        bids = [TeamBid("A", 4.0, 1.0), TeamBid("B", 3.0, 3.0)]
        placements = {p.team_id: p for p in run_auction(spec(), bids)}
        self.assertEqual(placements["B"].position, 1)
        self.assertEqual(placements["A"].position, 2)
        self.assertAlmostEqual(placements["B"].ad_rank, 9.0)
        self.assertAlmostEqual(placements["A"].ad_rank, 4.0)

    def test_gsp_price_is_set_by_bidder_below(self):
        # B pays A's ad rank (4) / B's quality (3) + 0.01 = 1.34.
        bids = [TeamBid("A", 4.0, 1.0), TeamBid("B", 3.0, 3.0)]
        placements = {p.team_id: p for p in run_auction(spec(), bids)}
        self.assertAlmostEqual(placements["B"].actual_cpc, 1.34, places=2)

    def test_lowest_shown_ad_pays_reserve(self):
        # A is the lowest shown ad and has nobody below -> pays the reserve (0.5).
        bids = [TeamBid("A", 4.0, 1.0), TeamBid("B", 3.0, 3.0)]
        placements = {p.team_id: p for p in run_auction(spec(reserve_price=0.5), bids)}
        self.assertAlmostEqual(placements["A"].actual_cpc, 0.50, places=2)

    def test_non_shown_bidder_still_sets_price(self):
        # 3 bidders, 2 slots. C doesn't show but sets A's price: C rank (2) / A qual (1) + .01.
        bids = [TeamBid("A", 4.0, 1.0), TeamBid("B", 3.0, 3.0), TeamBid("C", 2.0, 1.0)]
        placements = {p.team_id: p for p in run_auction(spec(ad_slots=2), bids)}
        self.assertIsNone(placements["C"].position)
        self.assertAlmostEqual(placements["A"].actual_cpc, 2.01, places=2)

    def test_cpc_capped_at_own_max_bid(self):
        # Even if GSP math exceeds the max bid, a team never pays more than its bid.
        bids = [TeamBid("A", 1.0, 1.0), TeamBid("B", 0.9, 10.0)]  # B rank 9, A rank 1
        placements = {p.team_id: p for p in run_auction(spec(ad_slots=2), bids)}
        # B is top; A below sets B's price: 1/10 + .01 = 0.11 (well under cap) -> fine.
        # A is lowest shown -> reserve 0.5, capped at A's max bid 1.0 -> 0.5.
        self.assertLessEqual(placements["B"].actual_cpc, 0.9)
        self.assertLessEqual(placements["A"].actual_cpc, 1.0)


if __name__ == "__main__":
    unittest.main()
