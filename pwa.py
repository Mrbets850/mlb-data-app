"""PWA support for the Streamlit MLB Edge app.

Streamlit Community Cloud does NOT let us serve arbitrary files from the
app's root URL scope, which is normally required to register a service
worker at `/service-worker.js`. To make the app installable on iOS/Android
anyway, we:

1. Inject `<link rel="manifest">`, theme-color and apple-mobile-web-app
   meta tags into the live document via a tiny components.html iframe that
   bubbles those tags up to the parent document with JavaScript.
2. Serve the manifest and icons as static assets from raw.githubusercontent.com
   (committed under `static/`).
3. Surface clear in-app iOS/Android install instructions for users who
   reach the Streamlit URL directly (e.g. from a Discord link).

A separate `static/index.html` PWA shell exists for the most reliable
install experience (host on GitHub Pages or any static host that allows
serving service-worker.js at root scope).
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


# Raw GitHub CDN base for all PWA static assets we commit under `static/`.
# Using a CDN URL means the manifest/icons resolve no matter what host the
# user is viewing the app from (streamlit.app, a wrapper page, etc.).
_PWA_CDN_BASE = (
    "https://raw.githubusercontent.com/Mrbets850/mlb-data-app/main/static"
)

_MANIFEST_URL = f"{_PWA_CDN_BASE}/manifest.json"
_ICON_180 = f"{_PWA_CDN_BASE}/icons/icon-180.png"
_ICON_192 = f"{_PWA_CDN_BASE}/icons/icon-192.png"
_ICON_512 = f"{_PWA_CDN_BASE}/icons/icon-512.png"

# Brand color from the supplied PWA bundle (matches splash + status bar).
_THEME_COLOR = "#1a0a2e"
_APP_TITLE = "MLB EDGE"


def inject_pwa_head_tags() -> None:
    """Inject manifest link + PWA meta tags into the host document `<head>`.

    Streamlit renders the app inside its own DOM and gives us no first-class
    way to add tags to `<head>`. We work around this by mounting a 0-height
    `components.html` iframe whose script reaches into `window.parent.document`
    and appends the tags. This is same-origin under streamlit.app so it
    works for installability checks (Chrome) and for Safari's
    "Add to Home Screen" metadata.
    """
    html = f"""
    <script>
    (function() {{
        // We're inside a components.html iframe; reach into the parent doc.
        var doc;
        try {{ doc = window.parent.document; }}
        catch (e) {{ return; }}
        if (!doc || !doc.head) return;

        function ensure(selector, build) {{
            if (doc.head.querySelector(selector)) return;
            doc.head.appendChild(build());
        }}

        ensure('link[rel="manifest"]', function() {{
            var l = doc.createElement('link');
            l.rel = 'manifest';
            l.href = {_MANIFEST_URL!r};
            return l;
        }});
        ensure('link[rel="apple-touch-icon"]', function() {{
            var l = doc.createElement('link');
            l.rel = 'apple-touch-icon';
            l.href = {_ICON_180!r};
            return l;
        }});
        ensure('link[rel="icon"][sizes="192x192"]', function() {{
            var l = doc.createElement('link');
            l.rel = 'icon';
            l.type = 'image/png';
            l.setAttribute('sizes', '192x192');
            l.href = {_ICON_192!r};
            return l;
        }});

        // Meta tags
        var metas = [
            ['theme-color',                              {_THEME_COLOR!r}],
            ['mobile-web-app-capable',                   'yes'],
            ['apple-mobile-web-app-capable',             'yes'],
            ['apple-mobile-web-app-status-bar-style',    'black-translucent'],
            ['apple-mobile-web-app-title',               {_APP_TITLE!r}],
            ['application-name',                         {_APP_TITLE!r}],
        ];
        metas.forEach(function(pair) {{
            var name = pair[0], content = pair[1];
            if (doc.head.querySelector('meta[name="' + name + '"]')) return;
            var m = doc.createElement('meta');
            m.setAttribute('name', name);
            m.setAttribute('content', content);
            doc.head.appendChild(m);
        }});
    }})();
    </script>
    """
    # height=0 keeps the iframe invisible; it only exists to run the script.
    components.html(html, height=0, width=0)


# ---------------------------------------------------------------------------
# In-app install help (rendered in a collapsed expander on the main page)
# ---------------------------------------------------------------------------

_INSTALL_HELP_MD = """\
**📲 Install THE MLB EDGE on your phone**

You can pin the app to your home screen so it opens like a normal app —
no app store needed.

**iPhone / iPad (Safari)**
1. Open this page in **Safari** (not Chrome / Discord's in-app browser).
2. Tap the **Share** button (square with the up-arrow at the bottom).
3. Scroll down and tap **Add to Home Screen**.
4. Tap **Add** in the top right.

> If you're tapping the link from inside Discord, tap the **•••** menu in
> the top-right corner of the in-app browser and choose **Open in Safari**
> first.

**Android (Chrome)**
1. Open this page in **Chrome**.
2. Tap the **⋮** menu in the top right.
3. Tap **Add to Home screen** *(or* **Install app***)*.
4. Confirm by tapping **Install**.

> If you're tapping the link from inside Discord, tap **Open in browser**
> from the **⋮** menu first so Chrome handles the install prompt.

Once installed, the icon launches the live MLB Edge dashboard in
full-screen — no browser bars, no tabs.
"""


def render_install_help_expander(*, expanded: bool = False) -> None:
    """Render a customer-facing install/help expander.

    Call this once near the top (or bottom) of the app's main page.
    """
    with st.expander("📲 Install on iPhone / Android — tap for instructions",
                     expanded=expanded):
        st.markdown(_INSTALL_HELP_MD)
