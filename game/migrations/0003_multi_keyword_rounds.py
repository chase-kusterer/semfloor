"""
Multi-keyword rounds.

Rounds move from a single `keyword` FK to a `keywords` many-to-many, and Bid /
RoundResult gain a `keyword` FK so bids and results are per (round, team, keyword).
Budgets also become "fresh each round" (a semantic change on Game.starting_budget;
no schema change needed there beyond help text).

Because the new FKs are non-nullable, this migration first WIPES in-flight play data
(bids, results, rounds) — teams, keywords, and game settings are kept. Rebuild the
round schedule from the new Setup page (or `manage.py seed_demo --reset` for the demo).
"""
from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def wipe_play_data(apps, schema_editor):
    Game = apps.get_model("game", "Game")
    Round = apps.get_model("game", "Round")
    Bid = apps.get_model("game", "Bid")
    RoundResult = apps.get_model("game", "RoundResult")
    Game.objects.update(current_round=None)
    RoundResult.objects.all().delete()
    Bid.objects.all().delete()
    Round.objects.all().delete()


class Migration(migrations.Migration):

    # Non-atomic on purpose: on PostgreSQL, the row deletions above queue deferred
    # FK triggers that stay pending until commit, and the subsequent ALTER TABLE on
    # game_round then fails with "cannot ALTER TABLE ... because it has pending
    # trigger events". Running each operation in its own transaction avoids this.
    atomic = False

    dependencies = [
        ("game", "0002_remove_team_session_key_game_max_team_size_and_more"),
    ]

    operations = [
        migrations.RunPython(wipe_play_data, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(name="bid", unique_together=set()),
        migrations.AlterUniqueTogether(name="roundresult", unique_together=set()),
        migrations.RemoveField(model_name="round", name="keyword"),
        migrations.AddField(
            model_name="round",
            name="keywords",
            field=models.ManyToManyField(
                help_text="The keywords up for auction in this round.",
                related_name="rounds", to="game.keyword",
            ),
        ),
        migrations.AddField(
            model_name="bid",
            name="keyword",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="bids", to="game.keyword",
            ),
        ),
        migrations.AddField(
            model_name="roundresult",
            name="keyword",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="results", to="game.keyword",
            ),
        ),
        migrations.AlterField(
            model_name="game",
            name="starting_budget",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("10000.00"),
                help_text="Play-money budget every team receives FRESH at the start of each round.",
                max_digits=12,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="bid", unique_together={("round", "team", "keyword")},
        ),
        migrations.AlterUniqueTogether(
            name="roundresult", unique_together={("round", "team", "keyword")},
        ),
        migrations.AlterModelOptions(
            name="roundresult",
            options={"ordering": ["keyword_id", "position"]},
        ),
    ]
