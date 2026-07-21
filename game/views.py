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
from .models import Bid, Game, Keyword, Round, Team, TeamMember
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
        keyword_bids = [
            {"keyword": k, "bid": bid_by_kw.get(k.id)}
            for k in current.keywords.all()
        ]
    return render(request, "game/console.html", {
        "game": game,
        "team": team,
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
        current_results = current.results.select_related("team", "keyword").all()
    event_choices = [(k, v["title"]) for k, v in services.EVENT_DECK.items()]
    return render(request, "game/facilitator.html", {
        "game": game,
        "teams": teams,
        "bots": game.teams.filter(is_bot=True),
        "current_round": current,
        "current_results": current_results,
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


@require_POST
@facilitator_required
def fac_bots(request, code):
    game = get_object_or_404(Game, code=code)
    try:
        count = max(0, int(request.POST.get("count", "0")))
        aggressiveness = float(request.POST.get("aggressiveness", "1.0"))
    except (TypeError, ValueError):
        messages.error(request, "Enter a whole number of bots and a numeric aggressiveness.")
        return redirect("game:facilitator", code=code)
    services.configure_bots(game, count, aggressiveness)
    messages.success(request, f"Bots set to {count} at aggressiveness {aggressiveness}.")
    return redirect("game:facilitator", code=code)


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
    game.save()
    messages.success(request, "Settings saved.")
    return redirect("game:setup_game", code=game.code)


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
        messages.error(request, f"There's already a keyword called “{label}”.")
        return redirect("game:setup_game", code=code)
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
    kw.save()
    messages.success(request, f"“{kw.label}” updated.")
    return redirect("game:setup_game", code=code)


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
    num_rounds = _int(request.POST.get("num_rounds"), game.num_rounds)
    rounds = services.build_rounds(game, num_rounds)
    if rounds is None:
        if game.rounds.exclude(status=Round.Status.PENDING).exists():
            messages.error(request, "Rounds have been played — reset the game before rebuilding the schedule.")
        else:
            messages.error(request, "Add at least one keyword first.")
    else:
        messages.success(request, f"Schedule built: {len(rounds)} round{'s' if len(rounds) != 1 else ''} covering {game.keywords.count()} keywords.")
    return redirect("game:setup_game", code=code)
