"""
The auction: Ad Rank ordering + generalized second-price (GSP) pricing.

Ad Rank = max_bid x quality_score. Ads are ranked highest-first and fill the
available slots. Under GSP, you never pay your own bid: the price for your slot is
set by the competitor ranked just below you.

    actual_cpc(i) = AdRank(i+1) / QualityScore(i) + 0.01

capped at your own max bid. The lowest shown ad has no one below it, so it pays the
keyword's reserve price (the floor). Higher quality lowers your CPC — that is the
lever that stops deep pockets from simply buying the top slot with junk ads.
"""
from __future__ import annotations

from dataclasses import dataclass

from .economics import KeywordSpec, TeamBid

CPC_INCREMENT = 0.01  # the standard "+ one cent" that keeps you just above the bid below


@dataclass
class Placement:
    """Auction outcome for one team, before the funnel runs."""

    team_id: str
    ad_rank: float
    position: int | None   # 1-based; None if the ad did not win a slot
    actual_cpc: float
    next_highest_bid: float | None = None  # max_bid of the ad ranked just below (price setter)


def _sort_key(bid: TeamBid):
    """Rank by Ad Rank desc; break ties by max_bid desc, then team_id for determinism."""
    ad_rank = bid.max_bid * bid.quality_score
    return (-ad_rank, -bid.max_bid, str(bid.team_id))


def run_auction(spec: KeywordSpec, bids: list[TeamBid]) -> list[Placement]:
    """Rank bids, assign slots, and price each shown ad via GSP."""
    # Standard eligibility rule: a bid below the reserve price cannot be shown.
    eligible = [b for b in bids if b.max_bid >= spec.reserve_price]
    ineligible = [b for b in bids if b.max_bid < spec.reserve_price]

    ordered = sorted(eligible, key=_sort_key)
    placements: list[Placement] = []

    for i, bid in enumerate(ordered):
        ad_rank = bid.max_bid * bid.quality_score
        shown = i < spec.ad_slots

        if not shown:
            placements.append(Placement(bid.team_id, ad_rank, None, 0.0))
            continue

        # The bidder immediately below sets the price — even if that bidder didn't win
        # a slot. If there is nobody below, this ad pays the reserve (floor) price.
        if i + 1 < len(ordered):
            below = ordered[i + 1]
            below_ad_rank = below.max_bid * below.quality_score
            cpc = below_ad_rank / bid.quality_score + CPC_INCREMENT
            next_highest = below.max_bid
        else:
            cpc = spec.reserve_price
            next_highest = None

        # The reserve is a floor for EVERY shown ad, not just the lowest; and you
        # never pay more than your own max bid (eligibility guarantees bid >= reserve).
        cpc = min(max(cpc, spec.reserve_price), bid.max_bid)
        placements.append(Placement(bid.team_id, ad_rank, i + 1, round(cpc, 2), next_highest))

    # Ineligible bids (below reserve) are never shown.
    for bid in ineligible:
        placements.append(Placement(bid.team_id, bid.max_bid * bid.quality_score, None, 0.0))

    return placements
