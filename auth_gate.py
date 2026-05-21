"""Access gate for The MLB Edge.

Uses a simple access code verified via URL query param.
The token persists across page navigation because all
query_params.clear() calls now preserve the token param.

Usage in app.py:
    from auth_gate import check_access
    check_access()

Set ACCESS_CODE in your Railway environment variables.
Optional: ADMIN_EMAILS (comma-separated) for admin bypass.
"""

import os
import streamlit as st


_ACCESS_CODE = os.environ.get("ACCESS_CODE", "")
_ADMIN_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
]


def _render_login_ui():
    """Show the branded login form."""
    st.markdown(
        """
        <style>
        .gate-wrap{max-width:440px;margin:12vh auto;text-align:center}
        .gate-logo{font-size:2.5rem;margin-bottom:8px}
        .gate-title{font-size:1.6rem;font-weight:900;color:#e2eeff;
                    letter-spacing:-.02em;margin-bottom:6px}
        .gate-sub{font-size:.92rem;color:#4e6a8a;margin-bottom:28px;line-height:1.5}
        .gate-sub a{color:#facc15;text-decoration:none}
        .gate-sub a:hover{text-decoration:underline}
        </style>
        <div class="gate-wrap">
            <div class="gate-logo">⚾</div>
            <div class="gate-title">Welcome to The MLB Edge</div>
            <div class="gate-sub">
                Enter your access code to continue.<br/>
                Don't have one?
                <a href="https://themlbedge.com" target="_blank">
                    Get it here for $4.99</a>.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    code_input = st.text_input(
        "Access Code", type="password", key="gate_code_input"
    )
    if st.button("Unlock", type="primary", use_container_width=True):
        val = (code_input or "").strip()
        if val == _ACCESS_CODE:
            st.query_params["token"] = _ACCESS_CODE
            st.rerun()
        else:
            st.error("Invalid access code. Please try again.")


def check_access():
    """Gate the app behind an access code.

    If ACCESS_CODE is not set, the gate is skipped (local dev).
    Admin emails bypass the gate entirely — they just need
    ?token=<ACCESS_CODE> in the URL (provided by the success page link).
    """
    if not _ACCESS_CODE:
        return

    url_token = st.query_params.get("token", "")

    if url_token == _ACCESS_CODE:
        return

    _render_login_ui()
    st.stop()
