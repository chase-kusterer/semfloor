"""Tests for GSP pricing rules, per-keyword budget split, and result columns."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase

from game import services
from game.engine.economics import KeywordSpec, TeamBid
from game.engine.resolve import resolve_keyword
from game.models import Bid, Game, Keyword, Round, RoundResult, Team


def _spec(**kw):
    base = dict(label="kw", search_volume=1000, ctr_curve=[0.3, 0.15, 0.08],
                conversion_rate=0.05, order_value=100.0, reserve_price=1.00, ad_slots=3)
    base.update(kw)
    return KeywordSpec(**base)


class GSPRuleTests(TestCase):
    def test_price_set_by_next_ad_rank_plus_penny(self):
        rows = {r.team_id: r for r in resolve_keyword(_spec(), [
            TeamBid("A", 5.0, 1.0), TeamBid("B", 3.0, 1.0), TeamBid("C", 2.0, 1.0)])}
        self.assertEqual(rows["A"].position, 1)
        self.assertEqual(rows["A"].actual_cpc, 3.01)   # B's ad rank / A's quality + 0.01
        self.assertEqual(rows["A"].next_highest_bid, 3.0)
        self.assertEqual(rows["B"].actual_cpc, 2.01)
        self.assertEqual(rows["C"].actual_cpc, 1.00)   # lowest shown pays reserve
        self.assertIsNone(rows["C"].next_highest_bid)
        self.assertEqual(rows["A"].bid_amount, 5.0)

    def test_bid_below_reserve_is_not_shown(self):
        rows = {r.team_id: r for r in resolve_keyword(_spec(reserve_price=1.00), [
            TeamBid("A", 5.0, 1.0), TeamBid("low", 0.50, 1.0)])}
        self.assertIsNone(rows["low"].position)
        self.assertEqual(rows["low"].clicks, 0)
        # And it doesn't set A's price either — A pays the reserve as lowest shown.
        self.assertEqual(rows["A"].actual_cpc, 1.00)

    def test_reserve_is_a_floor_for_every_position(self):
        # B's tiny ad rank would price A below the reserve without the floor.
        rows = {r.team_id: r for r in resolve_keyword(_spec(reserve_price=2.00), [
            TeamBid("A", 10.0, 1.0), TeamBid("B", 2.10, 1.0)])}
        self.assertEqual(rows["A"].position, 1)
        self.assertEqual(rows["A"].actual_cpc, 2.11)
        self.assertGreaterEqual(rows["B"].actual_cpc, 2.00)  # floored at reserve

    def test_never_pay_more_than_own_bid(self):
        # Higher quality below can push the raw GSP price above your own bid.
        rows = {r.team_id: r for r in resolve_keyword(_spec(), [
            TeamBid("A", 3.0, 1.0), TeamBid("B", 2.0, 2.0)])}
        top = [r for r in rows.values() if r.position == 1][0]
        self.assertLessEqual(top.actual_cpc, top.bid_amount)


class BudgetSplitTests(TestCase):
    """A team ranked on every keyword must get clicks on every keyword."""

    def setUp(self):
        self.game = Game.objects.create(code="SPLIT", name="Split",
                                        starting_budget=Decimal("1000.00"),
                                        num_rounds=1, ad_slots=3)
        self.k1 = Keyword.objects.create(game=self.game, order=1, label="pricey",
                                         search_volume=100000, ctr_curve=[0.5],
                                         conversion_rate=0.01, order_value=Decimal("10.00"),
                                         reserve_price=Decimal("0.50"))
        self.k2 = Keyword.objects.create(game=self.game, order=2, label="cheap",
                                         search_volume=5000, ctr_curve=[0.3],
                                         conversion_rate=0.05, order_value=Decimal("80.00"),
                                         reserve_price=Decimal("0.20"))
        services.build_rounds(self.game, 1)
        self.team = Team.objects.create(game=self.game, name="T",
                                        budget_remaining=Decimal("1000.00"))
        services.configure_bots(self.game, 0, 1.0)

    def test_expensive_keyword_cannot_starve_the_other(self):
        rnd = self.game.rounds.get(number=1)
        rnd.status = Round.Status.OPEN
        rnd.save()
        # Huge demand on k1 at $2/click would eat the whole $1000 several times over.
        Bid.objects.create(round=rnd, team=self.team, keyword=self.k1, max_bid=Decimal("2.00"))
        Bid.objects.create(round=rnd, team=self.team, keyword=self.k2, max_bid=Decimal("1.00"))
        rnd.status = Round.Status.CLOSED
        rnd.save()
        services.resolve_round(rnd)
        r1 = RoundResult.objects.get(round=rnd, keyword=self.k1, team=self.team)
        r2 = RoundResult.objects.get(round=rnd, keyword=self.k2, team=self.team)
        # k1 spend is capped at its even share (<= 500), leaving k2 its own share.
        self.assertLessEqual(r1.spend, Decimal("500.00"))
        self.assertEqual(r2.position, 1)
        self.assertGreater(r2.clicks, 0)
        self.assertGreater(r2.spend, 0)
        # Bid columns stored for the GSP walkthrough.
        self.assertEqual(r1.bid_amount, Decimal("2.00"))
        self.assertIsNone(r1.next_highest_bid)  # solo bidder — reserve set the price


class ResultsExportAndStateTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        self.fac = Client()
        self.fac.force_login(User.objects.get(username="prof"))
        self.game = Game.objects.create(code="EXP", name="Exp",
                                        starting_budget=Decimal("1000.00"), num_rounds=1)
        self.kw = Keyword.objects.create(game=self.game, order=1, label="solo",
                                         search_volume=1000, ctr_curve=[0.3],
                                         conversion_rate=0.05, order_value=Decimal("60.00"),
                                         reserve_price=Decimal("0.50"))
        services.build_rounds(self.game, 1)
        self.team = Team.objects.create(game=self.game, name="T",
                                        budget_remaining=Decimal("1000.00"))
        services.configure_bots(self.game, 0, 1.0)
        rnd = self.game.rounds.get(number=1)
        rnd.status = Round.Status.OPEN
        rnd.save()
        Bid.objects.create(round=rnd, team=self.team, keyword=self.kw, max_bid=Decimal("1.50"))
        rnd.status = Round.Status.CLOSED
        rnd.save()
        services.resolve_round(rnd)
        services.reveal_round(rnd)
        self.game.current_round = rnd
        self.game.save()
        self.rnd = rnd

    def test_results_csv_export(self):
        r = self.fac.get(f"/g/{self.game.code}/facilitator/results.csv")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Bid amount", body)
        self.assertIn("Next highest bid", body)
        self.assertIn("Impressions", body)
        self.assertIn("solo", body)
        self.assertIn("1.50", body)
        # Facilitator-only.
        anon = Client()
        r = anon.get(f"/g/{self.game.code}/facilitator/results.csv")
        self.assertNotEqual(r.status_code, 200)

    def test_state_rows_include_impressions(self):
        from game.state import build_game_state
        s = build_game_state(self.game, team=self.team)
        rows = s["current_round"]["results_by_keyword"][0]["rows"]
        shown = [r for r in rows if r["position"]]
        self.assertTrue(shown)
        self.assertEqual(shown[0]["impressions"], 1000)
        you_rows = s["you"]["last_result"]["rows"]
        self.assertIn("impressions", you_rows[0])
