"""
The conversion funnel: turn a won ad position into clicks, conversions, and revenue.

Flow:  impressions -> clicks -> spend, and clicks -> conversions -> revenue.

Clicks and conversions are *expected* outcomes (the average over many searches), which
is why conversions can be fractional. Money is rounded to cents. A team's per-round
budget caps how many clicks it can actually pay for (budget pacing): if you can only
afford 74 clicks, you get 74, not the full demand.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .economics import KeywordSpec


@dataclass
class FunnelOutcome:
    impressions: int
    clicks: int
    spend: float
    conversions: float
    revenue: float


def run_funnel(spec: KeywordSpec, position: int | None, actual_cpc: float,
               budget_remaining: float | None) -> FunnelOutcome:
    """Compute the funnel for a single shown (or not-shown) ad."""
    if position is None:
        return FunnelOutcome(0, 0, 0.0, 0.0, 0.0)

    impressions = spec.search_volume
    ctr = spec.ctr_for_position(position)
    demanded_clicks = impressions * ctr

    # Budget pacing: cap clicks by what the team can afford at this CPC.
    if budget_remaining is not None and actual_cpc > 0:
        affordable = budget_remaining / actual_cpc
        demanded_clicks = min(demanded_clicks, affordable)

    clicks = int(math.floor(demanded_clicks))  # whole, paid-for clicks
    spend = round(clicks * actual_cpc, 2)
    conversions = round(clicks * spec.conversion_rate, 2)
    revenue = round(conversions * spec.order_value, 2)

    return FunnelOutcome(impressions, clicks, spend, conversions, revenue)
