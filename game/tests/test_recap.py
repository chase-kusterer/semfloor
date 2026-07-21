"""P&L history in the state snapshot and the recap page."""
from decimal import Decimal

from django.core.management import call_command
from django.test import Client, TestCase

from game import services
from game.models import Bid, Game, Team
from game.state import build_game_state


class RecapTests(TestCase):
    def setUp(self):
        call_command("seed_demo")
        self.game = Game.objects.get(code="DEMO")
        self.team = Team.objects.create(game=self.game, name="Humans",
                                        budget_remaining=self.game.starting_budget)

    def _play_round(self, bid="2.00"):
        rnd = services.open_next_round(self.game)
        # Bid on every keyword in the round.
        for kw in rnd.keywords.all():
            Bid.objects.update_or_create(round=rnd, team=self.team, keyword=kw,
                                         defaults={"max_bid": Decimal(bid), "quality_score": 6.0})
        services.close_round(rnd)
        services.resolve_round(rnd)
        services.reveal_round(rnd)
        return rnd

    def test_history_shape_and_cumulative(self):
        self._play_round(); self._play_round()
        st = build_game_state(self.game, team=self.team)
        hist = st["history"]
        self.assertIsNotNone(hist)
        self.assertEqual(hist["rounds"], [1, 2])
        # cumulative[i] must equal the running sum of per_round for every team
        for tser in hist["teams"]:
            running = 0.0
            for i, p in enumerate(tser["per_round"]):
                running = round(running + p, 2)
                self.assertAlmostEqual(tser["cumulative"][i], running, places=2)
        # teams are ordered by final standing (desc)
        finals = [t["final"] for t in hist["teams"]]
        self.assertEqual(finals, sorted(finals, reverse=True))
        # the requesting team gets its own per-round history
        self.assertEqual(len(st["you"]["history"]), 2)
        self.assertIn("cumulative", st["you"]["history"][0])

    def test_history_is_none_before_any_reveal(self):
        st = build_game_state(self.game)
        self.assertIsNone(st["history"])

    def test_recap_page_renders(self):
        self._play_round()
        r = Client().get(f"/g/{self.game.code}/recap/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "P&amp;L over rounds")
