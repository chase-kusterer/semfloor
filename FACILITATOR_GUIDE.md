# SEM Trading Floor — Facilitator Guide

This guide covers getting the app running, setting up a game, and running a live
session with your class. It assumes the code from this repository.

There are three screens:

- **Big board** — the shared, projected view (this round's keywords + live leaderboard + per-keyword results).
- **Team console** — one bid box per keyword in the round; teams see per-keyword results and their P&L.
- **Facilitator dashboard** — your control panel for the rounds. Requires a staff login.

---

## The 5-minute path (after the app is deployed)

1. Log in once at `/admin/login/` with your facilitator (superuser) account.
2. Go to **`/setup/`** and click **New game** (or open the demo game).
3. On the setup page:
   - **Settings** — per-round budget, minimum bid, ad slots, number of rounds, team size.
   - **Keywords** — click **Load starter pack** for 8 ready-made keywords, tweak or
     delete any, add your own, or **Clear all** and start from scratch.
   - **Build rounds** — one click splits the keywords evenly across your rounds.
4. Copy the **student link** shown on the setup page (`/g/<CODE>/`) and paste it on
   your course page. Students click it, enter a name, and join a team — nothing else.
5. Open the **facilitator dashboard** and run the session: open → close → resolve →
   reveal, firing market events as you like.

Budgets are **fresh each round**: opening a round tops every team back up to the
per-round budget, and that budget is **split evenly across the round's keywords** —
overspending demand on one keyword can never starve a team's other keywords of
clicks. Profit and ROAS accumulate across rounds on the leaderboard.

The facilitator dashboard's results table is built for teaching the **generalized
second-price auction**: it shows each team's own bid, the *next highest bid* (the
ad ranked just below, which sets the price), the actual CPC, and impressions.
A **Download results CSV** button exports every round's results — bids included —
for debriefs or grading. Two pricing rules to point out to students: bids below a
keyword's reserve price are never shown, and no shown ad ever pays less than the
reserve or more than its own bid.

---

## 1. One-time setup

You only do this once per environment.

### Option A — Run it online (recommended for an online class)
Follow `DEPLOY.md` (Render gives you a one-click blueprint). When it's live you'll have a
public URL like `https://your-app.onrender.com`. Then create your facilitator login and,
optionally, the demo game:

```bash
python manage.py createsuperuser     # this is YOUR facilitator login
python manage.py seed_demo           # optional: creates a demo game (code DEMO)
```

On a host like Render you run those in the service's shell/console.

### Option B — Run it on your own machine (good for testing)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d                 # starts PostgreSQL
python manage.py migrate
python manage.py seed_demo           # optional demo game (code DEMO)
python manage.py createsuperuser     # your facilitator login
python manage.py runserver
```

The app is then at `http://127.0.0.1:8000/`. Note: if students are on other devices,
they need network access to your machine, so for a real online class use Option A.

---

## 2. The URLs you'll use

Replace `<CODE>` with your game's join code (the demo game's code is `DEMO`).

- Facilitator dashboard: `/g/<CODE>/facilitator/`  (log in first at `/admin/login/`)
- Big board (project this): `/g/<CODE>/board/`
- Student join screen: `/`  (they type the code + a name)
- Admin (edit settings/keywords): `/admin/`

Log in once at `/admin/login/` with your superuser; the facilitator pages will then open.

---

## 3. Set up a game

You can use the seeded `DEMO` game as-is, or tailor it.

1. Go to `/admin/` and open **Games**. Use the demo game or click **Add game**.
2. Set the game options:
   - **Max team size** — members allowed per team (set to 1 to force solo play).
   - **Num rounds**, **Starting budget**, **Ad slots** (ad positions per keyword),
     **Min bid**.
3. Open **Keywords** (or edit them inline on the game). Each keyword has hidden
   "fundamentals" that make it behave like an asset:
   - **Search volume** (impressions available), **CTR curve** (clicks by position),
     **Conversion rate**, **Order value** (revenue per conversion), **Reserve price**
     (floor CPC), and **Asset class** (flavor text students can see).
   - The demo's five keywords are pre-tuned as a branded blue chip, a crowded momentum
     keyword, a high-volume volatile one, a mid-cap, and a speculative one. Adjust to taste.
4. Set the bots on the facilitator dashboard (see below) — how many and how aggressive.

Tip: keep the fundamentals to yourself. The learning comes from students inferring them
from results, the way a trader reads a stock.

---

## 4. Run a live session

Project the **big board** and share the join link + code with students.

**Students join:** they open `/`, enter the code and a name, then either create a team or
join an existing one (a team that's full at your max size can't be joined).

Then run each round from the **facilitator dashboard**. The controls light up in order;
you can only do the next valid step:

1. **Open next round.** This reveals the keyword on the board and the bots place their
   bids automatically.
2. **Students bid** on their consoles: a max bid per click (and an optional ad headline).
   Any teammate can submit; the latest bid wins. Give them a fixed window (2–3 minutes
   works well).
3. **(Optional) Fire a market event** before resolving — surge, slump, high-intent,
   tire-kickers, or price war. Its effect applies when you resolve.
4. **Close bids.** This locks every team's bid.
5. **Resolve.** The auction and funnel run: each team gets a rank and cost, clicks turn
   into conversions and revenue, and the leaderboard updates. You'll see the round's
   results table on the dashboard.
6. **Reveal on board.** Results appear on the big board and on each team's console
   (each team also sees its own position and profit).
7. **Debrief**, then click **Open next round** for the next keyword.

Project the **recap** page (`/g/<CODE>/recap/`, or the link on your dashboard) as your closing view: it shows each team's cumulative profit-and-loss curve across the rounds, the final standings, and a profit-by-round grid, and it fills in live as you reveal each round. After the final round the leaderboard is your finish line. To play again with the same
class, use **Reset game** (clears progress and budgets, keeps teams and keywords).

### A note on Quality Score
Rank is Bid × Quality, and cost is set by the bidder below you, so quality — not just
budget — decides who wins cheaply. In this build, bots come with varied quality and
human bids default to a mid score (5). If you want to reward good ad copy and landing
choices, set each team's **Quality score** on their **Bid** in `/admin/` before you
resolve. (A built-in scoring screen is a natural future enhancement.)

---

## 5. Managing bots

On the dashboard, the **Bots** panel sets how many bots play and their aggressiveness
(a multiplier on how hard they bid relative to a keyword's fair value). Bots give a small
class real competition. Around 3–5 bots at aggressiveness ~1.0 is a good starting point;
raise aggressiveness to make winning slots more expensive.

---

## 6. Talking points for the debrief

The game is built to teach a few lessons; watch for them in the results:

- **Winning the auction is not winning.** A team can rank #1 and still lose money by
  overpaying on a low-converting keyword. Point to negative-profit rows.
- **Quality beats budget.** A relevant, high-quality ad ranks well and pays less per click.
- **Diversify and budget.** Spreading bids across keywords and pacing spend beats dumping
  everything into one "hot" term.
- **Vanity keywords are traps.** High-volume, low-intent keywords (the volatile "large
  cap") burn budget fast.
- **The market moves.** Events and competitors change the right answer round to round.

---

## 7. Troubleshooting

- **"There is no open round to bid on."** Open a round from the dashboard first.
- **A student can't submit a bid.** Bidding only works while the round status is *Open*
  (before you close it).
- **The board isn't updating.** It refreshes every couple of seconds on its own; if a
  device looks stuck, reload the page.
- **"Team is full."** Raise **Max team size** on the game in `/admin/`.
- **Facilitator page sends me to a login.** That's expected — log in with your superuser
  at `/admin/login/`.
- **I want to start over.** Use **Reset game** on the dashboard (keeps teams/keywords) or,
  for a clean slate, `python manage.py seed_demo --reset`.
- **Results look off.** Double-check the keyword's fundamentals in `/admin/`; extreme
  values (e.g. a tiny conversion rate) produce large losses by design.

---

## Quick reference

| Action | Where |
| --- | --- |
| Log in as facilitator | `/admin/login/` |
| Run the rounds | `/g/<CODE>/facilitator/` |
| Project the game | `/g/<CODE>/board/` |
| Show the recap / P&L history | `/g/<CODE>/recap/` |
| Students join | `/` + game code |
| Edit game / keywords / bids | `/admin/` |
| Reset progress | Dashboard → Reset game |
