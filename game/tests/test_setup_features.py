"""Tests for the setup/quality/play-mode feature batch."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase

from game import services
from game.models import Bid, Game, Keyword, Round, RoundResult, Team, TeamMember


def _mk_keywords(game, n):
    for i in range(1, n + 1):
        Keyword.objects.create(game=game, order=i, label=f"kw{i}",
                               search_volume=1000, ctr_curve=[0.3],
                               conversion_rate=0.05, order_value=Decimal("60.00"),
                               reserve_price=Decimal("0.20"))


class ScheduleBugAndActiveTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        self.c = Client()
        self.c.force_login(User.objects.get(username="prof"))
        self.game = Game.objects.create(code="POOL", name="Pool")
        _mk_keywords(self.game, 30)

    def test_one_round_two_keywords_uses_only_two(self):
        # The reported bug: 1 round x 2/kw must NOT balloon into 15 rounds.
        self.c.post("/g/POOL/setup/rounds/build/", {"num_rounds": "1",
                                                    "keywords_per_round": "2"})
        rounds = list(self.game.rounds.order_by("number"))
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].keywords.count(), 2)
        self.assertEqual([k.label for k in rounds[0].keywords.order_by("order")],
                         ["kw1", "kw2"])
        self.game.refresh_from_db()
        self.assertEqual(self.game.num_rounds, 1)

    def test_three_rounds_of_four(self):
        self.c.post("/g/POOL/setup/rounds/build/", {"num_rounds": "3",
                                                    "keywords_per_round": "4"})
        counts = [r.keywords.count() for r in self.game.rounds.order_by("number")]
        self.assertEqual(counts, [4, 4, 4])  # 12 of the 30 used

    def test_inactive_keywords_are_skipped(self):
        self.game.keywords.filter(label__in=["kw1", "kw3"]).update(is_active=False)
        self.c.post("/g/POOL/setup/rounds/build/", {"num_rounds": "1",
                                                    "keywords_per_round": "2"})
        rnd = self.game.rounds.get(number=1)
        self.assertEqual([k.label for k in rnd.keywords.order_by("order")],
                         ["kw2", "kw4"])

    def test_active_toggle_autosaves_with_json(self):
        kw = self.game.keywords.get(label="kw1")
        r = self.c.post("/g/POOL/setup/keywords/edit/",
                        {"keyword_id": kw.id, "label": "kw1", "is_active": "0",
                         "search_volume": kw.search_volume,
                         "conversion_rate": kw.conversion_rate,
                         "order_value": kw.order_value,
                         "reserve_price": kw.reserve_price},
                        headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        kw.refresh_from_db()
        self.assertFalse(kw.is_active)


class IndividualPlayTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(code="SOLO", name="Solo",
                                        starting_budget=Decimal("1000.00"))

    def test_play_individually_checkbox_creates_personal_team(self):
        stu = Client()
        r = stu.post("/join/", {"code": "SOLO", "display_name": "Ana",
                                "play_individually": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/play/", r.headers["Location"])
        team = Team.objects.get(game=self.game, name="Ana")
        self.assertEqual(team.members.count(), 1)

    def test_individual_mode_forces_personal_team(self):
        self.game.play_mode = Game.PlayMode.INDIVIDUAL
        self.game.save()
        stu = Client()
        r = stu.post("/join/", {"code": "SOLO", "display_name": "Bo"})
        self.assertIn("/play/", r.headers["Location"])
        self.assertTrue(Team.objects.filter(game=self.game, name="Bo").exists())

    def test_duplicate_names_get_suffixes(self):
        for _ in range(2):
            c = Client()
            c.post("/join/", {"code": "SOLO", "display_name": "Sam",
                              "play_individually": "1"})
        names = set(Team.objects.filter(game=self.game).values_list("name", flat=True))
        self.assertEqual(names, {"Sam", "Sam (2)"})

    def test_team_mode_without_checkbox_goes_to_team_select(self):
        stu = Client()
        r = stu.post("/join/", {"code": "SOLO", "display_name": "Cy"})
        self.assertIn("/teams/", r.headers["Location"])


class QualityScoreTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(code="QS", name="QS",
                                        starting_budget=Decimal("1000.00"))
        _mk_keywords(self.game, 4)
        services.build_rounds(self.game, 1)  # all 4 keywords in one round
        self.rnd = self.game.rounds.get(number=1)
        self.t1 = Team.objects.create(game=self.game, name="A",
                                      budget_remaining=Decimal("1000.00"))
        self.t2 = Team.objects.create(game=self.game, name="B",
                                      budget_remaining=Decimal("1000.00"))

    def test_uniform_mode_default_five(self):
        qv = services.quality_vector(self.rnd, self.t1)
        self.assertEqual(set(qv.values()), {5.0})
        self.assertEqual(len(qv), 4)

    def test_random_mode_equal_sums_within_bounds(self):
        self.game.quality_mode = Game.QualityMode.RANDOM
        self.game.quality_min, self.game.quality_max = 0.0, 10.0
        self.game.save()
        self.rnd.refresh_from_db()
        v1 = services.quality_vector(self.rnd, self.t1)
        v2 = services.quality_vector(self.rnd, self.t2)
        self.assertNotEqual(v1, v2)  # different draws per team
        self.assertAlmostEqual(sum(v1.values()), sum(v2.values()), delta=0.5)
        for v in list(v1.values()) + list(v2.values()):
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 10.0)
        # Deterministic: same call, same numbers.
        self.assertEqual(v1, services.quality_vector(self.rnd, self.t1))

    def test_manual_mode_respects_team_bounds(self):
        self.game.quality_mode = Game.QualityMode.MANUAL
        self.game.save()
        self.t1.quality_min, self.t1.quality_max = 8.0, 10.0
        self.t1.save()
        self.t2.quality_min, self.t2.quality_max = 0.0, 2.0
        self.t2.save()
        self.rnd.refresh_from_db()
        for v in services.quality_vector(self.rnd, self.t1).values():
            self.assertGreaterEqual(v, 8.0)
        for v in services.quality_vector(self.rnd, self.t2).values():
            self.assertLessEqual(v, 2.0)

    def test_resolve_uses_quality_and_bot_opt_in(self):
        self.game.quality_mode = Game.QualityMode.MANUAL
        self.game.save()
        self.t1.quality_min = self.t1.quality_max = 9.0
        self.t1.save()
        self.rnd.status = Round.Status.OPEN
        self.rnd.save()
        kw = self.rnd.keywords.first()
        Bid.objects.create(round=self.rnd, team=self.t1, keyword=kw,
                           max_bid=Decimal("1.00"))
        self.rnd.status = Round.Status.CLOSED
        self.rnd.save()
        services.resolve_round(self.rnd)
        res = RoundResult.objects.get(round=self.rnd, team=self.t1, keyword=kw)
        self.assertAlmostEqual(res.ad_rank, 9.0, places=1)  # 1.00 bid x 9.0 quality
        # Bots keep their own quality unless opted in.
        self.assertFalse(self.game.quality_apply_bots)
        bot = Team.objects.create(game=self.game, name="bot", is_bot=True,
                                  bot_aggressiveness=1.0,
                                  budget_remaining=Decimal("1000.00"))
        q_off = services.team_quality_for_resolve(self.rnd, bot, kw, 6.5)
        self.assertEqual(q_off, 6.5)  # stored bot quality wins
        self.game.quality_apply_bots = True
        self.game.save()
        self.rnd.refresh_from_db()
        q_on = services.team_quality_for_resolve(self.rnd, bot, kw, 6.5)
        self.assertGreaterEqual(q_on, bot.quality_min)
        self.assertLessEqual(q_on, bot.quality_max)

    def test_console_shows_quality_when_enabled(self):
        self.rnd.status = Round.Status.OPEN
        self.rnd.save()
        self.game.current_round = self.rnd
        self.game.save()
        stu = Client()
        stu.post("/join/", {"code": "QS", "display_name": "Ana"})
        stu.post("/g/QS/teams/join/", {"team_id": self.t1.id})
        r = stu.get("/g/QS/play/")
        self.assertContains(r, "Your quality")
        self.game.quality_show_players = False
        self.game.save()
        r = stu.get("/g/QS/play/")
        self.assertNotContains(r, "Your quality")


class AutosaveEndpointTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("prof", "p@example.com", "pw")
        self.c = Client()
        self.c.force_login(User.objects.get(username="prof"))
        self.game = Game.objects.create(code="AUTO", name="Auto")
        self.team = Team.objects.create(game=self.game, name="T",
                                        budget_remaining=Decimal("0.00"))

    def test_settings_autosave_json_and_quality_fields(self):
        r = self.c.post("/g/AUTO/setup/settings/", {
            "name": "Auto", "starting_budget": "5000", "min_bid": "0",
            "ad_slots": "3", "max_team_size": "4",
            "play_mode": "individual",
            "quality_mode": "random", "quality_min": "2", "quality_max": "8",
            "quality_apply_bots": "1", "quality_show_players": "0",
        }, headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.game.refresh_from_db()
        self.assertEqual(self.game.play_mode, "individual")
        self.assertEqual(self.game.quality_mode, "random")
        self.assertEqual((self.game.quality_min, self.game.quality_max), (2.0, 8.0))
        self.assertTrue(self.game.quality_apply_bots)
        self.assertFalse(self.game.quality_show_players)

    def test_team_quality_endpoint(self):
        r = self.c.post("/g/AUTO/setup/teams/quality/", {
            "team_id": self.team.id, "quality_min": "7", "quality_max": "3",
        }, headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.json()["ok"])
        self.team.refresh_from_db()
        self.assertEqual((self.team.quality_min, self.team.quality_max), (3.0, 7.0))

    def test_bots_autosave_json(self):
        r = self.c.post("/g/AUTO/facilitator/bots/", {"count": "2", "aggressiveness": "1.1"},
                        headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(self.game.teams.filter(is_bot=True).count(), 2)

    def test_setup_page_renders_new_sections(self):
        self.c.post("/g/AUTO/setup/keywords/starter/")  # scroll window needs rows
        r = self.c.get("/g/AUTO/setup/")
        self.assertContains(r, "Quality scores")
        self.assertContains(r, "Uniform Player Quality Score(s)")
        self.assertContains(r, "Apply quality score settings to bots?")
        self.assertContains(r, "Show Quality Scores to Players")
        self.assertContains(r, "Team Mode")
        self.assertContains(r, "Individual Mode")
        self.assertContains(r, "kw-scroll")
        self.assertContains(r, "save-toast")


class ViewTogglesAndScheduleRandomTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("prof3", "p3@example.com", "pw")
        self.fac = Client()
        self.fac.force_login(User.objects.get(username="prof3"))
        self.game = Game.objects.create(code="TGL", name="Toggle",
                                        starting_budget=Decimal("1000.00"))
        _mk_keywords(self.game, 12)

    def test_randomized_schedule_draws_from_pool(self):
        # With a fixed order it would always pick kw1..kw4; over several shuffles
        # of 12 keywords the selection must differ at least once.
        selections = set()
        for _ in range(6):
            self.fac.post("/g/TGL/setup/rounds/build/",
                          {"num_rounds": "2", "keywords_per_round": "2",
                           "randomize_keywords": "1"})
            labels = tuple(sorted(
                k.label for r in self.game.rounds.all() for k in r.keywords.all()))
            selections.add(labels)
        self.assertGreater(len(selections), 1)
        # Unticked -> deterministic list order.
        self.fac.post("/g/TGL/setup/rounds/build/",
                      {"num_rounds": "2", "keywords_per_round": "2"})
        labels = [k.label for r in self.game.rounds.order_by("number")
                  for k in r.keywords.order_by("order")]
        self.assertEqual(labels, ["kw1", "kw2", "kw3", "kw4"])

    def test_console_has_toggles_and_reload_marker(self):
        services.build_rounds(self.game, 2)
        team = Team.objects.create(game=self.game, name="T",
                                   budget_remaining=Decimal("1000.00"))
        stu = Client()
        stu.post("/join/", {"code": "TGL", "display_name": "Ana"})
        stu.post("/g/TGL/teams/join/", {"team_id": team.id})
        r = stu.get("/g/TGL/play/")
        self.assertContains(r, ">Planner<")
        self.assertContains(r, ">Big Board<")
        self.assertNotContains(r, ">Recap<")   # nothing revealed yet
        self.assertContains(r, "renderedRound")
        # Reveal a round -> Recap button appears.
        rnd = services.open_next_round(self.game)
        services.close_round(rnd)
        services.resolve_round(rnd)
        services.reveal_round(rnd)
        r = stu.get("/g/TGL/play/")
        self.assertContains(r, ">Recap<")

    def test_board_and_recap_have_toggles(self):
        anon = Client()
        self.assertContains(anon.get("/g/TGL/board/"), ">Planner<")
        self.assertContains(anon.get("/g/TGL/recap/"), ">Planner<")

    def test_setup_renders_randomize_checkbox_and_new_label(self):
        r = self.fac.get("/g/TGL/setup/")
        self.assertContains(r, "Randomize keyword selection")
        self.assertContains(r, "Show Quality Scores to Players")
