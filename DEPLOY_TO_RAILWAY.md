# Deploy THE MLB EDGE to Railway

Follow these steps before changing `themlbedge.com`.

## Before you start

- Your GitHub account is already connected to Railway.
- Use the test branch first: `cursor/railway-migration-66af`
- Do not change DNS yet.
- Do not delete the Streamlit deployment yet.

## Step 1: Create the Railway project

1. Open Railway.
2. Click **New Project**.
3. Click **Deploy from GitHub repo**.
4. Select the GitHub repo that contains this code.
5. When Railway asks for a branch, select:

```text
cursor/railway-migration-66af
```

6. Let Railway create the service.

## Step 2: Confirm build settings

Railway should auto-detect this as a Python app because the repo has
`requirements.txt`.

Railway should also read `railway.json` automatically.

If Railway does not use the correct start command, paste this start command
into the service settings:

```bash
streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false
```

## Step 3: Add environment variables

The app can boot with no custom environment variables.

For full feature parity, add only the variables you already have values for.
Do not invent values.

In Railway:

1. Open the project.
2. Click the Streamlit service.
3. Click **Variables**.
4. Click **New Variable**.
5. Add the variables you need from the list below.

Recommended optional variables:

```text
ODDS_API_KEY
SPORTRADAR_MLB_API_KEY
SPORTSDATAIO_MLB_API_KEY
MRBETS850_ADMIN_PIN
MRBETS850_GITHUB_TOKEN
MRBETS850_PICKS_REPO
MRBETS850_PICKS_PATH
MRBETS850_PICKS_BRANCH
```

Only use the alias names below if those are the names you already use:

```text
THE_ODDS_API_KEY
THE_ODDS_API
ODDSAPI_KEY
odds_api_key
MLB_EDGE_ADMIN_PIN
GITHUB_TOKEN
```

Do not set this in production unless you are intentionally testing a specific
date:

```text
MRBETS850_TODAY_OVERRIDE
```

Do not set `PORT`. Railway provides it automatically.

## Step 4: Deploy

1. Open the Railway service.
2. Click **Deployments**.
3. Wait for the deployment to finish.
4. If the deployment fails, open the failed deployment logs and copy the error.

## Step 5: Generate a public Railway URL

1. Open the Railway service.
2. Click **Settings**.
3. Find **Networking**.
4. Click **Generate Domain** or **Generate Public Domain**.
5. Railway will create a temporary URL ending in a Railway domain.

Use this Railway URL for testing before touching `themlbedge.com`.

## Step 6: Test the Railway URL

Open the Railway URL in a browser and check:

1. The app loads without a startup error.
2. The THE MLB EDGE branding still appears.
3. Main navigation works.
4. MLB schedule data loads.
5. Player prop/odds features behave as expected if `ODDS_API_KEY` is set.
6. Live HR Tracker opens.
7. RBI Edge Model opens.
8. MRBETS850 HR Picks opens.
9. If you use the picks editor, confirm the admin PIN works.
10. If you use GitHub picks sync, edit one test pick and confirm it persists.

## Step 7: Do not switch the domain yet

Only move to `DOMAIN_SETUP.md` after the Railway test URL works.

If the Railway deployment fails, keep the existing Streamlit hosting and paste
the Railway logs into Cursor so the smallest necessary fix can be made.
