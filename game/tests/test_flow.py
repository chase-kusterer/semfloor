"""Join flow (direct link + code), team-size enforcement, multi-keyword bidding,
the setup wizard, and the state endpoint (HTTP)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase

from game import services
from game.models import Bid, Game, Keyword, Team


class JoinFlowTests(TestCase):
    def setUp(self):
        call_command("seed_demo")
        self.game = Game.objects.get(code="DEMO")
        self.game.max_team_size = 2
        self.game.save()

    def test_join_create_and_console(self):
        c = Client()
        self.assertEqual(c.get("/").status_code, 200)
        r = c.post("/join/", {"code": "demo", "display_name": "Alice"})  # lowercase ok
        self.assertRedirects(r, f"/g/{self.game.code}/teams/")
        r = c.post(f"/g/{self.game.code}/teams/create/", {"team_name": "Team A"})
        self.assertRedirects(r, f"/g/{self.game.code}/play/")
        self.assertEqual(Team.objects.get(name="Team A").member_count, 1)

    def test_direct_join_link_skips_code_entry(self):
        """/g/<CODE>/ is the link the instructor posts on the course page."""
        c = Client()
        r = c.get(f"/g/{self.game.code}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Your name")
        self.assertContains(r, self.game.code)     # hidden code field
        self.assertNotContains(r, 'id="code"')     # no visible code input
        # Lowercase in the pasted URL still works.
        self.assertEqual(c.get(f"/g/{self.game.code.lower()}/").status_code, 200)
        # After joining, the same link goes straight to the console.
        c.post("/join/", {"code": self.game.code, "display_name": "Zed"})
        c.post(f"/g/{self.game.code}/teams/create/", {"team_name": "Zeds"})
        r = c.get(f"/g/{self.game.code}/")
        self.assertRedirects(r, f"/g/{self.game.code}/play/")

    def test_team_size_cap_enforced(self):
        c1 = Client(); c1.post("/join/", {"code": "DEMO", "display_name": "A"})
        c1.post(f"/g/{self.game.code}/teams/create/", {"team_name": "Cap"})
        team = Team.objects.get(name="Cap")
        c2 = Client(); c2.post("/join/", {"code": "DEMO", "display_name": "B"})
        c2.post(f"/g/{self.game.code}/teams/join/", {"team_id": team.id})
        self.assertEqual(team.member_count, 2)  # full at max_team_size=2
        c3 = Client(); c3.post("/join/", {"code": "DEMO", "display_name": "C"})
        c3.post(f"/g/{self.game.code}/teams/join/", {"team_id": team.id})
        self.assertEqual(team.member_count, 2)  # third person blocked

    def test_state_json(self):
        r = Client().get(f"/g/{self.game.code}/state.json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("leaderboard", r.json())
        self.assertEqual(r.json()["game"]["max_team_size"], 2)


class MultiKeywordBidTests(TestCase):
    def setUp(self):
        call_command("seed_demo")
        self.game = Game.objects.get(code="DEMO")
        self.c = Client()
        self.c.get(f"/g/{self.game.code}/")
        self.c.post("/join/", {"code": "DEMO", "display_name": "Alice"})
        self.c.post(f"/g/{self.game.code}/teams/create/", {"team_name": "Humans"})
        self.team = Team.objects.get(name="Humans")
        self.rnd = services.open_next_round(self.game)
        self.kws = list(self.rnd.keywords.all())

    def test_bid_on_some_keywords_blank_means_sit_out(self):
        data = {f"bid_{self.kws[0].id}": "3.00", f"bid_{self.kws[1].id}": ""}
        r = self.c.post(f"/g/{self.game.code}/play/bid/", data)
        self.assertRedirects(r, f"/g/{self.game.code}/play/")
        bids = Bid.objects.filter(round=self.rnd, team=self.team)
        self.assertEqual(bids.count(), 1)
        self.assertEqual(bids.first().keyword_id, self.kws[0].id)

    def test_resubmit_replaces_and_blank_clears(self):
        self.c.post(f"/g/{self.game.code}/play/bid/",
                    {f"bid_{self.kws[0].id}": "3.00", f"bid_{self.kws[1].id}": "2.00"})
        self.assertEqual(Bid.objects.filter(round=self.rnd, team=self.team).count(), 2)
        # Resubmit: raise one, clear the other.
        self.c.post(f"/g/{self.game.code}/play/bid/",
                    {f"bid_{self.kws[0].id}": "4.50", f"bid_{self.kws[1].id}": ""})
        bids = Bid.objects.filter(round=self.rnd, team=self.team)
        self.assertEqual(bids.count(), 1)
        self.assertEqual(bids.first().max_bid, Decimal("4.50"))

    def test_min_bid_enforced_per_keyword(self):
        self.c.post(f"/g/{self.game.code}/play/bid/",
                    {f"bid_{self.kws[0].id}": "0.10",   # below min 0.50 -> rejected
                     f"bid_{self.kws[1].id}": "1.00"})  # fine
        bids = Bid.objects.filter(round=self.rnd, team=self.team)
        self.assertEqual(bids.count(), 1)
        self.assertEqual(bids.first().keyword_id, self.kws[1].id)

    def test_console_shows_all_round_keywords(self):
        r = self.c.get(f"/g/{self.game.code}/play/")
        self.assertEqual(r.status_code, 200)
        for kw in self.kws:
            self.assertContains(r, kw.label)
            self.assertContains(r, f'name="bid_{kw.id}"')


class SetupWizardTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        self.c = Client()
        self.c.force_login(User.objects.get(username="prof"))

    def test_create_game_starter_pack_and_schedule(self):
        # Create a game from the setup home page.
        r = self.c.post("/setup/", {"name": "MKT 101", "starting_budget": "8000",
                                    "max_team_size": "3"})
        game = Game.objects.get(name="MKT 101")
        self.assertRedirects(r, f"/g/{game.code}/setup/")
        self.assertEqual(game.starting_budget, Decimal("8000.00"))
        # Load the starter pack.
        self.c.post(f"/g/{game.code}/setup/keywords/starter/")
        self.assertEqual(game.keywords.count(), len(services.STARTER_KEYWORDS))
        # Loading again adds no duplicates.
        self.c.post(f"/g/{game.code}/setup/keywords/starter/")
        self.assertEqual(game.keywords.count(), len(services.STARTER_KEYWORDS))
        # Build a 4-round schedule: 8 keywords -> 2 per round.
        self.c.post(f"/g/{game.code}/setup/rounds/build/", {"num_rounds": "4"})
        counts = [r.keywords.count() for r in game.rounds.order_by("number")]
        self.assertEqual(counts, [2, 2, 2, 2])
        # Clear all keywords wipes the pending schedule too.
        self.c.post(f"/g/{game.code}/setup/keywords/clear/")
        self.assertEqual(game.keywords.count(), 0)
        self.assertEqual(game.rounds.count(), 0)

    def test_add_and_remove_keyword(self):
        self.c.post("/setup/", {"name": "G2"})
        game = Game.objects.get(name="G2")
        self.c.post(f"/g/{game.code}/setup/keywords/add/",
                    {"label": "espresso machines", "asset_class": "Niche",
                     "search_volume": "4000", "conversion_rate": "0.04",
                     "order_value": "120", "reserve_price": "1.00"})
        kw = Keyword.objects.get(game=game, label="espresso machines")
        self.assertEqual(kw.search_volume, 4000)
        self.c.post(f"/g/{game.code}/setup/keywords/delete/", {"keyword_id": kw.id})
        self.assertEqual(game.keywords.count(), 0)

    def test_setup_page_renders(self):
        self.c.post("/setup/", {"name": "G3"})
        game = Game.objects.get(name="G3")
        r = self.c.get(f"/g/{game.code}/setup/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Student link")
        self.assertContains(r, f"/g/{game.code}/")
