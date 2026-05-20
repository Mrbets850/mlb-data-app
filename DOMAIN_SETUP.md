# Domain Setup for themlbedge.com on Railway

Do not change DNS until the Railway test URL works.

Use this order:

1. Deploy and test Railway with the generated Railway URL.
2. Confirm the app works on desktop and mobile.
3. Add `themlbedge.com` and `www.themlbedge.com` in Railway.
4. Change DNS only after Railway gives you the exact DNS records.
5. Keep the old Streamlit/GitHub Pages setup available during the cutover.

## 1. First test with the Railway URL

Before touching the custom domain:

1. Open Railway.
2. Open the `the-mlb-edge` project.
3. Open the `mlb-data-app` service.
4. Go to **Settings**.
5. Go to **Networking**.
6. Click **Generate Domain** if you have not already.
7. Open the generated Railway URL.
8. Test the checklist in `DEPLOY_TO_RAILWAY.md`.

Only continue when the Railway URL works.

## 2. Add the apex/root domain

The apex/root domain is:

```text
themlbedge.com
```

In Railway:

1. Open the `mlb-data-app` service.
2. Click **Settings**.
3. Find **Networking**.
4. Click **Custom Domain** or **Add Domain**.
5. Enter:

```text
themlbedge.com
```

6. Railway will show the DNS record you need.
7. Copy Railway's exact DNS instructions.

At your DNS provider:

1. Find the DNS settings for `themlbedge.com`.
2. Add or update the record Railway tells you to use.
3. Do not delete unrelated records unless you know they are not used.

Railway commonly asks for a `CNAME` or an `ALIAS`/`ANAME`/flattened CNAME
depending on your DNS provider. Use the exact value Railway shows.

## 3. Add the www domain

The www domain is:

```text
www.themlbedge.com
```

In Railway:

1. Open the same `mlb-data-app` service.
2. Go to **Settings**.
3. Go to **Networking**.
4. Click **Custom Domain** or **Add Domain** again.
5. Enter:

```text
www.themlbedge.com
```

6. Railway will show a DNS record for `www`.

At your DNS provider:

1. Add the `www` record Railway gives you.
2. This is usually a `CNAME` for `www`.

## 4. Root and www handling

Recommended setup:

- `themlbedge.com` points to Railway.
- `www.themlbedge.com` points to Railway too.

After both work, choose which one you want to share publicly. Railway may allow
one domain to redirect to the other. If Railway offers a redirect option, use:

- Primary: `themlbedge.com`
- Redirect: `www.themlbedge.com` to `themlbedge.com`

If Railway does not offer that option in your plan/settings, it is still okay
for both domains to load the same app.

## 5. Avoid downtime

To avoid downtime:

1. Do not change DNS until the Railway generated URL works.
2. Do not shut down the Streamlit app first.
3. Do not delete the GitHub Pages PWA repo.
4. Add Railway custom domains first, then update DNS.
5. After DNS changes, test both:

```text
https://themlbedge.com
https://www.themlbedge.com
```

6. Keep the old Streamlit URL available until the domain has worked for a
   full round of your own testing.

DNS can take time to update. During that period, some visitors may see the old
site and some may see Railway. That is normal.

## 6. Important PWA/landing-page note

This repo includes landing/PWA files, and a related GitHub Pages PWA repo is
referenced by the existing docs:

```text
Mrbets850/mlb-edge-pwa
```

For the safest migration, leave that repo live until Railway is fully tested.
After `themlbedge.com` works on Railway, update the PWA repo so its iframe and
manifest point to the final Railway/custom-domain URL instead of the old
Streamlit URL.

Do this as a separate safe change after the app migration is proven.
