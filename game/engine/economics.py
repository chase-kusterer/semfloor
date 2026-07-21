"""Value objects passed in and out of the engine. Pure Python, no Django."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KeywordSpec:
    """A keyword's fundamentals, as the engine needs them."""

    label: str
    search_volume: int            # impressions available to each shown ad
    ctr_curve: list[float]        # click-through rate by position (index 0 = position 1)
    conversion_rate: float        # share of clicks that convert (0-1)
    order_value: float            # revenue per conversion
    reserve_price: float          # floor CPC paid by the lowest shown ad
    ad_slots: int = 3             # number of ad positions available

    def ctr_for_position(self, position: int) -> float:
        """CTR for a 1-based position; positions past the curve earn its last value."""
        if position < 1 or not self.ctr_curve:
            return 0.0
        idx = min(position, len(self.ctr_curve)) - 1
        return self.ctr_curve[idx]


@dataclass
class TeamBid:
    """One team's order for one keyword."""

    team_id: str                       # any hashable id (DB pk, bot name, etc.)
    max_bid: float                     # most the team is willing to pay per click
    quality_score: float               # earned 1-10 rubric score; the Ad Rank multiplier
    budget_remaining: float | None = None  # None = uncapped; else caps clicks by budget


@dataclass
class ResultRow:
    """Everything one team gets back for one keyword."""

    team_id: str
    ad_rank: float
    position: int | None       # None = ad not shown
    actual_cpc: float
    impressions: int
    clicks: int
    spend: float
    conversions: float
    revenue: float
    profit: float
    roas: float | None         # revenue / spend; None when there is no spend
    fields_note: str = field(default="", repr=False)
