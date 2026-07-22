"""
The state seam.

build_game_state(game) returns a plain, JSON-serializable snapshot of everything the
big board and team consoles need to render. It is the SINGLE source of truth for the
UI, deliberately separated from how that snapshot is delivered (polling now, WebSockets
later — both call this same function).

Multi-keyword rounds: the current round exposes a `keywords` list, revealed results are
grouped per keyword (plus per-team totals for the round), and the private `you` block
carries this team's bid per keyword. Hidden keyword fundamentals (conversion rate,
order value, etc.) are intentionally NOT included.
"""
from __future__ import annotations

from collections import defaultdict

from django.db.models import F, Sum

from .models import RoundResult


def _pnl_history(game):
    """
    Cumulative and per-round profit for every team across the *revealed* rounds.

    A team's profit for a round is the SUM across that round's keywords. Teams that
    didn't bid count as 0 for that round. Ordered by final standing.
    """
    revealed = list(game.rounds.filter(status="revealed").order_by("number")
                    .prefetch_related("keywords"))
    if not revealed:
        return None
    round_ids = [r.id for r in revealed]
    profit_by = {
        (row["round_id"], row["team_id"]): float(row["total"])
        for row in RoundResult.objects.filter(round_id__in=round_ids)
        .values("round_id", "team_id").annotate(total=Sum("profit"))
    }
    series = []
    for t in game.teams.all():
        per_round, cumulative, running = [], [], 0.0
        for r in revealed:
            p = profit_by.get((r.id, t.id), 0.0)
            running += p
            per_round.append(round(p, 2))
            cumulative.append(round(running, 2))
        series.append({
            "team": t.name, "is_bot": t.is_bot,
            "per_round": per_round, "cumulative": cumulative,
            "final": cumulative[-1] if cumulative else 0.0,
        })
    series.sort(key=lambda s: s["final"], reverse=True)
    return {
        "rounds": [r.number for r in revealed],
        "keywords": [r.keyword_labels() for r in revealed],
        "teams": series,
    }


def _team_history(game, team):
    """Per-round detail for one team (revealed rounds only), summed across keywords."""
    revealed = list(game.rounds.filter(status="revealed").order_by("number")
                    .prefetch_related("keywords"))
    agg_by_round = {
        row["round_id"]: row
        for row in RoundResult.objects.filter(round__game=game, team=team,
                                              round__status="revealed")
        .values("round_id")
        .annotate(spend=Sum("spend"), revenue=Sum("revenue"), profit=Sum("profit"))
    }
    rows, running = [], 0.0
    for r in revealed:
        agg = agg_by_round.get(r.id)
        profit = float(agg["profit"]) if agg else 0.0
        running += profit
        rows.append({
            "round": r.number, "keyword": r.keyword_labels(),
            "spend": float(agg["spend"]) if agg else 0.0,
            "revenue": float(agg["revenue"]) if agg else 0.0,
            "profit": round(profit, 2), "cumulative": round(running, 2),
        })
    return rows


def _result_row(r):
    return {
        "team": r.team.name,
        "is_bot": r.team.is_bot,
        "position": r.position,
        "below_floor": bool(r.position is None and r.bid_amount
                            and r.bid_amount < r.keyword.reserve_price),
        "impressions": r.impressions if r.position else 0,
        "clicks": r.clicks,
        "spend": float(r.spend),
        "revenue": float(r.revenue),
        "profit": float(r.profit),
        "roas": r.roas,
    }


def build_game_state(game, team=None) -> dict:
    """
    Public snapshot of a game for the board and consoles.

    Pass `team` to also include a private `you` block (budget, this round's bids) for
    the requesting team's console. Hidden keyword fundamentals are never included.
    """
    current = game.current_round
    round_info = None
    if current is not None:
        kws = list(current.keywords.all())
        round_info = {
            "number": current.number,
            "status": current.status,
            # Only labels are public while a round is live.
            "keywords": [{"id": k.id, "label": k.label, "asset_class": k.asset_class}
                         for k in kws],
            "keyword": ", ".join(k.label for k in kws),  # display string
        }
        # Once revealed, the board shows results per keyword plus round totals.
        if current.status == "revealed":
            all_results = list(current.results.select_related("team", "keyword")
                               .order_by("keyword_id", F("position").asc(nulls_last=True)))
            by_kw = defaultdict(list)
            for r in all_results:
                by_kw[r.keyword_id].append(_result_row(r))
            round_info["results_by_keyword"] = [
                {"keyword": k.label, "rows": by_kw.get(k.id, [])} for k in kws
            ]
            # Per-team totals for the whole round (leaderboard-style).
            totals = defaultdict(lambda: {"clicks": 0, "spend": 0.0,
                                          "revenue": 0.0, "profit": 0.0})
            names = {}
            for r in all_results:
                t = totals[r.team_id]
                t["clicks"] += r.clicks
                t["spend"] += float(r.spend)
                t["revenue"] += float(r.revenue)
                t["profit"] += float(r.profit)
                names[r.team_id] = (r.team.name, r.team.is_bot)
            round_info["round_totals"] = sorted(
                [{"team": names[tid][0], "is_bot": names[tid][1],
                  "clicks": v["clicks"], "spend": round(v["spend"], 2),
                  "revenue": round(v["revenue"], 2), "profit": round(v["profit"], 2)}
                 for tid, v in totals.items()],
                key=lambda x: x["profit"], reverse=True,
            )

    leaderboard = [
        {
            "team": t.name,
            "is_bot": t.is_bot,
            "members": t.member_count,
            "budget_remaining": float(t.budget_remaining),
            "cumulative_profit": float(t.cumulative_profit),
            "cumulative_revenue": float(t.cumulative_revenue),
            "cumulative_spend": float(t.cumulative_spend),
        }
        # Team.Meta already orders by -cumulative_profit, so this is leaderboard order.
        for t in game.teams.all()
    ]

    state = {
        "game": {"code": game.code, "name": game.name, "status": game.status,
                 "num_rounds": game.num_rounds, "ad_slots": game.ad_slots,
                 "max_team_size": game.max_team_size,
                 "round_budget": float(game.starting_budget)},
        "current_round": round_info,
        "leaderboard": leaderboard,
        # Per-team cumulative P&L across revealed rounds (for the recap chart).
        "history": _pnl_history(game),
    }

    if team is not None:
        current_bids = []
        if current is not None:
            bid_by_kw = {b.keyword_id: b for b in current.bids.filter(team=team)}
            for k in current.keywords.all():
                b = bid_by_kw.get(k.id)
                current_bids.append({"keyword_id": k.id, "keyword": k.label,
                                     "amount": float(b.max_bid) if b else None})
        state["you"] = {
            "team": team.name,
            "members": [m.display_name or "member" for m in team.members.all()],
            "budget_remaining": float(team.budget_remaining),
            "cumulative_profit": float(team.cumulative_profit),
            "current_bids": current_bids,
            # This team's round-by-round history (revealed rounds only).
            "history": _team_history(game, team),
        }
        # After reveal, show this team its own per-keyword result for the round.
        if current is not None and current.status == "revealed":
            my = list(current.results.filter(team=team).select_related("keyword")
                      .order_by("keyword_id"))
            if my:
                rows = [{
                    "keyword": r.keyword.label, "position": r.position,
                    "below_floor": bool(r.position is None and r.bid_amount
                                        and r.bid_amount < r.keyword.reserve_price),
                    "impressions": r.impressions if r.position else 0,
                    "clicks": r.clicks, "spend": float(r.spend),
                    "revenue": float(r.revenue), "profit": float(r.profit),
                    "roas": r.roas,
                } for r in my]
                state["you"]["last_result"] = {
                    "rows": rows,
                    "spend": round(sum(r["spend"] for r in rows), 2),
                    "revenue": round(sum(r["revenue"] for r in rows), 2),
                    "profit": round(sum(r["profit"] for r in rows), 2),
                }

    return state
