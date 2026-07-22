"""
Views: the join flow (direct link + team-size enforcement), the three surfaces (big
board, team console, facilitator dashboard), the state.json polling endpoint, the
facilitator round controls, and the one-page Setup wizard.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import keyword_io, services
from .decorators import facilitator_required
from .models import Bid, Game, Keyword, Round, RoundResult, Team, TeamMember
from .state import build_game_state


# --- small session helpers -------------------------------------------------

def _session_key(request) -> str:
    """Return this browser's session key, creating a session if one doesn't exist yet."""
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _current_membership(request, game) -> TeamMember | None:
    """The TeamMember (if any) this browser session holds in the given game."""
    key = request.session.session_key
    if not key:
        return None
    return TeamMember.objects.filter(game=game, session_key=key).select_related("team").first()


# --- join flow -------------------------------------------------------------

def join(request):
    """Landing page: enter a game join code and a display name."""
    return render(request, "game/join.html", {"game": None})


def direct_join(request, code):
    """
    The one-click student link: /g/<CODE>/.

    Post this URL on the course page; students who click it skip the code entry —
    they just type their name and pick a team. If this browser already holds a seat,
    it goes straight to the team console.
    """
    game = get_object_or_404(Game, code=code.upper())
    if _current_membership(request, game) is not None:
        return redirect("game:console", code=game.code)
    return render(request, "game/join.html", {"game": game})


@require_POST
def join_submit(request):
    """Validate the code, remember the display name, and go pick a team."""
    code = (request.POST.get("code") or "").strip().upper()
    display_name = (request.POST.get("display_name") or "").strip()
    game = Game.objects.filter(code=code).first()
    if game is None:
        messages.error(request, "No game found with that code.")
        return redirect("game:join")

    _session_key(request)
    request.session["display_name"] = display_name

    # Individual play: the game is in Individual Mode, or the player ticked
    # "Play Individually" — either way they get a personal one-seat team.
    if game.play_mode == Game.PlayMode.INDIVIDUAL or request.POST.get("play_individually"):
        if _current_membership(request, game) is None:
            base = display_name or "Player"
            name, n = base, 2
            while game.teams.filter(name=name).exists():
                name = f"{base} ({n})"
                n += 1
            team = Team.objects.create(game=game, name=name,
                                       budget_remaining=game.starting_budget)
            TeamMember.objects.create(game=game, team=team,
                                      session_key=_session_key(request),
                                      display_name=display_name)
        return redirect("game:console", code=game.code)

    return redirect("game:team_select", code=game.code)


def team_select(request, code):
    """Choose an existing team with an open seat, or create a new one."""
    game = get_object_or_404(Game, code=code)
    membership = _current_membership(request, game)
    if membership is not None:
        # Already seated — go straight to the console.
        return redirect("game:console", code=game.code)

    teams = [
        {"obj": t, "count": t.member_count, "open": t.has_open_seat()}
        for t in game.teams.filter(is_bot=False)
    ]
    return render(request, "game/teams.html", {"game": game, "teams": teams})


@require_POST
def team_create(request, code):
    """Create a new team and seat the current session as its first member."""
    game = get_object_or_404(Game, code=code)
    if _current_membership(request, game) is not None:
        return redirect("game:console", code=game.code)

    name = (request.POST.get("team_name") or "").strip()
    if not name:
        messages.error(request, "Please enter a team name.")
        return redirect("game:team_select", code=game.code)
    if game.teams.filter(name=name).exists():
        messages.error(request, "That team name is taken. Pick another.")
        return redirect("game:team_select", code=game.code)

    team = Team.objects.create(game=game, name=name, budget_remaining=game.starting_budget)
    TeamMember.objects.create(
        game=game, team=team, session_key=_session_key(request),
        display_name=request.session.get("display_name", ""),
    )
    return redirect("game:console", code=game.code)


@require_POST
def team_join(request, code):
    """Join an existing team if it still has an open seat (enforces max_team_size)."""
    game = get_object_or_404(Game, code=code)
    if _current_membership(request, game) is not None:
        return redirect("game:console", code=game.code)

    team = get_object_or_404(Team, game=game, pk=request.POST.get("team_id"), is_bot=False)
    if not team.has_open_seat():
        messages.error(request, f"“{team.name}” is full ({game.max_team_size} max). Pick another team.")
        return redirect("game:team_select", code=game.code)

    TeamMember.objects.create(
        game=game, team=team, session_key=_session_key(request),
        display_name=request.session.get("display_name", ""),
    )
    return redirect("game:console", code=game.code)


# --- the three surfaces ----------------------------------------------------

def board(request, code):
    """Big board: projected view. Renders once, then polls state.json to stay fresh."""
    game = get_object_or_404(Game, code=code)
    return render(request, "game/board.html", {
        "game": game,
        "state": build_game_state(game),
        "state_url": reverse("game:state_json", args=[game.code]),
    })


def recap(request, code):
    """
    End-of-game recap: a per-team P&L curve across all revealed rounds, the final
    standings, and a round-by-round profit matrix. Public and projectable; it fills in
    live as rounds are revealed, so it doubles as a closing slide.
    """
    game = get_object_or_404(Game, code=code)
    return render(request, "game/recap.html", {
        "game": game,
        "state": build_game_state(game),
        "state_url": reverse("game:state_json", args=[game.code]),
    })


def console(request, code):
    """Team console. Requires a seat in this game; otherwise send to team selection."""
    game = get_object_or_404(Game, code=code)
    membership = _current_membership(request, game)
    if membership is None:
        return redirect("game:team_select", code=game.code)

    team = membership.team
    current = game.current_round
    keyword_bids = []
    if current is not None:
        bid_by_kw = {b.keyword_id: b for b in current.bids.filter(team=team)}
        qv = (services.quality_vector(current, team)
              if game.quality_show_players else {})
        keyword_bids = [
            {"keyword": k, "bid": bid_by_kw.get(k.id), "quality": qv.get(k.id)}
            for k in current.keywords.all()
        ]
    return render(request, "game/console.html", {
        "game": game,
        "team": team,
        "has_recap": game.rounds.filter(status=Round.Status.REVEALED).exists(),
        "membership": membership,
        "current_round": current,
        "keyword_bids": keyword_bids,
        "state": build_game_state(game, team=team),
        "state_url": reverse("game:state_json", args=[game.code]),
    })


@require_POST
def submit_bid(request, code):
    """
    Store or replace this team's bids for the open round — one amount per keyword.

    The console posts fields named bid_<keyword_id> (and optional ad_<keyword_id>).
    A blank amount means "no bid on that keyword" and removes any existing bid for it.
    Any teammate can submit while the round is open; the latest submission wins.
    """
    game = get_object_or_404(Game, code=code)
    membership = _current_membership(request, game)
    if membership is None:
        return redirect("game:team_select", code=game.code)
    team = membership.team

    current = game.current_round
    if current is None or current.status != Round.Status.OPEN:
        messages.error(request, "There is no open round to bid on right now.")
        return redirect("game:console", code=game.code)

    placed, cleared, errors = [], 0, []
    for keyword in current.keywords.all():
        raw = (request.POST.get(f"bid_{keyword.id}") or "").strip()
        if raw == "":
            # No bid on this keyword: clear any earlier one.
            n, _ = Bid.objects.filter(round=current, team=team, keyword=keyword).delete()
            cleared += n
            continue
        try:
            max_bid = Decimal(raw).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError):
            errors.append(f"“{keyword.label}”: enter a valid amount.")
            continue
        if max_bid < 0:
            errors.append(f"“{keyword.label}”: a bid can't be negative.")
            continue
        if max_bid < game.min_bid:
            errors.append(f"“{keyword.label}”: minimum bid is {game.min_bid}.")
            continue
        Bid.objects.update_or_create(
            round=current, team=team, keyword=keyword,
            defaults={
                "max_bid": max_bid,
                "ad_text": (request.POST.get(f"ad_{keyword.id}") or "").strip(),
            },
        )
        placed.append(keyword.label)

    for e in errors:
        messages.error(request, e)
    if placed:
        messages.success(request, f"Bids recorded on: {', '.join(placed)}.")
    elif cleared and not errors:
        messages.success(request, "Bids cleared.")
    elif not errors:
        messages.info(request, "No bids entered — leave amounts blank to sit a keyword out.")
    return redirect("game:console", code=game.code)


@facilitator_required
def facilitator(request, code):
    """Facilitator dashboard: round controls, events, bots, roster, share links."""
    game = get_object_or_404(Game, code=code)
    teams = [
        {"obj": t, "members": list(t.members.all()), "count": t.member_count}
        for t in game.teams.filter(is_bot=False)
    ]
    join_url = request.build_absolute_uri(reverse("game:direct_join", args=[game.code]))
    current = game.current_round
    current_results = None
    if current is not None and current.status in (Round.Status.RESOLVED, Round.Status.REVEALED):
        current_results = True  # kept for template's "results exist" check
    # Every resolved/revealed round gets its own tab.
    results_rounds = []
    for rnd in (game.rounds.filter(status__in=[Round.Status.RESOLVED, Round.Status.REVEALED])
                .order_by("number").prefetch_related("keywords")):
        rows = list(rnd.results.select_related("team", "keyword").all())
        for r in rows:
            # Quality score back-computed from ad rank (ad_rank = bid x quality).
            r.quality = round(r.ad_rank / float(r.bid_amount), 1) if r.bid_amount else None
            r.below_floor = (r.position is None and r.bid_amount
                             and r.bid_amount < r.keyword.reserve_price)
        results_rounds.append({"round": rnd, "rows": rows})
    event_choices = [(k, v["title"]) for k, v in services.EVENT_DECK.items()]
    return render(request, "game/facilitator.html", {
        "game": game,
        "teams": teams,
        "bots": game.teams.filter(is_bot=True),
        "current_round": current,
        "current_results": current_results,
        "results_rounds": results_rounds,
        "event_choices": event_choices,
        "rounds": game.rounds.prefetch_related("keywords").all(),
        "join_url": join_url,
        "state_url": reverse("game:state_json", args=[game.code]),
    })


# --- state seam ------------------------------------------------------------

def state_json(request, code):
    """
    The polling snapshot (also the exact payload Channels will push in the live-ticker
    sprint). Includes a private `you` block when the requester holds a seat.
    """
    game = get_object_or_404(Game, code=code)
    membership = _current_membership(request, game)
    team = membership.team if membership else None
    return JsonResponse(build_game_state(game, team=team))


# ---------------------------------------------------------------------------
# Facilitator round controls
# ---------------------------------------------------------------------------

@require_POST
@facilitator_required
def fac_open_round(request, code):
    game = get_object_or_404(Game, code=code)
    current = game.current_round
    if current is not None and current.status != Round.Status.REVEALED:
        messages.error(request, "Finish the current round (close, resolve, reveal) before opening the next.")
    elif not game.rounds.exists():
        messages.error(request, "No rounds yet — build the schedule on the Setup page first.")
    else:
        rnd = services.open_next_round(game)
        if rnd is None:
            messages.info(request, "No rounds left — the game is finished.")
        else:
            messages.success(
                request,
                f"Round {rnd.number} is open: {rnd.keyword_labels()}. "
                f"Budgets topped up to {game.starting_budget}; bots have bid.",
            )
    return redirect("game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_close_round(request, code):
    game = get_object_or_404(Game, code=code)
    current = game.current_round
    if current is None or current.status != Round.Status.OPEN:
        messages.error(request, "There is no open round to close.")
    else:
        services.close_round(current)
        messages.success(request, f"Round {current.number} closed. Bids are locked.")
    return redirect("game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_resolve_round(request, code):
    game = get_object_or_404(Game, code=code)
    current = game.current_round
    if current is None or current.status != Round.Status.CLOSED:
        messages.error(request, "Close the round before resolving it.")
    else:
        services.resolve_round(current)
        messages.success(request, f"Round {current.number} resolved. Leaderboard updated.")
    return redirect("game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_reveal_round(request, code):
    game = get_object_or_404(Game, code=code)
    current = game.current_round
    if current is None or current.status != Round.Status.RESOLVED:
        messages.error(request, "Resolve the round before revealing it.")
    else:
        services.reveal_round(current)
        messages.success(request, f"Round {current.number} results are now on the big board.")
    return redirect("game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_event(request, code):
    game = get_object_or_404(Game, code=code)
    current = game.current_round
    key = request.POST.get("event_key", "")
    if current is None or current.status not in (Round.Status.OPEN, Round.Status.CLOSED):
        messages.error(request, "Fire an event during an open (or closed, pre-resolve) round.")
    elif services.fire_event(game, key) is None:
        messages.error(request, "Unknown event.")
    else:
        messages.success(request, f"Event fired: {services.EVENT_DECK[key]['title']}.")
    return redirect("game:facilitator", code=code)


# --- team & player management (facilitator) ---------------------------------

@require_POST
@facilitator_required
def fac_team_add(request, code):
    """Create an empty team players can join (or that plays as a no-show)."""
    game = get_object_or_404(Game, code=code)
    name = (request.POST.get("team_name") or "").strip()
    if not name:
        return _saved(request, "Enter a team name.", redirect_to="game:facilitator",
                      code=code, ok=False)
    if game.teams.filter(name=name).exists():
        return _saved(request, f"There's already a team called “{name}”.",
                      redirect_to="game:facilitator", code=code, ok=False)
    Team.objects.create(game=game, name=name, budget_remaining=game.starting_budget)
    return _saved(request, f"Team “{name}” created.", redirect_to="game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_team_edit(request, code):
    """Rename a team (autosaves)."""
    game = get_object_or_404(Game, code=code)
    team = get_object_or_404(Team, game=game, pk=request.POST.get("team_id"), is_bot=False)
    name = (request.POST.get("team_name") or "").strip()
    if not name:
        return _saved(request, "A team needs a name.", redirect_to="game:facilitator",
                      code=code, ok=False)
    if game.teams.filter(name=name).exclude(pk=team.pk).exists():
        return _saved(request, f"There's already a team called “{name}”.",
                      redirect_to="game:facilitator", code=code, ok=False)
    team.name = name
    team.save()
    return _saved(request, f"Team renamed to “{name}”.", redirect_to="game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_team_delete(request, code):
    """Delete a team, its seats, and (cascade) its bids and results."""
    game = get_object_or_404(Game, code=code)
    team = get_object_or_404(Team, game=game, pk=request.POST.get("team_id"), is_bot=False)
    name = team.name
    team.delete()
    return _saved(request, f"Team “{name}” deleted (its members, bids and results went with it).",
                  redirect_to="game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_member_edit(request, code):
    """Rename a player and/or move them to another team (autosaves)."""
    game = get_object_or_404(Game, code=code)
    member = get_object_or_404(TeamMember, game=game, pk=request.POST.get("member_id"))
    if "display_name" in request.POST:
        member.display_name = (request.POST.get("display_name") or "").strip()
    target_id = request.POST.get("team_id")
    if target_id and str(member.team_id) != str(target_id):
        target = get_object_or_404(Team, game=game, pk=target_id, is_bot=False)
        if not target.has_open_seat():
            return _saved(request, f"“{target.name}” is full ({game.max_team_size} seats).",
                          redirect_to="game:facilitator", code=code, ok=False)
        member.team = target
    member.save()
    label = member.display_name or "Player"
    return _saved(request, f"“{label}” saved.", redirect_to="game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_member_delete(request, code):
    """Remove a player's seat; they can rejoin from the student link."""
    game = get_object_or_404(Game, code=code)
    member = get_object_or_404(TeamMember, game=game, pk=request.POST.get("member_id"))
    label = member.display_name or "Player"
    member.delete()
    return _saved(request, f"“{label}” removed — they can rejoin from the student link.",
                  redirect_to="game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_bots(request, code):
    game = get_object_or_404(Game, code=code)
    try:
        count = max(0, int(request.POST.get("count", "0")))
        aggressiveness = float(request.POST.get("aggressiveness", "1.0"))
    except (TypeError, ValueError):
        return _saved(request, "Enter a whole number of bots and a numeric aggressiveness.",
                      redirect_to="game:facilitator", code=code, ok=False)
    services.configure_bots(game, count, aggressiveness)
    return _saved(request, f"Bots set to {count} at aggressiveness {aggressiveness}.",
                  redirect_to="game:facilitator", code=code)


@require_POST
@facilitator_required
def fac_reset(request, code):
    game = get_object_or_404(Game, code=code)
    services.reset_game(game)
    messages.success(request, "Game reset: progress cleared, teams and keywords kept.")
    return redirect("game:facilitator", code=code)


# ---------------------------------------------------------------------------
# Setup wizard — everything the instructor needs, no Django admin required.
# ---------------------------------------------------------------------------

def _saved(request, message, redirect_to=None, code=None, ok=True):
    """Autosave-aware response: JSON for fetch() calls, messages+redirect otherwise."""
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": ok, "message": message})
    (messages.success if ok else messages.error)(request, message)
    return redirect(redirect_to or "game:setup_game", code=code)


def _dec(raw, default):
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return default


def _int(raw, default):
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def _clean_code(raw, *, exclude_pk=None):
    """Validate a custom join code. Returns (code, error) — code is None if invalid/blank."""
    code = (raw or "").strip().upper()
    if not code:
        return None, None
    if not code.isalnum() or len(code) > 8:
        return None, "Code must be 1–8 letters/numbers (no spaces or symbols)."
    qs = Game.objects.filter(code=code)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        return None, f"Code {code} is already used by another game."
    return code, None


@facilitator_required
def setup_home(request):
    """List existing games and create a new one — the instructor's front door."""
    if request.method == "POST":
        custom_code, err = _clean_code(request.POST.get("code"))
        if err:
            messages.error(request, err)
            return redirect("game:setup_home")
        kwargs = dict(
            name=(request.POST.get("name") or "SEM Trading Floor").strip() or "SEM Trading Floor",
            starting_budget=_dec(request.POST.get("starting_budget"), Decimal("10000.00")),
            min_bid=max(Decimal("0.00"), _dec(request.POST.get("min_bid"), Decimal("0.00"))),
            max_team_size=_int(request.POST.get("max_team_size"), 4),
        )
        if custom_code:
            kwargs["code"] = custom_code
        game = Game.objects.create(**kwargs)
        messages.success(request, f"Game created — join code {game.code}. Now add keywords and build rounds.")
        return redirect("game:setup_game", code=game.code)
    return render(request, "game/setup_home.html", {
        "games": Game.objects.order_by("-created_at"),
    })


@facilitator_required
def setup_game(request, code):
    """One page to configure everything: settings, keywords, and the round schedule."""
    game = get_object_or_404(Game, code=code)
    join_url = request.build_absolute_uri(reverse("game:direct_join", args=[game.code]))
    played = game.rounds.exclude(status=Round.Status.PENDING).exists()
    return render(request, "game/setup.html", {
        "game": game,
        "keywords": game.keywords.all(),
        "rounds": game.rounds.prefetch_related("keywords").all(),
        "bots": game.teams.filter(is_bot=True),
        "join_url": join_url,
        "played": played,
        "asset_classes": keyword_io.ASSET_CLASS_DEFINITIONS,
        "human_teams": game.teams.filter(is_bot=False),
    })


@require_POST
@facilitator_required
def setup_settings(request, code):
    """Save the game's core settings."""
    game = get_object_or_404(Game, code=code)
    # Optional: change the join code (e.g. to MKT101). Blocked once anyone has
    # joined, since the student link contains the code.
    new_code, err = _clean_code(request.POST.get("code"), exclude_pk=game.pk)
    if err:
        messages.error(request, err)
        return redirect("game:setup_game", code=code)
    if new_code and new_code != game.code:
        if game.teams.filter(is_bot=False).exists():
            messages.error(request, "Can't change the code after students have joined — the link they use contains it.")
            return redirect("game:setup_game", code=code)
        game.code = new_code
    game.name = (request.POST.get("name") or game.name).strip() or game.name
    game.starting_budget = _dec(request.POST.get("starting_budget"), game.starting_budget)
    game.min_bid = _dec(request.POST.get("min_bid"), game.min_bid)
    game.ad_slots = _int(request.POST.get("ad_slots"), game.ad_slots)
    game.max_team_size = _int(request.POST.get("max_team_size"), game.max_team_size)
    if request.POST.get("play_mode") in dict(Game.PlayMode.choices):
        game.play_mode = request.POST["play_mode"]
    # Quality score settings (present on the same autosaving setup page).
    if request.POST.get("quality_mode") in dict(Game.QualityMode.choices):
        game.quality_mode = request.POST["quality_mode"]
    def _f(name, current, lo=0.0, hi=10.0):
        try:
            return min(hi, max(lo, float(request.POST[name])))
        except (KeyError, TypeError, ValueError):
            return current
    game.quality_uniform = _f("quality_uniform", game.quality_uniform)
    game.quality_min = _f("quality_min", game.quality_min)
    game.quality_max = _f("quality_max", game.quality_max)
    if game.quality_min > game.quality_max:
        game.quality_min, game.quality_max = game.quality_max, game.quality_min
    if "quality_apply_bots" in request.POST:
        game.quality_apply_bots = request.POST["quality_apply_bots"] in ("1", "true", "on")
    if "quality_show_players" in request.POST:
        game.quality_show_players = request.POST["quality_show_players"] in ("1", "true", "on")
    game.save()
    return _saved(request, "Settings saved.", code=game.code)


@require_POST
@facilitator_required
def setup_keyword_add(request, code):
    """Add one keyword from the inline form."""
    game = get_object_or_404(Game, code=code)
    label = (request.POST.get("label") or "").strip()
    if not label:
        messages.error(request, "Enter a keyword label.")
        return redirect("game:setup_game", code=code)
    if game.keywords.filter(label=label).exists():
        messages.error(request, f"“{label}” already exists in this game.")
        return redirect("game:setup_game", code=code)
    order = (game.keywords.order_by("-order").values_list("order", flat=True).first() or 0) + 1
    try:
        cvr = float(request.POST.get("conversion_rate") or 0.03)
    except ValueError:
        cvr = 0.03
    Keyword.objects.create(
        game=game, order=order, label=label,
        asset_class=(request.POST.get("asset_class") or "").strip(),
        search_volume=_int(request.POST.get("search_volume"), 5000),
        conversion_rate=cvr,
        order_value=_dec(request.POST.get("order_value"), Decimal("50.00")),
        reserve_price=_dec(request.POST.get("reserve_price"), Decimal("0.50")),
    )
    messages.success(request, f"Keyword “{label}” added.")
    return redirect("game:setup_game", code=code)


@require_POST
@facilitator_required
def setup_keyword_delete(request, code):
    game = get_object_or_404(Game, code=code)
    kw = get_object_or_404(Keyword, game=game, pk=request.POST.get("keyword_id"))
    if kw.rounds.exclude(status=Round.Status.PENDING).exists():
        messages.error(request, f"“{kw.label}” has been played — reset the game before removing it.")
    else:
        kw.delete()
        messages.success(request, f"Keyword “{kw.label}” removed.")
    return redirect("game:setup_game", code=code)


@require_POST
@facilitator_required
def setup_keyword_edit(request, code):
    """Inline edit of one keyword row on the setup page."""
    game = get_object_or_404(Game, code=code)
    kw = get_object_or_404(Keyword, game=game, pk=request.POST.get("keyword_id"))
    label = (request.POST.get("label") or kw.label).strip() or kw.label
    if label != kw.label and game.keywords.filter(label=label).exclude(pk=kw.pk).exists():
        return _saved(request, f"There's already a keyword called “{label}”.",
                      code=code, ok=False)
    kw.label = label
    kw.asset_class = (request.POST.get("asset_class") or "").strip()
    kw.search_volume = _int(request.POST.get("search_volume"), kw.search_volume)
    try:
        cvr = float(request.POST.get("conversion_rate"))
        kw.conversion_rate = cvr / 100 if cvr > 1 else cvr  # accept 3 or 0.03
    except (TypeError, ValueError):
        pass
    kw.order_value = _dec(request.POST.get("order_value"), kw.order_value)
    kw.reserve_price = _dec(request.POST.get("reserve_price"), kw.reserve_price)
    if "is_active" in request.POST:
        kw.is_active = request.POST["is_active"] in ("1", "true", "on")
    kw.save()
    return _saved(request, f"“{kw.label}” saved.", code=code)


@require_POST
@facilitator_required
def setup_team_quality(request, code):
    """Manual quality mode: save one team's min/max quality bounds (autosaves)."""
    game = get_object_or_404(Game, code=code)
    team = get_object_or_404(Team, game=game, pk=request.POST.get("team_id"), is_bot=False)
    try:
        lo = min(10.0, max(0.0, float(request.POST.get("quality_min"))))
        hi = min(10.0, max(0.0, float(request.POST.get("quality_max"))))
    except (TypeError, ValueError):
        return _saved(request, "Enter numbers between 0 and 10.", code=code, ok=False)
    team.quality_min, team.quality_max = min(lo, hi), max(lo, hi)
    team.save()
    return _saved(request, f"“{team.name}” quality range saved.", code=code)


@require_POST
@facilitator_required
def setup_keywords_import(request, code):
    """Upload a CSV/XLSX — native format or a Google Keyword Planner export."""
    game = get_object_or_404(Game, code=code)
    if game.rounds.exclude(status=Round.Status.PENDING).exists():
        messages.error(request, "Rounds have been played — reset the game before importing keywords.")
        return redirect("game:setup_game", code=code)
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "Choose a .csv or .xlsx file first.")
        return redirect("game:setup_game", code=code)
    try:
        parsed = keyword_io.parse_keyword_upload(upload.name, upload.read())
    except keyword_io.KeywordImportError as e:
        messages.error(request, str(e))
        return redirect("game:setup_game", code=code)
    replace = request.POST.get("mode") == "replace"
    created, updated = keyword_io.import_keywords(game, parsed, replace=replace)
    bits = []
    if created:
        bits.append(f"{created} added")
    if updated:
        bits.append(f"{updated} updated")
    messages.success(request, f"Import complete: {', '.join(bits) or 'nothing changed'}."
                     + (" Existing keywords were replaced." if replace else ""))
    return redirect("game:setup_game", code=code)


@facilitator_required
def fac_results_export(request, code):
    """Download all resolved results as CSV (facilitator only — includes bids)."""
    import csv as _csv
    import io as _io
    game = get_object_or_404(Game, code=code)
    out = _io.StringIO()
    w = _csv.writer(out)
    w.writerow(["Round", "Keyword", "Team", "Bot", "Bid amount", "Quality score",
                "Ad rank", "Next highest bid",
                "Position", "CPC", "Impressions", "Clicks", "Spend", "Conversions",
                "Revenue", "Profit", "ROAS"])
    results = (RoundResult.objects.filter(round__game=game)
               .select_related("round", "team", "keyword")
               .order_by("round__number", "keyword__order", "position"))
    which = request.GET.get("rounds", "all")
    if which != "all":
        try:
            results = results.filter(round__number=int(which))
        except (TypeError, ValueError):
            pass
    for r in results:
        quality = round(r.ad_rank / float(r.bid_amount), 1) if r.bid_amount else ""
        if r.position is not None:
            pos = r.position
        elif r.bid_amount and r.bid_amount < r.keyword.reserve_price:
            pos = "below floor"
        else:
            pos = "not shown"
        w.writerow([r.round.number, r.keyword.label, r.team.name,
                    "yes" if r.team.is_bot else "no",
                    r.bid_amount, quality, r.ad_rank,
                    r.next_highest_bid if r.next_highest_bid is not None else "",
                    pos,
                    r.actual_cpc, r.impressions if r.position else 0, r.clicks,
                    r.spend, r.conversions, r.revenue, r.profit,
                    r.roas if r.roas is not None else ""])
    resp = HttpResponse(out.getvalue(), content_type="text/csv")
    suffix = "all_rounds" if which == "all" else f"round_{which}"
    resp["Content-Disposition"] = f'attachment; filename="{game.code}_results_{suffix}.csv"'
    return resp


@facilitator_required
def setup_keywords_export(request, code):
    """Download all keywords as the native CSV (edit and re-upload)."""
    game = get_object_or_404(Game, code=code)
    csv_text = keyword_io.export_keywords_csv(game)
    resp = HttpResponse(csv_text, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{game.code}_keywords.csv"'
    return resp


@require_POST
@facilitator_required
def setup_starter_pack(request, code):
    """Load the built-in starter keywords (skips duplicates)."""
    game = get_object_or_404(Game, code=code)
    n = services.load_starter_pack(game)
    if n:
        messages.success(request, f"Starter pack loaded: {n} keyword{'s' if n != 1 else ''} added. Tweak any values below.")
    else:
        messages.info(request, "All starter keywords are already in this game.")
    return redirect("game:setup_game", code=code)


@require_POST
@facilitator_required
def setup_keywords_clear(request, code):
    """Blank slate: remove every keyword (only while nothing has been played)."""
    game = get_object_or_404(Game, code=code)
    if game.rounds.exclude(status=Round.Status.PENDING).exists():
        messages.error(request, "Rounds have been played — reset the game before clearing keywords.")
    else:
        game.rounds.all().delete()
        n = game.keywords.count()
        game.keywords.all().delete()
        messages.success(request, f"Cleared {n} keyword{'s' if n != 1 else ''}.")
    return redirect("game:setup_game", code=code)


@require_POST
@facilitator_required
def setup_build_rounds(request, code):
    """Split the keywords across N rounds, evenly, in keyword order."""
    game = get_object_or_404(Game, code=code)
    nr_raw = (request.POST.get("num_rounds") or "").strip()
    num_rounds = _int(nr_raw, game.num_rounds) if nr_raw else None
    kpr_raw = (request.POST.get("keywords_per_round") or "").strip()
    keywords_per_round = _int(kpr_raw, 0) if kpr_raw else None
    if num_rounds is None and keywords_per_round is None:
        num_rounds = game.num_rounds
    randomize = request.POST.get("randomize_keywords") in ("1", "true", "on")
    rounds = services.build_rounds(game, num_rounds, keywords_per_round=keywords_per_round,
                                   randomize=randomize)
    if rounds is None:
        if game.rounds.exclude(status=Round.Status.PENDING).exists():
            messages.error(request, "Rounds have been played — reset the game before rebuilding the schedule.")
        else:
            messages.error(request, "Add at least one keyword first.")
    else:
        messages.success(request, f"Schedule built: {len(rounds)} round{'s' if len(rounds) != 1 else ''} covering {game.keywords.count()} keywords.")
    return redirect("game:setup_game", code=code)
