# Railway Migration Plan

This plan keeps the current app and landing/PWA files safe while preparing a
testable Railway deployment. No domain or DNS change is part of this branch.

## A. Audit summary

### Main app entry file

- `app.py` is the Streamlit app entry point.
- The Railway start command should be:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

Railway provides the `PORT` environment variable. Binding to `0.0.0.0` lets
Railway route public traffic to the app.

### Is this a Streamlit app?

Yes. `app.py`, `rbi_model.py`, `live_hr_tracker.py`, `pwa.py`, and
`mrbets850_hr_picks.py` import Streamlit and render the user interface.

### Dependency files

- `requirements.txt` is the Python dependency file Railway will install from.
- Streamlit is now listed explicitly in `requirements.txt` so Railway can run
  the app without relying on Streamlit Cloud's preinstalled runtime.
- There is no Dockerfile, Procfile, Pipfile, Poetry file, or package lock file.

### Landing page and branding files to preserve

Do not delete or rename these files:

- `static/index.html` - branded PWA/landing shell that embeds the dashboard.
- `static/manifest.json` - PWA manifest.
- `static/service-worker.js` - PWA service worker for static hosting.
- `static/icons/` - app icons used by the manifest and landing shell.
- `assets/mlb_edge_logo.jpeg` - MLB Edge logo used by the Streamlit app.
- `assets/mrbets850_logo.jpg` - MrBets850 logo.
- `assets/homerun_power_combo.jpeg` - app image asset.
- `PWA.md` and `CUSTOMERS.md` - customer-facing install/PWA instructions.

The current Streamlit app also injects PWA metadata from `pwa.py`.

### Environment variables likely needed

The app can run without secrets because it falls back to public MLB data for
core behavior. These variables are optional and should only be set if you
already use those features:

- `ODDS_API_KEY` - The Odds API key for sportsbook odds and HR lines.
- `MRBETS850_ADMIN_PIN` - admin PIN for the curated HR picks editor.
- `MLB_EDGE_ADMIN_PIN` - alternate admin PIN name supported by the app.
- `MRBETS850_GITHUB_TOKEN` - optional GitHub token for saving HR picks back to
  GitHub through the app.
- `GITHUB_TOKEN` - alternate token name supported by the HR picks module.
- `MRBETS850_PICKS_REPO` - optional override for the HR picks repo.
- `MRBETS850_PICKS_PATH` - optional override for the HR picks JSON path.
- `MRBETS850_PICKS_BRANCH` - optional override for the HR picks branch.
- `SPORTRADAR_MLB_API_KEY` - optional premium lineup provider.
- `SPORTSDATAIO_MLB_API_KEY` - optional premium lineup provider.
- `MRBETS850_TODAY_OVERRIDE` - testing-only date override; do not set in
  production unless intentionally testing.

Important: do not invent new secret values. Copy only values you already have.

### Background jobs, scripts, cron-like tasks, and data pipelines

- `.github/workflows/refresh-data.yml` is a GitHub Actions scheduled workflow
  that refreshes Baseball Savant CSV files several times daily and commits
  changes back to `main`.
- `scripts/refresh_savant.py` is the script run by that workflow.
- The Streamlit app reads several Savant CSV files from the GitHub raw URL for
  `Mrbets850/mlb-data-app` on branch `main`.
- There is no always-running worker process in this repo.
- No separate Railway cron service is required for the minimum migration. Keep
  the GitHub Action in place for now.

### Related GitHub repos

This repo is `Mrbets850/mlb-data-app`.

The docs and app reference a related public PWA/landing repo:

- `Mrbets850/mlb-edge-pwa`
- It appears to contain `index.html`, `manifest.json`, `service-worker.js`,
  `favicon.png`, and `icons/`.

For the safest first migration, do not move or delete the PWA repo. Keep it on
GitHub Pages until the Railway deployment is tested. After Railway is verified,
the PWA repo can be updated separately to point its iframe/manifest to the new
Railway URL or to `https://themlbedge.com` after DNS is switched.

### Is this repo alone enough for Railway?

Yes, this repo alone is enough to deploy the Streamlit dashboard to Railway.

The separate PWA repo is not required to make the dashboard run on Railway. It
is related to the installable landing/PWA experience and should be preserved.

## B. Recommended migration structure

Use one Railway project with one service first:

- Railway project: `the-mlb-edge`
- Railway service: `mlb-data-app`
- GitHub repo: `Mrbets850/mlb-data-app`
- Branch to deploy for testing: `cursor/railway-migration-8d8c`
- Runtime: Python with Nixpacks
- Start command:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

Keep these outside Railway for now:

- GitHub Actions data refresh workflow.
- GitHub Pages PWA/landing repo `Mrbets850/mlb-edge-pwa`.
- The custom domain `themlbedge.com`.

This gives the smallest successful migration and keeps the current live site
and customer install page available while Railway is tested.

## C. File change plan

### What this branch changes

- Add Streamlit to `requirements.txt`.
- Add `railway.json` with the Railway start command.
- Add deployment and domain handoff docs:
  - `MIGRATION_PLAN.md`
  - `DEPLOY_TO_RAILWAY.md`
  - `ENV_TEMPLATE.txt`
  - `DOMAIN_SETUP.md`

### What this branch does not change

- Does not delete or move landing page files.
- Does not remove PWA assets.
- Does not redesign the app.
- Does not change `static/index.html` or the separate PWA repo.
- Does not change `themlbedge.com` DNS.
- Does not add Stripe.
- Does not add or expose secrets.
- Does not disable the Streamlit-hosted app.
- Does not disable the GitHub Actions data refresh workflow.

## D. Risks and mitigations

### Risk: Optional secrets are missing in Railway

Mitigation: core app behavior should still run using public MLB data. Optional
features such as odds, premium lineup providers, and admin editing need their
existing keys/PINs copied to Railway.

### Risk: HR picks edits may not persist across Railway restarts without GitHub persistence

The app has a local JSON fallback, but Railway service storage can be
ephemeral. If you use the in-app HR picks editor and want edits to survive
redeploys reliably, set `MRBETS850_GITHUB_TOKEN` and related optional settings.

### Risk: PWA landing shell still points to the old Streamlit URL

The current PWA shell and docs reference `https://mrbets850.streamlit.app/`.
For the safest first test, leave that alone. After the Railway app works,
update the PWA repo in a separate change to point to the Railway/custom-domain
URL.

### Risk: DNS cutover too early

Do not change DNS until the Railway deployment URL works and you have tested
the important pages. See `DOMAIN_SETUP.md`.

## E. Recommended GitHub branch strategy

Use a safe test branch first:

- Working branch: `cursor/railway-migration-8d8c`
- Base branch: `main`
- Deploy this branch to Railway first.
- Keep `main` and the existing Streamlit deployment unchanged until Railway is
  confirmed working.
- After testing, merge the branch to `main` only if the Railway deployment is
  successful.
