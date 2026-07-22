"""Round lifecycle (multi-keyword) + market events + reset + facilitator auth."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase

from game import services
from game.models import Bid, Game, Round, Team


class LifecycleTests(TestCase):
    def setUp(self):
        call_command("seed_demo")
        self.game = Game.objects.get(code="DEMO")

    def test_full_round_updates_leaderboard(self):
        team = Team.objects.create(game=self.game, name="Humans",
                                   budget_remaining=Decimal("0.00"))
        rnd = services.open_next_round(self.game)
        self.assertEqual(rnd.status, Round.Status.OPEN)
        self.assertGreater(rnd.keywords.count(), 1)  # multi-keyword round
        team.refresh_from_db()
        # Fresh allocation on open: everyone is topped up to the round budget.
        self.assertEqual(team.budget_remaining, self.game.starting_budget)
        # Bots bid on EVERY keyword in the round.
        bot = self.game.teams.filter(is_bot=True).first()
        self.assertEqual(rnd.bids.filter(team=bot).count(), rnd.keywords.count())

        # The human team bids on two keywords.
        kws = list(rnd.keywords.all())
        for kw in kws[:2]:
            Bid.objects.update_or_create(round=rnd, team=team, keyword=kw,
                                         defaults={"max_bid": Decimal("3.00"), "quality_score": 7.0})
        services.close_round(rnd)
        services.resolve_round(rnd)
        team.refresh_from_db()
        results = rnd.results.filter(team=team)
        self.assertEqual(results.count(), 2)  # one result per keyword bid
        total_profit = sum(r.profit for r in results)
        total_spend = sum(r.spend for r in results)
        self.assertEqual(team.cumulative_profit, total_profit)
        self.assertEqual(team.budget_remaining, self.game.starting_budget - total_spend)

    def test_budget_is_fresh_each_round(self):
        services.configure_bots(self.game, 0, 1.0)  # no bots: the human surely wins a slot
        team = Team.objects.create(game=self.game, name="Humans")
        r1 = services.open_next_round(self.game)
        kw = r1.keywords.first()
        Bid.objects.create(round=r1, team=team, keyword=kw,
                           max_bid=Decimal("3.00"), quality_score=7.0)
        services.close_round(r1); services.resolve_round(r1); services.reveal_round(r1)
        team.refresh_from_db()
        spent = team.cumulative_spend
        self.assertGreater(spent, 0)
        self.assertLess(team.budget_remaining, self.game.starting_budget)
        # Opening the next round tops the budget back up.
        services.open_next_round(self.game)
        team.refresh_from_db()
        self.assertEqual(team.budget_remaining, self.game.starting_budget)
        # ...but cumulative totals persist.
        self.assertEqual(team.cumulative_spend, spent)

    def test_event_applies_to_every_keyword_at_resolve(self):
        rnd = services.open_next_round(self.game)
        bases = {k.id: k.to_spec().search_volume for k in rnd.keywords.all()}
        services.fire_event(self.game, "surge")  # search_volume x1.6
        for k in rnd.keywords.all():
            vol = services.effective_spec(rnd, k).search_volume
            expected = bases[k.id] * 1.6
            # Realized volume = event-adjusted volume with ±11% market jitter,
            # deterministic per (round, keyword).
            self.assertGreaterEqual(vol, int(expected * 0.89) - 1)
            self.assertLessEqual(vol, int(expected * 1.11) + 1)
            self.assertEqual(vol, services.effective_spec(rnd, k).search_volume)  # deterministic

    def test_reset_clears_progress(self):
        rnd = services.open_next_round(self.game)
        services.close_round(rnd); services.resolve_round(rnd)
        services.reset_game(self.game); self.game.refresh_from_db()
        self.assertIsNone(self.game.current_round_id)
        self.assertEqual(self.game.rounds.filter(status=Round.Status.PENDING).count(),
                         self.game.rounds.count())

    def test_build_rounds_splits_evenly_and_locks_after_play(self):
        # 5 demo keywords over 3 rounds -> 2 / 2 / 1.
        counts = [r.keywords.count() for r in self.game.rounds.order_by("number")]
        self.assertEqual(counts, [2, 2, 1])
        # Rebuild to 2 rounds -> 3 / 2.
        services.build_rounds(self.game, 2)
        counts = [r.keywords.count() for r in self.game.rounds.order_by("number")]
        self.assertEqual(counts, [3, 2])
        # Once a round has been played, rebuilding is refused.
        services.open_next_round(self.game)
        self.assertIsNone(services.build_rounds(self.game, 4))


class FacilitatorAuthTests(TestCase):
    def setUp(self):
        call_command("seed_demo")
        self.game = Game.objects.get(code="DEMO")

    def test_dashboard_requires_staff(self):
        r = Client().get(f"/g/{self.game.code}/facilitator/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r["Location"])

    def test_setup_requires_staff(self):
        r = Client().get("/setup/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r["Location"])
        r = Client().get(f"/g/{self.game.code}/setup/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r["Location"])

    def test_control_blocked_for_anonymous(self):
        r = Client().post(f"/g/{self.game.code}/facilitator/round/open/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r["Location"])
        self.assertIsNone(Game.objects.get(code="DEMO").current_round)  # nothing opened

    def test_staff_can_drive_rounds(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        c = Client(); c.force_login(User.objects.get(username="prof"))
        r = c.post(f"/g/{self.game.code}/facilitator/round/open/")
        self.assertEqual(r.status_code, 302)
        self.game.refresh_from_db()
        self.assertIsNotNone(self.game.current_round)
