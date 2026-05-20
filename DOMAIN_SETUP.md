# Domain Setup for themlbedge.com

Do not start this step until the Railway public URL works.

## Goal

Move `themlbedge.com` to the Railway app with as little downtime as possible.

## Important safety rule

Adding a custom domain inside Railway is safe. The live domain does not move
until DNS is changed at your domain/DNS provider.

Do not change DNS until:

1. The Railway deployment is successful.
2. The Railway public URL opens the app.
3. You have tested the important pages and features.

## Apex/root domain

The apex/root domain is:

```text
themlbedge.com
```

In Railway:

1. Open the Railway project.
2. Click the Streamlit service.
3. Click **Settings**.
4. Go to **Networking**.
5. Click **Custom Domain**.
6. Enter:

```text
themlbedge.com
```

7. Railway will show DNS records to add.
8. Copy the DNS records exactly into your DNS provider.

For an apex/root domain, your DNS provider may call the record one of these:

- `ALIAS`
- `ANAME`
- flattened `CNAME`
- `A` or `AAAA`

Use the record type and value Railway shows you. Do not guess.

## www domain

The www domain is:

```text
www.themlbedge.com
```

Recommended setup:

1. Add `www.themlbedge.com` as another custom domain in the same Railway
   service.
2. Copy the DNS record Railway shows.
3. Add that DNS record at your DNS provider.

For `www`, this is usually a `CNAME`, but use exactly what Railway displays.

## Avoid downtime

Before changing DNS:

1. Keep the current Streamlit hosting active.
2. Keep the current landing page active.
3. Keep existing GitHub repos unchanged.
4. Test the Railway public URL first.
5. If your DNS provider has a TTL setting, lower it before switching if
   possible. A lower TTL can make rollback faster.

When ready:

1. Add the Railway custom domain in Railway.
2. Add or update the DNS records at your DNS provider.
3. Wait for Railway to show the domain as active.
4. Visit:

```text
https://themlbedge.com
https://www.themlbedge.com
```

5. Confirm both open the expected app or landing page.

## Landing page handling

This repo preserves the existing landing/PWA files:

- `static/index.html`
- `static/manifest.json`
- `static/service-worker.js`

Those files currently point to the old Streamlit URL. That is safe during
testing.

After the Railway app works, decide which setup you want:

### Option A: Domain opens the Streamlit app directly

Point `themlbedge.com` to the Railway Streamlit service.

### Option B: Domain keeps the landing/PWA page first

Keep the landing page host, then update its iframe/app URL to the tested
Railway URL or final custom domain.

If the landing page is in a separate GitHub Pages repo, update that repo only
after Railway is tested.

## Rollback plan

If the domain switch does not work:

1. Do not delete the Railway service.
2. Change DNS back to the previous Streamlit or landing page records.
3. Wait for DNS to update.
4. Paste the Railway logs or domain error into Cursor for a focused fix.
