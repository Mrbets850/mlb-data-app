# Railway Migration Plan

This plan is for moving THE MLB EDGE from Streamlit hosting to Railway safely.
It does not switch the live domain and it does not delete or redesign the
landing page.

## A. Audit summary

### Main app entry file

- Main app: `app.py`
- App type: Streamlit
- Main Railway start command:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false
```

### Related app modules

- `live_hr_tracker.py` - live home run tracker page/module.
- `mrbets850_hr_picks.py` - MRBETS850 homerun picks page/module.
- `rbi_model.py` - RBI Edge Model page/module.
- `pwa.py` - injects mobile/PWA tags into the Streamlit app.
- `services/` - shared service code for lineups, live game state, player
  detail, slate rollover, and pitcher weak spots.

### Dependency files found

- `requirements.txt` - Python dependencies used by the app.
- `.streamlit/config.toml` - Streamlit theme configuration.
- `.devcontainer/devcontainer.json` - development container setup and local
  Streamlit start command.

### Landing page and branding files to preserve

Do not delete these files:

- `static/index.html`
- `static/manifest.json`
- `static/service-worker.js`
- `pwa.py`
- `PWA.md`
- `CUSTOMERS.md`

Notes:

- `static/index.html` currently embeds the old Streamlit URL:
  `https://mrbets850.streamlit.app/`
- `static/manifest.json` also points at `https://mrbets850.streamlit.app/`.
- Those URLs should not be changed until the Railway test URL is confirmed.
- The repo references `assets/mlb_edge_logo.jpeg`, `assets/mrbets850_logo.jpg`,
  and `static/icons/...`, but those files are not present in this checkout.
  The app handles missing logo files gracefully, but PWA install icons may need
  to be restored from the current live source or another repo if they are used
  in production.

### Environment variables likely needed

No environment variable is required for the app to boot on Railway.

Optional variables for full feature parity:

- `ODDS_API_KEY` - recommended canonical key for The Odds API.
- `THE_ODDS_API_KEY` - supported alias.
- `THE_ODDS_API` - supported alias.
- `ODDSAPI_KEY` - supported alias.
- `odds_api_key` - supported lowercase legacy key.
- `SPORTRADAR_MLB_API_KEY` - optional premium lineup provider.
- `SPORTSDATAIO_MLB_API_KEY` - optional premium lineup provider.
- `MRBETS850_ADMIN_PIN` - optional admin PIN for the HR picks editor.
- `MLB_EDGE_ADMIN_PIN` - optional alternate admin PIN name.
- `MRBETS850_GITHUB_TOKEN` - optional GitHub token for durable HR picks sync.
- `GITHUB_TOKEN` - optional alternate token name used by the app.
- `MRBETS850_PICKS_REPO` - optional repo for HR picks sync.
- `MRBETS850_PICKS_PATH` - optional file path for HR picks sync.
- `MRBETS850_PICKS_BRANCH` - optional branch for HR picks sync.
- `MRBETS850_TODAY_OVERRIDE` - development/test override only. Do not set this
  in production.

Railway automatically provides `PORT`. Do not add `PORT` manually unless
Railway support specifically tells you to.

### Background jobs, scripts, and pipelines

- `.github/workflows/refresh-data.yml` runs on GitHub Actions several times per
  day and refreshes Baseball Savant CSV files.
- `scripts/refresh_savant.py` is the data refresh script used by GitHub
  Actions.
- `scripts/smoke_rbi_model.py` and `scripts/test_live_hr_tracker.py` are
  developer/test scripts.

Recommendation: keep the Savant refresh job on GitHub Actions for now. Do not
move it to Railway during the first migration.

## B. Recommended migration structure

Use one Railway project with one service:

```text
Railway project: mlb-edge
Service: streamlit-app
Source: this GitHub repo
Branch: cursor/railway-migration-66af for testing, then main later after merge
Start command: streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false
```

Keep these outside Railway for now:

- GitHub Actions data refresh workflow.
- Existing GitHub repos.
- Existing landing/PWA files.
- Custom domain `themlbedge.com` until the Railway deployment URL works.

Do not create multiple Railway services for the first migration. The repo runs
as a single Streamlit web app and there is no separate worker, Redis, database,
or cron process required for the initial move.

## C. File change plan

Changes made for Railway readiness:

- Add `streamlit` to `requirements.txt` because Railway installs from
  `requirements.txt`.
- Add `railway.json` with the Railway start command and Streamlit health check.
- Update `rbi_model.py` so its Odds API key can come from Railway environment
  variables, not only Streamlit secrets.
- Add this migration documentation:
  - `MIGRATION_PLAN.md`
  - `DEPLOY_TO_RAILWAY.md`
  - `ENV_TEMPLATE.txt`
  - `DOMAIN_SETUP.md`

## What will not change

- No landing page files are deleted.
- No static/PWA URLs are changed before the Railway URL is tested.
- No custom domain changes are made in code.
- No Stripe work is added.
- No database, worker, or new service is introduced.
- No secrets are added to the repo.
- Existing GitHub repos are not deleted or replaced.

## Risks and safe handling

### Risk: live domain downtime

Do not change DNS until the Railway deployment URL is working.

### Risk: landing page or PWA points to the old app

This is intentional during testing. After Railway works, update the landing/PWA
URL in a separate small change or in the separate GitHub Pages repo if that is
where the live landing page is hosted.

### Risk: HR picks persistence

Railway file storage is normally ephemeral. If the HR picks editor is used in
production, use the existing GitHub sync variables:

- `MRBETS850_GITHUB_TOKEN`
- `MRBETS850_PICKS_REPO`
- `MRBETS850_PICKS_PATH`
- `MRBETS850_PICKS_BRANCH`

Do not rely on local JSON writes alone unless a Railway volume is added later.

### Risk: missing icon/logo assets

This checkout does not contain `assets/` or `static/icons/`. If the current
live landing page uses those files, restore them before making Railway the
primary production host.

## Recommended GitHub branch strategy

1. Test Railway from this branch: `cursor/railway-migration-66af`.
2. Do not change `main` until the Railway preview works.
3. After testing, merge the branch into `main`.
4. Only after the Railway app works from the production branch should DNS be
   changed for `themlbedge.com`.
