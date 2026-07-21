# SEM Trading Floor — a keyword-auction simulation (Django)

An online, round-based classroom game that turns Google-style keyword bidding into a
"play-money trading floor": teams allocate a budget across keywords, a generalized
second-price auction sets their rank and cost, a conversion funnel turns clicks into
revenue, and a leaderboard ranks teams by profit / ROAS.

## What's new in this release (multi-keyword rounds + easy setup)

- **Multiple keywords per round.** Each round now carries a *set* of keywords. Teams
  submit one bid per keyword on a single form; the auction resolves each keyword in
  order, drawing down the team's round budget sequentially. The big board, console,
  recap and `state.json` all show per-keyword results plus round totals.
- **Fresh budget every round.** When a round opens, every team's budget is topped back
  up to the game's per-round budget (`Game.starting_budget`). Cumulative profit/ROAS
  still accumulate across rounds for the leaderboard.
- **One-click student link.** Paste `https://<your-host>/g/<CODE>/` on your course
  page. Students click it, type a display name, and pick a team. No accounts, no SSO,
  no email — a browser session is their seat.
- **Facilitator setup wizard at `/setup/`** — no Django admin needed. Create a game,
  edit settings (budget, rounds, slots, team size), load a **starter keyword pack**
  (with one-click clear), add or delete keywords, and build the round schedule
  (keywords are split evenly across rounds). Everything locks once play starts, and
  the wizard shows the copy-paste student link.

> **Migration note:** upgrading an existing database runs
> `game/migrations/0003_multi_keyword_rounds.py`, which clears existing rounds, bids
> and round results (schema changed from one keyword per round to many). Games, teams
> and keywords are preserved. Rebuild the schedule from `/setup/`.

## End-of-game recap (P&L history)
A recap surface at `/g/<CODE>/recap/` shows a per-team cumulative **P&L curve** across
the revealed rounds, the final standings, and a round-by-round profit matrix. It fills in
live as each round is revealed, so it doubles as a closing slide. Each team console also
gets a **"Your round-by-round"** table. Links to the recap are on the big board and the
facilitator dashboard.

## What's in the ship pass
- **Facilitator auth**: the dashboard and all round-control endpoints now require a
  staff login (the instructor, via `/admin/login/`). Students still just use a join code.
- **Production static files** via WhiteNoise (`collectstatic` at build; compressed,
  hashed assets served by the app).
- **Security hardening** when `DEBUG=False`: HTTPS-aware proxy header, secure cookies,
  `CSRF_TRUSTED_ORIGINS` from the environment.
- **Deploy config**: `Procfile`, `render.yaml` (one-click Render blueprint),
  `Dockerfile`, and `DEPLOY.md` with step-by-step notes.
- **Test suite**: `python manage.py test` runs 19 tests (engine math + join flow +
  team-size cap + full round lifecycle + events + reset + facilitator auth).
- Housekeeping: `.gitignore`, `.dockerignore`, expanded `.env.example`, Postgres
  healthcheck in `docker-compose.yml`.

## What's new in Phase 3
- The full round lifecycle, driven from the facilitator dashboard:
  **open -> close -> resolve -> reveal**.
- **Bots** join the auction automatically when a round opens, bidding around each
  keyword's fair value (conversion rate x order value) scaled by an adjustable
  aggressiveness. Facilitators can change the bot count and aggressiveness live.
- A **market-event deck** (surge, slump, high-intent, tire-kickers, price war) the
  facilitator can fire onto the current round; effects apply when the round resolves.
- Resolving runs the rules engine for every bid, writes per-team results, and updates
  the leaderboard (budget, spend, revenue, profit). Results appear on the big board and
  each console once the round is **revealed**. A **reset** clears progress but keeps
  teams and keywords.
- All of this lives in `game/services.py` (orchestration) so the live-ticker sprint can
  call the same functions from a WebSocket consumer.

## What's new in Phase 2
- Join flow: landing page (game code + name) then pick or create a team.
- Editable **team size**: `Game.max_team_size` caps members per team; teams of one are
  allowed. Enforced at join via the new `TeamMember` model (one seat per browser/game).
- The three surfaces: **big board**, **team console** (with a bid form), and a
  **facilitator dashboard** (read-only in Phase 2; round-control buttons come in Phase 3).
- The `state.json` polling endpoint plus a tiny front-end poller that keeps the board
  and consoles fresh. Swapping this for WebSockets later touches only the delivery layer.

## What's in Phase 1 (foundation, still here)
- Django project scaffold configured for **PostgreSQL** (via `DATABASE_URL`).
- Data models: `Game`, `Keyword`, `Round`, `Team`, `Bid`, `RoundResult`, `Event`.
- Django **admin** for facilitator CRUD and editing keyword "fundamentals".
- A **standalone, Django-agnostic rules engine** (`game/engine/`) implementing the
  auction and the conversion funnel, with its own unit tests.
- A `state` service (the seam the polling endpoint and, later, WebSockets will use).
- A `seed_demo` management command that creates a demo game with the five keywords
  modeled as "asset classes".

The only remaining (optional) work is the **live-ticker sprint**: swap polling for
WebSockets via Django Channels, reusing `game/state.py` and `game/services.py`
unchanged. The seams for it are already in place (`asgi.py`, the state snapshot, and
the polling endpoint).

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # then edit if needed
docker compose up -d                 # starts PostgreSQL
python manage.py migrate
python manage.py seed_demo           # creates a demo game + keywords + bots
python manage.py createsuperuser     # this is the FACILITATOR login (staff)
python manage.py runserver
# then open http://127.0.0.1:8000/ (join screen) and
# http://127.0.0.1:8000/g/DEMO/facilitator/ (facilitator dashboard — staff login required)
```

## Run the tests
```bash
python -m unittest discover -s game/engine/tests -t .   # engine only, no database
python manage.py test                                    # full suite (19 tests)
```

## Deploying
See `DEPLOY.md` for Render (one-click blueprint), Railway/Heroku, and Docker, plus how
to create the facilitator account and set `CSRF_TRUSTED_ORIGINS`.
