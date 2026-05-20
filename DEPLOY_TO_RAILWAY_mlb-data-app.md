# DEPLOY TO RAILWAY: mlb-data-app

**App:** THE MLB EDGE  
**Repo:** https://github.com/Mrbets850/mlb-data-app  
**Date prepared:** 2026-05-20

---

## Safety rules before you start

- Do not change `themlbedge.com` DNS until the Railway test URL works.
- Do not delete the Streamlit Community Cloud deployment yet.
- Do not close this file until all test steps pass.

---

## Branch recommendation

Deploy from **`main`**.

The Railway configuration (`railway.json`) is already committed to `main`. You
do not need to create a special deployment branch. If you want to preview a
change before it goes live, use a feature branch and connect it as a separate
Railway service.

---

## Files that were changed / created for Railway

All changes are already on `main`. You do not need to make any code changes
before deploying.

| File | Status | Purpose |
|------|--------|---------|
| `railway.json` | EXISTS — correct | Nixpacks builder + Streamlit start command + health check |
| `requirements.txt` | EXISTS — correct | All Python dependencies including `streamlit` |
| `.streamlit/config.toml` | EXISTS — correct | Dark gold theme; read by Streamlit at startup |

---

## Start command

Railway reads this automatically from `railway.json`. You should not need to
type it manually. If Railway asks you to confirm or override the start command,
use this exactly:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false
```

---

## Step 1: Create the Railway project

1. Open https://railway.app and sign in.
2. Click **New Project**.
3. Click **Deploy from GitHub repo**.
4. Find and select: `Mrbets850/mlb-data-app`
5. When Railway asks for a branch, select: **`main`**
6. Let Railway create the service and start the first build.

Railway should automatically detect this as a Python app because
`requirements.txt` is present at the repo root.

---

## Step 2: Confirm build settings

After the project is created:

1. Click the service name (Railway may auto-name it `mlb-data-app` or similar).
2. Click **Settings**.
3. Confirm:
   - Builder: **Nixpacks** (auto-detected)
   - Start command: Railway should read this from `railway.json` automatically.
     If the start command field is empty or wrong, paste the exact command from
     the section above.
4. Click **Save** if you changed anything.

---

## Step 3: Add environment variables

The app will boot without any custom environment variables. Add only the
variables for which you already have values.

How to add a variable:
1. Click the service.
2. Click **Variables** tab.
3. Click **New Variable**.
4. Enter the variable name and value.
5. Repeat for each variable.
6. Click **Deploy** or wait for Railway to redeploy automatically.

### Recommended variables to add now

Add only the ones you have real values for. Leave others blank — do not invent
values.

```
ODDS_API_KEY              ← your The Odds API key (use this name)
MRBETS850_ADMIN_PIN       ← your HR picks editor PIN
MRBETS850_GITHUB_TOKEN    ← a GitHub personal access token with repo write scope
MRBETS850_PICKS_REPO      ← Mrbets850/mlb-data-app
MRBETS850_PICKS_PATH      ← data/mrbets850_hr_picks.json
MRBETS850_PICKS_BRANCH    ← main
```

### Optional premium lineup providers (add if you have keys)

```
SPORTRADAR_MLB_API_KEY
SPORTSDATAIO_MLB_API_KEY
```

### Aliases (use only if those are the names your current setup uses)

```
THE_ODDS_API_KEY
THE_ODDS_API
ODDSAPI_KEY
odds_api_key
MLB_EDGE_ADMIN_PIN
GITHUB_TOKEN
```

### Do NOT set these

```
PORT                       ← Railway provides this automatically
MRBETS850_TODAY_OVERRIDE   ← Dev/test override — never set in production
```

See `ENV_TEMPLATE_mlb-data-app.txt` for the full reference list.

---

## Step 4: Watch the first deployment

1. Click **Deployments** tab on the service.
2. Wait for the build to complete. First build takes 2–5 minutes.
3. If the build fails, click the failed deployment and read the log.
   Common first-build issues:
   - Missing package → check `requirements.txt`
   - Syntax error in app → check the last git commit on `main`
   - Port conflict → `railway.json` should prevent this; re-check start command

---

## Step 5: Generate a public Railway URL

1. Click **Settings** tab on the service.
2. Find **Networking** section.
3. Click **Generate Domain** (or **Generate Public Domain** — wording varies).
4. Railway will create a URL ending in `.up.railway.app`.
5. Copy this URL — you will use it for all testing below.

---

## Step 6: Test the Railway public URL

Open the Railway URL in a browser and confirm each item below before touching
the live domain.

### Core app

- [ ] App loads without an error page or traceback
- [ ] "THE MLB EDGE" title and branding appear
- [ ] Dark gold theme is applied (not the default Streamlit light theme)
- [ ] Top navigation pills/tabs are visible

### Main views

- [ ] ⚾ Games tab loads with today's MLB schedule
- [ ] 🔥 Hot Batters tab loads
- [ ] 🧊 Cold Batters tab loads
- [ ] 💣 HR Milestones tab loads
- [ ] 🥎 Pitcher Breakdown tab loads
- [ ] 🎯 Pitcher Weak Spots tab loads
- [ ] ⚾ RBI Edge Model tab loads
- [ ] 🏟️ Live HR Tracker tab loads
- [ ] 👑 MRBETS850 HOMERUN PICKS OF DAY tab loads

### API-dependent features (if you set `ODDS_API_KEY`)

- [ ] Odds data appears in views that show prop lines
- [ ] No "API key not configured" warning in views that need odds

### HR picks editor (if you set `MRBETS850_ADMIN_PIN`)

- [ ] Admin PIN field appears in the HR picks view
- [ ] Entering the correct PIN unlocks the editor
- [ ] Saving a pick shows success (not an error)
- [ ] If `MRBETS850_GITHUB_TOKEN` is set: the saved pick appears in
  `data/mrbets850_hr_picks.json` in the GitHub repo

### Data freshness

- [ ] Savant data loads (no "file not found" errors for the CSV data)
- [ ] Player stats and breakdowns show current-season data

---

## Step 7: Do not switch the domain yet

Only proceed to `DOMAIN_SETUP.md` after all test items above pass.

If the Railway deployment fails, keep Streamlit Community Cloud running and
paste the Railway build or runtime logs into Cursor for diagnosis.

---

## Step 8: Switch the custom domain (when ready)

Follow `DOMAIN_SETUP.md` for the full DNS cutover steps.

Short version:
1. In the Railway service, go to **Settings → Networking → Custom Domain**.
2. Add `themlbedge.com`.
3. Add `www.themlbedge.com` as a second custom domain.
4. Copy the DNS records Railway shows you.
5. Add those records at your DNS provider.
6. Wait for Railway to confirm the domain is active.
7. Test `https://themlbedge.com` and `https://www.themlbedge.com`.

---

## Step 9: Update the PWA installer URL (after domain works)

Once `themlbedge.com` is confirmed on Railway:

1. In the separate `Mrbets850/mlb-edge-pwa` GitHub repo, open `index.html`.
2. Find: `https://mrbets850.streamlit.app/`
3. Replace both occurrences with: `https://themlbedge.com/`
4. Commit and push to `main` of that repo.
5. GitHub Pages updates automatically within a few minutes.

---

## Rollback plan

If anything goes wrong:

1. Keep the Railway service running — do not delete it.
2. If DNS was already changed: revert DNS at your provider to the previous
   records. The Streamlit URL will still work.
3. Paste the Railway error logs into Cursor for a focused fix.
4. The fix will be a small change. You do not need to start over.

---

## Git commands (if you need to push a fix)

If you make changes to the code and need to push to Railway:

```bash
git add <changed files>
git commit -m "fix: <describe the fix>"
git push origin main
```

Railway will auto-detect the push to `main` and redeploy.

---

## What Railway auto-detects from this repo

| Setting | Source | Value |
|---------|--------|-------|
| Builder | `railway.json` → `build.builder` | Nixpacks |
| Start command | `railway.json` → `deploy.startCommand` | `streamlit run app.py --server.port $PORT ...` |
| Health check | `railway.json` → `deploy.healthcheckPath` | `/_stcore/health` |
| Python version | Nixpacks detects from `requirements.txt` | Python 3.x |
| Dependencies | `requirements.txt` | All packages installed automatically |
| Theme | `.streamlit/config.toml` | Dark gold theme |
