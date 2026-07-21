"""
Django admin registrations — the facilitator's back office for Phase 1.

LIVE-TICKER SPRINT (later phase): when we move from round-based polling to a
continuous live ticker, delivery moves to WebSockets via Django Channels. That work
lives in the delivery layer (asgi.py, a new game/consumers.py, and game/routing.py)
and reuses game/state.py unchanged. The models and this admin do NOT need to change:
admin stays the CRUD surface for games, keyword fundamentals, teams, and events.
"""
from django.contrib import admin

from .models import Bid, Event, Game, Keyword, Round, RoundResult, Team, TeamMember


class KeywordInline(admin.TabularInline):
    model = Keyword
    extra = 0
    fields = ("order", "label", "asset_class", "search_volume",
              "conversion_rate", "order_value", "reserve_price")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "status", "num_rounds", "starting_budget",
                    "ad_slots", "max_team_size", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "code")
    inlines = [KeywordInline]


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ("label", "game", "order", "asset_class", "search_volume",
                    "conversion_rate", "order_value", "reserve_price")
    list_filter = ("game", "asset_class")
    search_fields = ("label",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "is_bot", "member_count", "budget_remaining", "cumulative_profit")
    list_filter = ("game", "is_bot")
    search_fields = ("name",)


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = ("display_name", "team", "game", "joined_at")
    list_filter = ("game",)
    search_fields = ("display_name",)


@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
    list_display = ("number", "game", "keyword_labels", "status", "opened_at", "closed_at")
    list_filter = ("game", "status")
    filter_horizontal = ("keywords",)


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ("team", "round", "keyword", "max_bid", "quality_score", "updated_at")
    list_filter = ("round__game",)


@admin.register(RoundResult)
class RoundResultAdmin(admin.ModelAdmin):
    list_display = ("team", "round", "keyword", "position", "actual_cpc", "clicks",
                    "conversions", "revenue", "profit", "roas")
    list_filter = ("round__game",)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "game", "round", "created_at")
    list_filter = ("game",)
