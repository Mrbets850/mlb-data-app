"""Email-based access gate verified against Stripe payments.

Usage in app.py:
    from auth_gate import check_access
    check_access()   # blocks with st.stop() if not authenticated

Requires STRIPE_SECRET_KEY in environment variables.
Optional: ADMIN_EMAILS (comma-separated) for admin bypass.
Optional: AUTH_SALT for token hashing (defaults to 'mlb-edge-2026').
"""

import hashlib
import hmac
import os
import streamlit as st


_STRIPE_SK = os.environ.get("STRIPE_SECRET_KEY", "")
_AUTH_SALT = os.environ.get("AUTH_SALT", "mlb-edge-2026")
_ADMIN_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
]


def _make_token(email: str) -> str:
    """Derive a short HMAC token from an email address."""
    return hmac.new(
        _AUTH_SALT.encode(), email.lower().strip().encode(), hashlib.sha256
    ).hexdigest()[:24]


def _verify_stripe_email(email: str) -> bool:
    """Check Stripe for a completed checkout session from this email."""
    if not _STRIPE_SK:
        return False
    try:
        import stripe

        stripe.api_key = _STRIPE_SK
        sessions = stripe.checkout.Session.list(
            customer_details={"email": email.lower().strip()},
            status="complete",
            limit=1,
        )
        return len(sessions.data) > 0
    except Exception as exc:
        st.error(f"Could not verify payment. Please try again. ({exc})")
        return False


def _preserve_token_in_params():
    """Return the current token value so callers can restore it after
    clearing query params."""
    return st.query_params.get("token", "")


def _render_login_ui():
    """Show the branded login form and handle submission."""
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
        .gate-info{font-size:.78rem;color:#3d4e6a;margin-top:20px;line-height:1.5}
        </style>
        <div class="gate-wrap">
            <div class="gate-logo">⚾</div>
            <div class="gate-title">Welcome to The MLB Edge</div>
            <div class="gate-sub">
                Log in with the email you used at checkout.<br/>
                Don't have access yet?
                <a href="https://themlbedge.com" target="_blank">
                    Get it here for $4.99</a>.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    email_input = st.text_input(
        "Email address", key="gate_email_input", placeholder="you@example.com"
    )
    if st.button("Log In", type="primary", use_container_width=True):
        _email = (email_input or "").strip().lower()
        if not _email or "@" not in _email:
            st.error("Please enter a valid email address.")
        elif _email in _ADMIN_EMAILS or _verify_stripe_email(_email):
            st.session_state["verified_email"] = _email
            st.query_params["token"] = _make_token(_email)
            st.rerun()
        else:
            st.error(
                "No payment found for this email. Please use the email you "
                "entered at checkout, or purchase access at themlbedge.com."
            )
    st.markdown(
        '<div class="gate-info">'
        "Your email is only used to verify your purchase. "
        "We don't store passwords.</div>",
        unsafe_allow_html=True,
    )


def check_access():
    """Gate the app behind email-based Stripe verification.

    Call this once near the top of app.py, after st.set_page_config().
    If STRIPE_SECRET_KEY is not set, the gate is skipped entirely
    (useful for local development).
    """
    if not _STRIPE_SK:
        return

    url_token = st.query_params.get("token", "")

    # Validate token against a previously verified email in this session.
    is_valid = False
    if url_token and "verified_email" in st.session_state:
        is_valid = url_token == _make_token(st.session_state["verified_email"])

    # Admin bypass: check token against each admin email.
    if not is_valid and url_token:
        for admin_email in _ADMIN_EMAILS:
            if url_token == _make_token(admin_email):
                is_valid = True
                st.session_state["verified_email"] = admin_email
                break

    if is_valid:
        return

    _render_login_ui()
    st.stop()
