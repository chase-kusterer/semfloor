"""
Top-level entry point: resolve one keyword end to end.

resolve_keyword() runs the auction and then the funnel for every team, and returns a
list of ResultRow (one per team). This is the single function the Django service layer
(Phase 3) will call to turn a round's bids into results.
"""
from __future__ import annotations

from .auction import run_auction
from .economics import KeywordSpec, ResultRow, TeamBid
from .funnel import run_funnel


def resolve_keyword(spec: KeywordSpec, bids: list[TeamBid]) -> list[ResultRow]:
    """Run auction + funnel for one keyword and return per-team results."""
    placements = run_auction(spec, bids)
    bids_by_id = {b.team_id: b for b in bids}

    rows: list[ResultRow] = []
    for p in placements:
        bid = bids_by_id[p.team_id]
        f = run_funnel(spec, p.position, p.actual_cpc, bid.budget_remaining)
        profit = round(f.revenue - f.spend, 2)
        roas = round(f.revenue / f.spend, 2) if f.spend > 0 else None
        rows.append(ResultRow(
            team_id=p.team_id,
            ad_rank=round(p.ad_rank, 2),
            position=p.position,
            actual_cpc=p.actual_cpc,
            impressions=f.impressions,
            clicks=f.clicks,
            spend=f.spend,
            conversions=f.conversions,
            revenue=f.revenue,
            profit=profit,
            roas=roas,
        ))
    return rows
