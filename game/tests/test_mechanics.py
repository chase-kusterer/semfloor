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
        # Volume is jittered within ±11% of the configured 1000, market-wide.
        self.assertGreaterEqual(shown[0]["impressions"], 885)
        self.assertLessEqual(shown[0]["impressions"], 1115)
        you_rows = s["you"]["last_result"]["rows"]
        self.assertIn("impressions", you_rows[0])


class ZeroMinBidAndVisibilityTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        self.fac = Client()
        self.fac.force_login(User.objects.get(username="prof"))
        self.game = Game.objects.create(code="ZERO", name="Zero",
                                        starting_budget=Decimal("1000.00"), num_rounds=1)
        self.assertEqual(self.game.min_bid, Decimal("0.00"))  # new default
        self.kw = Keyword.objects.create(game=self.game, order=1, label="floor kw",
                                         search_volume=1000, ctr_curve=[0.3],
                                         conversion_rate=0.05, order_value=Decimal("60.00"),
                                         reserve_price=Decimal("1.00"))
        services.build_rounds(self.game, 1)
        self.team = Team.objects.create(game=self.game, name="T",
                                        budget_remaining=Decimal("1000.00"))
        services.configure_bots(self.game, 0, 1.0)
        self.stu = Client()
        self.stu.post("/join/", {"code": "ZERO", "display_name": "Ana"})
        self.stu.post("/g/ZERO/teams/join/", {"team_id": self.team.id})
        rnd = self.game.rounds.get(number=1)
        services.open_next_round(self.game)
        self.rnd = self.game.current_round

    def test_zero_bid_accepted_negative_rejected(self):
        r = self.stu.post("/g/ZERO/play/bid/", {f"bid_{self.kw.id}": "0"})
        self.assertEqual(Bid.objects.filter(round=self.rnd, team=self.team).count(), 1)
        self.assertEqual(Bid.objects.get(round=self.rnd, team=self.team).max_bid,
                         Decimal("0.00"))
        self.stu.post("/g/ZERO/play/bid/", {f"bid_{self.kw.id}": "-1"})
        # Negative rejected: existing zero bid still stands unchanged.
        self.assertEqual(Bid.objects.get(round=self.rnd, team=self.team).max_bid,
                         Decimal("0.00"))

    def test_zero_bid_below_floor_is_flagged_not_ranked(self):
        self.stu.post("/g/ZERO/play/bid/", {f"bid_{self.kw.id}": "0.50"})  # under 1.00 floor
        self.rnd.status = Round.Status.CLOSED
        self.rnd.save()
        services.resolve_round(self.rnd)
        services.reveal_round(self.rnd)
        res = RoundResult.objects.get(round=self.rnd, team=self.team)
        self.assertIsNone(res.position)
        from game.state import build_game_state
        s = build_game_state(self.game, team=self.team)
        row = s["you"]["last_result"]["rows"][0]
        self.assertTrue(row["below_floor"])

    def test_console_shows_price_floor(self):
        r = self.stu.get("/g/ZERO/play/")
        self.assertContains(r, "Price floor")
        self.assertContains(r, "1.00")

    def test_facilitator_table_and_csv_show_quality(self):
        self.stu.post("/g/ZERO/play/bid/", {f"bid_{self.kw.id}": "2.00"})
        self.rnd.status = Round.Status.CLOSED
        self.rnd.save()
        services.resolve_round(self.rnd)
        r = self.fac.get("/g/ZERO/facilitator/")
        self.assertContains(r, "Quality")
        self.assertContains(r, "Ad rank")
        csv_r = self.fac.get("/g/ZERO/facilitator/results.csv")
        body = csv_r.content.decode()
        self.assertIn("Quality score", body)
        self.assertIn("Ad rank", body)
        self.assertIn("5.0", body)  # default human quality back-computed

    def test_bot_quality_symmetric_around_human_default(self):
        services.configure_bots(self.game, 3, 1.0)
        services.generate_bot_bids(self.rnd)
        qs = [float(b.quality_score) for b in
              Bid.objects.filter(round=self.rnd, team__is_bot=True)]
        self.assertTrue(qs)
        self.assertTrue(all(3.0 <= q <= 7.0 for q in qs))


class TabbedResultsAndExportOptionsTests(TestCase):
    """Facilitator sees a tab per resolved round; CSV export can scope to one round."""

    def setUp(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        self.fac = Client()
        self.fac.force_login(User.objects.get(username="prof"))
        self.game = Game.objects.create(code="TABS", name="Tabs",
                                        starting_budget=Decimal("1000.00"), num_rounds=2)
        for i, label in enumerate(["alpha kw", "beta kw"], start=1):
            Keyword.objects.create(game=self.game, order=i, label=label,
                                   search_volume=1000, ctr_curve=[0.3],
                                   conversion_rate=0.05, order_value=Decimal("60.00"),
                                   reserve_price=Decimal("0.20"))
        services.build_rounds(self.game, 2)
        self.team = Team.objects.create(game=self.game, name="T",
                                        budget_remaining=Decimal("1000.00"))
        services.configure_bots(self.game, 0, 1.0)
        for n in (1, 2):
            rnd = services.open_next_round(self.game)
            kw = rnd.keywords.first()
            Bid.objects.create(round=rnd, team=self.team, keyword=kw,
                               max_bid=Decimal("1.00"))
            services.close_round(rnd)
            services.resolve_round(rnd)
            services.reveal_round(rnd)

    def test_dashboard_has_a_tab_and_pane_per_round(self):
        r = self.fac.get("/g/TABS/facilitator/")
        body = r.content.decode()
        self.assertEqual(body.count('class="round-tab'), 2)
        self.assertEqual(body.count('class="round-pane"'), 2)
        self.assertIn("Download current round", body)
        self.assertIn("Download all rounds", body)
        self.assertIn("results-filter", body)
        self.assertIn('data-sort="num"', body)

    def test_export_scopes(self):
        all_rows = self.fac.get("/g/TABS/facilitator/results.csv?rounds=all").content.decode()
        r1 = self.fac.get("/g/TABS/facilitator/results.csv?rounds=1").content.decode()
        r2 = self.fac.get("/g/TABS/facilitator/results.csv?rounds=2").content.decode()
        self.assertIn("alpha kw", all_rows)
        self.assertIn("beta kw", all_rows)
        self.assertIn("alpha kw", r1)
        self.assertNotIn("beta kw", r1)
        self.assertIn("beta kw", r2)
        self.assertNotIn("alpha kw", r2)
        # Filenames reflect the scope.
        resp = self.fac.get("/g/TABS/facilitator/results.csv?rounds=2")
        self.assertIn("round_2", resp["Content-Disposition"])

    def test_same_round_same_realized_volume_for_all_teams(self):
        rnd = self.game.rounds.get(number=1)
        kw = rnd.keywords.first()
        specs = [services.effective_spec(rnd, kw).search_volume for _ in range(3)]
        self.assertEqual(len(set(specs)), 1)  # deterministic, market-wide
        base = kw.search_volume
        self.assertGreaterEqual(specs[0], int(base * 0.89) - 1)
        self.assertLessEqual(specs[0], int(base * 1.11) + 1)
