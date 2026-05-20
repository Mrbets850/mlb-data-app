# Deploy THE MLB EDGE to Railway

Follow these steps after this branch is pushed to GitHub.

## 1. Create the Railway project

1. Open Railway.
2. Click **New Project**.
3. Click **Deploy from GitHub repo**.
4. Select the repo:
   - `Mrbets850/mlb-data-app`
5. If Railway asks for a branch, select:
   - `cursor/railway-migration-8d8c`
6. If Railway asks for a project name, use:
   - `the-mlb-edge`
7. If Railway asks for a service name, use:
   - `mlb-data-app`

Railway should detect this as a Python app because the repo has
`requirements.txt`.

## 2. Confirm the start command

This branch includes `railway.json`, so Railway should use this automatically:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

If the first deploy fails because Railway did not use the right command:

1. Open your Railway project.
2. Click the `mlb-data-app` service.
3. Open **Settings**.
4. Find **Deploy** or **Start Command**.
5. Paste this exact command:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

6. Save.
7. Redeploy.

## 3. Add environment variables

Open the Railway service:

1. Click `mlb-data-app`.
2. Click **Variables**.
3. Add only the variables you already use.
4. Do not create fake values.

### Minimum required variables

None found. The core app should run without secrets.

### Recommended if you already have them

Add these if you already use these features:

```text
ODDS_API_KEY=your_existing_odds_api_key
MRBETS850_ADMIN_PIN=your_existing_admin_pin
MRBETS850_GITHUB_TOKEN=your_existing_github_token_for_hr_picks
MRBETS850_PICKS_REPO=Mrbets850/mlb-data-app
MRBETS850_PICKS_PATH=data/mrbets850_hr_picks.json
MRBETS850_PICKS_BRANCH=main
SPORTRADAR_MLB_API_KEY=your_existing_sportradar_key
SPORTSDATAIO_MLB_API_KEY=your_existing_sportsdataio_key
```

Notes:

- If you do not use odds features, skip `ODDS_API_KEY`.
- If you do not edit HR picks inside the app, skip the admin/GitHub variables.
- If you do not pay for Sportradar or SportsDataIO, skip those variables.
- Railway automatically provides `PORT`; do not add `PORT` yourself.

## 4. Deploy

1. Open the `mlb-data-app` service.
2. Click **Deployments**.
3. Wait for the latest deployment to finish.
4. The deployment should show as successful/active.

If it fails:

1. Open the failed deployment.
2. Copy the build/deploy logs.
3. Paste the logs back into Cursor or your migration helper.
4. Fix the smallest error first.

## 5. Generate a public Railway test URL

1. Open the `mlb-data-app` service.
2. Click **Settings**.
3. Find **Networking**.
4. Click **Generate Domain**.
5. Railway will create a temporary public URL ending in a Railway domain.

Use this Railway URL for testing before changing `themlbedge.com`.

## 6. Test before touching the live domain

Open the generated Railway URL in a browser and test:

1. The page loads without a crash.
2. The title/branding still says THE MLB EDGE.
3. The main slate/dashboard loads.
4. Date selection works.
5. Team logos, player names, matchup tables, and cards render.
6. The RBI model page opens.
7. The Live HR Tracker page opens.
8. The MRBETS850 HOMERUN PICKS page opens.
9. If you added `ODDS_API_KEY`, odds/lines appear where expected.
10. If you added an admin PIN, the HR picks editor unlock works.
11. Mobile view is usable.
12. The old Streamlit URL still works during testing.

Do not switch DNS until this Railway URL passes your checks.

## 7. After testing

When the Railway URL works:

1. Merge the migration branch to `main`.
2. Change the Railway service branch from `cursor/railway-migration-8d8c` to
   `main`, or create a new Railway deployment from `main`.
3. Test again.
4. Only then start the custom-domain steps in `DOMAIN_SETUP.md`.
