"""
URL routes for the game app.

Namespaced under "game" so templates use {% url 'game:console' code=... %}.
"""
from django.urls import path

from . import views

app_name = "game"

urlpatterns = [
    # Join flow
    path("", views.join, name="join"),
    path("join/", views.join_submit, name="join_submit"),
    # The one-click student link (paste this on the course page): /g/<CODE>/
    path("g/<str:code>/", views.direct_join, name="direct_join"),
    path("g/<str:code>/teams/", views.team_select, name="team_select"),
    path("g/<str:code>/teams/create/", views.team_create, name="team_create"),
    path("g/<str:code>/teams/join/", views.team_join, name="team_join"),

    # Surfaces
    path("g/<str:code>/board/", views.board, name="board"),
    path("g/<str:code>/recap/", views.recap, name="recap"),
    path("g/<str:code>/play/", views.console, name="console"),
    path("g/<str:code>/play/bid/", views.submit_bid, name="submit_bid"),
    path("g/<str:code>/facilitator/", views.facilitator, name="facilitator"),

    # State seam (polling now; Channels later)
    path("g/<str:code>/state.json", views.state_json, name="state_json"),

    # Facilitator round controls
    path("g/<str:code>/facilitator/round/open/", views.fac_open_round, name="fac_open_round"),
    path("g/<str:code>/facilitator/round/close/", views.fac_close_round, name="fac_close_round"),
    path("g/<str:code>/facilitator/round/resolve/", views.fac_resolve_round, name="fac_resolve_round"),
    path("g/<str:code>/facilitator/round/reveal/", views.fac_reveal_round, name="fac_reveal_round"),
    path("g/<str:code>/facilitator/event/", views.fac_event, name="fac_event"),
    path("g/<str:code>/facilitator/bots/", views.fac_bots, name="fac_bots"),
    path("g/<str:code>/facilitator/reset/", views.fac_reset, name="fac_reset"),

    # Setup wizard (facilitator-only; no Django admin needed)
    path("setup/", views.setup_home, name="setup_home"),
    path("g/<str:code>/setup/", views.setup_game, name="setup_game"),
    path("g/<str:code>/setup/settings/", views.setup_settings, name="setup_settings"),
    path("g/<str:code>/setup/keywords/add/", views.setup_keyword_add, name="setup_keyword_add"),
    path("g/<str:code>/setup/keywords/delete/", views.setup_keyword_delete, name="setup_keyword_delete"),
    path("g/<str:code>/setup/keywords/starter/", views.setup_starter_pack, name="setup_starter_pack"),
    path("g/<str:code>/setup/keywords/clear/", views.setup_keywords_clear, name="setup_keywords_clear"),
    path("g/<str:code>/setup/rounds/build/", views.setup_build_rounds, name="setup_build_rounds"),
]
