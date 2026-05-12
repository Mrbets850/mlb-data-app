# Install THE MLB EDGE on Your Phone

Pin the MLB Edge dashboard to your home screen for one-tap access — no
app store, no download.

## 👉 Recommended installer (Discord customers, iPhone & Android)

**https://mrbets850.github.io/mlb-edge-pwa/**

Open that link on your phone. It's the official installer page — it uses
the uploaded **MLB EDGE** logo, registers a service worker for the best
install experience, and is the page we share in Discord.

> **Why this link and not the streamlit.app one?** Streamlit Community
> Cloud can't host a service worker at the right scope, so Chrome's
> install banner doesn't reliably show up on the raw streamlit.app URL.
> The GitHub Pages installer above fixes that and ships with the branded
> MLB EDGE icon.

## Direct Streamlit link (fallback)

If for some reason the installer page won't load, the raw dashboard is
still reachable here:

👉 https://mrbets850.streamlit.app/

You can still "Add to Home Screen" from this URL, but the icon and
install prompt are less reliable than the installer link above.

## iPhone / iPad (Safari)

1. Tap **https://mrbets850.github.io/mlb-edge-pwa/** to open it in
   **Safari**.
   - If you tapped from inside Discord, tap the **•••** menu in the
     top-right of the in-app browser and choose **Open in Safari** first.
2. Tap the **Share** button (the square with the up-arrow at the bottom of
   the screen).
3. Scroll down and tap **Add to Home Screen**.
4. Tap **Add** in the top right.

You'll now see a gold ⚾ **MLB EDGE** icon on your home screen that opens
the full-screen dashboard.

## Android (Chrome)

1. Tap **https://mrbets850.github.io/mlb-edge-pwa/** to open it in
   **Chrome**.
   - If you tapped from inside Discord, tap the **⋮** menu and choose
     **Open in Chrome** first.
2. You should see an **Install app** prompt — tap it.
3. If you don't see the prompt, tap the **⋮** menu in the top-right
   corner and tap **Add to Home screen** *(some versions say*
   **Install app***)*.
4. Tap **Install** to confirm.

The MLB Edge icon will appear on your home screen / app drawer.

## Suggested Discord announcement

> 📱 **MLB EDGE is now installable on your phone!**
>
> Add the dashboard to your home screen for one-tap access — no app
> store, no login wall, just the live board with the gold ⚾ MLB EDGE
> icon.
>
> 👉 **Installer:** https://mrbets850.github.io/mlb-edge-pwa/
>
> **iPhone:** Open the installer link in Safari → Share → **Add to Home Screen**
> **Android:** Open the installer link in Chrome → tap **Install app** (or ⋮ menu → **Install app**)
>
> Prefer the raw dashboard? It still lives at
> https://mrbets850.streamlit.app/ — but use the installer link above for
> the proper MLB EDGE icon and full-screen install.
>
> Full instructions inside the app — tap the "📲 Install on iPhone /
> Android" expander at the bottom of the page.

## Troubleshooting

- **"The Share menu doesn't show 'Add to Home Screen'"** — you're not in
  Safari (iOS) / Chrome (Android). Open the installer link in the real
  browser app, not Discord's in-app viewer.
- **"It says 'Add Bookmark' instead"** — same fix; open in Safari/Chrome.
- **App opens with a browser bar** — you tapped the bookmark instead of
  the installed icon. Long-press the installed icon and pick the right
  one, or re-install fresh from
  https://mrbets850.github.io/mlb-edge-pwa/.
- **"Install app" doesn't appear on Android** — make sure you're on the
  installer URL (`mrbets850.github.io/mlb-edge-pwa`), not the raw
  streamlit.app URL. The streamlit URL can't register a service worker
  so Chrome won't show the banner there.
