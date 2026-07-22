"""
Database models for the SEM Trading Floor.

These persist game state and results. The *rules* (how an auction resolves, how the
funnel turns clicks into revenue) deliberately live outside the models, in the
Django-agnostic engine at game/engine/. Keyword.to_spec() below is the bridge: it
converts a stored Keyword row into the plain dataclass the engine consumes.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.crypto import get_random_string

# Default click-through-rate curve, indexed by ad position (1st, 2nd, 3rd, ...).
# Higher positions earn dramatically more clicks; editable per keyword in the admin.
DEFAULT_CTR_CURVE = [0.30, 0.16, 0.10, 0.07, 0.05, 0.03]


def _make_game_code() -> str:
    """Short, unambiguous join code (no easily-confused characters)."""
    return get_random_string(5, allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


class Game(models.Model):
    """A single run of the simulation for one class/section."""

    class Status(models.TextChoices):
        SETUP = "setup", "Setup"
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"

    code = models.CharField(max_length=8, unique=True, default=_make_game_code,
                            help_text="Join code students type to enter the game.")
    name = models.CharField(max_length=120, default="SEM Trading Floor")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SETUP)

    # Rules / configuration (all facilitator-editable).
    num_rounds = models.PositiveIntegerField(default=5)
    starting_budget = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("10000.00"),
        help_text="Play-money budget every team receives FRESH at the start of each round.",
    )
    min_bid = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"),
                                  help_text="Lowest bid students may enter; 0 lets any non-negative bid through (keyword reserve prices still act as the auction floor).")
    ad_slots = models.PositiveIntegerField(default=3, help_text="Ad positions available per keyword.")
    max_team_size = models.PositiveIntegerField(
        default=4, validators=[MinValueValidator(1)],
        help_text="Maximum members per team. Set to 1 to force solo play.",
    )

    # Pointer to the round currently in play (nullable until the first round opens).
    current_round = models.OneToOneField(
        "Round", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Keyword(models.Model):
    """
    A tradable keyword and its hidden "fundamentals".

    Students never see these numbers directly; they experience them through the
    outcomes (clicks, conversions, revenue). Think of each keyword as an asset with
    its own volume, intent, and price behavior.
    """

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="keywords")
    order = models.PositiveIntegerField(default=0, help_text="Round order for this keyword.")
    label = models.CharField(max_length=120)
    asset_class = models.CharField(
        max_length=80, blank=True,
        help_text='Flavor text, e.g. "Branded blue chip" or "Speculative meme".',
    )

    # --- Fundamentals consumed by the engine ---
    search_volume = models.PositiveIntegerField(default=5000, help_text="Impressions available per shown ad.")
    ctr_curve = models.JSONField(default=list, blank=True,
                                 help_text="Click-through rate by position; empty = use default curve.")
    conversion_rate = models.FloatField(default=0.03, help_text="Share of clicks that convert (0-1).")
    order_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("50.00"),
                                      help_text="Revenue per conversion.")
    reserve_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.50"),
                                        help_text="Floor CPC paid by the lowest shown ad.")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.label} [{self.game.code}]"

    def to_spec(self):
        """Bridge: build the engine's plain KeywordSpec from this DB row."""
        from game.engine.economics import KeywordSpec

        return KeywordSpec(
            label=self.label,
            search_volume=self.search_volume,
            ctr_curve=list(self.ctr_curve) or list(DEFAULT_CTR_CURVE),
            conversion_rate=float(self.conversion_rate),
            order_value=float(self.order_value),
            reserve_price=float(self.reserve_price),
            ad_slots=self.game.ad_slots,
        )


class Team(models.Model):
    """A competing team (or a bot). One shared console per team in the UI."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=80)

    budget_remaining = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cumulative_spend = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cumulative_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cumulative_profit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # Bots let a small class still face real competition (added to the auction in Phase 3).
    is_bot = models.BooleanField(default=False)
    bot_aggressiveness = models.FloatField(default=1.0,
                                           help_text="Bid multiplier vs. a keyword's fair value (bots only).")

    # Identity is now tracked per-member via TeamMember (a team can seat several people).
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("game", "name")
        ordering = ["-cumulative_profit", "name"]

    def __str__(self):
        tag = " [bot]" if self.is_bot else ""
        return f"{self.name}{tag} ({self.game.code})"

    @property
    def member_count(self) -> int:
        return self.members.count()

    def has_open_seat(self) -> bool:
        """True if another person can still join this team (bounded by max_team_size)."""
        return self.member_count < self.game.max_team_size


class TeamMember(models.Model):
    """
    A person occupying a seat on a team, identified by their browser session.

    This is what enforces team size: a team is "full" once its member count reaches the
    game's max_team_size. One browser session holds at most one seat per game, so a
    student cannot silently occupy two teams. Solo play is just a team with one member.
    """

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="members")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    session_key = models.CharField(max_length=64)
    display_name = models.CharField(max_length=80, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("game", "session_key")  # one seat per browser per game
        ordering = ["joined_at"]

    def __str__(self):
        who = self.display_name or "member"
        return f"{who} on {self.team.name} ({self.game.code})"


class Round(models.Model):
    """
    One trading round. A round now auctions SEVERAL keywords at once: teams see all of
    the round's keywords together and decide how to spread their (fresh) round budget
    across them. Rounds move through these states in order.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"      # created, not yet accepting bids
        OPEN = "open", "Open"               # accepting bids
        CLOSED = "closed", "Closed"         # bids locked, not yet resolved
        RESOLVED = "resolved", "Resolved"   # auction + funnel computed
        REVEALED = "revealed", "Revealed"   # results shown on the big board

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="rounds")
    number = models.PositiveIntegerField()
    keywords = models.ManyToManyField(Keyword, related_name="rounds",
                                      help_text="The keywords up for auction in this round.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("game", "number")
        ordering = ["number"]

    def __str__(self):
        return f"Round {self.number} ({self.game.code})"

    def keyword_labels(self) -> str:
        """Comma-joined labels, for headers and admin lists."""
        return ", ".join(k.label for k in self.keywords.all())


class Bid(models.Model):
    """
    A team's order for ONE KEYWORD in one round. One bid per (round, team, keyword);
    resubmitting updates the same row, so the latest submission from any teammate wins
    while the round is open. A team may bid on any subset of the round's keywords.
    """

    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="bids")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="bids")
    keyword = models.ForeignKey(Keyword, on_delete=models.CASCADE, related_name="bids")

    max_bid = models.DecimalField(max_digits=8, decimal_places=2)
    # Quality score is *earned* (ad + landing-page rubric), not random. Nullable until
    # the facilitator scores it; the auction uses it as the Ad Rank multiplier.
    quality_score = models.FloatField(null=True, blank=True)
    ad_text = models.CharField(max_length=200, blank=True)
    landing_choice = models.CharField(max_length=80, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("round", "team", "keyword")

    def __str__(self):
        return f"{self.team.name} bids {self.max_bid} on {self.keyword.label} (round {self.round.number})"


class RoundResult(models.Model):
    """Computed outcome for one team on one keyword in one round (written at resolve)."""

    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="results")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="results")
    keyword = models.ForeignKey(Keyword, on_delete=models.CASCADE, related_name="results")

    bid_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"),
                                     help_text="The team's own max bid on this keyword.")
    next_highest_bid = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                           help_text="Bid of the ad ranked just below — the GSP price setter.")
    ad_rank = models.FloatField(default=0.0)
    position = models.PositiveIntegerField(null=True, blank=True, help_text="Null = ad not shown.")
    actual_cpc = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))

    impressions = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    spend = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    conversions = models.FloatField(default=0.0)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    profit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    roas = models.FloatField(null=True, blank=True, help_text="revenue / spend; null if no spend.")

    class Meta:
        unique_together = ("round", "team", "keyword")
        ordering = ["keyword_id", "position"]

    def __str__(self):
        return f"{self.team.name} @ {self.keyword.label} r{self.round.number}: profit {self.profit}"


class Event(models.Model):
    """
    A market event ("news card") the facilitator can fire between rounds, e.g. a demand
    spike or a competitor entering. `effect` is a small JSON blob describing the modifier
    the engine/service will apply (wired up in Phase 3).
    """

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="events")
    round = models.ForeignKey(Round, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    effect = models.JSONField(default=dict, blank=True,
                              help_text='e.g. {"keyword": "dubai", "search_volume_mult": 1.6}')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.game.code})"
