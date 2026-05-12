# PWA Support — THE MLB EDGE

This repo bundles the assets needed to make the Streamlit app installable
as a Progressive Web App (PWA) on iOS and Android home screens.

## What ships in this repo

| Path | Purpose |
|------|---------|
| `pwa.py` | Streamlit helper that injects `<link rel="manifest">` and PWA meta tags into the running app, plus a customer-facing install-instructions expander. |
| `static/manifest.json` | PWA manifest (name, icons, theme color, display mode). Icon `src` values are absolute `raw.githubusercontent.com` URLs so the manifest works regardless of which host serves the app. |
| `static/icons/icon-*.png` | App icons at every size required by iOS, Android, and Chrome (72 – 512 px). |
| `static/service-worker.js` | App-shell service worker (network-first, cache fallback). Used only by the optional standalone shell (see below) — Streamlit Cloud cannot serve this at root scope. |
| `static/index.html` | Optional standalone PWA shell that embeds the Streamlit app in an iframe. Host this on GitHub Pages (or any static host) for the most reliable install experience. |

## How injection works in the live Streamlit app

`pwa.inject_pwa_head_tags()` is called once after `st.set_page_config`. It
mounts a 0-height `st.components.v1.html` iframe whose JS reaches into
`window.parent.document.head` and appends:

- `<link rel="manifest" href="…/static/manifest.json">`
- `<link rel="apple-touch-icon" …>`
- `<link rel="icon" sizes="192x192" …>`
- `<meta name="theme-color" content="#1a0a2e">`
- `<meta name="mobile-web-app-capable" content="yes">`
- `<meta name="apple-mobile-web-app-capable" content="yes">`
- `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">`
- `<meta name="apple-mobile-web-app-title" content="MLB EDGE">`
- `<meta name="application-name" content="MLB EDGE">`

These tags are enough for **Safari "Add to Home Screen"** and for **Chrome
to mark the app as installable** when combined with a reachable manifest.

## Caveat: no service worker on Streamlit Community Cloud

Chrome's full installability prompt (`beforeinstallprompt`) requires a
service worker registered at a scope that includes the page being viewed.
**Streamlit Community Cloud does not let us serve arbitrary files like
`/service-worker.js` at the app's root scope** — only the Streamlit app
shell is served from `/`. We cannot register a service worker there.

What this means in practice:

- **iOS / Safari (Add to Home Screen)** — fully works using the meta tags
  we inject. The app opens full-screen from the home-screen icon. No
  service worker is required.
- **Android / Chrome** — the icon, title, and standalone display mode
  work via the manifest, but Chrome may not auto-prompt with the install
  banner. Users can still install manually via **⋮ → Install app /
  Add to Home screen**. The in-app expander documents this clearly.

### Workaround: standalone PWA shell

For a full install-prompt experience, host `static/index.html` on
**GitHub Pages** (or Netlify / Vercel / any static host) where you control
the root scope. That page already:

- Includes the manifest and meta tags directly in `<head>` (no injection
  needed).
- Registers `service-worker.js` from the same origin's root.
- Embeds the Streamlit app inside an iframe with a branded splash screen
  and Android install banner / iOS install tip.

To enable GitHub Pages serving from this repo:

1. Repo **Settings → Pages → Source: Deploy from a branch → `main` / `/static`**.
2. The shell becomes available at `https://<user>.github.io/mlb-data-app/`.
3. Share *that* URL with customers (instead of the raw streamlit.app URL)
   for the best install experience.

If you'd rather keep one canonical URL, the streamlit.app link still
works for Safari add-to-home-screen and for manual Chrome installs.

## Local testing

```bash
pip install -r requirements.txt streamlit
streamlit run app.py
```

Then, in DevTools (Application → Manifest), confirm the manifest loads
without errors and that icons resolve. Lighthouse → PWA audit should
report:

- ✅ Web app manifest meets the installability requirements
- ✅ Provides a valid apple-touch-icon
- ✅ Has a `<meta name="viewport">` tag with `width` or `initial-scale`
- ⚠ Does not register a service worker that controls page and start_url
  *(expected on Streamlit Cloud — see Caveat above)*

## Updating icons or branding

Replace the PNGs in `static/icons/` (keep the same filenames and sizes)
and edit `static/manifest.json`. The `pwa.py` constants
`_THEME_COLOR` / `_APP_TITLE` control what's injected into the live app.
