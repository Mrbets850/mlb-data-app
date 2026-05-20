# MASTER RAILWAY ARCHITECTURE

**Project:** THE MLB EDGE  
**Author:** Railway Migration Engineer  
**Date:** 2026-05-20  
**Target platform:** Railway Hobby

---

## All repos reviewed

| Repo | Type | Railway role |
|------|------|-------------|
| `Mrbets850/mlb-data-app` | Main Streamlit app | **Deploy to Railway** |
| `Mrbets850/mlb-edge-pwa` | Static PWA installer | GitHub Pages only — do not deploy to Railway |

---

## Which repos become Railway services

### Deploy: `Mrbets850/mlb-data-app`

This is the entire app. One Railway service. One web process. No worker, no
database, no cron, no Redis.

**Start command:**
```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false
```

**Already configured in:** `railway.json`

### Do not deploy: `Mrbets850/mlb-edge-pwa`

Free GitHub Pages static site. No server needed. Keep it where it is.

---

## Which repos stay GitHub-only

| Repo | Why GitHub-only |
|------|----------------|
| `Mrbets850/mlb-edge-pwa` | Pure static site, free on GitHub Pages, no server needed |

Additionally, within `mlb-data-app`, the following components stay on GitHub
and are not moved to Railway:

| Component | Where it runs | Why |
|-----------|--------------|-----|
| `.github/workflows/refresh-data.yml` | GitHub Actions | Scheduled Savant CSV refresh runs fine on free GitHub Actions cron |
| `scripts/refresh_savant.py` | GitHub Actions | Called by the workflow above |

---

## Recommended Railway project structure

```
Railway project: mlb-edge
└── Service: streamlit-app
    ├── Source: GitHub repo Mrbets850/mlb-data-app
    ├── Branch: main
    ├── Builder: Nixpacks (auto-detected)
    ├── Start command: (from railway.json — auto-detected)
    └── Health check: /_stcore/health
```

**One project. One service. That is all.**

---

## Recommended service structure

| Service | Needed? | Reason |
|---------|---------|--------|
| Web service (Streamlit) | YES | The app |
| Worker service | NO | No background jobs run at Railway |
| Cron service | NO | Data refresh stays on GitHub Actions |
| PostgreSQL | NO | App uses no database |
| Redis | NO | App uses no cache layer |
| Volume (persistent disk) | Recommended if using HR picks editor | See note below |

### Volume note

Railway's file system is ephemeral — files written during runtime are lost on
restart. The HR picks editor (`mrbets850_hr_picks.py`) can write picks to
`data/mrbets850_hr_picks.json` locally. If you use the picks editor in
production, enable GitHub sync via environment variables so picks survive
restarts. A Railway Volume is an alternative but adds cost.

**Recommended approach:** Use the GitHub sync variables (`MRBETS850_GITHUB_TOKEN`,
`MRBETS850_PICKS_REPO`, etc.) so picks persist to GitHub without needing a
Railway Volume.

---

## One Railway project vs multiple projects

**Use one Railway project with one service.**

Reasons:
- The app is a single Streamlit monolith with no separate processes
- No microservices, no API/frontend split, no worker queue
- Railway Hobby billing is per-service; fewer services = lower cost
- One project is easier to manage, monitor, and roll back
- GitHub Actions already handles the only background job (Savant refresh)

A second Railway project or service is not needed now and would add cost with
no benefit.

---

## Why this is the simplest low-cost setup for Railway Hobby

### Cost profile

| Resource | Cost | Notes |
|----------|------|-------|
| One Railway service (Streamlit app) | ~$5/month Hobby plan | Includes 512MB RAM + shared CPU |
| GitHub Pages (PWA installer) | Free | No Railway service needed |
| GitHub Actions (Savant refresh) | Free tier | Runs 5× daily, well within free minutes |
| Custom domain | Free in Railway | Add after initial deployment |
| Volume (optional) | +$0.25/GB/month | Only if you need local file persistence |

### Architecture diagram

```
User
 │
 ├─► themlbedge.com ──────────────────► Railway: streamlit-app
 │                                         app.py + modules
 │                                         MLB StatsAPI (free)
 │                                         Odds API (your key)
 │                                         Savant CSVs (GitHub raw)
 │
 └─► mrbets850.github.io/mlb-edge-pwa ► GitHub Pages (free)
                                          Install landing page
                                          PWA launcher
```

### What stays free / outside Railway

```
GitHub Actions (refresh-data.yml)
 └── runs scripts/refresh_savant.py
     └── commits updated CSVs to mlb-data-app/main
         └── Railway redeploys (or app loads fresh CSVs on next request)
```

---

## Migration order

1. **Now:** Deploy `mlb-data-app` to Railway from `main` branch.
2. **After Railway URL works:** Test all features on the Railway public URL.
3. **After testing:** Switch `themlbedge.com` DNS to Railway (see `DOMAIN_SETUP.md`).
4. **After DNS:** Update `mlb-edge-pwa/index.html` dashboard URL to `themlbedge.com`.
5. **Later (optional):** Shut down Streamlit Community Cloud deployment once
   custom domain is confirmed.

---

## What you do not need

- You do not need a separate Railway frontend service
- You do not need a Railway database
- You do not need a Railway worker or cron service
- You do not need to move the GitHub Actions workflow to Railway
- You do not need to migrate the PWA repo to Railway
- You do not need Stripe yet
- You do not need multiple Railway projects

---

## Reference documents

| Document | Purpose |
|----------|---------|
| `REPO_AUDIT_mlb-data-app.md` | Detailed audit of the main app repo |
| `REPO_AUDIT_mlb-edge-pwa.md` | Detailed audit of the PWA repo |
| `DEPLOY_TO_RAILWAY_mlb-data-app.md` | Step-by-step Railway deployment guide |
| `ENV_TEMPLATE_mlb-data-app.txt` | All environment variables for the Railway service |
| `DOMAIN_SETUP.md` | DNS cutover instructions for themlbedge.com |
| `MIGRATION_PLAN.md` | Broader migration context and branch strategy |
