# Deploying SEM Trading Floor

The app is a standard Django + PostgreSQL project. Any host that runs Django works;
below are the two easiest paths plus a container option.

## Before you deploy
- Set `DEBUG=False` and a strong `SECRET_KEY` in the environment.
- Set `ALLOWED_HOSTS` to your domain (e.g. `.onrender.com` or `yourschool.edu`).
- After the first deploy, set `CSRF_TRUSTED_ORIGINS=https://<your-domain>` so the
  facilitator's POST actions work over HTTPS.
- Static files are served by WhiteNoise; the build step runs `collectstatic`.

## Option A — Render (one click)
1. Push this repo to GitHub.
2. In Render: **New > Blueprint**, select the repo. `render.yaml` provisions a free
   Postgres database and a web service, wires `DATABASE_URL`, and generates `SECRET_KEY`.
3. After the first deploy, add `CSRF_TRUSTED_ORIGINS=https://<your-service>.onrender.com`
   to the service's environment and redeploy.
4. Create your facilitator login: open the service's **Shell** tab and run
   `python manage.py createsuperuser` (and optionally `python manage.py seed_demo`).
   Note: Render's free tier doesn't include the Shell tab — if you're on free, run
   `DATABASE_URL="<external connection string from the Render DB page>" python manage.py createsuperuser`
   from your own machine instead; it connects to the same database.

Migrations run automatically at startup (`startCommand` in `render.yaml`), because
the free tier doesn't support `preDeployCommand`. On a paid plan you can move
`python manage.py migrate --noinput` into `preDeployCommand` for cleaner deploys.

## Option B — Railway / Heroku-style
- The `Procfile` defines `release: migrate` and `web: gunicorn semfloor.wsgi`.
- Provision a PostgreSQL add-on (it sets `DATABASE_URL`), then set `SECRET_KEY`,
  `DEBUG=False`, `ALLOWED_HOSTS`, and `CSRF_TRUSTED_ORIGINS`.

## Option C — Docker
```bash
docker build -t semfloor .
docker run -p 8000:8000 --env-file .env semfloor
```

## Create the facilitator account
Facilitator pages require a **staff** login. Create one once:
```bash
python manage.py createsuperuser
```
The instructor signs in at `/admin/login/`, then opens `/g/<CODE>/facilitator/`.
Students never need an account — they join with the game code.

## First-run checklist on the server
```bash
python manage.py migrate
python manage.py seed_demo        # optional demo game (code DEMO)
python manage.py createsuperuser  # the facilitator
```
