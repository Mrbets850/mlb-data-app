# REPO AUDIT: mlb-data-app

**Repo URL:** https://github.com/Mrbets850/mlb-data-app  
**Audit date:** 2026-05-20  
**Auditor:** Railway Migration Engineer

---

## Purpose

This is the complete MLB Edge product. It is a single monolithic Streamlit web
application (~18,000 lines) that provides MLB analytics and prop betting
intelligence. Features include:

- Live schedule, lineups, and game state (MLB StatsAPI)
- Hot/cold batter rankings, HR milestones, HR sleepers
- Pitcher breakdown, pitcher weak spots by slot
- HR parlay generator, RBI Edge Model, K Generator
- Live HR Tracker, MRBETS850 HR Picks board
- Total Bases, HRR, AI Hits Parlay generators
- Ballpark weather data
- PWA head-tag injection for mobile install

All features are served from a single `app.py` entry point. Sub-modules
(`rbi_model.py`, `live_hr_tracker.py`, `mrbets850_hr_picks.py`, `pwa.py`) and
a `services/` package are imported and rendered conditionally.

---

## Repo type

**Main app.** This is the only web service in the project.

---

## Deploy decision

**Deploy now.**

This is the app. Nothing else in the project needs to run as a web service.

---

## Railway role recommendation

**Primary web service.** One Railway project, one service, one GitHub repo
connection. No worker, no database, no Redis, no cron service needed at Railway.

---

## Entrypoint / start command

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false
```

This is already written into `railway.json`. Railway will pick it up
automatically.

---

## Current Railway readiness

| Check | Status | Notes |
|-------|--------|-------|
| `railway.json` present | DONE | Correct Nixpacks builder + start command |
| `requirements.txt` present | DONE | Includes `streamlit` and all dependencies |
| `$PORT` used in start command | DONE | Already in `railway.json` |
| `--server.address 0.0.0.0` | DONE | Already in `railway.json` |
| Streamlit health check path | DONE | `/_stcore/health` in `railway.json` |
| No hardcoded secrets | DONE | All secrets via `os.environ` / `st.secrets` |
| Graceful secret degradation | DONE | App boots without any env vars |

**No code changes are required.** The app is Railway-ready as-is.

---

## Files that belong to this service

### Entry point
- `app.py` — main Streamlit app

### Sub-modules (imported by app.py)
- `rbi_model.py`
- `live_hr_tracker.py`
- `mrbets850_hr_picks.py`
- `pwa.py`

### Services package
- `services/__init__.py`
- `services/lineup_service.py`
- `services/live_game_state.py`
- `services/slate_rollover.py`
- `services/player_detail.py`
- `services/pitcher_weak_spots.py`

### Configuration
- `requirements.txt`
- `railway.json`
- `.streamlit/config.toml`

### Static / branding (preserve — do not delete)
- `static/index.html`
- `static/manifest.json`
- `static/service-worker.js`
- `data/mrbets850_hr_picks.json`
- `assets/mlb_edge_logo.jpeg` (referenced in code; may need to be restored)
- `assets/mrbets850_logo.jpg` (referenced in code; may need to be restored)

### Data (Savant CSVs at repo root)
- `Data:savant_batters.csv.csv`
- `Data:savant_batters_all.csv.csv`
- `Data:savant_pitchers.csv.csv`
- `Data:savant_pitcher_stats.csv`
- `Data:savant_bat_tracking.csv`

### GitHub Actions (keep on GitHub, not Railway)
- `.github/workflows/refresh-data.yml`
- `scripts/refresh_savant.py`

---

## Dependencies

```
pandas
numpy
streamlit
pybaseball
MLB-StatsAPI
lxml
html5lib
beautifulsoup4
requests
```

All are standard PyPI packages. Nixpacks will install them from
`requirements.txt` during the Railway build step.

---

## Environment variables

No variable is required for the app to boot. All premium features degrade
gracefully when keys are absent.

| Variable | Required | Purpose |
|----------|----------|---------|
| `ODDS_API_KEY` | Optional | The Odds API — canonical key name |
| `THE_ODDS_API_KEY` | Optional | Alias for Odds API key |
| `THE_ODDS_API` | Optional | Alias for Odds API key |
| `ODDSAPI_KEY` | Optional | Alias for Odds API key |
| `odds_api_key` | Optional | Lowercase legacy alias |
| `SPORTRADAR_MLB_API_KEY` | Optional | Premium lineup provider #1 |
| `SPORTSDATAIO_MLB_API_KEY` | Optional | Premium lineup provider #2 |
| `MRBETS850_ADMIN_PIN` | Optional | Admin PIN for HR picks editor |
| `MLB_EDGE_ADMIN_PIN` | Optional | Alternate admin PIN name |
| `MRBETS850_GITHUB_TOKEN` | Optional | GitHub API token for picks sync |
| `GITHUB_TOKEN` | Optional | Alternate token name |
| `MRBETS850_PICKS_REPO` | Optional | GitHub repo for picks sync |
| `MRBETS850_PICKS_PATH` | Optional | File path for picks sync |
| `MRBETS850_PICKS_BRANCH` | Optional | Branch for picks sync |
| `MRBETS850_TODAY_OVERRIDE` | Dev only | Force slate date — do not set in prod |
| `PORT` | Auto | Provided by Railway — do not set manually |

---

## Streamlit-specific hosting assumptions

The following Streamlit assumptions exist and are already handled:

| Assumption | Status |
|-----------|--------|
| Default port 8501 | Overridden via `$PORT` in `railway.json` |
| Localhost binding | Overridden via `--server.address 0.0.0.0` |
| CORS protection | Disabled via `--server.enableCORS false` |
| XSRF protection | Disabled via `--server.enableXsrfProtection false` |
| `st.secrets` | Secrets resolved via `os.environ` fallback — works on Railway |
| `st.set_page_config` | Called once at top of `app.py` — correct |

The legacy `.streamlitconfig.toml` at the repo root (with a dot prefix) is not
read by Streamlit. The active theme is `.streamlit/config.toml`. The legacy
file can be ignored or deleted in a future cleanup pass; it is not harmful.

---

## Files likely to change during migration

**None at this time.** The Railway configuration is complete. The only files
that will change in this migration cycle are documentation files.

Future changes to watch for (not part of this audit):

- `static/index.html` and `static/manifest.json` — currently point to
  `https://mrbets850.streamlit.app/`. Update these only after the Railway URL
  is confirmed working and before DNS is moved.
- `pwa.py` — embeds the old Streamlit URL in PWA head tags. Update after
  Railway URL is confirmed.

---

## Risks

### Risk 1: Ephemeral file system

Railway does not persist files written at runtime. If the HR picks editor saves
picks locally to `data/mrbets850_hr_picks.json` and the service restarts, the
file reverts to the last committed version.

**Mitigation:** Set `MRBETS850_GITHUB_TOKEN`, `MRBETS850_PICKS_REPO`,
`MRBETS850_PICKS_PATH`, and `MRBETS850_PICKS_BRANCH` to use the GitHub Contents
API sync path instead of local JSON writes.

### Risk 2: Missing logo/icon assets

`assets/mlb_edge_logo.jpeg`, `assets/mrbets850_logo.jpg`, and
`static/icons/*.png` are referenced in code but are not present in this local
checkout. The app handles missing logos gracefully (no crash), but the app will
appear without custom logos until those files are restored to the repo.

**Mitigation:** Restore the assets from the live repo or from the deployed
Streamlit app before making Railway the primary production host.

### Risk 3: Savant CSV load path

Savant CSVs are loaded from `raw.githubusercontent.com` at runtime. If the
GitHub Actions refresh workflow stops running or is rate-limited, CSV data will
go stale. This is not a Railway-specific risk but is worth monitoring.

**Mitigation:** The GitHub Actions workflow remains on GitHub Actions and is not
touched by this migration.

### Risk 4: Domain cutover downtime

Switching `themlbedge.com` DNS before the Railway deployment is confirmed will
cause downtime.

**Mitigation:** Test the Railway public URL thoroughly before touching DNS. See
`DEPLOY_TO_RAILWAY_mlb-data-app.md` for the full test checklist.

---

## Exact next step

1. Create the Railway project from the `main` branch of this repo.
2. Set environment variables for any keys you already have.
3. Click Deploy.
4. Generate a Railway public domain and test it.
5. Only after testing: switch `themlbedge.com` DNS by following `DOMAIN_SETUP.md`.

See `DEPLOY_TO_RAILWAY_mlb-data-app.md` for the complete step-by-step guide.
