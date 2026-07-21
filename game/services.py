"""
Orchestration: the round lifecycle, bots, and market events.

This service layer sits between the views (HTTP) and the rules engine (pure math). A
round moves PENDING -> OPEN -> CLOSED -> RESOLVED -> REVEALED.

Multi-keyword rounds: a round auctions SEVERAL keywords at once. Teams get a FRESH
budget allocation at the start of every round and decide how to spread it across the
round's keywords. Resolving runs the engine once per keyword; a team's spend on earlier
keywords (in keyword order) reduces the budget available to its later ones, so
over-committing everywhere is a real strategic mistake.
"""
from __future__ import annotations

import random
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .engine import TeamBid, resolve_keyword
from .models import Bid, Event, Keyword, Round, RoundResult, Team

# Human bids default to a mid quality score until a facilitator scores them (in the
# admin, on the Bid). Bots get their own varied quality below.
DEFAULT_HUMAN_QUALITY = 5.0

BOT_NAMES = ["Algo Traders", "Index Fund", "Momentum Bot", "Quant Desk",
             "Arb Bot", "Value Fund", "Day Trader", "HODL Bot"]

# A small deck of market events the facilitator can fire onto the current round. Each
# effect is a set of multipliers applied to the round's keyword fundamentals at resolve
# time. Events apply to EVERY keyword in the round.
EVENT_DECK = {
    "surge":       {"title": "Seasonal surge",     "effect": {"search_volume_mult": 1.6},
                    "description": "A spike in searches across this round's keywords."},
    "slump":       {"title": "Demand slump",       "effect": {"search_volume_mult": 0.55},
                    "description": "Interest cools; far fewer searches."},
    "intent":      {"title": "High-intent traffic","effect": {"conversion_rate_mult": 1.5},
                    "description": "Searchers are ready to buy."},
    "tirekickers": {"title": "Tire-kickers",       "effect": {"conversion_rate_mult": 0.5},
                    "description": "Lots of browsing, little buying."},
    "pricewar":    {"title": "Price war",          "effect": {"reserve_price_mult": 1.8},
                    "description": "Competitors flood in and lift the floor price."},
}

# The starter pack the Setup page can load with one click. Same shape as the demo
# seed: label, asset_class, search_volume, ctr_curve, conversion_rate, order_value,
# reserve_price. All values are editable after loading.
STARTER_KEYWORDS = [
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
    ("mba scholarships", "High-intent niche",
     2200, [0.36, 0.18, 0.11, 0.07, 0.05], 0.09, "180.00", "1.00"),
    ("crm software", "B2B heavyweight",
     8000, [0.24, 0.13, 0.09, 0.06, 0.04], 0.025, "400.00", "6.00"),
    ("cheap flights", "Commodity churn",
     45000, [0.20, 0.11, 0.07, 0.05, 0.03], 0.015, "22.00", "0.60"),
]


# --- keyword spec, adjusted by any events on the round ----------------------

def effective_spec(rnd: Round, keyword: Keyword):
    """One keyword's fundamentals with this round's fired events applied."""
    spec = keyword.to_spec()
    for ev in rnd.events.all():
        e = ev.effect or {}
        spec.search_volume = int(round_half(spec.search_volume * e.get("search_volume_mult", 1)))
        spec.conversion_rate *= e.get("conversion_rate_mult", 1)
        spec.order_value *= e.get("order_value_mult", 1)
        spec.reserve_price *= e.get("reserve_price_mult", 1)
    return spec


def round_half(x: float) -> float:
    return float(int(x + 0.5))


# --- bots -------------------------------------------------------------------

def generate_bot_bids(rnd: Round):
    """
    Give every bot a bid on EVERY keyword in this round, so a small class still faces a
    real auction on each one.

    A rational bidder's ceiling is the expected revenue per click (conversion_rate x
    order_value). Bots bid around that value, scaled by their aggressiveness and a little
    per-round randomness, with a varied quality score. Deterministic per
    (round, keyword, bot).
    """
    game = rnd.game
    min_bid = float(game.min_bid)
    for keyword in rnd.keywords.all():
        spec = keyword.to_spec()  # bots price off the base fundamentals
        fair_value = spec.conversion_rate * spec.order_value
        for bot in game.teams.filter(is_bot=True):
            rng = random.Random(f"{rnd.id}-{keyword.id}-{bot.id}")
            jitter = rng.uniform(0.7, 1.15)
            bid = max(min_bid, fair_value * bot.bot_aggressiveness * jitter)
            quality = round(rng.uniform(3.5, 8.0), 1)
            Bid.objects.update_or_create(
                round=rnd, team=bot, keyword=keyword,
                defaults={"max_bid": Decimal(str(round(bid, 2))), "quality_score": quality},
            )


def configure_bots(game, count: int, aggressiveness: float):
    """Grow or shrink the bot roster to `count` and set their aggressiveness."""
    bots = list(game.teams.filter(is_bot=True).order_by("id"))
    # Add bots up to count (skip names already taken).
    i = 0
    while len(bots) < count and i < len(BOT_NAMES):
        name = BOT_NAMES[i]
        i += 1
        if game.teams.filter(name=name).exists():
            continue
        bots.append(Team.objects.create(
            game=game, name=name, is_bot=True, bot_aggressiveness=aggressiveness,
            budget_remaining=game.starting_budget,
        ))
    # Remove extras if count decreased.
    while len(bots) > count:
        bots.pop().delete()
    game.teams.filter(is_bot=True).update(bot_aggressiveness=aggressiveness)


# --- round schedule ---------------------------------------------------------

def build_rounds(game, num_rounds: int):
    """
    (Re)build the round schedule: split the game's keywords across `num_rounds`
    pending rounds, in keyword order, as evenly as possible (earlier rounds get the
    extra keyword when it doesn't divide evenly).

    Refuses (returns None) if any round has already been played, to protect results;
    reset the game first. Existing pending rounds are replaced.
    """
    if game.rounds.exclude(status=Round.Status.PENDING).exists():
        return None
    keywords = list(game.keywords.all())
    if not keywords or num_rounds < 1:
        return None
    num_rounds = min(num_rounds, len(keywords))

    game.rounds.all().delete()
    base, extra = divmod(len(keywords), num_rounds)
    idx = 0
    rounds = []
    for n in range(1, num_rounds + 1):
        take = base + (1 if n <= extra else 0)
        rnd = Round.objects.create(game=game, number=n)
        rnd.keywords.set(keywords[idx:idx + take])
        idx += take
        rounds.append(rnd)
    game.num_rounds = num_rounds
    game.save()
    return rounds


# --- round lifecycle --------------------------------------------------------

@transaction.atomic
def open_next_round(game):
    """
    Open the next pending round: every team's budget is topped back up to the game's
    per-round allocation, then bots bid on each of the round's keywords.
    Returns the round, or None when there are no rounds left.
    """
    nxt = game.rounds.filter(status=Round.Status.PENDING).order_by("number").first()
    if nxt is None:
        game.status = game.Status.FINISHED
        game.current_round = None
        game.save()
        return None
    # Fresh allocation: everyone starts the round with the same budget.
    game.teams.update(budget_remaining=game.starting_budget)
    nxt.status = Round.Status.OPEN
    nxt.opened_at = timezone.now()
    nxt.save()
    game.current_round = nxt
    game.status = game.Status.RUNNING
    game.save()
    generate_bot_bids(nxt)
    return nxt


def close_round(round: Round):
    """Lock bids."""
    round.status = Round.Status.CLOSED
    round.closed_at = timezone.now()
    round.save()


@transaction.atomic
def resolve_round(round: Round):
    """
    Run the auction + funnel for every keyword in the round and post the results.

    Keywords resolve in order; a team's spend on earlier keywords reduces the budget
    it has left for later ones (its bid caps clicks by remaining budget). Cumulative
    spend/revenue/profit move the leaderboard. Guarded to run only once
    (CLOSED -> RESOLVED) so totals never double-count.
    """
    teams = {t.id: t for t in round.game.teams.all()}
    budget_left = {tid: float(t.budget_remaining) for tid, t in teams.items()}
    totals = {tid: {"spend": Decimal("0.00"), "revenue": Decimal("0.00"),
                    "profit": Decimal("0.00")} for tid in teams}

    for keyword in round.keywords.all():
        spec = effective_spec(round, keyword)
        bids = list(round.bids.filter(keyword=keyword).select_related("team"))
        team_bids = [
            TeamBid(
                team_id=str(b.team_id),
                max_bid=float(b.max_bid),
                quality_score=float(b.quality_score if b.quality_score is not None
                                    else DEFAULT_HUMAN_QUALITY),
                budget_remaining=budget_left[b.team_id],
            )
            for b in bids
        ]
        rows = resolve_keyword(spec, team_bids)
        for r in rows:
            tid = int(r.team_id)
            spend = Decimal(str(r.spend))
            revenue = Decimal(str(r.revenue))
            profit = Decimal(str(r.profit))
            RoundResult.objects.update_or_create(
                round=round, team_id=tid, keyword=keyword,
                defaults={
                    "ad_rank": r.ad_rank, "position": r.position,
                    "actual_cpc": Decimal(str(r.actual_cpc)),
                    "impressions": r.impressions, "clicks": r.clicks, "spend": spend,
                    "conversions": r.conversions, "revenue": revenue,
                    "profit": profit, "roas": r.roas,
                },
            )
            budget_left[tid] = max(0.0, budget_left[tid] - r.spend)
            totals[tid]["spend"] += spend
            totals[tid]["revenue"] += revenue
            totals[tid]["profit"] += profit

    for tid, t in teams.items():
        agg = totals[tid]
        if agg["spend"] or agg["revenue"]:
            t.budget_remaining -= agg["spend"]
            t.cumulative_spend += agg["spend"]
            t.cumulative_revenue += agg["revenue"]
            t.cumulative_profit += agg["profit"]
            t.save()

    round.status = Round.Status.RESOLVED
    round.save()


def reveal_round(round: Round):
    """Show the round's results on the big board."""
    round.status = Round.Status.REVEALED
    round.save()


def fire_event(game, key: str):
    """Attach a market event from the deck to the current round (all its keywords)."""
    card = EVENT_DECK.get(key)
    if card is None or game.current_round is None:
        return None
    return Event.objects.create(
        game=game, round=game.current_round,
        title=card["title"], description=card["description"], effect=card["effect"],
    )


@transaction.atomic
def reset_game(game):
    """Wipe progress (results, bids, events, totals) but keep teams, keywords, and bots."""
    RoundResult.objects.filter(round__game=game).delete()
    Bid.objects.filter(round__game=game).delete()
    game.events.all().delete()
    game.rounds.update(status=Round.Status.PENDING, opened_at=None, closed_at=None)
    for t in game.teams.all():
        t.budget_remaining = game.starting_budget
        t.cumulative_spend = Decimal("0.00")
        t.cumulative_revenue = Decimal("0.00")
        t.cumulative_profit = Decimal("0.00")
        t.save()
    game.current_round = None
    game.status = game.Status.SETUP
    game.save()


def load_starter_pack(game):
    """Append the starter keywords to the game (skipping labels it already has)."""
    existing = set(game.keywords.values_list("label", flat=True))
    order = (game.keywords.order_by("-order").values_list("order", flat=True).first() or 0)
    created = 0
    for label, asset_class, volume, ctr, cvr, ov, reserve in STARTER_KEYWORDS:
        if label in existing:
            continue
        order += 1
        Keyword.objects.create(
            game=game, order=order, label=label, asset_class=asset_class,
            search_volume=volume, ctr_curve=ctr, conversion_rate=cvr,
            order_value=Decimal(ov), reserve_price=Decimal(reserve),
        )
        created += 1
    return created
