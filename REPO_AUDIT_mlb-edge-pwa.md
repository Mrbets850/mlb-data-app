# REPO AUDIT: mlb-edge-pwa

**Repo URL:** https://github.com/Mrbets850/mlb-edge-pwa  
**Live URL:** https://mrbets850.github.io/mlb-edge-pwa/  
**Audit date:** 2026-05-20  
**Auditor:** Railway Migration Engineer

---

## Purpose

This is the MLB Edge PWA installer and public landing page. It is a pure static
site (HTML + CSS + JavaScript, no Python, no build step) hosted for free on
GitHub Pages.

Its job is to let users install the MLB Edge app onto their phone home screen
(iOS and Android). It does this by:

1. Showing branded splash/install UI with gold and purple MLB Edge design
2. Triggering the browser's native PWA install prompt on Android via
   `beforeinstallprompt`
3. Showing Safari/iOS manual install instructions for iPhone users
4. Providing a "Open Live Dashboard" link that opens the live Streamlit app
5. Registering a service worker for offline install capability
6. Redirecting standalone PWA launches to the live dashboard URL

---

## Repo type

**Utility / static landing page.** This repo is the public-facing installer and
does not run any server process.

---

## Files in this repo

| File | Purpose |
|------|---------|
| `index.html` | Main installer page — branded UI, install logic, service worker registration |
| `manifest.json` | PWA manifest — app name, icons, start URL, display mode |
| `service-worker.js` | App-shell service worker for offline install capability |
| `favicon.png` | Browser tab favicon |
| `icons/` | PWA icon set (72×72 through 512×512 PNG) |
| `README.md` | Customer install instructions for iPhone, Android, and Discord |

---

## Deploy decision

**Do not deploy to Railway.**

This repo is a free, zero-maintenance static site. GitHub Pages hosts it at no
cost with zero configuration. There is no server process, no Python, no
dependency management, and no environment variables.

Deploying a static HTML file to Railway would consume a Hobby service slot and
add cost with no benefit.

**Keep this repo on GitHub Pages permanently.**

---

## Railway role recommendation

**None.** This repo is GitHub-only.

---

## Streamlit-specific hosting assumptions

The `index.html` file currently has the Streamlit Community Cloud URL hardcoded
in two places:

```
const dashboardUrl = "https://mrbets850.streamlit.app/";
```

and the "Open Live Dashboard" button `href`:

```html
<a class="button secondary" href="https://mrbets850.streamlit.app/" rel="noopener">
```

The PWA manifest's `start_url` and `scope` are GitHub Pages scoped:
```
"start_url": "/mlb-edge-pwa/?source=pwa"
"scope": "/mlb-edge-pwa/"
```

These URLs do not affect Railway deployment of the main app. However, after
the Railway deployment of `mlb-data-app` is confirmed and `themlbedge.com` DNS
is switched, these two `index.html` URLs should be updated to either the Railway
public URL or the custom domain.

---

## Dependencies

None. Pure static HTML/CSS/JavaScript.

---

## Environment variables

None.

---

## Risks

### Risk 1: Dashboard URL becomes stale after Railway migration

When the main app moves from `mrbets850.streamlit.app` to Railway (and then to
`themlbedge.com`), the PWA installer will still try to open the old Streamlit
URL. The Streamlit Community Cloud URL will continue to work as long as that
deployment is active, so this is a low-urgency issue.

**Mitigation (do after Railway is confirmed working):**
1. In the `mlb-edge-pwa` repo, edit `index.html`.
2. Replace `https://mrbets850.streamlit.app/` with the final Railway or custom
   domain URL.
3. Commit and push to the `mlb-edge-pwa` main branch.
4. GitHub Pages updates automatically within a few minutes.

### Risk 2: PWA manifest scope limits

The manifest's `scope` is `/mlb-edge-pwa/`, which means the PWA shell is
scoped to the GitHub Pages path. This is correct for the installer. The actual
app is served from a different origin (Railway). This is intentional — the PWA
is just the launcher, not the host of the app itself.

---

## Files likely to change after Railway migration

| File | When to change | What to change |
|------|---------------|---------------|
| `index.html` | After Railway URL confirmed + DNS switched | Replace `mrbets850.streamlit.app` with final domain |

---

## Exact next step

Do nothing now. Keep this repo on GitHub Pages.

After the Railway deployment of `mlb-data-app` is confirmed:
1. Update the dashboard URL in `mlb-edge-pwa/index.html` from
   `https://mrbets850.streamlit.app/` to the Railway URL or `themlbedge.com`.
2. Commit and push to `mlb-edge-pwa` main.
3. GitHub Pages will update automatically.

This is a one-line edit in a single file. No Railway deployment is needed.
