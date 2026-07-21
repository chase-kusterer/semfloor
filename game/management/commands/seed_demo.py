"""
Seed a demo game so you have something to click through immediately.

Creates a Game (join code "DEMO" if free), five keywords modeled as "asset
classes", a 3-round schedule (keywords split 2 / 2 / 1), and a few bot teams. Teams
receive the budget FRESH each round. Fundamentals here are
illustrative starting points — all are editable in the admin.

    python manage.py seed_demo            # create (skips if DEMO already exists)
    python manage.py seed_demo --reset    # delete an existing DEMO game first
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from game import services
from game.models import Game, Keyword, Team

# label, asset_class, search_volume, ctr_curve, conversion_rate, order_value, reserve
KEYWORDS = [
    ("hult international business school", "Branded blue chip",
     1500, [0.42, 0.20, 0.12, 0.08, 0.05], 0.14, "250.00", "0.50"),
    ("google analytics", "Crowded momentum",
     12000, [0.28, 0.15, 0.10, 0.07, 0.05], 0.02, "60.00", "3.50"),
    ("dubai", "High-volume, volatile",
     60000, [0.22, 0.12, 0.08, 0.05, 0.03], 0.008, "35.00", "1.20"),
    ("digital analytics", "Mid-cap",
     3000, [0.30, 0.16, 0.10, 0.07, 0.05], 0.05, "140.00", "1.80"),
    ("ninja", "Speculative / ambiguous",
     9000, [0.26, 0.14, 0.09, 0.06, 0.04], 0.012, "25.00", "0.80"),
]

BOTS = [("Algo Traders", 1.1), ("Index Fund", 0.9), ("Momentum Bot", 1.25)]


class Command(BaseCommand):
    help = "Create a demo game with five keyword 'asset classes' and a few bots."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete an existing DEMO game before seeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            Game.objects.filter(code="DEMO").delete()
            self.stdout.write("Deleted any existing DEMO game.")

        if Game.objects.filter(code="DEMO").exists():
            self.stdout.write(self.style.WARNING(
                "A game with code DEMO already exists. Use --reset to recreate it."))
            return

        game = Game.objects.create(
            code="DEMO", name="SEM Trading Floor (Demo)",
            num_rounds=3, starting_budget=Decimal("10000.00"),
            min_bid=Decimal("0.50"), ad_slots=3,
        )

        for i, (label, asset_class, volume, ctr, cvr, ov, reserve) in enumerate(KEYWORDS, start=1):
            Keyword.objects.create(
                game=game, order=i, label=label, asset_class=asset_class,
                search_volume=volume, ctr_curve=ctr, conversion_rate=cvr,
                order_value=Decimal(ov), reserve_price=Decimal(reserve),
            )
        # Split the keywords across 3 pending rounds (2 / 2 / 1).
        services.build_rounds(game, 3)

        for name, aggressiveness in BOTS:
            Team.objects.create(
                game=game, name=name, is_bot=True, bot_aggressiveness=aggressiveness,
                budget_remaining=game.starting_budget,
            )

        self.stdout.write(self.style.SUCCESS(
            f"Created demo game '{game.name}' (code {game.code}) with "
            f"{len(KEYWORDS)} keywords across 3 rounds and {len(BOTS)} bots."))
        self.stdout.write("Open /setup/ to inspect or edit the game.")
