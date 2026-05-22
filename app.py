# app version: 2026-04-29-rbi2-redeploy
import streamlit as st
import pandas as pd
import numpy as np
import requests
from rbi_model import render_rbi_model_page
from services.lineup_service import (
    get_service as get_lineup_service,
    format_freshness as _format_lineup_freshness,
)
from services.slate_rollover import (
    compute_default_slate_date as _compute_default_slate_date,
    default_schedule_fetcher as _slate_rollover_fetcher,
    now_utc as _slate_now_utc,
)
from services.live_game_state import (
    get_live_game_state as _svc_get_live_game_state,
    apply_live_pitcher_to_game_row as _svc_apply_live_pitcher,
    freshness_label as _svc_pitcher_freshness_label,
)
import re
import unicodedata
import urllib.parse
import io
import os
import base64
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Streamlit Cloud servers run in UTC, so date.today() flips at 7pm CT (8pm CDT)
# and breaks the slate-date picker for late-evening users. Always anchor
# "today" to America/Chicago so the date matches when MLB games are played.
MLB_TZ = ZoneInfo("America/Chicago")

def today_ct() -> date:
    """Return today's date in Central Time (matches the MLB slate day)."""
    return datetime.now(MLB_TZ).date()


# Slate-rollover lookups hit the same StatsAPI schedule endpoint as the
# main app, but we want a shorter TTL (5 min) than the 30-min schedule
# cache: once the last game goes final, the rollover should advance
# within ~grace_minutes regardless of what the bigger schedule cache
# happens to be holding.
@st.cache_data(ttl=300, show_spinner=False)
def _slate_rollover_cached_games(target_iso: str):
    return _slate_rollover_fetcher(date.fromisoformat(target_iso))


def _slate_rollover_fetch_for_app(target_date: date):
    return _slate_rollover_cached_games(target_date.strftime("%Y-%m-%d"))


def auto_default_slate_date():
    """Compute the rollover-aware default slate date.

    Wraps :func:`services.slate_rollover.compute_default_slate_date`
    with a cached schedule fetcher so the picker doesn't refetch on
    every Streamlit rerun. Returns the full ``RolloverDecision`` so
    callers can surface the reason / grace timer if they want.
    Falls back to plain ``today_ct()`` if the rollover lookup blows up
    for any reason — the picker default must never break the app.
    """
    try:
        return _compute_default_slate_date(
            today_ct(), _slate_now_utc(), _slate_rollover_fetch_for_app
        )
    except Exception:
        from services.slate_rollover import RolloverDecision
        return RolloverDecision(
            slate_date=today_ct(), rolled_over=False, reason="current"
        )

# ===========================================================================
# Config
# ===========================================================================
GITHUB_USER = "Mrbets850"
GITHUB_REPO = "mlb-data-app"
GITHUB_BRANCH = "main"

CSV_FILES = {
    "batters":         "Data:savant_batters.csv.csv",
    # Low-PA batter leaderboard (min=1 PA). Used as a *backfill* source for
    # rookies, call-ups, and bench bats whose appearance in a daily lineup
    # would otherwise leave the Matchup heat-map board empty. Merged into
    # batters_df only for player_ids not present in the qualified leaderboard.
    "batters_all":     "Data:savant_batters_all.csv.csv",
    # Prior-season batter leaderboard (min=1 PA). Used as a final fallback
    # for player_ids that have a current-season row but with NaN Statcast
    # values (too-small sample). Filled in cell-by-cell only — never replaces
    # current-season data. Refreshed by scripts/refresh_savant.py.
    "batters_prev":    "Data:savant_batters_prev.csv.csv",
    "pitchers":        "Data:savant_pitchers.csv.csv",
    # Pitcher results (xwOBA, Whiff%, Barrel%-against, HH%, FB%, K%, BB%, etc.)
    # used by the Slate Pitchers tab. Joined to the slate by player_id.
    "pitcher_stats":   "Data:savant_pitcher_stats.csv",
    # Per-batter bat-tracking leaderboard: real avg_bat_speed (mph),
    # swing_length (ft), batted_ball_events (BIP), and swings_competitive
    # (Pitches) per player_id. Merged into batters_df below so the lineup
    # tables can show actual Pitches/BIP instead of placeholders.
    "bat_tracking":    "Data:savant_bat_tracking.csv",
    # Prior-season bat-tracking — fallback for current-season call-ups with
    # no bat-tracking sample yet.
    "bat_tracking_prev": "Data:savant_bat_tracking_prev.csv",
}

def raw_github_url(path: str) -> str:
    # The Savant CSVs in this repo are stored at the repo root with literal
    # "Data:" prefixes (e.g. "Data:savant_batters.csv.csv") — the colon is
    # part of the filename, not a path separator. Keep ":" in the safe set
    # so it is not percent-encoded to %3A in the resulting URL.
    encoded = urllib.parse.quote(path, safe="/:")
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{encoded}"

CSV_URLS = {label: raw_github_url(name) for label, name in CSV_FILES.items()}

# CSVs that are optional fallbacks — their absence is expected and should not
# surface a user-facing warning banner. The app already handles empty frames
# for these keys downstream (see `_batters_prev_raw` / `_bat_tracking_prev_df`).
OPTIONAL_CSVS = {"batters_prev", "bat_tracking_prev"}

# ---------------------------------------------------------------------------
# Brand assets (logo + player headshots)
# ---------------------------------------------------------------------------
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOGO_FILENAME = "mlb_edge_logo.jpeg"

@st.cache_data(show_spinner=False)
def _logo_data_uri() -> str:
    """Read the MrBets850 logo from /assets and return a data: URI we can drop
    directly into <img src=...>. Cached so the bytes are only read once."""
    path = os.path.join(ASSETS_DIR, LOGO_FILENAME)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return ""
    ext = os.path.splitext(LOGO_FILENAME)[1].lower().lstrip(".") or "jpeg"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

LOGO_URI = _logo_data_uri()

def player_headshot_url(player_id) -> str:
    """MLB official player headshot from the same CDN that serves team logos.
    Returns an empty string when player_id is missing/invalid."""
    try:
        pid = int(player_id)
    except (TypeError, ValueError):
        return ""
    if pid <= 0:
        return ""
    return (
        "https://img.mlbstatic.com/mlb-photos/image/upload/"
        "d_people:generic:headshot:67:current.png/"
        "w_180,q_auto:best/v1/people/" + str(pid) + "/headshot/67/current"
    )

st.set_page_config(
    page_title="THE MLB EDGE",
    page_icon="⚾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# PWA: inject manifest link, theme color and apple-mobile meta tags so the
# app is installable on iOS / Android home screens. See pwa.py and PWA.md
# for Streamlit-specific caveats (no service worker at root scope).
# ---------------------------------------------------------------------------
from pwa import inject_pwa_head_tags, render_install_help_expander
inject_pwa_head_tags()

# from auth_gate import check_access
# check_access()

# Team data (id used to fetch logos from MLB CDN)
# ===========================================================================
TEAM_INFO = {
    "Arizona Diamondbacks": {"abbr": "ARI", "id": 109, "lat": 33.4484, "lon": -112.0740},
    "Atlanta Braves":       {"abbr": "ATL", "id": 144, "lat": 33.7490, "lon": -84.3880},
    "Baltimore Orioles":    {"abbr": "BAL", "id": 110, "lat": 39.2904, "lon": -76.6122},
    "Boston Red Sox":       {"abbr": "BOS", "id": 111, "lat": 42.3601, "lon": -71.0589},
    "Chicago Cubs":         {"abbr": "CHC", "id": 112, "lat": 41.8781, "lon": -87.6298},
    "Chicago White Sox":    {"abbr": "CWS", "id": 145, "lat": 41.8781, "lon": -87.6298},
    "Cincinnati Reds":      {"abbr": "CIN", "id": 113, "lat": 39.1031, "lon": -84.5120},
    "Cleveland Guardians":  {"abbr": "CLE", "id": 114, "lat": 41.4993, "lon": -81.6944},
    "Colorado Rockies":     {"abbr": "COL", "id": 115, "lat": 39.7392, "lon": -104.9903},
    "Detroit Tigers":       {"abbr": "DET", "id": 116, "lat": 42.3314, "lon": -83.0458},
    "Houston Astros":       {"abbr": "HOU", "id": 117, "lat": 29.7604, "lon": -95.3698},
    "Kansas City Royals":   {"abbr": "KC",  "id": 118, "lat": 39.0997, "lon": -94.5786},
    "Los Angeles Angels":   {"abbr": "LAA", "id": 108, "lat": 33.8366, "lon": -117.9143},
    "Los Angeles Dodgers":  {"abbr": "LAD", "id": 119, "lat": 34.0522, "lon": -118.2437},
    "Miami Marlins":        {"abbr": "MIA", "id": 146, "lat": 25.7617, "lon": -80.1918},
    "Milwaukee Brewers":    {"abbr": "MIL", "id": 158, "lat": 43.0389, "lon": -87.9065},
    "Minnesota Twins":      {"abbr": "MIN", "id": 142, "lat": 44.9778, "lon": -93.2650},
    "New York Mets":        {"abbr": "NYM", "id": 121, "lat": 40.7128, "lon": -74.0060},
    "New York Yankees":     {"abbr": "NYY", "id": 147, "lat": 40.7128, "lon": -74.0060},
    "Athletics":            {"abbr": "ATH", "id": 133, "lat": 38.5816, "lon": -121.4944},
    "Philadelphia Phillies":{"abbr": "PHI", "id": 143, "lat": 39.9526, "lon": -75.1652},
    "Pittsburgh Pirates":   {"abbr": "PIT", "id": 134, "lat": 40.4406, "lon": -79.9959},
    "San Diego Padres":     {"abbr": "SD",  "id": 135, "lat": 32.7157, "lon": -117.1611},
    "San Francisco Giants": {"abbr": "SF",  "id": 137, "lat": 37.7749, "lon": -122.4194},
    "Seattle Mariners":     {"abbr": "SEA", "id": 136, "lat": 47.6062, "lon": -122.3321},
    "St. Louis Cardinals":  {"abbr": "STL", "id": 138, "lat": 38.6270, "lon": -90.1994},
    "Tampa Bay Rays":       {"abbr": "TB",  "id": 139, "lat": 27.9506, "lon": -82.4572},
    "Texas Rangers":        {"abbr": "TEX", "id": 140, "lat": 32.7767, "lon": -96.7970},
    "Toronto Blue Jays":    {"abbr": "TOR", "id": 141, "lat": 43.6532, "lon": -79.3832},
    "Washington Nationals": {"abbr": "WSH", "id": 120, "lat": 38.9072, "lon": -77.0369},
}

TEAM_FIXES = {"CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF",
              "TBR": "TB", "WSN": "WSH", "OAK": "ATH"}

DEFAULT_PARK_FACTORS = {
    "ARI": 100, "ATL": 100, "BAL": 100, "BOS": 100, "CHC": 100, "CWS": 100,
    "CIN": 100, "CLE": 100, "COL": 112, "DET": 98, "HOU": 101, "KC": 99,
    "LAA": 99, "LAD": 102, "MIA": 95, "MIL": 102, "MIN": 101, "NYM": 98,
    "NYY": 103, "ATH": 98, "PHI": 104, "PIT": 98, "SD": 95, "SF": 94,
    "SEA": 95, "STL": 100, "TB": 97, "TEX": 106, "TOR": 103, "WSH": 101
}

# Compass bearing FROM HOME PLATE TO CENTER FIELD for each park.
# Lets us turn raw wind direction into "out / in / cross" relative to CF.
# Source: public stadium-orientation tables (degrees, 0=N, 90=E).
STADIUM_CF_BEARING = {
    "ARI":   2, "ATL":  60, "BAL":  33, "BOS":  43, "CHC":  46, "CWS":  37,
    "CIN":  29, "CLE":   0, "COL":   0, "DET":  47, "HOU":  20, "KC":   34,
    "LAA":  40, "LAD":  24, "MIA":  40, "MIL":  40, "MIN":   2, "NYM":  25,
    "NYY":  75, "ATH":  60, "PHI":  16, "PIT":  60, "SD":   55, "SF":   88,
    "SEA":   8, "STL":  62, "TB":   45, "TEX":   3, "TOR":   0, "WSH":  35,
}

# Domed / retractable-roof parks where outdoor wind is irrelevant when closed.
# We assume "closed" by default for these; on a hot summer day Houston/AZ might
# have it open but we can't know that without scraping, so we play it safe.
DOMED_PARKS = {"ARI", "HOU", "MIA", "MIL", "SEA", "TB", "TOR", "TEX"}

ABBR_TO_ID = {info["abbr"]: info["id"] for info in TEAM_INFO.values()}

def logo_url(team_id: int, size: int = 60) -> str:
    """MLB official CDN team logo."""
    return f"https://www.mlbstatic.com/team-logos/{team_id}.svg"

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ===========================================================================
# Data-source freshness registry
# ---------------------------------------------------------------------------
# Every external data fetch records its outcome here so we can surface
# per-source timestamps and "stale / unavailable / live" badges in the UI
# instead of silently using empty or stale data.
#
# Each entry: {
#   "label":       human-readable name (e.g. "MLB StatsAPI · Schedule")
#   "url":         source URL or endpoint
#   "fetched_at":  pd.Timestamp UTC of the last successful fetch (None if never)
#   "status":      "live" | "stale" | "fallback" | "error" | "unconfigured"
#   "detail":      short freeform string (row counts, err msg, fallback reason)
#   "max_age_min": int — how many minutes until this source is considered stale
# }
# ===========================================================================
DATA_SOURCES: dict = {}

def _utc_now() -> pd.Timestamp:
    # Use tz-aware now then drop the tz, so we stay naive-UTC like the
    # Last-Modified parsing path. Using Timestamp.utcnow() raises a
    # deprecation warning in newer pandas.
    return pd.Timestamp.now(tz="UTC").tz_localize(None)

def record_source(key: str, *, label: str, url: str = "", status: str = "live",
                  detail: str = "", max_age_min: int = 60,
                  fetched_at: "pd.Timestamp | None" = None) -> None:
    """Record the outcome of a data fetch. Called from inside fetcher
    functions. If status='live' and fetched_at is omitted, uses now()."""
    if status == "live" and fetched_at is None:
        fetched_at = _utc_now()
    prev = DATA_SOURCES.get(key, {})
    DATA_SOURCES[key] = {
        "label":       label or prev.get("label", key),
        "url":         url   or prev.get("url", ""),
        "status":      status,
        "detail":      detail,
        "fetched_at":  fetched_at if fetched_at is not None else prev.get("fetched_at"),
        "max_age_min": max_age_min,
    }

def source_age_minutes(key: str) -> "float | None":
    info = DATA_SOURCES.get(key)
    if not info or info.get("fetched_at") is None:
        return None
    delta = (_utc_now() - info["fetched_at"]).total_seconds() / 60.0
    return max(0.0, delta)

def source_is_stale(key: str) -> bool:
    """True if the source has data but it's older than its max_age."""
    info = DATA_SOURCES.get(key)
    if not info: return False
    age = source_age_minutes(key)
    if age is None: return False
    return age > float(info.get("max_age_min", 60))

def render_source_chips(keys: "list[str]") -> str:
    """Render small inline status chips for the given source keys.
    Used beneath player-prop tables so the user sees *exactly* which feeds
    fed the table and how fresh each one is."""
    chips = []
    for key in keys:
        info = DATA_SOURCES.get(key)
        if not info:
            continue
        bg, fg, txt = status_pill(info.get("status", ""))
        age = source_age_minutes(key)
        age_str = ("never" if age is None else
                   ("just now" if age < 1 else
                    f"{int(round(age))}m" if age < 60 else
                    f"{age/60:.1f}h" if age < 24*60 else
                    f"{age/(60*24):.1f}d"))
        chips.append(
            f'<span style="display:inline-block;margin:2px 6px 2px 0;'
            f'background:rgba(255,255,255,.06);border-radius:8px;padding:2px 8px;'
            f'font-size:.74rem;color:#e2e8f0;">'
            f'<span style="background:{bg};color:{fg};border-radius:999px;'
            f'padding:1px 6px;margin-right:6px;font-weight:800;font-size:.65rem;'
            f'letter-spacing:.04em;">{txt}</span>'
            f'<b>{info.get("label", key)}</b> · '
            f'<span style="color:#475569;">{age_str}</span></span>'
        )
    if not chips:
        return ""
    return ('<div style="margin:4px 0 8px 0;line-height:1.7;">' + "".join(chips) + '</div>')


def fmt_age(minutes: "float | None") -> str:
    if minutes is None:
        return "never"
    m = float(minutes)
    if m < 1:    return "just now"
    if m < 60:   return f"{int(round(m))} min ago"
    if m < 24*60:
        h = m / 60.0
        return f"{h:.1f} hr ago"
    d = m / (60.0 * 24.0)
    return f"{d:.1f} d ago"

def status_pill(status: str) -> "tuple[str, str, str]":
    """Return (background, foreground, label) for a status badge."""
    s = (status or "").lower()
    if s == "live":         return ("#065f46", "#d1fae5", "LIVE")
    if s == "stale":        return ("#92400e", "#fde68a", "STALE")
    if s == "fallback":     return ("#1e3a8a", "#bfdbfe", "FALLBACK")
    if s == "error":        return ("#7f1d1d", "#fecaca", "ERROR")
    if s == "unconfigured": return ("#374151", "#e5e7eb", "OFF")
    return ("#374151", "#e5e7eb", s.upper() or "—")

# ===========================================================================
# Tier system (calibrated to actual matchup_score distribution: ~85-200)
# ===========================================================================
TIER_ELITE  = 130
TIER_STRONG = 110
TIER_OK     = 95

def score_tier(score):
    try:
        v = float(score)
    except Exception:
        return ("neutral", "N/A")
    if v >= TIER_ELITE:  return ("elite",  "Elite")
    if v >= TIER_STRONG: return ("strong", "Strong")
    if v >= TIER_OK:     return ("ok",     "OK")
    return ("avoid", "Avoid")

# ===========================================================================
# Global styles — Edge Intel design system
# New palette: deep navy base · teal accent · amber emphasis
# Typography: DM Sans (UI) + DM Mono (data)
# ===========================================================================
st.markdown("""
<style>
/* ---- Google Fonts ---- */
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600;9..40,700;9..40,800;9..40,900&family=DM+Mono:wght@400;500&display=swap');

/* ---- base layout ---- */
/* Wide PC layout: bounded max-width (~1600px) centered */
.block-container {
    padding-top: 0.4rem;
    padding-bottom: 3rem;
    padding-left: 1.25rem;
    padding-right: 1.25rem;
    max-width: 1600px !important;
    width: 100% !important;
    margin-left: auto !important;
    margin-right: auto !important;
}
[data-testid="stAppViewContainer"] > .main,
[data-testid="stMain"],
section.main,
section[data-testid="stMain"] > div,
[data-testid="stAppViewContainer"] section.main > div {
    max-width: 100% !important;
    width: 100% !important;
}
/* Neutralize the outermost app shell so the dark/green Streamlit theme
   default never shows through on the far-left strip, the right gutter,
   or the collapsed sidebar. We cover every wrapper Streamlit uses
   between the iframe root and the content block, including the
   right-side toolbar/decoration that sits flush with the viewport edge. */
html, body,
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stMain"],
[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
[data-testid="stBottom"],
section.main,
.main,
.stApp {
    background: #0a1628 !important;
    background-color: #0a1628 !important;
    background-image: none !important;
}
body, html { background: #0a1628 !important; }
body::before {
    content: "";
    position: fixed; inset: 0;
    background: #0a1628;
    z-index: -1;
    pointer-events: none;
}
/* Subtle grid texture */
body::after {
    content: "";
    position: fixed; inset: 0; z-index: -1; pointer-events: none;
    background-image:
        linear-gradient(rgba(0,200,150,.012) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,200,150,.012) 1px, transparent 1px);
    background-size: 48px 48px;
}
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stToolbar"] *, [data-testid="stHeader"] * { color: #5a7a9c !important; }
@media (min-width: 1200px) {
    .block-container { padding-left: 2rem; padding-right: 2rem; }
}
@media (min-width: 1600px) {
    .block-container { padding-left: 2.5rem; padding-right: 2.5rem; }
}
@media (max-width: 640px) {
    .block-container { padding-left: 0.6rem; padding-right: 0.6rem; }
    /* Hard-lock the document to the phone viewport — no element may push
       horizontal scroll on the page. Wide tables / heatmaps that don't
       have a mobile-card twin can still scroll inside their own card. */
    html, body { max-width: 100vw; overflow-x: hidden !important; }
    .block-container { max-width: 100vw !important; overflow-x: hidden; }
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    [data-testid="stHorizontalBlock"] > div {
        min-width: 0 !important; width: 100% !important; flex: 1 1 100% !important;
    }
    /* Streamlit inserts inline images at native width — clamp them. */
    [data-testid="stMarkdownContainer"] img { max-width: 100% !important; height: auto; }
    /* Buttons + radios are tappable full-width on phones. */
    [data-testid="stButton"] button,
    [data-testid="stDownloadButton"] button { width: 100% !important; }
}
/* Let wide tables scroll horizontally INSIDE their card on desktop,
   instead of being clipped at the page boundary. */
[data-testid="stDataFrame"], [data-testid="stTable"] { width: 100% !important; }
[data-testid="stDataFrame"] > div { overflow-x: auto !important; }
.stDataFrame, .stTable { width: 100% !important; }
/* Helper used in markdown captions to hide "swipe horizontally" copy on
   phones now that mobile cards no longer scroll horizontally. */
.mobile-hide-swipe { display: inline; }
@media (max-width: 640px) { .mobile-hide-swipe { display: none !important; } }
html, body, [class*="css"] {
    font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #e2eeff;
    font-size: 15px;
    font-weight: 400;
}
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6 {
    color: #eef4ff !important;
    font-weight: 700 !important;
    font-family: 'DM Sans', sans-serif !important;
    letter-spacing: -0.02em;
}
[data-testid="stMarkdownContainer"] > p,
[data-testid="stMarkdownContainer"] > p > * {
    color: #8aaccc;
    font-weight: 400;
    font-size: 0.88rem;
}
[data-testid="stMarkdownContainer"] strong,
[data-testid="stMarkdownContainer"] b {
    color: #e2eeff;
    font-weight: 700;
}
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] * {
    color: #4e6a8a !important;
    font-weight: 400 !important;
    font-size: 0.78rem !important;
}
.section-card, .section-card *,
.carousel-wrap, .carousel-wrap *,
.mhm-wrap, .mhm-wrap *,
.scout-rows, .scout-rows *,
.lineup-banner, .lineup-banner *,
[data-testid="stDataFrame"] *, [data-testid="stTable"] * {
    color: inherit;
}
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] *,
[data-testid="stMarkdownContainer"] > p,
[data-testid="stMarkdownContainer"] > p > strong,
[data-testid="stRadio"] label, [data-testid="stRadio"] label * {
    color: #7a9bbf !important;
    font-weight: 500 !important;
    font-size: 0.82rem !important;
}

/* ---- Streamlit tabs — clean underline style ---- */
.stTabs [data-baseweb="tab-list"] {
    gap: 0 !important;
    border-bottom: 1px solid rgba(255,255,255,0.07) !important;
    background: transparent !important;
    padding: 0 !important;
    border-radius: 0 !important;
    border-top: none !important;
    border-left: none !important;
    border-right: none !important;
    box-shadow: none !important;
    overflow-x: auto;
    scrollbar-width: none;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
.stTabs [data-baseweb="tab-list"]::after { display: none !important; }
.stTabs [data-baseweb="tab"] {
    color: #4e6a8a !important;
    font-weight: 600 !important;
    font-size: 0.78rem !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
    background: transparent !important;
    border-radius: 0 !important;
    padding: 9px 15px !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    transition: all 0.12s ease !important;
    min-height: 38px !important;
    white-space: nowrap;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #b0c8e4 !important;
    background: transparent !important;
    border-bottom-color: rgba(0,200,150,0.3) !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: #00c896 !important;
    background: transparent !important;
    border-bottom: 2px solid #00c896 !important;
    font-weight: 700 !important;
    box-shadow: none !important;
    transform: none !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] * { color: #00c896 !important; }
.stTabs [data-baseweb="tab-highlight"] { background: #00c896 !important; height: 2px !important; }
@media (max-width: 640px) {
    .stTabs [data-baseweb="tab-list"] {
        flex-wrap: nowrap !important;
        overflow-x: auto !important;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 8px 11px !important;
        font-size: 0.72rem !important;
        flex-shrink: 0 !important;
    }
    .game-views-sub { display: none !important; }
}

/* ---- Expanders ---- */
[data-testid="stExpander"] {
    background: rgba(12, 26, 46, 0.85) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 6px !important;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary * {
    color: #b0c8e4 !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
}

/* ---- Form controls ---- */
[data-baseweb="select"] > div,
[data-baseweb="select"] > div > div,
[data-baseweb="select"] input,
[data-testid="stDateInput"] input,
[data-testid="stTimeInput"] input,
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea {
    background: #0f1f35 !important;
    color: #b0c8e4 !important;
    font-weight: 500 !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    font-size: 0.85rem !important;
}
[data-baseweb="select"] > div *,
[data-baseweb="select"] input::placeholder { color: #7a9bbf !important; }
[data-baseweb="select"] svg { color: #4e6a8a !important; fill: #4e6a8a !important; }

/* Dropdown menu */
[data-baseweb="popover"],
[data-baseweb="popover"] [data-baseweb="menu"],
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] ul,
[data-baseweb="menu"] [role="listbox"],
[data-baseweb="menu"] ul {
    background: #0f1f35 !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    box-shadow: 0 12px 32px rgba(0,0,0,0.55) !important;
}
[data-baseweb="popover"] [role="option"],
[data-baseweb="popover"] [role="option"] *,
[data-baseweb="popover"] li,
[data-baseweb="popover"] li *,
[data-baseweb="menu"] [role="option"],
[data-baseweb="menu"] [role="option"] *,
[data-baseweb="menu"] li,
[data-baseweb="menu"] li * {
    color: #b0c8e4 !important;
    font-weight: 500 !important;
    background-color: transparent !important;
    font-size: 0.85rem !important;
}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] li:hover,
[data-baseweb="menu"] [role="option"]:hover,
[data-baseweb="menu"] li:hover {
    background-color: rgba(0,200,150,0.10) !important;
}
[data-baseweb="popover"] [role="option"]:hover *,
[data-baseweb="popover"] li:hover *,
[data-baseweb="menu"] [role="option"]:hover *,
[data-baseweb="menu"] li:hover * { color: #00c896 !important; }
[data-baseweb="popover"] [role="option"][aria-selected="true"],
[data-baseweb="popover"] li[aria-selected="true"],
[data-baseweb="menu"] [role="option"][aria-selected="true"],
[data-baseweb="menu"] li[aria-selected="true"] {
    background-color: rgba(0,200,150,0.14) !important;
}
[data-baseweb="popover"] [role="option"][aria-selected="true"] *,
[data-baseweb="popover"] li[aria-selected="true"] *,
[data-baseweb="menu"] [role="option"][aria-selected="true"] *,
[data-baseweb="menu"] li[aria-selected="true"] * {
    color: #00c896 !important; font-weight: 700 !important;
}
[data-baseweb="tag"] {
    background: rgba(0,200,150,0.12) !important;
    color: #00c896 !important;
    border: 1px solid rgba(0,200,150,0.25) !important;
}
[data-baseweb="tag"] * { color: #00c896 !important; }
[data-baseweb="calendar"],
[data-baseweb="calendar"] * { color: #b0c8e4 !important; }
[data-baseweb="calendar"] { background: #0f1f35 !important; }

/* ---- Slider ---- */
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] + div,
[data-testid="stSlider"] [data-baseweb="tickmark"] {
    color: #00c896 !important; font-weight: 700 !important;
}

/* ---- Buttons ---- */
[data-testid="stDownloadButton"] button,
[data-testid="stButton"] button {
    background: rgba(0,200,150,0.08) !important;
    color: #00c896 !important;
    font-weight: 700 !important;
    border: 1px solid rgba(0,200,150,0.35) !important;
    box-shadow: none !important;
    font-size: 0.82rem !important;
    letter-spacing: 0.02em !important;
    border-radius: 4px !important;
    transition: all 0.12s ease !important;
}
[data-testid="stDownloadButton"] button:hover,
[data-testid="stButton"] button:hover {
    background: rgba(0,200,150,0.16) !important;
    border-color: #00c896 !important;
}
.stButton > button {
    border-radius: 4px !important; font-weight: 700; font-size: 0.82rem !important;
}

/* ---- Metric tiles (st.metric) ---- */
[data-testid="stMetric"] [data-testid="stMetricLabel"],
[data-testid="stMetric"] [data-testid="stMetricLabel"] * {
    color: #4e6a8a !important; font-weight: 600 !important;
    font-size: 0.72rem !important; letter-spacing: 0.06em !important; text-transform: uppercase !important;
}
[data-testid="stMetric"] [data-testid="stMetricValue"],
[data-testid="stMetric"] [data-testid="stMetricValue"] * {
    color: #e2eeff !important; font-weight: 700 !important;
    font-family: 'DM Mono', monospace !important;
}

/* ---- Inline style overrides ---- */
[data-testid="stMarkdownContainer"] div[style*="color:#475569"],
[data-testid="stMarkdownContainer"] div[style*="color: #475569"],
[data-testid="stMarkdownContainer"] div[style*="color:#64748b"],
[data-testid="stMarkdownContainer"] div[style*="color: #64748b"],
[data-testid="stMarkdownContainer"] div[style*="color:#0f172a"] {
    color: #7a9bbf !important;
}
[data-testid="stMarkdownContainer"] div[style*="color:#475569"] b,
[data-testid="stMarkdownContainer"] div[style*="color:#64748b"] b { color: #00c896 !important; }

/* ---- desktop font sizes ---- */
@media (min-width: 1100px) {
    html, body, [class*="css"] { font-size: 15px; }
}
@media (min-width: 1500px) {
    html, body, [class*="css"] { font-size: 16px; }
}

/* hide empty markdown wrappers */
.element-container:has(> .stMarkdown:only-child > [data-testid="stMarkdownContainer"]:empty) { display: none; }

/* ====================================================================
   COMMAND BAR (replaces .brand-bar)
   ==================================================================== */
@keyframes livePulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}
.cmd-bar {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 10px 16px;
    background: #0c1a2e;
    border: 1px solid rgba(0,200,150,0.18);
    border-radius: 6px;
    margin-bottom: 10px;
}
.cmd-bar-left { display: flex; align-items: center; gap: 10px; }
.cmd-bar-logo {
    width: 36px; height: 36px; flex: 0 0 36px;
    border-radius: 5px; border: 1px solid rgba(0,200,150,0.25);
    object-fit: contain; background: #081526; padding: 2px;
}
.cmd-bar-wordmark { display: flex; flex-direction: column; gap: 1px; }
.cmd-bar-name {
    font-size: 0.9rem; font-weight: 800; color: #eef4ff;
    letter-spacing: -0.01em; line-height: 1.1; font-family: 'DM Sans', sans-serif;
}
.cmd-bar-tag {
    font-size: 0.58rem; font-weight: 600; color: #00c896;
    letter-spacing: 0.14em; text-transform: uppercase;
}
.cmd-bar-center {
    display: flex; align-items: center; gap: 14px; flex: 1; justify-content: center;
}
.cmd-stat { display: flex; flex-direction: column; align-items: center; gap: 1px; }
.cmd-stat-val {
    font-size: 0.95rem; font-weight: 700; color: #eef4ff;
    font-family: 'DM Mono', monospace; line-height: 1;
}
.cmd-stat-label {
    font-size: 0.56rem; font-weight: 600; color: #3a5a7a;
    letter-spacing: 0.1em; text-transform: uppercase;
}
.cmd-divider { width: 1px; height: 26px; background: rgba(255,255,255,0.07); }
.cmd-bar-right { display: flex; flex-direction: column; align-items: flex-end; gap: 3px; }
.cmd-time { font-size: 0.7rem; font-weight: 500; color: #4e6a8a; font-family: 'DM Mono', monospace; }
.cmd-health { display: flex; align-items: center; gap: 5px; }
.cmd-health-dot { width: 5px; height: 5px; border-radius: 50%; background: #00c896; }
.cmd-health-dot.warn { background: #f59e0b; }
.cmd-health-dot.err  { background: #ef4444; }
.cmd-health-text { font-size: 0.64rem; font-weight: 600; color: #3a5a7a; letter-spacing: 0.04em; }
@media (max-width: 600px) {
    .cmd-bar-center { display: none; }
    .cmd-bar-name { font-size: 0.82rem; }
}

/* ====================================================================
   LIVE TICKER — compact dark strip
   ==================================================================== */
.live-ticker {
    background: #0c1a2e;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 5px;
    padding: 5px 10px; margin-bottom: 8px;
    overflow-x: auto; -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
}
.live-ticker::-webkit-scrollbar { display: none; }
.live-ticker .row { display: flex; align-items: center; gap: 8px; min-width: max-content; }
.live-ticker .label {
    color: #3a5a7a; font-size: 0.6rem; font-weight: 700;
    letter-spacing: 0.1em; text-transform: uppercase; padding-right: 8px;
    border-right: 1px solid rgba(255,255,255,0.07); flex-shrink: 0;
    font-family: 'DM Mono', monospace;
}
.live-ticker .game {
    display: inline-flex; align-items: center; gap: 4px;
    background: rgba(255,255,255,0.03); padding: 3px 8px;
    border-radius: 3px; color: #b0c8e4; font-weight: 600;
    font-size: 0.76rem; white-space: nowrap;
    border: 1px solid rgba(255,255,255,0.06); font-family: 'DM Mono', monospace;
}
.live-ticker .game .vs { color: #3a5a7a; }
.live-ticker .game .runs { color: #eef4ff; font-weight: 700; }
.live-ticker .game .inning { color: #3a5a7a; font-size: 0.65rem; margin-left: 3px; }
.live-ticker .game.final .runs { color: #7a9bbf; }
.live-ticker .game.final::before {
    content: "F"; background: rgba(245,158,11,0.18); color: #f59e0b;
    font-size: 0.56rem; padding: 1px 4px; border-radius: 2px;
    margin-right: 3px; font-weight: 700; letter-spacing: 0.04em;
}
.live-ticker .game.live::before {
    content: ""; width: 6px; height: 6px; border-radius: 50%;
    background: #ef4444; display: inline-block; margin-right: 3px;
    animation: livePulse 1.6s ease-in-out infinite;
}

/* ====================================================================
   GAME CAROUSEL — dark compact rail
   ==================================================================== */
.carousel-wrap {
    background: #0c1a2e;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 5px;
    padding: 6px 4px; margin-bottom: 10px;
}
.carousel-strip {
    display: flex; gap: 5px;
    overflow-x: auto; scroll-behavior: smooth;
    padding: 3px 8px 5px;
    scrollbar-width: thin;
    scrollbar-color: rgba(255,255,255,0.08) transparent;
}
.carousel-strip::-webkit-scrollbar { height: 3px; }
.carousel-strip::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 3px; }
.carousel-strip::-webkit-scrollbar-track { background: transparent; }
.game-pill {
    flex: 0 0 auto; min-width: 148px;
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.07);
    border-left: 2px solid transparent;
    border-radius: 4px; padding: 7px 9px;
    text-align: center; cursor: pointer;
    transition: all 0.1s ease; display: block;
    text-decoration: none !important; color: inherit !important; user-select: none;
}
.game-pill:hover {
    border-color: rgba(255,255,255,0.12);
    border-left-color: rgba(0,200,150,0.45);
    background: rgba(255,255,255,0.04);
}
.game-pill.active {
    border-color: rgba(0,200,150,0.25);
    border-left-color: #00c896;
    background: rgba(0,200,150,0.05);
}
.game-pill .logos { display: flex; align-items: center; justify-content: center; gap: 5px; margin-bottom: 3px; }
.game-pill .logos img { width: 26px; height: 26px; object-fit: contain; }
.game-pill .at { color: #3a5a7a; font-weight: 600; font-size: 0.82rem; }
.game-pill .matchup-text { display: block; color: #b0c8e4; font-weight: 700; font-size: 0.76rem; letter-spacing: 0.02em; }
.game-pill .time { display: block; color: #3a5a7a; font-size: 0.65rem; font-weight: 500; margin-top: 2px; font-family: 'DM Mono', monospace; }
.game-pill .score-line {
    display: flex; align-items: center; justify-content: center;
    gap: 5px; margin-top: 3px; font-weight: 700;
    font-size: 0.82rem; color: #b0c8e4; font-family: 'DM Mono', monospace;
}
.game-pill .score-line .sep { color: #3a5a7a; }
.game-pill .status-chip {
    display: inline-block; margin-top: 3px;
    padding: 1px 6px; border-radius: 3px;
    font-size: 0.58rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
}
.game-pill .status-chip.live {
    background: rgba(239,68,68,0.14); color: #f87171;
    border: 1px solid rgba(239,68,68,0.22);
}
.game-pill .status-chip.live::before {
    content: ""; display: inline-block;
    width: 5px; height: 5px; border-radius: 50%;
    background: #ef4444; margin-right: 4px; vertical-align: middle;
    animation: livePulse 1.6s ease-in-out infinite;
}
.game-pill .status-chip.final {
    background: rgba(90,120,156,0.12); color: #5a7a9c;
    border: 1px solid rgba(90,120,156,0.18);
}
.game-pill .status-chip.postponed {
    background: rgba(245,158,11,0.12); color: #f59e0b;
    border: 1px solid rgba(245,158,11,0.2);
}
@media (max-width: 640px) {
    .carousel-wrap { padding: 5px 4px; }
    .carousel-strip {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 5px; overflow: visible !important; padding: 3px;
    }
    .carousel-strip::-webkit-scrollbar { display: none; }
    .game-pill { min-width: 0; width: 100%; padding: 7px 5px; }
    .game-pill .logos img { width: 22px; height: 22px; }
    .game-pill .matchup-text { font-size: 0.72rem; }
    .game-pill .time { font-size: 0.62rem; }
}
@keyframes livePulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
}

/* ====================================================================
   GAME HEADER (box score card)
   ==================================================================== */
.gh-scorebox {
    margin-top: 10px; background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07); border-radius: 5px;
    padding: 8px 12px; color: #e2eeff;
}
.gh-scorebox .status-row {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px;
}
.gh-scorebox .status-pill {
    font-size: 0.6rem; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 3px;
}
.gh-scorebox .status-pill.live   { background: rgba(239,68,68,0.18); color: #f87171; }
.gh-scorebox .status-pill.final  { background: rgba(0,200,150,0.14); color: #00c896; }
.gh-scorebox .status-pill.postponed { background: rgba(245,158,11,0.14); color: #f59e0b; }
.gh-scorebox .status-pill.live::before {
    content: ""; display: inline-block;
    width: 6px; height: 6px; border-radius: 50%;
    background: #ef4444; margin-right: 5px; vertical-align: middle;
    animation: livePulse 1.6s ease-in-out infinite;
}
.gh-scorebox .status-meta { font-size: 0.7rem; color: #4e6a8a; font-weight: 500; }
.gh-scorebox table {
    width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums;
    color: #b0c8e4; font-family: 'DM Mono', monospace;
}
.gh-scorebox table th, .gh-scorebox table td {
    padding: 4px 6px; text-align: center; font-size: 0.74rem;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}
.gh-scorebox table th {
    color: #3a5a7a; font-size: 0.58rem; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase;
}
.gh-scorebox table td.team { text-align: left; font-weight: 700; white-space: nowrap; color: #e2eeff; }
.gh-scorebox table td.rhe { font-weight: 700; color: #00c896; }
.gh-scorebox table td.rhe.runs { font-size: 0.88rem; }
.gh-scorebox .scrollwrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.gh-scorebox .diamond {
    display: inline-flex; align-items: center; gap: 7px;
    margin-left: auto; font-size: 0.7rem; color: #4e6a8a; font-weight: 500;
}
.gh-scorebox .diamond .bases {
    display: inline-grid; grid-template-columns: 10px 10px 10px;
    grid-template-rows: 10px 10px; gap: 2px;
}
.gh-scorebox .diamond .base {
    width: 10px; height: 10px; transform: rotate(45deg);
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.10);
}
.gh-scorebox .diamond .base.on { background: #f59e0b; border-color: #f59e0b; }

.game-header {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 12px;
}
.game-header .matchup-display { display: flex; align-items: center; gap: 10px; }
.game-header .matchup-display img { width: 46px; height: 46px; object-fit: contain; }
.game-header .matchup-display .vs { color: #3a5a7a; font-weight: 600; font-size: 1.2rem; padding: 0 4px; }
.game-header .team-abbr { color: #eef4ff; font-size: 1.55rem; font-weight: 800; letter-spacing: 0.01em; }
.game-header .meta { color: #4e6a8a; font-size: 0.78rem; font-weight: 500; margin-top: 3px; }
.game-header .probables { text-align: right; color: #b0c8e4; font-size: 0.88rem; font-weight: 600; }
.game-header .probables .label {
    color: #3a5a7a; font-size: 0.6rem; letter-spacing: 0.1em; text-transform: uppercase; font-weight: 700;
}
.game-header .probables .hand { color: #4e6a8a; }
.kpi-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.kpi {
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.07);
    border-radius: 4px; padding: 5px 9px; color: #b0c8e4; font-size: 0.78rem; font-weight: 600;
}
.kpi .k {
    color: #3a5a7a; font-size: 0.56rem; letter-spacing: 0.1em; text-transform: uppercase;
    display: block; margin-bottom: 2px; font-weight: 600;
}

/* ====================================================================
   SECTION PANELS — all-dark surface system (NO white cards)
   ==================================================================== */
.section-card {
    background: #0c1a2e; border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px; padding: 14px 16px; margin-bottom: 10px;
}
.section-card.dark {
    background: #0c1a2e; border-color: rgba(255,255,255,0.07); color: #e2eeff;
}

/* Section header: thin teal accent bar + uppercase label */
.section-title, .section-title-lg {
    display: flex; align-items: center; gap: 10px;
    font-size: 0.68rem; font-weight: 700; color: #4e6a8a;
    letter-spacing: 0.12em; text-transform: uppercase;
    margin: 0 0 12px; padding: 0;
    text-shadow: none;
}
.section-title::before, .section-title-lg::before {
    content: ''; display: inline-block;
    width: 3px; height: 13px; border-radius: 2px;
    background: #00c896; flex-shrink: 0;
}
.section-title img, .section-title-lg img { width: 18px; height: 18px; }

/* ====================================================================
   TIER PILLS — sharp, semantic, minimal
   ==================================================================== */
.tier {
    display: inline-flex; align-items: center;
    padding: 2px 7px; border-radius: 3px;
    font-weight: 700; font-size: 0.62rem; letter-spacing: 0.06em; text-transform: uppercase;
    border: 1px solid transparent; font-family: 'DM Mono', monospace;
}
.tier-elite   { background: rgba(16,185,129,0.14); color: #10b981; border-color: rgba(16,185,129,0.28); }
.tier-strong  { background: rgba(0,200,150,0.11); color: #00c896; border-color: rgba(0,200,150,0.24); }
.tier-ok      { background: rgba(245,158,11,0.11); color: #f59e0b; border-color: rgba(245,158,11,0.24); }
.tier-avoid   { background: rgba(239,68,68,0.11); color: #ef4444; border-color: rgba(239,68,68,0.24); }
.tier-neutral { background: rgba(90,120,156,0.10); color: #5a7a9c; border-color: rgba(90,120,156,0.18); }

/* ====================================================================
   LINEUP BANNER — team header row for matchup board
   ==================================================================== */
.lineup-banner {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 12px;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    margin-top: 12px;
}
.lineup-banner img { width: 26px; height: 26px; }
.lineup-banner .lineup-title { font-weight: 700; font-size: 0.88rem; color: #e2eeff; }
.lineup-banner .vs-pitcher { color: #dbeafe; font-size: 0.8rem; font-weight: 800; margin-left: 5px; }
.lineup-banner .badge { margin-left: auto; }

/* ====================================================================
   DATAFRAME
   ==================================================================== */
[data-testid="stDataFrame"] {
    border-radius: 5px; overflow: hidden;
    border: 1px solid rgba(255,255,255,0.07);
}

/* ====================================================================
   FOOTER
   ==================================================================== */
.footer {
    margin-top: 14px; padding: 10px 14px; border-radius: 4px;
    background: rgba(255,255,255,0.02); color: #3a5a7a;
    font-size: 0.74rem; text-align: center; font-weight: 400;
    border: 1px solid rgba(255,255,255,0.04);
}

/* ====================================================================
   SCOUTING ROWS (replaces .mhm-card heat-map tile grid)
   ==================================================================== */
.scout-rows { display: block; margin: 0 0 12px 0; }
.scout-row {
    display: flex; align-items: center; gap: 0;
    background: #0a1628;
    border: 1px solid rgba(255,255,255,0.06);
    border-top: none;
    padding: 8px 11px;
    transition: background 0.1s ease;
    position: relative;
    border-left: 3px solid transparent;
}
.scout-row:last-child { border-radius: 0 0 5px 5px; }
.scout-row:hover {
    background: rgba(0,200,150,0.04);
    border-left-color: rgba(0,200,150,0.5);
}
.scout-row.tier-elite   { border-left-color: #10b981; }
.scout-row.tier-strong  { border-left-color: #00c896; }
.scout-row.tier-ok      { border-left-color: #f59e0b; }
.scout-row.tier-avoid   { border-left-color: #ef4444; }
.scout-spot {
    flex: 0 0 24px; width: 24px; height: 24px;
    display: flex; align-items: center; justify-content: center;
    background: rgba(255,255,255,0.04);
    border-radius: 3px;
    font-size: 0.68rem; font-weight: 700; color: #3a5a7a;
    font-family: 'DM Mono', monospace;
    margin-right: 9px; flex-shrink: 0;
}
.scout-player {
    flex: 0 0 160px; min-width: 0; margin-right: 10px; flex-shrink: 0;
}
@media (max-width: 640px) { .scout-player { flex-basis: 120px; } }
.scout-player-name {
    font-size: 0.95rem; font-weight: 800; color: #ffffff;
    display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-family: 'DM Sans', sans-serif; line-height: 1.2;
    text-shadow: 0 1px 3px rgba(0,0,0,0.5);
}
.scout-player-meta {
    font-size: 0.62rem; font-weight: 600; color: #7dd3fc;
    display: block; margin-top: 2px; letter-spacing: 0.02em;
}
.scout-hand-row {
    display:flex; flex-wrap:wrap; gap:4px; align-items:center;
    margin-top:4px;
}
.scout-hand-badge {
    display:inline-flex; align-items:center; justify-content:center;
    padding:2px 7px; border-radius:999px;
    background:#facc15; color:#0f172a !important;
    border:1px solid rgba(250,204,21,.75);
    font-size:.6rem; font-weight:900; letter-spacing:.06em;
    text-transform:uppercase; line-height:1.15;
    box-shadow:0 1px 3px rgba(0,0,0,.45);
}
.scout-vs-hand {
    display:inline-flex; align-items:center; justify-content:center;
    padding:2px 7px; border-radius:999px;
    background:rgba(125,211,252,.18); color:#e0f2fe !important;
    border:1px solid rgba(125,211,252,.45);
    font-size:.6rem; font-weight:900; letter-spacing:.04em;
    text-transform:uppercase; line-height:1.15;
}
.scout-player-crush {
    font-size: 0.6rem; font-weight: 600; color: #00c896;
    display: block; margin-top: 2px; line-height: 1.15;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.scout-player-form {
    font-size: 0.6rem; font-weight: 500; color: #7dd3fc;
    display: block; margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.scout-metrics {
    display: flex; align-items: center; gap: 3px;
    flex: 1 1 auto; overflow: hidden; margin-right: 9px;
}
.scout-metric {
    display: flex; flex-direction: column; align-items: center;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 3px; padding: 4px 6px; min-width: 48px; flex-shrink: 0;
}
.scout-metric-label {
    font-size: 0.54rem; font-weight: 700; color: #3a5a7a;
    letter-spacing: 0.08em; text-transform: uppercase;
    margin-bottom: 2px; line-height: 1; white-space: nowrap;
    font-family: 'DM Sans', sans-serif;
}
.scout-metric-val {
    font-size: 0.8rem; font-weight: 700; color: #b0c8e4; line-height: 1;
    font-family: 'DM Mono', monospace;
}
.scout-metric-bar {
    width: 100%; height: 2px; background: rgba(255,255,255,0.05);
    border-radius: 1px; margin-top: 3px; overflow: hidden;
}
.scout-metric-bar-fill { height: 100%; border-radius: 1px; }
.scout-outcome {
    flex: 0 0 auto; display: flex; flex-direction: column; align-items: flex-end;
    gap: 3px; margin-left: auto; flex-shrink: 0; min-width: 82px;
}
.scout-likely {
    font-size: 0.68rem; font-weight: 600; color: #8aaccc;
    text-align: right; line-height: 1.2; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; max-width: 110px;
}
.scout-likely-reason {
    font-size: 0.58rem; font-weight: 500; color: #3a5a7a;
    text-align: right; line-height: 1.2; max-width: 110px; white-space: normal;
}
.scout-matchup-score {
    font-size: 0.95rem; font-weight: 800; color: #eef4ff; line-height: 1;
    font-family: 'DM Mono', monospace;
}
.scout-matchup-label {
    font-size: 0.52rem; font-weight: 600; color: #3a5a7a;
    text-transform: uppercase; letter-spacing: 0.08em;
}
.scout-row-spacer { height: 8px; }
@media (max-width: 640px) {
    .scout-metrics {
        display: flex;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
        flex-wrap: nowrap;
        gap: 3px;
        padding-bottom: 2px;
    }
    .scout-metrics::-webkit-scrollbar { display: none; }
    .scout-metric { min-width: 42px; padding: 3px 5px; flex-shrink: 0; }
    .scout-metric-label { font-size: 0.48rem; }
    .scout-metric-val { font-size: 0.72rem; }
    .scout-row { padding: 7px 9px; flex-wrap: wrap; }
    .scout-player { flex-basis: auto; flex: 1 1 auto; }
    .scout-outcome { min-width: 65px; }
    .scout-likely { font-size: 0.62rem; max-width: 75px; }
}

/* ====================================================================
   SCOUT CTA (replaces gold .mhm-cta-pill)
   ==================================================================== */
.mhm-cards { display: block; margin: 0 0 12px 0; }
.mhm-card-spacer { height: 8px; }
.scout-cta-btn {
    display: flex; align-items: center; justify-content: center; gap: 6px;
    width: 100%; min-height: 38px; padding: 7px 14px; margin-top: -1px;
    background: rgba(0,200,150,0.07);
    color: #00c896 !important;
    border: 1px solid rgba(0,200,150,0.18);
    border-top: 1px dashed rgba(0,200,150,0.12);
    border-radius: 0 0 5px 5px;
    font-family: 'DM Mono', monospace;
    font-weight: 700; font-size: 0.68rem; line-height: 1.2;
    letter-spacing: 0.1em; text-transform: uppercase; text-align: center;
    white-space: nowrap; user-select: none; pointer-events: none;
}
.scout-cta-btn::after { content: " →"; font-weight: 400; }
.scout-cta-btn, .scout-cta-btn * { color: #00c896 !important; }
@media (max-width: 640px) { .scout-cta-btn { min-height: 42px; font-size: 0.64rem; } }
.mhm-cta-click { display:block; height:0; margin:0 !important; padding:0 !important; }

div[data-testid="stElementContainer"]:has(.mhm-cta-click) +
  div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) {
  margin-top:-38px !important; margin-bottom:10px !important;
  position:relative; z-index:5;
}
div[data-testid="element-container"]:has(.mhm-cta-click) +
  div[data-testid="element-container"]:has(div[data-testid="stButton"]) {
  margin-top:-38px !important; margin-bottom:10px !important;
  position:relative; z-index:5;
}
.mhm-cta-click + div[data-testid="stButton"] {
  margin-top:-38px !important; margin-bottom:10px !important;
  position:relative; z-index:5;
}
@media (max-width:640px) {
  div[data-testid="stElementContainer"]:has(.mhm-cta-click) +
    div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]),
  div[data-testid="element-container"]:has(.mhm-cta-click) +
    div[data-testid="element-container"]:has(div[data-testid="stButton"]),
  .mhm-cta-click + div[data-testid="stButton"] {
    margin-top:-42px !important;
  }
}
div[data-testid="stElementContainer"]:has(.mhm-cta-click) +
  div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) button,
div[data-testid="element-container"]:has(.mhm-cta-click) +
  div[data-testid="element-container"]:has(div[data-testid="stButton"]) button,
.mhm-cta-click + div[data-testid="stButton"] button {
  width:100% !important; min-height:38px !important;
  background: transparent !important; background-color: transparent !important;
  background-image: none !important; color: transparent !important;
  border: 0 !important; border-radius: 0 0 5px 5px !important;
  box-shadow: none !important; padding: 7px 14px !important;
  cursor: pointer !important; text-shadow: none !important; outline: none !important;
}
div[data-testid="stElementContainer"]:has(.mhm-cta-click) +
  div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) button *,
div[data-testid="element-container"]:has(.mhm-cta-click) +
  div[data-testid="element-container"]:has(div[data-testid="stButton"]) button *,
.mhm-cta-click + div[data-testid="stButton"] button * {
  color: transparent !important; background: transparent !important; text-shadow: none !important;
}
@media (max-width:640px) {
  div[data-testid="stElementContainer"]:has(.mhm-cta-click) +
    div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) button,
  div[data-testid="element-container"]:has(.mhm-cta-click) +
    div[data-testid="element-container"]:has(div[data-testid="stButton"]) button,
  .mhm-cta-click + div[data-testid="stButton"] button { min-height:42px !important; }
}

/* ====================================================================
   INSIGHTS PANEL (replaces Top 3 white cards)
   ==================================================================== */
.insights-panel {
    background: #0c1a2e; border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px; overflow: hidden; margin: 10px 0 12px;
}
.insights-panel-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 14px;
    background: rgba(0,200,150,0.05);
    border-bottom: 1px solid rgba(0,200,150,0.12);
}
.insights-panel-title {
    font-size: 0.62rem; font-weight: 700; color: #00c896;
    letter-spacing: 0.12em; text-transform: uppercase; font-family: 'DM Sans', sans-serif;
}
.insights-panel-sub { font-size: 0.6rem; font-weight: 600; color: #7dd3fc; }
.insight-row {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 14px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    transition: background 0.1s ease;
}
.insight-row:last-child { border-bottom: none; }
.insight-row:hover { background: rgba(0,200,150,0.03); }
.insight-rank {
    flex: 0 0 22px; width: 22px; height: 22px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.68rem; font-weight: 800; color: #5a7a9c;
    font-family: 'DM Mono', monospace;
}
.insight-rank.rank-1 { color: #10b981; }
.insight-rank.rank-2 { color: #00c896; }
.insight-rank.rank-3 { color: #5a7a9c; }
.insight-photo-wrap {
    position: relative; width: 38px; height: 38px; flex-shrink: 0;
}
.insight-photo {
    position: absolute; inset: 0;
    width: 38px; height: 38px; border-radius: 50%;
    object-fit: cover; background: transparent;
    border: 2px solid rgba(0,200,150,0.3);
}
.insight-photo-fallback {
    position: absolute; inset: 0;
    width: 38px; height: 38px; border-radius: 50%;
    background: linear-gradient(135deg, rgba(0,200,150,0.15), rgba(0,100,80,0.2));
    color: #00c896; display: flex; align-items: center; justify-content: center;
    font-weight: 900; font-size: 0.9rem; letter-spacing: -0.02em;
    border: 2px solid rgba(0,200,150,0.25);
}
.insight-player { flex: 0 0 150px; min-width: 0; }
.insight-player-name {
    font-size: 0.88rem; font-weight: 700; color: #eef4ff;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-family: 'DM Sans', sans-serif; line-height: 1.2;
}
.insight-player-meta { font-size: 0.62rem; font-weight: 600; color: #7dd3fc; margin-top: 2px; }
.insight-stats { display: flex; gap: 8px; flex: 1 1 auto; align-items: center; flex-wrap: wrap; }
.insight-stat { display: flex; flex-direction: column; min-width: 42px; }
.insight-stat-val {
    font-size: 0.9rem; font-weight: 700; color: #e2eeff; line-height: 1;
    font-family: 'DM Mono', monospace;
}
.insight-stat-label {
    font-size: 0.56rem; font-weight: 700; color: #94a3b8;
    text-transform: uppercase; letter-spacing: 0.06em; margin-top: 2px;
}
.insight-score-bar { flex: 1 1 auto; min-width: 55px; max-width: 110px; }
.insight-score-bar-track {
    height: 3px; background: rgba(255,255,255,0.05);
    border-radius: 2px; overflow: hidden;
}
.insight-score-bar-fill {
    height: 100%; border-radius: 2px;
    background: linear-gradient(90deg, #00c896, #10b981);
}
.insight-score-num {
    font-size: 0.68rem; font-weight: 700; color: #00c896; margin-top: 3px;
    font-family: 'DM Mono', monospace;
}
.insight-crush { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.insight-crush-chip {
    display: inline-flex; align-items: center; gap: 3px;
    background: rgba(245,158,11,0.09); color: #f59e0b;
    border: 1px solid rgba(245,158,11,0.18);
    border-radius: 3px; padding: 2px 6px;
    font-weight: 600; font-size: 0.62rem; white-space: nowrap;
}
@media (max-width: 640px) {
    .insight-player { flex-basis: 95px; }
    .insight-stats { gap: 5px; }
    .insight-score-bar-track { display: none; }
    .insight-score-num {
        font-size: 0.82rem; font-weight: 900; color: #00c896;
        background: rgba(0,200,150,0.1); border-radius: 4px;
        padding: 2px 6px; white-space: nowrap;
    }
    .insight-stat-val { font-size: 0.78rem; }
    .insight-player-meta { color: #7dd3fc; }
}

/* ====================================================================
   DARK MODE OVERRIDES
   ==================================================================== */
[data-theme="dark"] [data-testid="stMarkdownContainer"] p,
[data-theme="dark"] [data-testid="stMarkdownContainer"] li,
[data-theme="dark"] [data-testid="stMarkdownContainer"] span,
[data-theme="dark"] [data-testid="stCaptionContainer"],
[data-theme="dark"] [data-testid="stWidgetLabel"],
[data-theme="dark"] label { color: #7a9bbf !important; }
[data-theme="dark"] .section-title, [data-theme="dark"] .section-title-lg {
    color: #4e6a8a !important;
}
[data-theme="dark"] [style*="color:#475569"],
[data-theme="dark"] [style*="color: #475569"],
[data-theme="dark"] [style*="color:#64748b"],
[data-theme="dark"] [style*="color: #64748b"] { color: #4e6a8a !important; }
[data-theme="dark"] .footer {
    background: rgba(255,255,255,0.02) !important; color: #3a5a7a !important;
}

/* ====================================================================
   VIBRANT MRBETS850 BRAND THEME — white / purple / gold
   --------------------------------------------------------------------
   Kept as the final global override so existing layout + data logic stays
   untouched while every board/card gets a brighter, readable surface.
   ==================================================================== */
:root {
    --edge-bg: #fff8e7;
    --edge-surface: #ffffff;
    --edge-surface-2: #fffdf6;
    --edge-purple: #3b1f6b;
    --edge-purple-2: #5b21b6;
    --edge-purple-soft: #f3e8ff;
    --edge-gold: #facc15;
    --edge-gold-2: #f59e0b;
    --edge-ink: #1f123d;
    --edge-muted: #5b4b79;
    --edge-border: rgba(91,33,182,.22);
    --edge-shadow: 0 10px 28px rgba(59,31,107,.14);
}
html, body,
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stMain"],
[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
[data-testid="stBottom"],
section.main,
.main,
.stApp {
    background: var(--edge-bg) !important;
    background-color: var(--edge-bg) !important;
    color: var(--edge-ink) !important;
}
body::before {
    background: linear-gradient(135deg, #fff8e7 0%, #ffffff 48%, #f3e8ff 100%) !important;
}
body::after {
    background-image:
        radial-gradient(circle at 16px 16px, rgba(250,204,21,.18) 1px, transparent 1px),
        linear-gradient(rgba(91,33,182,.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(91,33,182,.04) 1px, transparent 1px) !important;
    background-size: 44px 44px, 44px 44px, 44px 44px !important;
}
html, body, [class*="css"],
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span,
[data-testid="stCaptionContainer"],
[data-testid="stWidgetLabel"],
label {
    color: var(--edge-ink) !important;
    font-weight: 700 !important;
}
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6,
.section-title,
.section-title-lg {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
    text-shadow: none !important;
}
[data-testid="stMarkdownContainer"] strong,
[data-testid="stMarkdownContainer"] b {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] *,
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] *,
[data-testid="stRadio"] label,
[data-testid="stRadio"] label *,
[data-testid="stMarkdownContainer"] > p,
[data-testid="stMarkdownContainer"] > p > * {
    color: var(--edge-muted) !important;
    font-weight: 800 !important;
}

/* Core app cards/boards */
.section-card,
.section-card.dark,
.lineup-banner,
.mhm-wrap,
.scout-row,
.insights-panel,
.cmd-bar,
.live-ticker,
.carousel-wrap,
.game-pill,
.gh-scorebox,
[data-testid="stExpander"],
[data-testid="stDataFrame"],
[data-testid="stTable"] {
    background: var(--edge-surface) !important;
    background-color: var(--edge-surface) !important;
    color: var(--edge-ink) !important;
    border-color: var(--edge-border) !important;
    box-shadow: var(--edge-shadow) !important;
}
.section-card *,
.section-card.dark *,
.lineup-banner *,
.mhm-wrap *,
.scout-row *,
.insights-panel *,
.cmd-bar *,
.live-ticker *,
.carousel-wrap *,
.game-pill *,
.gh-scorebox *,
[data-testid="stDataFrame"] *,
[data-testid="stTable"] * {
    color: var(--edge-ink);
}
.lineup-banner {
    border-radius: 14px 14px 0 0 !important;
    border: 2px solid var(--edge-purple) !important;
    border-bottom: 0 !important;
}
.lineup-banner .lineup-title,
.game-header .team-abbr,
.scout-player-name,
.insight-player-name,
.cmd-game,
.game-pill .matchup-text,
.gh-scorebox table td.team {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
.lineup-banner .vs-pitcher,
.game-header .probables,
.game-header .probables *,
.scout-player-meta,
.insight-player-meta,
.cmd-sub,
.game-header .meta,
.game-pill .time,
.gh-scorebox .status-meta {
    color: var(--edge-muted) !important;
    font-weight: 800 !important;
}
.scout-row {
    border: 2px solid var(--edge-border) !important;
    border-left: 6px solid var(--edge-purple) !important;
    border-radius: 14px !important;
    margin: 8px 0 !important;
}
.scout-row:hover {
    background: #fff7d6 !important;
    border-left-color: var(--edge-gold-2) !important;
}
.scout-spot,
.scout-metric,
.insight-stat,
.pdc-recap-tile {
    background: var(--edge-purple-soft) !important;
    color: var(--edge-purple) !important;
    border-color: rgba(91,33,182,.25) !important;
}
.scout-metric-label,
.scout-matchup-label,
.insight-stat-label {
    color: var(--edge-muted) !important;
    font-weight: 900 !important;
}
.scout-metric-val,
.scout-matchup-score,
.insight-stat-val,
.insight-score-num {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
.scout-hand-badge,
.pdc-hand-pill {
    background: var(--edge-gold) !important;
    color: #1f123d !important;
    border-color: var(--edge-gold-2) !important;
}
.scout-vs-hand,
.pdc-hand-pill.pitch {
    background: var(--edge-purple) !important;
    color: #ffffff !important;
    border-color: var(--edge-purple-2) !important;
}

/* Generic mobile/player cards and generator cards */
.mc-card,
.rbi-card,
.rbi-page-header,
.spd-card,
.spc-card,
.pbd-card,
.pbd-split-card,
.aip-card,
.aip-leg,
.rr-card,
.pws-card,
.pdc-card,
.pdc-next,
.pdc-hrdue,
.pdc-log-table,
.hrs-table,
.tg-table,
.sp-table,
.sp-wrap,
.hrs-wrap,
.tg-wrap {
    background: var(--edge-surface) !important;
    background-color: var(--edge-surface) !important;
    color: var(--edge-ink) !important;
    border-color: var(--edge-border) !important;
    box-shadow: var(--edge-shadow) !important;
}
.mc-card *,
.spd-card *,
.spc-card *,
.pbd-card *,
.pbd-split-card *,
.aip-card *,
.rr-card *,
.pws-card *,
.pdc-card *,
.pdc-next *,
.pdc-hrdue *,
.pdc-log-table *,
.hrs-table *,
.tg-table *,
.sp-table * {
    color: var(--edge-ink);
}
.mc-name,
.rbi-name,
.rbi-page-title,
.rbi-page-eyebrow,
.spd-name,
.spc-name,
.pbd-title,
.pbd-id-name,
.aip-leg-name,
.aip-card-title,
.rr-name,
.pws-matchup,
.pws-pitcher,
.pdc-name,
.hrs-name,
.tg-name,
.sp-pitcher {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
.mc-sub,
.mc-foot,
.rbi-sub,
.rbi-foot,
.rbi-page-sub,
.rbi-empty,
.spd-sub,
.spc-meta,
.pbd-subtitle,
.pbd-id-matchup,
.aip-meta,
.aip-ctx,
.rr-meta,
.pdc-meta,
.pdc-empty,
.hrs-meta,
.tg-meta {
    color: var(--edge-muted) !important;
    font-weight: 800 !important;
}
.mc-chip,
.rbi-chip,
.spd-chip,
.pbd-kpi,
.pbd-split-grid > div,
.aip-stats,
.rbi-card,
.tg-park,
.sp-src {
    background: var(--edge-purple-soft) !important;
    border-color: rgba(91,33,182,.24) !important;
    color: var(--edge-ink) !important;
}
.mc-chip-label,
.rbi-chip-label,
.spd-chip-label,
.pbd-kpi-label,
.pbd-split-grid span,
.pdc-chip .lab {
    color: var(--edge-muted) !important;
    font-weight: 900 !important;
}
.mc-chip-val,
.rbi-chip-val,
.spd-chip-val,
.pbd-kpi-value,
.pbd-split-grid b,
.pdc-chip .val {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
.mc-score,
.rbi-score,
.spd-score,
.spc-bigscore .val,
.aip-score,
.rr-score,
.pbd-rankbadge,
.pdc-grade {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
.rbi-tier,
.mc-tier,
.spd-tier,
.pbd-badge,
.hrs-pill,
.tg-pill {
    background: #fff7d6 !important;
    color: var(--edge-purple) !important;
    border-color: var(--edge-gold-2) !important;
    font-weight: 900 !important;
}

/* Tables, form controls, tabs, and buttons */
.stTabs [data-baseweb="tab-list"] {
    border-bottom-color: rgba(91,33,182,.25) !important;
}
.stTabs [data-baseweb="tab"],
.stTabs [data-baseweb="tab"] * {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"],
.stTabs [data-baseweb="tab"][aria-selected="true"] * {
    color: #1f123d !important;
}
.stTabs [data-baseweb="tab-highlight"] {
    background: var(--edge-gold) !important;
    height: 4px !important;
}
[data-baseweb="select"] > div,
[data-baseweb="select"] > div > div,
[data-baseweb="select"] input,
[data-testid="stDateInput"] input,
[data-testid="stTimeInput"] input,
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea {
    background: #ffffff !important;
    color: var(--edge-ink) !important;
    border: 2px solid rgba(91,33,182,.28) !important;
    font-weight: 800 !important;
}
[data-baseweb="popover"],
[data-baseweb="popover"] [data-baseweb="menu"],
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] ul,
[data-baseweb="menu"] [role="listbox"],
[data-baseweb="menu"] ul,
[data-baseweb="calendar"] {
    background: #ffffff !important;
    border: 2px solid rgba(91,33,182,.24) !important;
    box-shadow: var(--edge-shadow) !important;
}
[data-baseweb="popover"] [role="option"],
[data-baseweb="popover"] [role="option"] *,
[data-baseweb="popover"] li,
[data-baseweb="popover"] li *,
[data-baseweb="menu"] [role="option"],
[data-baseweb="menu"] [role="option"] *,
[data-baseweb="menu"] li,
[data-baseweb="menu"] li *,
[data-baseweb="calendar"],
[data-baseweb="calendar"] * {
    color: var(--edge-ink) !important;
    font-weight: 800 !important;
}
[data-testid="stDownloadButton"] button,
[data-testid="stButton"] button {
    background: linear-gradient(135deg, var(--edge-purple) 0%, var(--edge-purple-2) 100%) !important;
    color: #ffffff !important;
    border: 2px solid var(--edge-gold) !important;
    border-radius: 999px !important;
    font-weight: 900 !important;
    box-shadow: 0 6px 14px rgba(59,31,107,.22) !important;
}
[data-testid="stDownloadButton"] button *,
[data-testid="stButton"] button * {
    color: #ffffff !important;
    font-weight: 900 !important;
}
[data-testid="stDownloadButton"] button:hover,
[data-testid="stButton"] button:hover {
    background: linear-gradient(135deg, var(--edge-gold) 0%, var(--edge-gold-2) 100%) !important;
    color: #1f123d !important;
    border-color: var(--edge-purple) !important;
}

/* Apps & Generators category tiles — override their later dark carousel CSS. */
.top-tab-row {
    background: #ffffff !important;
    border: 2px solid var(--edge-purple) !important;
    box-shadow: 0 10px 28px rgba(59,31,107,.16) !important;
    animation: none !important;
}
.top-tab-row::before {
    background-image: radial-gradient(circle, rgba(250,204,21,.22) 1px, transparent 1px) !important;
}
.apps-gen-title {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
    text-shadow: none !important;
}
.apps-gen-sub {
    color: var(--edge-muted) !important;
    font-weight: 800 !important;
}
.top-tab-pill,
.top-tab-pill:link,
.top-tab-pill:visited,
.top-tab-pill *,
.top-tab-pill a,
.top-tab-pill a:link,
.top-tab-pill a:visited {
    background: var(--edge-purple-soft) !important;
    color: var(--edge-purple) !important;
    border: 2px solid rgba(91,33,182,.25) !important;
    font-weight: 900 !important;
    text-decoration: none !important;
    text-shadow: none !important;
}
.top-tab-pill:hover,
.top-tab-pill:hover * {
    background: #fff7d6 !important;
    color: #1f123d !important;
    border-color: var(--edge-gold-2) !important;
}
.top-tab-pill.active,
.top-tab-pill.active *,
.top-tab-pill.active:link,
.top-tab-pill.active:visited {
    background: linear-gradient(135deg, var(--edge-purple) 0%, var(--edge-purple-2) 100%) !important;
    color: #ffffff !important;
    border-color: var(--edge-gold) !important;
    box-shadow: 0 8px 18px rgba(59,31,107,.24) !important;
}

/* Streamlit metric output — fix washed-out white values on the light theme. */
[data-testid="stMetric"] {
    background: #ffffff !important;
    border: 1px solid rgba(91,33,182,.18) !important;
    border-radius: 14px !important;
    padding: 8px 10px !important;
    box-shadow: 0 6px 16px rgba(59,31,107,.10) !important;
}
[data-testid="stMetric"] [data-testid="stMetricLabel"],
[data-testid="stMetric"] [data-testid="stMetricLabel"] *,
[data-testid="stMetric"] [data-testid="stMetricDelta"],
[data-testid="stMetric"] [data-testid="stMetricDelta"] * {
    color: var(--edge-muted) !important;
    font-weight: 900 !important;
}
[data-testid="stMetric"] [data-testid="stMetricValue"],
[data-testid="stMetric"] [data-testid="stMetricValue"] * {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
    text-shadow: none !important;
}

/* Links inside generated cards/boards should never render default pale blue. */
[data-testid="stMarkdownContainer"] a,
[data-testid="stMarkdownContainer"] a *,
.mc-card a, .mc-card a *,
.aip-card a, .aip-card a *,
.rr-card a, .rr-card a *,
.pws-card a, .pws-card a *,
.pbd-card a, .pbd-card a *,
.section-card a, .section-card a * {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
    text-decoration-color: var(--edge-gold-2) !important;
    text-decoration-thickness: 2px !important;
    text-underline-offset: 3px !important;
}

/* Final text safety net for card descendants, including late-injected CSS. */
.mc-card :is(div,span,p,b,strong,small),
.rbi-card :is(div,span,p,b,strong,small),
.rbi-page-header :is(div,span,p,b,strong,small),
.spd-card :is(div,span,p,b,strong,small),
.spc-card :is(div,span,p,b,strong,small),
.pbd-card :is(div,span,p,b,strong,small),
.aip-card :is(div,span,p,b,strong,small,li),
.rr-card :is(div,span,p,b,strong,small,li),
.pws-card :is(div,span,p,b,strong,small),
.section-card :is(div,span,p,b,strong,small),
.scout-row :is(div,span,p,b,strong,small),
.insights-panel :is(div,span,p,b,strong,small) {
    text-shadow: none !important;
}

/* Override common inline dark/muted colors produced by existing renderers. */
[style*="background:#0a1628"],
[style*="background: #0a1628"],
[style*="background:#0b1220"],
[style*="background: #0b1220"],
[style*="background:#0c1a2e"],
[style*="background: #0c1a2e"],
[style*="background:#111827"],
[style*="background: #111827"],
[style*="background:#15102b"],
[style*="background: #15102b"],
[style*="background:#1c1340"],
[style*="background: #1c1340"] {
    background: var(--edge-surface) !important;
    background-color: var(--edge-surface) !important;
}
[style*="color:#475569"],
[style*="color: #475569"],
[style*="color:#64748b"],
[style*="color: #64748b"],
[style*="color:#4e6a8a"],
[style*="color: #4e6a8a"],
[style*="color:#3a5a7a"],
[style*="color: #3a5a7a"],
[style*="color:#94a3b8"],
[style*="color: #94a3b8"],
[style*="color:#a3a0c4"],
[style*="color: #a3a0c4"],
[style*="color:#b0c8e4"],
[style*="color: #b0c8e4"],
[style*="color:#e2e8f0"],
[style*="color: #e2e8f0"] {
    color: var(--edge-ink) !important;
    font-weight: 800 !important;
}
[style*="color:#7dd3fc"],
[style*="color: #7dd3fc"],
[style*="color:#00c896"],
[style*="color: #00c896"] {
    color: var(--edge-purple) !important;
    font-weight: 900 !important;
}
</style>
""", unsafe_allow_html=True)

# ===========================================================================
# Helpers
# ===========================================================================
def clean_name(name):
    if pd.isna(name): return ""
    txt = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    txt = re.sub(r"[^A-Za-z0-9 ]+", "", txt).lower().strip()
    return re.sub(r"\s+", " ", txt)

def norm_team(team):
    if pd.isna(team): return ""
    t = str(team).strip().upper()
    return TEAM_FIXES.get(t, t)

def safe_float(x, default=0.0):
    try:
        if pd.isna(x): return default
        return float(x)
    except Exception:
        return default

def _flip_last_first(name):
    if pd.isna(name): return ""
    s = str(name).strip()
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[1]} {parts[0]}"
    return s

# ===========================================================================
# Data loading
# ===========================================================================
@st.cache_data(ttl=1800, show_spinner=False)
def load_remote_csv(url, optional: bool = False):
    """Fetch a Savant CSV via raw GitHub.

    Returns (df, last_modified_utc). last_modified_utc is the GitHub
    object's Last-Modified header parsed to a UTC timestamp, or None
    if not available. We HEAD the URL first (cheap) so we know exactly
    how stale the underlying file in the repo is — independent of our
    in-process cache TTL.

    When ``optional`` is True a failed fetch returns an empty frame
    silently — used for prior-season fallback files that may not yet be
    committed."""
    last_modified = None
    try:
        # Try a HEAD request to capture the file's actual last-modified time.
        # raw.githubusercontent.com returns Last-Modified for committed files.
        head = requests.head(url, headers=HEADERS, timeout=15, allow_redirects=True)
        lm = head.headers.get("Last-Modified") or head.headers.get("last-modified")
        if lm:
            try:
                last_modified = pd.to_datetime(lm, utc=True).tz_convert(None)
            except Exception:
                last_modified = None
    except Exception:
        last_modified = None

    try:
        df = pd.read_csv(url)
    except Exception as exc:
        if not optional:
            st.warning(f"CSV load failed: {url} :: {exc}")
        return pd.DataFrame(), last_modified
    if df.empty:
        return df, last_modified
    name_col = None
    for c in df.columns:
        if str(c).strip().lower() in ("last_name, first_name", "player_name", "name", "player"):
            name_col = c; break
    if name_col is not None:
        df[name_col] = df[name_col].apply(_flip_last_first)
        if name_col != "Name":
            df = df.rename(columns={name_col: "Name"})
    return df, last_modified

@st.cache_data(ttl=1800, show_spinner=False)
def load_all_csvs():
    """Load every Savant CSV and register source freshness for each."""
    out = {}
    SOURCE_LABEL = {
        "batters":           "Baseball Savant · Batter Statcast leaderboard",
        "batters_all":       "Baseball Savant · Batter Statcast leaderboard (low-PA backfill)",
        "batters_prev":      "Baseball Savant · Batter Statcast leaderboard (prior-season fallback)",
        "pitchers":          "Baseball Savant · Pitcher Statcast leaderboard",
        "pitcher_stats":     "Baseball Savant · Pitcher results leaderboard",
        "bat_tracking":      "Baseball Savant · Bat-tracking leaderboard",
        "bat_tracking_prev": "Baseball Savant · Bat-tracking leaderboard (prior-season fallback)",
    }
    # Savant CSVs are refreshed nightly via GitHub Actions, so a 36-hr
    # cushion accounts for in-day Savant publishing latency without
    # flagging a healthy file as stale.
    for label, url in CSV_URLS.items():
        is_optional = label in OPTIONAL_CSVS
        df, last_mod = load_remote_csv(url, optional=is_optional)
        out[label] = df
        key = f"savant:{label}"
        if df is None or df.empty:
            # Optional fallbacks log as "missing" rather than "error" — their
            # absence is expected when the prior-season refresh hasn't run
            # yet. Downstream consumers already treat them as opt-in.
            record_source(
                key,
                label=SOURCE_LABEL.get(label, f"Savant · {label}"),
                url=url,
                status="missing" if is_optional else "error",
                detail=(
                    "Optional fallback CSV not present yet."
                    if is_optional
                    else "Empty CSV — Savant may have rate-limited the nightly refresh."
                ),
                max_age_min=36 * 60,
                fetched_at=last_mod,
            )
            continue
        status = "live"
        detail = f"{len(df):,} rows"
        if last_mod is not None:
            age_min = (_utc_now() - last_mod).total_seconds() / 60.0
            if age_min > 36 * 60:
                status = "stale"
                detail += f" · file last updated {fmt_age(age_min)}"
        record_source(
            key,
            label=SOURCE_LABEL.get(label, f"Savant · {label}"),
            url=url, status=status, detail=detail,
            max_age_min=36 * 60,
            fetched_at=last_mod,
        )
    return out

def standardize_columns(df):
    """Map raw Savant columns to canonical names. Keeps original columns too."""
    if df.empty: return df
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lower_map = {c.lower(): c for c in df.columns}

    if "name" not in [c.lower() for c in df.columns]:
        for k in ("player_name", "player"):
            if k in lower_map:
                df.rename(columns={lower_map[k]: "Name"}, inplace=True); break
    elif "Name" not in df.columns and "name" in lower_map:
        df.rename(columns={lower_map["name"]: "Name"}, inplace=True)

    for col in ("team", "team_abbr", "team_name"):
        if col in lower_map:
            df.rename(columns={lower_map[col]: "Team"}, inplace=True); break

    rename_map = {
        "home_run": "HR", "home_runs": "HR", "hr": "HR",
        "batting_avg": "AVG", "avg": "AVG",
        "on_base_percent": "OBP", "obp": "OBP",
        "slg_percent": "SLG", "slg": "SLG",
        "on_base_plus_slg": "OPS", "ops": "OPS",
        "isolated_power": "ISO", "iso": "ISO",
        "xiso": "xISO",
        "woba": "wOBA", "xwoba": "xwOBA",
        "xobp": "xOBP", "xslg": "xSLG", "xba": "xBA",
        "barrel_batted_rate": "Barrel%", "barrels_per_bbe_percent": "Barrel%",
        "hard_hit_percent": "HardHit%", "hard_hit_rate": "HardHit%",
        "exit_velocity_avg": "EV", "avg_hit_speed": "EV", "avg_best_speed": "EV",
        "launch_angle_avg": "LA", "launch_angle": "LA",
        "k_percent": "K%", "bb_percent": "BB%",
        "sweet_spot_percent": "SweetSpot%",
        "whiff_percent": "Whiff%", "swing_percent": "Swing%",
        "pull_percent": "Pull%", "opposite_percent": "Oppo%",
        "groundballs_percent": "GB%", "flyballs_percent": "FB%",
        "linedrives_percent": "LD%",
        # Bat-tracking metrics. The custom batter leaderboard does not include
        # these; they come from a separate Savant bat-tracking CSV that is
        # merged into batters_df by player_id at app startup.
        "avg_swing_speed": "BatSpeed",
        "avg_bat_speed": "BatSpeed",
        "p_throws": "pitch_hand", "throws": "pitch_hand",
        "stand": "bat_side", "bats": "bat_side",
    }
    for raw, final in rename_map.items():
        if raw in lower_map and final not in df.columns:
            df.rename(columns={lower_map[raw]: final}, inplace=True)

    if "Name" not in df.columns: df["Name"] = ""
    if "Team" not in df.columns: df["Team"] = ""
    df["name_key"] = df["Name"].apply(clean_name)
    df["team_key"] = df["Team"].apply(norm_team)

    # OPS is a first-class batter metric across the app. Source CSVs use a
    # variety of column names (on_base_plus_slg, ops, OPS) — those are
    # already remapped above. When the source omits a precomputed OPS but
    # OBP and SLG are present, derive it so every downstream view (matchup
    # heat maps, player cards, scoring rationale) can rely on a single
    # canonical "OPS" column. Missing OBP or SLG → leave OPS NaN; callers
    # fall back to league average rather than fabricating a value.
    if "OPS" not in df.columns and {"OBP", "SLG"}.issubset(df.columns):
        _obp = pd.to_numeric(df["OBP"], errors="coerce")
        _slg = pd.to_numeric(df["SLG"], errors="coerce")
        df["OPS"] = _obp + _slg
    elif "OPS" in df.columns and {"OBP", "SLG"}.issubset(df.columns):
        # Fill *missing* OPS cells from OBP+SLG without overwriting existing
        # source values (some feeds publish OPS only for qualified hitters).
        _ops = pd.to_numeric(df["OPS"], errors="coerce")
        _obp = pd.to_numeric(df["OBP"], errors="coerce")
        _slg = pd.to_numeric(df["SLG"], errors="coerce")
        df["OPS"] = _ops.fillna(_obp + _slg)
    return df


# OPS appears on every batter-facing card / table / generator in the app. These
# helpers centralize the read-with-OBP+SLG-fallback and the 3-decimal display
# format so each view doesn't reimplement the same coalescing logic.
def get_ops_value(row):
    """Return float OPS for a row (Series or dict-like), falling back to
    OBP + SLG when the precomputed OPS is missing. Returns None when neither
    source is available, so callers can render an em-dash instead of crashing.
    """
    if row is None:
        return None
    try:
        v = row.get("OPS") if hasattr(row, "get") else row["OPS"] if "OPS" in row else None
    except Exception:
        v = None
    try:
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            return float(v)
    except Exception:
        pass
    try:
        obp = row.get("OBP") if hasattr(row, "get") else None
        slg = row.get("SLG") if hasattr(row, "get") else None
        if obp is None or slg is None:
            return None
        obp_f = float(obp); slg_f = float(slg)
        if pd.isna(obp_f) or pd.isna(slg_f):
            return None
        return obp_f + slg_f
    except Exception:
        return None


def fmt_ops(v, dash="—"):
    """Format an OPS value to 3 decimals; returns the dash placeholder when
    the value is missing or non-numeric."""
    try:
        if v is None:
            return dash
        f = float(v)
        if pd.isna(f):
            return dash
        return f"{f:.3f}"
    except Exception:
        return dash


# Schedule TTL is short (60s) for live-betting freshness — once a game is
# live, downstream consumers route through ``apply_live_pitcher_overlay``
# for the current pitcher, but the schedule itself can still change
# (probable announcements, postponements, lineup hydrate). A 60s TTL keeps
# the StatsAPI footprint negligible — one request per minute per active
# user session — while ensuring no live data is cut off behind a stale
# 30-minute cache.
@st.cache_data(ttl=60)
def get_schedule(selected_date):
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {"sportId": 1, "date": selected_date.strftime("%Y-%m-%d"),
              "hydrate": "probablePitcher,team"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        record_source("statsapi:schedule",
                      label="MLB StatsAPI · Schedule + probable pitchers",
                      url=url, status="error",
                      detail=f"Schedule fetch failed: {exc}",
                      max_age_min=30)
        raise
    rows = []
    for d in data.get("dates", []):
        for game in d.get("games", []):
            away_name = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
            home_name = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
            away_info = TEAM_INFO.get(away_name, {"abbr": away_name[:3].upper(), "id": 0, "lat": None, "lon": None})
            home_info = TEAM_INFO.get(home_name, {"abbr": home_name[:3].upper(), "id": 0, "lat": None, "lon": None})
            game_time_utc = game.get("gameDate")
            game_time_ct = pd.to_datetime(game_time_utc, utc=True).tz_convert("America/Chicago")
            # MLB's authoritative day/night classification — matches the bucket
            # used by statSplits sitCodes=d/n, so the Day vs Night HR tab can
            # filter today's slate without re-classifying by local hour.
            day_night = (game.get("dayNight") or "").strip().lower()
            rows.append({
                "game_pk": game.get("gamePk"),
                "label": f'{away_info["abbr"]} @ {home_info["abbr"]} · {game_time_ct.strftime("%-I:%M %p")}',
                "short_label": f'{away_info["abbr"]} @ {home_info["abbr"]}',
                "time_short": game_time_ct.strftime("%-I:%M %p"),
                "game_time_ct": game_time_ct.strftime("%a %b %-d · %-I:%M %p CT"),
                "game_time_utc": game_time_utc,
                "day_night": day_night if day_night in ("day", "night") else "",
                "status": game.get("status", {}).get("detailedState"),
                "away_team": away_name, "away_abbr": away_info["abbr"], "away_id": away_info["id"],
                "home_team": home_name, "home_abbr": home_info["abbr"], "home_id": home_info["id"],
                "venue": game.get("venue", {}).get("name", "Unknown"),
                "venue_id": game.get("venue", {}).get("id"),
                "away_probable": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "home_probable": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "away_probable_id": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id"),
                "home_probable_id": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id"),
                "lat": home_info["lat"], "lon": home_info["lon"],
                "park_factor": DEFAULT_PARK_FACTORS.get(home_info["abbr"], 100),
            })
    # Count probable pitchers posted vs TBD so we can flag a slate where
    # MLB hasn't published probables yet.
    n_games = len(rows)
    n_probable = sum(
        1 for row in rows
        if (row["away_probable"] not in ("", "TBD") and row["home_probable"] not in ("", "TBD"))
    )
    record_source(
        "statsapi:schedule",
        label="MLB StatsAPI · Schedule + probable pitchers",
        url=url, status="live",
        detail=f"{n_games} games · {n_probable} have both probables posted",
        max_age_min=30,
    )
    return pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# RotoGrinders MLB weather (preferred, free, no credentials)
# ---------------------------------------------------------------------------
# Public page: https://rotogrinders.com/weather/mlb
# Server-rendered HTML — no API key, no JSON endpoint.
# We parse each game module:
#   <div class="module">
#     <div class="team-nameplate"><span data-abbr="TOR">...</span></div>  (away)
#     <div class="team-nameplate"><span data-abbr="MIN">...</span></div>  (home)
#     <span class="weather-gametime-value">65°</span> (temp)
#     <span class="weather-gametime-value">0%</span>  (precip)
#     <span class="weather-gametime-value">NW</span>  (wind dir compass)
#     <span class="weather-gametime-value">12</span>  (wind mph)
#   Domes render <div class="weather-column-empty"><p>This game is played in a dome.</p></div>
#
# RG uses a few abbreviations that diverge from MLB StatsAPI (TBR/TB,
# SFG/SF, KCR/KC, WSH/WAS, CHW/CWS, ...). We normalize both sides to a
# canonical key before matching.
RG_WEATHER_URL = "https://rotogrinders.com/weather/mlb"

# Compass label -> degrees that the wind is BLOWING FROM (matches Open-Meteo
# convention used by _wind_component_out).
_RG_COMPASS_DEG = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
    "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
    "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
    "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}

def _rg_norm_abbr(abbr: str) -> str:
    """Canonicalize team abbreviations so RG's `TBR` matches StatsAPI's `TB`."""
    if not abbr:
        return ""
    a = abbr.strip().upper()
    return {
        "TBR": "TB", "TBA": "TB",
        "SFG": "SF", "SFO": "SF",
        "KCR": "KC", "KCA": "KC",
        "WSH": "WAS", "WSN": "WAS",
        "CHW": "CWS", "CWX": "CWS",
        "SDP": "SD",
        "AZ":  "ARI",
    }.get(a, a)

@st.cache_data(ttl=1800)
def get_rotogrinders_weather():
    """Fetch and parse RotoGrinders' free public MLB weather page.
    Returns a dict keyed by (away_abbr, home_abbr) of normalized weather
    payloads compatible with the Open-Meteo schema used downstream:
      {temp_f, wind_mph, wind_dir_deg, rain_pct, dew_f, cloud_pct,
       sky, dome, source, source_label, source_url, away_abbr, home_abbr}
    Empty dict if the page can't be fetched or parsed — callers must treat
    a missing key as "RG had no data for this game" and fall back to
    Open-Meteo. We never raise out of this function."""
    games: dict = {}
    try:
        from bs4 import BeautifulSoup
        r = requests.get(RG_WEATHER_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        record_source("rotogrinders:weather",
                      label="RotoGrinders · MLB weather",
                      url=RG_WEATHER_URL, status="error",
                      detail=f"Fetch failed: {exc}",
                      max_age_min=180)
        return games

    parsed = 0
    for module in soup.select("div.module"):
        nameplates = module.select("div.team-nameplate span.team-nameplate-title")
        if len(nameplates) < 2:
            continue
        away_abbr = _rg_norm_abbr(nameplates[0].get("data-abbr", ""))
        home_abbr = _rg_norm_abbr(nameplates[1].get("data-abbr", ""))
        if not away_abbr or not home_abbr:
            continue

        body = module.select_one("div.module-body")
        if body is None:
            continue

        # Dome / roof closed: no temp/wind data in markup
        empty = body.select_one("div.weather-column-empty")
        dome = empty is not None and "dome" in empty.get_text(" ", strip=True).lower()
        venue_label = ""
        ven = module.select_one(".game-weather-stadium")
        if ven:
            venue_label = ven.get_text(" ", strip=True).replace("AT ", "").title()

        payload = {
            "temp_f": None, "wind_mph": None, "wind_dir_deg": None,
            "rain_pct": None, "dew_f": None, "cloud_pct": None,
            "sky": None, "dome": dome,
            "away_abbr": away_abbr, "home_abbr": home_abbr,
            "venue": venue_label,
            "source": "rotogrinders",
            "source_label": "RotoGrinders",
            "source_url": RG_WEATHER_URL,
        }

        if dome:
            # Roof / dome — no outdoor weather affects play. Mark as roofed
            # but leave numeric fields None so compute_weather_impact treats
            # the park as domed via DOMED_PARKS or via temp=None defaults.
            payload["sky"] = "Roof / Dome"
            games[(away_abbr, home_abbr)] = payload
            parsed += 1
            continue

        # Game-time summary chips: temp, precip%, wind dir compass, wind mph
        sets = body.select("div.weather-gametime-set")
        if len(sets) >= 1:
            vals = sets[0].select("span.weather-gametime-value")
            # vals[0] = "65°", vals[1] = "0%"
            if len(vals) >= 1:
                m = re.search(r"(-?\d+(?:\.\d+)?)", vals[0].get_text())
                if m:
                    try: payload["temp_f"] = float(m.group(1))
                    except Exception: pass
            if len(vals) >= 2:
                m = re.search(r"(\d+(?:\.\d+)?)", vals[1].get_text())
                if m:
                    try: payload["rain_pct"] = float(m.group(1))
                    except Exception: pass
        if len(sets) >= 2:
            vals = sets[1].select("span.weather-gametime-value")
            # vals[0] = "NW" (compass), vals[1] = "12" (mph)
            if len(vals) >= 1:
                compass = vals[0].get_text(strip=True).upper()
                payload["wind_dir_deg"] = _RG_COMPASS_DEG.get(compass)
                payload["wind_compass"] = compass
            if len(vals) >= 2:
                m = re.search(r"(\d+(?:\.\d+)?)", vals[1].get_text())
                if m:
                    try: payload["wind_mph"] = float(m.group(1))
                    except Exception: pass

        # Sky from the icon on the first set (sunny/cloudy/rainy class)
        icon = body.select_one("span.weather-gametime-icon i")
        if icon is not None:
            cls = " ".join(icon.get("class", [])).lower()
            if   "rain"  in cls: payload["sky"] = "Rain risk"
            elif "snow"  in cls: payload["sky"] = "Snow risk"
            elif "cloud" in cls or "overcast" in cls: payload["sky"] = "Overcast"
            elif "partly"in cls: payload["sky"] = "Partly cloudy"
            elif "sunny" in cls or "clear" in cls: payload["sky"] = "Clear"

        games[(away_abbr, home_abbr)] = payload
        parsed += 1

    if parsed == 0:
        record_source("rotogrinders:weather",
                      label="RotoGrinders · MLB weather",
                      url=RG_WEATHER_URL, status="error",
                      detail="No game blocks parsed from RotoGrinders page.",
                      max_age_min=180)
    else:
        record_source("rotogrinders:weather",
                      label="RotoGrinders · MLB weather",
                      url=RG_WEATHER_URL, status="live",
                      detail=f"{parsed} games parsed (preferred source)",
                      max_age_min=180)
    return games


@st.cache_data(ttl=3600)
def get_weather(lat, lon, game_time_utc):
    """Pull the hour of weather closest to first pitch from Open-Meteo.
    Returns temp_f, wind_mph, wind_dir_deg, rain_pct, dew_f, cloud_pct."""
    blank = {"temp_f": None, "wind_mph": None, "wind_dir_deg": None,
             "rain_pct": None, "dew_f": None, "cloud_pct": None}
    if lat is None or lon is None or not game_time_utc:
        return blank
    url = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude": lat, "longitude": lon,
              "hourly": ("temperature_2m,wind_speed_10m,wind_direction_10m,"
                         "precipitation_probability,dew_point_2m,cloud_cover"),
              "forecast_days": 7, "timezone": "UTC"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30); r.raise_for_status()
        data = r.json()
    except Exception as exc:
        record_source("openmeteo:weather",
                      label="Open-Meteo · Hourly forecast",
                      url=url, status="error",
                      detail=f"Forecast fetch failed: {exc}",
                      max_age_min=180)
        return blank
    hourly = pd.DataFrame(data.get("hourly", {}))
    if hourly.empty or "time" not in hourly.columns:
        record_source("openmeteo:weather",
                      label="Open-Meteo · Hourly forecast",
                      url=url, status="error",
                      detail="Forecast response had no hourly data.",
                      max_age_min=180)
        return blank
    record_source("openmeteo:weather",
                  label="Open-Meteo · Hourly forecast",
                  url=url, status="live",
                  detail="Per-game forecast available",
                  max_age_min=180)
    hourly["time"] = pd.to_datetime(hourly["time"])
    game_time = pd.to_datetime(game_time_utc, utc=True).tz_convert(None)
    idx = (hourly["time"] - game_time).abs().idxmin()
    row = hourly.loc[idx]
    temp_c = row.get("temperature_2m")
    dew_c = row.get("dew_point_2m")
    return {
        "temp_f":       None if pd.isna(temp_c) else round((temp_c * 9/5) + 32, 1),
        "wind_mph":     row.get("wind_speed_10m"),
        "wind_dir_deg": row.get("wind_direction_10m"),
        "rain_pct":     row.get("precipitation_probability"),
        "dew_f":        None if pd.isna(dew_c) else round((dew_c * 9/5) + 32, 1),
        "cloud_pct":    row.get("cloud_cover"),
    }

# ---------------------------------------------------------------------------
# Weather-impact model (Kevin Roth-style HR/Runs/K boost percentages)
# All math is transparent and tunable. No external service.
# ---------------------------------------------------------------------------
import math as _math

def _wind_component_out(wind_mph, wind_dir_deg, cf_bearing_deg):
    """Return signed wind speed projected onto the home-plate->CF axis.
    Positive = blowing OUT (toward CF, helps HRs).
    Negative = blowing IN (toward plate, kills HRs).
    Wind direction from Open-Meteo = where the wind is coming FROM.
    A wind FROM 0° (north) blowing TO 180° (south)."""
    if wind_mph is None or wind_dir_deg is None or cf_bearing_deg is None:
        return 0.0, "calm"
    try:
        w = float(wind_mph); d = float(wind_dir_deg); c = float(cf_bearing_deg)
    except Exception:
        return 0.0, "calm"
    if w < 1: return 0.0, "calm"
    # wind is moving TO (d + 180) mod 360. Project onto CF direction c.
    blow_to = (d + 180.0) % 360.0
    delta = _math.radians(blow_to - c)
    component = w * _math.cos(delta)  # +out, -in
    if component > w * 0.5:    label = "out to CF"
    elif component > w * 0.15:  label = "out"
    elif component < -w * 0.5:  label = "in from CF"
    elif component < -w * 0.15: label = "in"
    else:                       label = "crosswind"
    return component, label

def compute_weather_impact(weather: dict, park_factor: float, home_abbr: str) -> dict:
    """Translate forecast + park into HR / Runs / K boost percentages,
    plus the meta tags (sky condition, wind direction label, sample size).

    Model (transparent, tunable):
      HR%    = park_HR_effect + temp_effect + wind_out_effect - humidity_drag
      Runs%  = 0.55 * HR% + small temp/wind contribution
      K%     = small inverse of HR% (cold/heavy air → more Ks, hot/dry → fewer)
    Returns ints rounded to nearest %.
    """
    domed = home_abbr in DOMED_PARKS
    temp = weather.get("temp_f")
    wind = weather.get("wind_mph") or 0
    wind_dir = weather.get("wind_dir_deg")
    dew = weather.get("dew_f")
    cloud = weather.get("cloud_pct") or 0
    rain = weather.get("rain_pct") or 0

    # ---- Sky / sample labels ----
    if rain >= 60: sky = "Rain risk";   sky_icon = "🌧️"
    elif rain >= 30: sky = "Showers";   sky_icon = "🌦️"
    elif cloud >= 80: sky = "OVERcast"; sky_icon = "☁️"
    elif cloud >= 50: sky = "Cloudy";   sky_icon = "🌤️"
    else:             sky = "Clear";    sky_icon = "☀️"
    if domed: sky = "Roof / Dome"; sky_icon = "🏟️"

    # ---- Park base effect (HR pct from park factor index, capped) ----
    pf = float(park_factor) if park_factor is not None else 100.0
    park_hr_pct = max(-12.0, min(15.0, (pf - 100.0) * 0.7))

    # ---- Temperature: +1°F over 70 ≈ +0.4% HR (Statcast finding) ----
    if temp is None: temp_hr_pct = 0.0
    else:            temp_hr_pct = max(-6.0, min(8.0, (float(temp) - 70.0) * 0.4))

    # ---- Wind out/in to CF ----
    if domed or wind_dir is None:
        wind_component, wind_label = 0.0, ("roof closed" if domed else "unknown")
        wind_hr_pct = 0.0
    else:
        cf = STADIUM_CF_BEARING.get(home_abbr)
        wind_component, wind_label = _wind_component_out(wind, wind_dir, cf)
        # ~+1% HR per 1 mph of out-to-CF component, capped
        wind_hr_pct = max(-12.0, min(12.0, wind_component * 1.0))

    # ---- Humidity / dew point (heavier air = shorter flights) ----
    # Light effect: every 10°F of dew above 60 ≈ -0.6% HR
    if dew is None: hum_hr_pct = 0.0
    else:           hum_hr_pct = max(-3.0, min(2.0, (60.0 - float(dew)) * 0.06))

    # Combined HR delta
    hr_pct = park_hr_pct + temp_hr_pct + wind_hr_pct + hum_hr_pct
    # Runs scales sub-linearly with HRs; small temp boost too
    temp_run_pct = 0.0 if temp is None else max(-3.0, min(4.0, (float(temp) - 70.0) * 0.18))
    runs_pct = 0.55 * hr_pct + 0.4 * temp_run_pct
    # Strikeouts: cold heavy air = a hair more, hot light air = a hair less
    k_pct = -0.25 * temp_hr_pct - 0.15 * wind_hr_pct

    # ---- Sample size: based on park factor confidence + park type ----
    # We don't have actual game count without a DB, so we tag a qualitative label.
    if domed: sample = ("Stable sample", 90)  # roof closed = consistent indoors
    elif abs(hr_pct) >= 10: sample = ("Strong signal", 75)
    elif abs(hr_pct) >= 4:  sample = ("Moderate signal", 50)
    else:                   sample = ("Neutral", 30)

    return {
        "hr_pct":     int(round(hr_pct)),
        "runs_pct":   int(round(runs_pct)),
        "k_pct":      int(round(k_pct)),
        "sky":        sky,
        "sky_icon":   sky_icon,
        "wind_label": wind_label,
        "sample":     sample[0],
        "sample_score": sample[1],
        # raw inputs for the small "chip strip" at the top of the card
        "temp":       temp,
        "wind":       wind,
        "wind_dir_deg": wind_dir,
        "dew":        dew,
        "rain_pct":   rain,
    }

# ---------------------------------------------------------------------------
# Historical totals at this park (for the O/U strip on the Weather card)
# We pull MLB's schedule for last full season + current YTD at this venue,
# extract the final score for completed games, and compute avg + Under/Over
# distribution against today's book line.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def get_park_history_totals(venue_id: int, home_team_id: int) -> list:
    """Return a list of total runs scored in completed games at this park
    over the last ~18 months. Uses MLB Statsapi which is free."""
    if not venue_id and not home_team_id:
        return []
    today = today_ct()
    last_year = today.year - 1
    rows = []
    # Pull team's home schedule for last full season + this season YTD.
    # Home games guarantees the venue (handles team relocations correctly).
    for season in (last_year, today.year):
        try:
            params = {
                "sportId": 1, "season": season, "gameType": "R",
                "teamId": home_team_id, "hydrate": "venue",
                "startDate": f"{season}-03-01",
                "endDate": f"{season}-11-15",
            }
            r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                             params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for date_block in data.get("dates", []):
            for g in date_block.get("games", []):
                # Only completed games AT this venue and home for this team
                if g.get("status", {}).get("abstractGameState") != "Final":
                    continue
                home_team = g.get("teams", {}).get("home", {})
                if int(home_team.get("team", {}).get("id") or 0) != int(home_team_id):
                    continue
                # Filter to actual venue match if we have a venue_id
                if venue_id and int(g.get("venue", {}).get("id") or 0) != int(venue_id):
                    continue
                away_runs = g.get("teams", {}).get("away", {}).get("score")
                home_runs = home_team.get("score")
                if away_runs is None or home_runs is None:
                    continue
                rows.append(int(away_runs) + int(home_runs))
    return rows

def summarize_park_ou(totals: list, line: float = None) -> dict:
    """Given a list of historical total-runs and an optional sportsbook line,
    return avg, n, and (under/push/over) distribution as percentages."""
    if not totals:
        return {"n": 0, "avg": None, "under": None, "push": None, "over": None, "line": line}
    n = len(totals)
    avg = sum(totals) / n
    out = {"n": n, "avg": round(avg, 1), "line": line, "under": None, "push": None, "over": None}
    if line is not None:
        u = sum(1 for t in totals if t < line)
        # "push" only happens on whole-number lines
        if abs(line - round(line)) < 0.01:
            p = sum(1 for t in totals if abs(t - line) < 0.5)
            o = n - u - p
        else:
            p = 0
            o = n - u
        out["under"] = round(100 * u / n)
        out["push"]  = round(100 * p / n)
        out["over"]  = round(100 * o / n)
    return out

# ---------------------------------------------------------------------------
# Odds API key resolution. Looks for the canonical ODDS_API_KEY first, then a
# small set of common aliases people set up by mistake. Checks st.secrets and
# environment variables. Returns (key:str|None, source_label:str|None). The
# source label is safe to display ("secrets:ODDS_API_KEY", "env:THE_ODDS_API")
# and never includes the value itself.
# ---------------------------------------------------------------------------
ODDS_API_KEY_ALIASES = (
    "ODDS_API_KEY",
    "THE_ODDS_API_KEY",
    "THE_ODDS_API",
    "ODDSAPI_KEY",
)

# Public diagnostic snapshot for the HR-props fetcher. Populated by
# get_hr_player_odds_map(). Cleared/replaced on every fetch attempt. Kept
# at module scope (not @st.cache_data) so the UI can read fresh status even
# when the cached fetcher returns an empty dict from a prior call.
HR_ODDS_DIAG: dict = {
    "key_present":    False,
    "key_source":     None,   # e.g. "secrets:ODDS_API_KEY" — never the key itself
    "key_tail":       None,   # last 4 chars only, for confirmation
    "events_checked": 0,
    "events_with_hr_market": 0,
    "events_failed":  0,
    "players_found":  0,
    "betmgm_lines":   0,
    "status":         "uninitialized",
    "last_error":     "",
    "http_status":    None,
}


def _get_odds_api_key() -> "tuple[str | None, str | None, str | None]":
    """Resolve the Odds API key from st.secrets first, then environment.

    Returns (key, source_label, last4). Source label looks like
    "secrets:ODDS_API_KEY" or "env:THE_ODDS_API"; last4 is only the trailing
    four characters of the key, safe to surface in diagnostics.
    """
    # st.secrets behaves like a dict but can raise on missing / unconfigured.
    for name in ODDS_API_KEY_ALIASES:
        try:
            val = st.secrets[name]  # type: ignore[index]
        except Exception:
            val = None
        if val:
            s = str(val).strip()
            if s:
                return s, f"secrets:{name}", s[-4:] if len(s) >= 4 else "****"
    for name in ODDS_API_KEY_ALIASES:
        val = os.environ.get(name)
        if val:
            s = str(val).strip()
            if s:
                return s, f"env:{name}", s[-4:] if len(s) >= 4 else "****"
    return None, None, None


# ---------------------------------------------------------------------------
# Sportsbook O/U totals via the-odds-api.com (free 500/mo tier)
# Uses _get_odds_api_key() so common alias names also work.
# Cached 30 min so we don't burn requests.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def get_odds_totals_map() -> dict:
    """Return {(away_abbr, home_abbr): line_float} for today's MLB games.
    Returns {} if API key not configured or call fails."""
    SRC = "oddsapi:totals"
    LABEL = "the-odds-api · MLB totals (O/U lines)"
    key, key_src, _tail = _get_odds_api_key()
    if not key:
        record_source(SRC, label=LABEL, status="unconfigured",
                      detail=("No Odds API key found. Add ODDS_API_KEY "
                              "(or one of: " + ", ".join(ODDS_API_KEY_ALIASES[1:])
                              + ") to Streamlit secrets."),
                      max_age_min=60)
        return {}
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {"apiKey": key, "regions": "us", "markets": "totals",
              "oddsFormat": "american", "dateFormat": "iso"}
    try:
        r = requests.get(url, params=params, timeout=15); r.raise_for_status()
        data = r.json()
    except Exception as exc:
        record_source(SRC, label=LABEL, url=url, status="error",
                      detail=f"Odds fetch failed: {exc}",
                      max_age_min=60)
        return {}
    # Map full team names from the API back to your 3-letter abbrs.
    # TEAM_INFO is keyed by full team name (e.g. "New York Yankees").
    NAME_TO_ABBR = {name: info["abbr"] for name, info in TEAM_INFO.items()}
    out = {}
    for game in data or []:
        away = NAME_TO_ABBR.get(game.get("away_team"))
        home = NAME_TO_ABBR.get(game.get("home_team"))
        if not away or not home:
            continue
        # Take the median line across books for stability
        lines = []
        for book in game.get("bookmakers", []):
            for market in book.get("markets", []):
                if market.get("key") != "totals": continue
                for o in market.get("outcomes", []):
                    pt = o.get("point")
                    if pt is not None: lines.append(float(pt))
        if lines:
            lines.sort()
            mid = lines[len(lines)//2]
            out[(away, home)] = mid
    record_source(SRC, label=LABEL, url=url,
                  status="live" if out else "error",
                  detail=f"{len(out)} game O/U lines parsed" if out else "No usable totals in response",
                  max_age_min=60)
    return out

# ---------------------------------------------------------------------------
# Player HR prop odds (To Hit a Home Run, "batter_home_runs" market) via
# the-odds-api.com event endpoints. Resolves the API key via
# _get_odds_api_key() (supports ODDS_API_KEY plus common aliases in both
# st.secrets and env). Missing/empty key returns {} so callers can no-op.
#
# Returns: { clean_name(player) : {
#     "best_book": str, "best_price": int,         # highest American price
#     "bet_mgm": int|None,                          # BetMGM price if seen
#     "books": { book_key: int },                   # all book prices
#     "display_name": str,                          # original name from API
# } }
# Cached 20 min.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=1200, show_spinner=False)
def get_hr_player_odds_map() -> dict:
    SRC = "oddsapi:hr_props"
    LABEL = "the-odds-api · MLB batter_home_runs (To Hit a HR)"
    # Reset shared diagnostic snapshot so the UI never shows stale numbers
    # from a previous run when this attempt fails early.
    HR_ODDS_DIAG.update({
        "key_present": False, "key_source": None, "key_tail": None,
        "events_checked": 0, "events_with_hr_market": 0, "events_failed": 0,
        "players_found": 0, "betmgm_lines": 0,
        "status": "uninitialized", "last_error": "", "http_status": None,
    })
    key, key_src, key_tail = _get_odds_api_key()
    if not key:
        HR_ODDS_DIAG.update({
            "status": "no_key",
            "last_error": ("No Odds API key found. Tried: "
                           + ", ".join(ODDS_API_KEY_ALIASES)
                           + " in st.secrets and environment."),
        })
        record_source(SRC, label=LABEL, status="unconfigured",
                      detail=HR_ODDS_DIAG["last_error"],
                      max_age_min=30)
        return {}
    HR_ODDS_DIAG.update({
        "key_present": True, "key_source": key_src, "key_tail": key_tail,
    })

    events_url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
    try:
        r = requests.get(events_url, params={"apiKey": key, "dateFormat": "iso"},
                         timeout=15)
        HR_ODDS_DIAG["http_status"] = r.status_code
        if r.status_code == 401 or r.status_code == 403:
            msg = (f"Events fetch unauthorized (HTTP {r.status_code}). "
                   f"Key from {key_src} is invalid or revoked.")
            HR_ODDS_DIAG.update({"status": "auth_error", "last_error": msg})
            record_source(SRC, label=LABEL, url=events_url, status="error",
                          detail=msg, max_age_min=30)
            return {}
        if r.status_code == 429:
            msg = "Quota/rate limit reached on the-odds-api (HTTP 429)."
            HR_ODDS_DIAG.update({"status": "rate_limited", "last_error": msg})
            record_source(SRC, label=LABEL, url=events_url, status="error",
                          detail=msg, max_age_min=30)
            return {}
        r.raise_for_status()
        events = r.json() or []
    except Exception as exc:
        msg = f"Events fetch failed: {exc}"
        HR_ODDS_DIAG.update({"status": "network_error", "last_error": msg})
        record_source(SRC, label=LABEL, url=events_url, status="error",
                      detail=msg, max_age_min=30)
        return {}

    out: dict = {}
    parsed_events = 0
    failed_events = 0
    events_with_hr_market = 0
    for ev in events:
        ev_id = ev.get("id")
        if not ev_id:
            continue
        ev_url = (
            f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/"
            f"{ev_id}/odds"
        )
        params = {
            "apiKey": key, "regions": "us",
            "markets": "batter_home_runs",
            "oddsFormat": "american", "dateFormat": "iso",
        }
        try:
            er = requests.get(ev_url, params=params, timeout=15)
            if er.status_code != 200:
                failed_events += 1
                # Record the first non-200 status for diagnostics so the user
                # can see e.g. 422 "market unavailable" rather than silence.
                if not HR_ODDS_DIAG["last_error"]:
                    body = (er.text or "")[:160].replace("\n", " ")
                    HR_ODDS_DIAG["last_error"] = (
                        f"Event {ev_id} returned HTTP {er.status_code}: {body}"
                    )
                continue
            edata = er.json() or {}
        except Exception as exc:
            failed_events += 1
            if not HR_ODDS_DIAG["last_error"]:
                HR_ODDS_DIAG["last_error"] = f"Event fetch error: {exc}"
            continue
        parsed_events += 1
        ev_had_hr_market = False
        for book in edata.get("bookmakers", []) or []:
            book_key = str(book.get("key") or "").lower()
            book_title = book.get("title") or book_key
            for market in book.get("markets", []) or []:
                if market.get("key") != "batter_home_runs":
                    continue
                ev_had_hr_market = True
                for o in market.get("outcomes", []) or []:
                    # "Yes" side = "To Hit a HR". the-odds-api typically
                    # uses outcome name == player name with "description"
                    # = "Over"/"Yes". Some books only list the Yes side.
                    desc = str(o.get("description") or "").strip().lower()
                    if desc and desc not in {"yes", "over"}:
                        continue
                    player = o.get("name") or o.get("participant") or ""
                    player = str(player).strip()
                    if not player:
                        continue
                    try:
                        price = int(o.get("price"))
                    except Exception:
                        continue
                    cn = clean_name(player)
                    if not cn:
                        continue
                    rec = out.setdefault(cn, {
                        "best_book": book_title, "best_price": price,
                        "bet_mgm": None, "books": {},
                        "display_name": player,
                    })
                    # Track per-book price (keep most recent / highest if dup).
                    prev = rec["books"].get(book_key)
                    if prev is None or price > prev:
                        rec["books"][book_key] = price
                    if book_key == "betmgm":
                        if rec["bet_mgm"] is None or price > rec["bet_mgm"]:
                            rec["bet_mgm"] = price
                    # Best price across books for "best available" fallback.
                    if price > rec["best_price"]:
                        rec["best_price"] = price
                        rec["best_book"] = book_title
        if ev_had_hr_market:
            events_with_hr_market += 1

    betmgm_lines = sum(1 for v in out.values() if v.get("bet_mgm") is not None)
    HR_ODDS_DIAG.update({
        "events_checked":         parsed_events + failed_events,
        "events_with_hr_market":  events_with_hr_market,
        "events_failed":          failed_events,
        "players_found":          len(out),
        "betmgm_lines":           betmgm_lines,
    })
    if out:
        HR_ODDS_DIAG["status"] = "live"
        record_source(SRC, label=LABEL, url=events_url, status="live",
                      detail=(f"{len(out)} players w/ HR odds across "
                              f"{events_with_hr_market} games"
                              + (f"; {failed_events} events skipped"
                                 if failed_events else "")),
                      max_age_min=30)
    else:
        # Distinguish "no events" from "events but no HR market" from "all events failed".
        if not events:
            HR_ODDS_DIAG["status"] = "no_events"
            detail = ("No upcoming MLB events returned by the-odds-api. "
                      "There may be no games scheduled in the feed window.")
        elif failed_events and not parsed_events:
            HR_ODDS_DIAG["status"] = "all_events_failed"
            detail = (f"All {failed_events} event odds requests failed. "
                      f"Last error: {HR_ODDS_DIAG.get('last_error') or 'unknown'}")
        elif events_with_hr_market == 0:
            HR_ODDS_DIAG["status"] = "no_hr_market"
            detail = ("Events found, but the batter_home_runs market is not "
                      "currently offered. Player props are typically posted "
                      "later in the day; try again closer to first pitch. "
                      "Some Odds API plans also exclude player props — "
                      "verify your subscription includes them.")
        else:
            HR_ODDS_DIAG["status"] = "no_outcomes"
            detail = ("HR market present but no usable outcomes. "
                      "This can happen if every outcome lacks a price.")
        record_source(SRC, label=LABEL, url=events_url,
                      status="error" if parsed_events else "unconfigured",
                      detail=detail, max_age_min=30)
    return out

# 60s TTL keeps lineups/substitutions current for live-betting consumers
# without hammering the free MLB StatsAPI. Pregame and final games change
# infrequently so 60s is still over-budget there — fine.
@st.cache_data(ttl=60)
def get_boxscore(game_pk):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        record_source("statsapi:boxscore",
                      label="MLB StatsAPI · Boxscore + lineups",
                      url="https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore",
                      status="live", detail="Live confirmed lineups when posted; projected fallback otherwise.",
                      max_age_min=20)
        return r.json()
    except Exception as exc:
        record_source("statsapi:boxscore",
                      label="MLB StatsAPI · Boxscore + lineups",
                      status="error", detail=f"Boxscore fetch failed: {exc}",
                      max_age_min=20)
        raise

@st.cache_data(ttl=3600, show_spinner=False)
def get_team_injuries(team_id, team_name=None):
    """Return list of injured players currently on the team's MLB roster.
    Single API call (hydrates transactions for all roster members).
    Status codes: D7/D10/D15/D60 = IL, DTD = day-to-day, ILF/BRV/PL = other.
    Each item: {name, position, status, status_code, injury, return_date}.
    Filters to MLB-level players (parentTeamId == team_id) and uses the most
    recent MLB-team IL placement transaction for the injury detail.
    """
    if not team_id:
        return []
    try:
        url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
        params = {
            "rosterType": "fullRoster",
            "hydrate": "person(transactions)",
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        record_source("statsapi:injuries",
                      label="MLB StatsAPI · Roster + IL transactions",
                      status="error", detail=f"Roster fetch failed: {exc}",
                      max_age_min=120)
        return []

    # Only show MLB-level injuries (10/15/60-day IL + DTD)
    INJURY_CODES = {"D10", "D15", "D60", "DTD"}
    team_name_lower = (team_name or "").lower()
    out = []
    for p in data.get("roster", []):
        status = p.get("status", {}) or {}
        code = (status.get("code") or "").upper()
        if code not in INJURY_CODES:
            continue
        # Must be an MLB-level player on this team
        if p.get("parentTeamId") != team_id:
            continue
        person = p.get("person", {}) or {}
        name = person.get("fullName", "")
        pos = (p.get("position", {}) or {}).get("abbreviation", "")
        status_desc = status.get("description", "")

        # Find the most recent IL placement BY the MLB parent club.
        # If no such transaction exists, this player is on a minor-league IL (skip).
        injury_text = ""
        return_date = ""
        txns = person.get("transactions", []) or []
        for t in reversed(txns):
            desc = (t.get("description") or "")
            desc_low = desc.lower()
            if "injured list" not in desc_low or "placed" not in desc_low:
                continue
            if team_name_lower and team_name_lower not in desc_low:
                continue
            injury_text = desc
            return_date = t.get("effectiveDate", "") or t.get("date", "")
            break
        # Skip if this is not an MLB-level IL placement — these are minor-league injuries
        if not injury_text and code != "DTD":
            continue

        out.append({
            "name": name,
            "position": pos,
            "status": status_desc,
            "status_code": code,
            "injury": injury_text,
            "return_date": return_date,
        })
    # Sort: 60-day first, then 15-day, 10-day, day-to-day
    order = {"D60": 0, "D15": 1, "D10": 2, "DTD": 3}
    out.sort(key=lambda x: (order.get(x["status_code"], 9), x["name"]))
    record_source("statsapi:injuries",
                  label="MLB StatsAPI · Roster + IL transactions",
                  url="https://statsapi.mlb.com/api/v1/teams/{team_id}/roster",
                  status="live",
                  detail="Live MLB roster status (IL, DTD, etc.).",
                  max_age_min=120)
    return out


def render_team_injury_panel(team_label, team_abbr, injuries):
    """Render a single team's injury list as styled HTML."""
    if not injuries:
        return (
            f"<div style='background:#3b1f6b;border:2px solid #facc15;border-radius:14px;"
            f"padding:14px 16px;margin:8px 0;'>"
            f"<div style='color:#facc15;font-weight:900;font-size:1.05rem;margin-bottom:6px;'>"
            f"{team_label} <span style='opacity:0.7;font-weight:700;'>({team_abbr})</span></div>"
            f"<div style='color:#a7f3d0;font-size:0.92rem;'>No reported injuries — full roster available.</div>"
            f"</div>"
        )
    # Status pill colors
    PILL = {
        "D60": ("#7f1d1d", "#fecaca"),
        "D15": ("#991b1b", "#fecaca"),
        "D10": ("#b45309", "#fde68a"),
        "D7":  ("#b45309", "#fde68a"),
        "DTD": ("#1e3a8a", "#bfdbfe"),
        "PL":  ("#581c87", "#e9d5ff"),
        "BRV": ("#374151", "#e5e7eb"),
    }
    rows_html = []
    for inj in injuries:
        bg, fg = PILL.get(inj["status_code"], ("#374151", "#e5e7eb"))
        pill = (
            f"<span style='background:{bg};color:{fg};border-radius:999px;"
            f"padding:2px 10px;font-size:0.78rem;font-weight:800;letter-spacing:0.3px;"
            f"white-space:nowrap;'>{inj['status']}</span>"
        )
        injury_line = inj.get("injury") or "Details unavailable"
        # Trim noisy prefixes from MLB transaction text — keep only the meaningful part
        if injury_line and injury_line != "Details unavailable":
            # Strip leading "<Team> placed <POS> <Name> on the X-day injured list" boilerplate,
            # leaving the actual injury description / retroactive date.
            import re as _re
            m = _re.search(r"injured list(?:\s+retroactive\s+to\s+([^.]+))?\.\s*(.*)", injury_line, _re.I)
            if m:
                retro = (m.group(1) or "").strip()
                detail = (m.group(2) or "").strip()
                bits = []
                if detail:
                    bits.append(detail)
                if retro:
                    bits.append(f"Placed retroactive to {retro}.")
                if bits:
                    injury_line = " ".join(bits)
        rows_html.append(
            f"<div style='display:flex;flex-direction:column;gap:3px;"
            f"padding:10px 0;border-bottom:1px solid rgba(250,204,21,0.18);'>"
            f"<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;'>"
            f"<span style='color:#fff;font-weight:800;font-size:0.98rem;'>{inj['name']}</span>"
            f"<span style='color:#facc15;font-weight:700;font-size:0.82rem;'>{inj['position']}</span>"
            f"{pill}"
            f"</div>"
            f"<div style='color:#d1fae5;font-size:0.85rem;line-height:1.35;'>{injury_line}</div>"
            f"</div>"
        )
    count_il = sum(1 for x in injuries if x["status_code"].startswith("D") and x["status_code"] != "DTD")
    count_dtd = sum(1 for x in injuries if x["status_code"] == "DTD")
    summary_bits = []
    if count_il:
        summary_bits.append(f"{count_il} on IL")
    if count_dtd:
        summary_bits.append(f"{count_dtd} day-to-day")
    summary = " · ".join(summary_bits) if summary_bits else f"{len(injuries)} reported"
    return (
        f"<div style='background:#3b1f6b;border:2px solid #facc15;border-radius:14px;"
        f"padding:14px 16px;margin:8px 0;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin-bottom:6px;flex-wrap:wrap;gap:6px;'>"
        f"<div style='color:#facc15;font-weight:900;font-size:1.05rem;'>"
        f"{team_label} <span style='opacity:0.7;font-weight:700;'>({team_abbr})</span></div>"
        f"<div style='color:#a7f3d0;font-size:0.82rem;font-weight:700;'>{summary}</div>"
        f"</div>"
        + "".join(rows_html) +
        f"</div>"
    )


@st.cache_data(ttl=3600)
def get_recent_completed_games(team_id, before_date_str, n=12):
    """Return up to `n` of the team's most recent completed games before the given date."""
    if not team_id:
        return []
    try:
        before_dt = pd.to_datetime(before_date_str).date()
        start_dt = before_dt - timedelta(days=30)
        url = "https://statsapi.mlb.com/api/v1/schedule"
        params = {
            "sportId": 1, "teamId": team_id,
            "startDate": start_dt.strftime("%Y-%m-%d"),
            "endDate": (before_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=30); r.raise_for_status()
        data = r.json()
        pks = []
        for d in data.get("dates", []):
            for g in d.get("games", []):
                state = g.get("status", {}).get("abstractGameState", "")
                if state == "Final":
                    pks.append(g.get("gamePk"))
        return pks[-n:]
    except Exception:
        return []

@st.cache_data(ttl=3600)
def get_projected_lineup(team_id, team_abbr, before_date_str):
    """Return a DataFrame of the team's likely 9 hitters with projected batting-order spots
    derived from their most-used 9 over recent completed games. Used when MLB has not
    yet posted the official confirmed lineup for the day."""
    pks = get_recent_completed_games(team_id, before_date_str)
    if not pks:
        return pd.DataFrame()
    # Count how often each player appears in each batting-order spot
    spot_counts = {}    # name -> {1..9: count}
    appearances = {}    # name -> total_games
    last_seen_data = {} # name -> dict (bat_side, position, etc.)
    for pk in pks:
        try:
            box = get_boxscore(pk)
            for side in ("away", "home"):
                team_box = box.get("teams", {}).get(side, {})
                if team_box.get("team", {}).get("id") != team_id:
                    continue
                for pdata in team_box.get("players", {}).values():
                    person = pdata.get("person", {})
                    name = person.get("fullName", "")
                    if not name:
                        continue
                    pos = pdata.get("position", {}).get("abbreviation", "")
                    if pos == "P":
                        continue
                    lineup_raw = str(pdata.get("battingOrder", "")).strip()
                    if not (lineup_raw[:1].isdigit()):
                        continue
                    # battingOrder is like "100", "200" ... "900" for starters
                    spot = int(lineup_raw[0])
                    if spot < 1 or spot > 9:
                        continue
                    spot_counts.setdefault(name, {}).setdefault(spot, 0)
                    spot_counts[name][spot] += 1
                    appearances[name] = appearances.get(name, 0) + 1
                    last_seen_data[name] = {
                        "position": pos,
                        "bat_side": pdata.get("batSide", {}).get("code", ""),
                        "pitch_hand": pdata.get("pitchHand", {}).get("code", ""),
                        "player_id": person.get("id"),
                    }
        except Exception:
            continue
    if not appearances:
        return pd.DataFrame()
    # Greedy assignment: for each spot 1..9, pick the player who started there most often
    # among players not yet assigned.
    assigned = {}  # spot -> name
    used = set()
    # Sort candidates per spot by frequency at that spot, then by total appearances
    for spot in range(1, 10):
        candidates = []
        for name, spots in spot_counts.items():
            if name in used:
                continue
            cnt = spots.get(spot, 0)
            if cnt > 0:
                candidates.append((name, cnt, appearances[name]))
        if not candidates:
            continue
        candidates.sort(key=lambda x: (-x[1], -x[2]))
        winner = candidates[0][0]
        assigned[spot] = winner
        used.add(winner)
    # Fill any missing spots with most-frequent remaining starters
    if len(assigned) < 9:
        remaining = sorted(
            [(n, c) for n, c in appearances.items() if n not in used],
            key=lambda x: -x[1],
        )
        for spot in range(1, 10):
            if spot in assigned:
                continue
            if remaining:
                name, _ = remaining.pop(0)
                assigned[spot] = name
                used.add(name)
    rows = []
    for spot in range(1, 10):
        name = assigned.get(spot)
        if not name:
            continue
        meta = last_seen_data.get(name, {})
        rows.append({
            "player_name": name,
            "name_key": clean_name(name),
            "team": norm_team(team_abbr),
            "position": meta.get("position", ""),
            "bat_side": meta.get("bat_side", ""),
            "pitch_hand": meta.get("pitch_hand", ""),
            "player_id": meta.get("player_id"),
            "lineup_spot": float(spot),
        })
    return pd.DataFrame(rows)

def roster_df_from_box(team_box, fallback_team):
    rows = []
    for pdata in team_box.get("players", {}).values():
        person = pdata.get("person", {})
        lineup_raw = str(pdata.get("battingOrder", "")).strip()
        lineup_spot = int(lineup_raw[0]) if lineup_raw[:1].isdigit() else None
        rows.append({
            "player_name": person.get("fullName", ""),
            "name_key": clean_name(person.get("fullName", "")),
            "team": norm_team(fallback_team),
            "position": pdata.get("position", {}).get("abbreviation", ""),
            "bat_side": pdata.get("batSide", {}).get("code", ""),
            "pitch_hand": pdata.get("pitchHand", {}).get("code", ""),
            "player_id": person.get("id"),
            "lineup_spot": lineup_spot,
        })
    return pd.DataFrame(rows)

def lookup_pitch_hand(roster_df, pitcher_name):
    if roster_df.empty or not pitcher_name or pitcher_name == "TBD": return ""
    exact = roster_df[roster_df["name_key"] == clean_name(pitcher_name)]
    if not exact.empty: return exact.iloc[0]["pitch_hand"]
    return ""

def _merge_weather_rg_first(rg: dict, fallback: dict) -> dict:
    """Prefer RotoGrinders fields per-key; fall back to Open-Meteo when RG
    didn't supply a number (domed games, parse gaps). Annotates the resulting
    dict with `source` / `source_label` so the UI can show where the live
    fields came from. Open-Meteo always supplies dew_f / cloud_pct since RG
    doesn't expose those at game time."""
    out = dict(fallback or {})
    if rg:
        for k in ("temp_f", "wind_mph", "wind_dir_deg", "rain_pct"):
            v = rg.get(k)
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                out[k] = v
        # Pass through useful RG-only annotations.
        for k in ("sky", "dome", "wind_compass", "venue"):
            if rg.get(k) is not None:
                out[k] = rg[k]
        # Source attribution: RG primary, Open-Meteo backfill for fields
        # RG can't provide.
        rg_used = any(rg.get(k) is not None for k in
                      ("temp_f", "wind_mph", "wind_dir_deg", "rain_pct")) or rg.get("dome")
        if rg_used:
            out["source"] = "rotogrinders"
            out["source_label"] = "RotoGrinders"
            out["source_url"] = RG_WEATHER_URL
            return out
    out["source"] = "openmeteo"
    out["source_label"] = "Open-Meteo"
    out["source_url"] = "https://open-meteo.com/"
    return out


def get_combined_weather(game_row):
    """Weather pipeline used by the matchup/weather/overcast display.
    Preference order: RotoGrinders (free, accurate, user-requested) →
    Open-Meteo fallback. Returns the same schema as get_weather() plus
    source attribution fields used by the UI."""
    fallback = get_weather(game_row["lat"], game_row["lon"], game_row["game_time_utc"])
    try:
        rg_map = get_rotogrinders_weather()
    except Exception:
        rg_map = {}
    away = game_row.get("away_abbr", "")
    home = game_row.get("home_abbr", "")
    away_n = _rg_norm_abbr(away); home_n = _rg_norm_abbr(home)
    rg = None
    if rg_map:
        # Exact (away, home) match first; then any orientation; then by home only.
        for key in ((away_n, home_n), (home_n, away_n)):
            if key in rg_map:
                rg = rg_map[key]; break
        if rg is None:
            for (a, h), payload in rg_map.items():
                if h == home_n or a == away_n:
                    rg = payload; break
    return _merge_weather_rg_first(rg or {}, fallback)


def build_game_context(game_row):
    weather = get_combined_weather(game_row)
    try:
        box = get_boxscore(game_row["game_pk"])
        away_roster = roster_df_from_box(box.get("teams", {}).get("away", {}), game_row["away_abbr"])
        home_roster = roster_df_from_box(box.get("teams", {}).get("home", {}), game_row["home_abbr"])
    except Exception:
        away_roster = pd.DataFrame(); home_roster = pd.DataFrame()

    # Guard: if rosters are empty or missing expected columns, normalize them so
    # downstream filtering on `lineup_spot` / `position` doesn't raise KeyError.
    _expected_cols = ["lineup_spot", "position", "bat_side", "name", "player_id"]

    def _normalize_roster(df):
        if not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame(columns=_expected_cols)
        for _c in _expected_cols:
            if _c not in df.columns:
                df[_c] = pd.NA
        return df

    away_roster = _normalize_roster(away_roster)
    home_roster = _normalize_roster(home_roster)

    away_lineup = away_roster[(away_roster["lineup_spot"].notna()) & (away_roster["position"] != "P")].copy()
    home_lineup = home_roster[(home_roster["lineup_spot"].notna()) & (home_roster["position"] != "P")].copy()
    if not away_lineup.empty: away_lineup = away_lineup.sort_values("lineup_spot")
    if not home_lineup.empty: home_lineup = home_lineup.sort_values("lineup_spot")
    away_status = "Confirmed" if len(away_lineup) >= 9 else ""
    home_status = "Confirmed" if len(home_lineup) >= 9 else ""

    # Fall back to projected lineups (most-used 9 across recent games) when not confirmed
    game_date_str = pd.to_datetime(game_row["game_time_utc"]).strftime("%Y-%m-%d")
    if len(away_lineup) < 9:
        proj_a = get_projected_lineup(game_row["away_id"], game_row["away_abbr"], game_date_str)
        if not proj_a.empty:
            away_lineup = proj_a.sort_values("lineup_spot")
            away_status = "Projected"
    if len(home_lineup) < 9:
        proj_h = get_projected_lineup(game_row["home_id"], game_row["home_abbr"], game_date_str)
        if not proj_h.empty:
            home_lineup = proj_h.sort_values("lineup_spot")
            home_status = "Projected"
    if not away_status: away_status = "Not Posted"
    if not home_status: home_status = "Not Posted"

    home_pitch_hand = lookup_pitch_hand(home_roster, game_row["home_probable"])
    away_pitch_hand = lookup_pitch_hand(away_roster, game_row["away_probable"])
    if not away_lineup.empty:
        away_lineup["opposing_pitcher"] = game_row["home_probable"]
        away_lineup["opposing_pitch_hand"] = home_pitch_hand
    if not home_lineup.empty:
        home_lineup["opposing_pitcher"] = game_row["away_probable"]
        home_lineup["opposing_pitch_hand"] = away_pitch_hand
    return {
        "weather": weather,
        "away_lineup": away_lineup, "home_lineup": home_lineup,
        "away_status": away_status, "home_status": home_status,
        "home_pitch_hand": home_pitch_hand, "away_pitch_hand": away_pitch_hand,
    }

# ---------------------------------------------------------------------------
# Shared batter-pick eligibility filter
# ---------------------------------------------------------------------------
# Once a game has started, lineups are locked and pre-game batter props
# (HR / 2+ RBI / TB / HRR / parlays / hero strip) can no longer be
# meaningfully recommended. Centralized here so every batter generator —
# HR Sleepers, 2+ RBI, TB, HRR, AI HR Parlay, Round Robin, AI Hits Parlay,
# the RBI hero strip — uses the exact same eligibility rule.
_BATTER_PICK_COMPLETED_TOKENS = (
    "final", "completed", "game over", "postponed", "cancelled",
    "canceled", "suspended", "forfeit", "if necessary",
)
_BATTER_PICK_STARTED_TOKENS = (
    "in progress", "live", "manager challenge", "review", "delayed",
)
_BATTER_PICK_PREGAME_TOKENS = (
    "scheduled", "pre-game", "pregame", "pre game",
    "warmup", "warm-up", "warm up",
)


def is_pre_game_for_batter_pick(row) -> bool:
    """True if this game is still a pre-game matchup eligible for batter
    recommendations. Anything in-progress, completed, postponed, or whose
    start time has already passed is excluded."""
    status = str(row.get("status") or "").strip().lower()
    if any(tok in status for tok in _BATTER_PICK_COMPLETED_TOKENS):
        return False
    if any(tok in status for tok in _BATTER_PICK_STARTED_TOKENS):
        return False
    gt = row.get("game_time_utc")
    try:
        start_utc = pd.to_datetime(gt, utc=True)
    except Exception:
        start_utc = pd.NaT
    _now = pd.Timestamp.now('UTC')
    now_utc = _now if _now.tzinfo is not None else _now.tz_localize("UTC")
    if any(tok in status for tok in _BATTER_PICK_PREGAME_TOKENS):
        if pd.isna(start_utc):
            return True
        return start_utc > now_utc
    if pd.isna(start_utc):
        return False
    return start_utc > now_utc


def filter_pre_game_schedule(schedule_df) -> pd.DataFrame:
    """Return only the rows of schedule_df whose game has not yet started.
    Empty-safe — returns an empty DataFrame if the input is empty or None."""
    if schedule_df is None or len(schedule_df) == 0:
        return schedule_df if schedule_df is not None else pd.DataFrame()
    mask = schedule_df.apply(is_pre_game_for_batter_pick, axis=1)
    return schedule_df[mask].reset_index(drop=True)


def find_player_row(df, name_key, team, player_id=None):
    """Locate a hitter row in batters_df. ID-first when player_id is provided
    (lineups always carry it), then fall back to (name_key, team), then
    name_key alone, and finally a last-name "contains" fallback so that
    accent/suffix mismatches (e.g. "Jose Ramirez" vs "José Ramírez") still
    resolve to a real Savant row instead of leaving the lineup row blank."""
    if df is None or df.empty: return None
    if player_id is not None and "player_id" in df.columns:
        try:
            pid = int(player_id)
            id_match = df[pd.to_numeric(df["player_id"], errors="coerce") == pid]
            if not id_match.empty:
                return id_match.iloc[0]
        except (TypeError, ValueError):
            pass
    exact = df[(df["name_key"] == name_key) & (df["team_key"] == norm_team(team))]
    if not exact.empty: return exact.iloc[0]
    exact2 = df[df["name_key"] == name_key]
    if not exact2.empty: return exact2.iloc[0]
    if isinstance(name_key, str) and " " in name_key:
        last = name_key.split(" ")[-1]
        if len(last) >= 4:
            contains = df[df["name_key"].str.endswith(" " + last, na=False)]
            if not contains.empty:
                return contains.iloc[0]
    return None

# ===========================================================================
# Pitch-arsenal data (Baseball Savant pitch-arsenal-stats endpoint)
# Used by:
#   - Top 3 Hitters card  -> "Crushes" line (best pitch types per hitter)
#   - Pitcher Vulnerability panel -> "Pitch Mix" mini-table
# ===========================================================================
PITCH_NAME_MAP = {
    "FF": "4-Seam", "SI": "Sinker", "FC": "Cutter",
    "SL": "Slider", "ST": "Sweeper", "SV": "Slurve",
    "CU": "Curve",  "KC": "Knuckle Curve", "CS": "Slow Curve",
    "CH": "Change", "FS": "Splitter", "FO": "Forkball",
    "SC": "Screwball", "KN": "Knuckler", "EP": "Eephus",
}
PITCH_EMOJI = {
    "FF": "🔥", "SI": "⤵️",  "FC": "✂️",
    "SL": "➡️", "ST": "🌪️", "SV": "➿",
    "CU": "🎣", "KC": "🪝", "CS": "🪝",
    "CH": "🎃", "FS": "💧", "FO": "🍴",
    "SC": "🌀", "KN": "🦄", "EP": "🌞",
}

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pitch_arsenal_csv(kind: str, year: int) -> pd.DataFrame:
    """Pull Baseball Savant per-player per-pitch-type leaderboard.
    kind: 'pitcher' (what they throw / results allowed)
          'batter'  (what they face / results produced).
    Returns df with columns including: player_id, pitch_type, pitch_name,
    pitch_usage, pa, woba, slg, ba, whiff_percent, run_value_per_100, est_woba."""
    if kind not in ("pitcher", "batter"): return pd.DataFrame()
    url = ("https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?"
           f"year={year}&team=&min=10&type={kind}&pitchType=&csv=true")
    try:
        df = pd.read_csv(url, encoding="utf-8-sig",
                         storage_options={"User-Agent": "Mozilla/5.0"})
    except Exception:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
            df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
        except Exception:
            return pd.DataFrame()
    if df.empty: return df
    # normalize
    df.columns = [c.strip().strip('"') for c in df.columns]
    if "player_id" in df.columns:
        df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    for c in ("pitch_usage", "woba", "est_woba", "slg", "ba", "whiff_percent",
              "run_value_per_100", "pa", "pitches"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def load_pitch_arsenal(kind: str) -> pd.DataFrame:
    """Get arsenal data for current year, falling back to prior year if empty."""
    src_key = f"savant:arsenal:{kind}"
    label = f"Baseball Savant · Pitch arsenal ({kind})"
    yr = today_ct().year
    df = _fetch_pitch_arsenal_csv(kind, yr)
    fell_back = False
    if df is None or df.empty or len(df) < 30:  # spring/early-season fallback
        df = _fetch_pitch_arsenal_csv(kind, yr - 1)
        fell_back = True
    if df is None or df.empty:
        record_source(src_key, label=label, status="error",
                      detail="Savant arsenal endpoint returned no rows for current or prior season.",
                      max_age_min=24 * 60)
    elif fell_back:
        record_source(src_key, label=label, status="fallback",
                      detail=f"Using {yr - 1} arsenal — current season has too few PA so far ({len(df):,} rows).",
                      max_age_min=24 * 60)
    else:
        record_source(src_key, label=label, status="live",
                      detail=f"{len(df):,} rows · {yr} season",
                      max_age_min=24 * 60)
    return df

def pitcher_pitch_mix(arsenal_p: pd.DataFrame, player_id) -> pd.DataFrame:
    """Subset of pitcher arsenal for one pitcher, sorted by usage desc."""
    if arsenal_p is None or arsenal_p.empty or not player_id: return pd.DataFrame()
    try: pid = int(player_id)
    except Exception: return pd.DataFrame()
    sub = arsenal_p[arsenal_p["player_id"] == pid].copy()
    if sub.empty: return sub
    if "pitch_usage" in sub.columns:
        sub = sub.sort_values("pitch_usage", ascending=False)
    return sub.reset_index(drop=True)

def hitter_pitch_crush(arsenal_b: pd.DataFrame, player_id, top_n: int = 2,
                       min_pa: int = 15) -> pd.DataFrame:
    """Pitch types this hitter crushes most (highest woba, then est_woba),
    filtered to pitches with at least min_pa plate appearances against."""
    if arsenal_b is None or arsenal_b.empty or not player_id: return pd.DataFrame()
    try: pid = int(player_id)
    except Exception: return pd.DataFrame()
    sub = arsenal_b[arsenal_b["player_id"] == pid].copy()
    if sub.empty: return sub
    if "pa" in sub.columns:
        sub = sub[sub["pa"].fillna(0) >= min_pa]
    if sub.empty: return sub
    if "woba" in sub.columns:
        sub = sub.sort_values(["woba", "slg"], ascending=[False, False])
    return sub.head(top_n).reset_index(drop=True)

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_batter_game_log(player_id: int, season: int) -> pd.DataFrame:
    """Pull a batter's per-game hitting log for one season from MLB StatsAPI.

    Returns a DataFrame with one row per game (most recent last) and columns:
      date, ab, h, bb, hbp, sf, k, pa, obp_num, obp_den.

    Empty DataFrame on any failure or unrecognized payload — callers must
    handle the empty case (offseason, missing player, API hiccup).
    """
    if not player_id:
        return pd.DataFrame()
    try:
        pid = int(player_id)
    except Exception:
        return pd.DataFrame()
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
            params={"stats": "gameLog", "group": "hitting",
                    "season": int(season), "sportId": 1},
            timeout=10,
        )
        r.raise_for_status()
        payload = r.json() or {}
    except Exception:
        return pd.DataFrame()

    splits = []
    for s in (payload.get("stats") or []):
        for sp in (s.get("splits") or []):
            splits.append(sp)
    if not splits:
        return pd.DataFrame()

    rows = []
    for sp in splits:
        stat = sp.get("stat") or {}
        d = sp.get("date") or sp.get("game", {}).get("date")
        try:
            dt = pd.to_datetime(d).date() if d else None
        except Exception:
            dt = None
        def _i(k):
            try: return int(stat.get(k) or 0)
            except Exception: return 0
        ab = _i("atBats")
        h  = _i("hits")
        bb = _i("baseOnBalls")
        hbp = _i("hitByPitch")
        sf  = _i("sacFlies")
        k   = _i("strikeOuts")
        pa  = _i("plateAppearances") or (ab + bb + hbp + sf)
        hr  = _i("homeRuns")
        rows.append({
            "date": dt, "ab": ab, "h": h, "bb": bb, "hbp": hbp,
            "sf": sf, "k": k, "pa": pa, "hr": hr,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.dropna(subset=["date"])
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def get_batter_recent_form(player_id, end_date_iso: str) -> dict:
    """Compute L5/L10/L30D recent-form snapshots for one batter.

    Cache key includes player_id + end_date_iso + window logic so each slate
    date gets its own snapshot — no leakage from yesterday's window.

    Args:
        player_id: MLB person ID.
        end_date_iso: slate end date as 'YYYY-MM-DD' (UTC/date-aware caller
            should pass the slate's selected_date).

    Returns dict with keys 'L5', 'L10', 'L30D' each mapping to:
      {'avg': float|None, 'obp': float|None, 'k_pct': float|None,
       'games': int, 'pa': int}
    Missing/empty windows return None for the rate fields. Never raises.
    """
    empty = {"avg": None, "obp": None, "k_pct": None, "games": 0, "pa": 0}
    out = {"L5": dict(empty), "L10": dict(empty), "L30D": dict(empty)}
    if not player_id:
        return out
    try:
        end_dt = pd.to_datetime(end_date_iso).date()
    except Exception:
        end_dt = today_ct()

    # Pull current-season log; fall back to prior season for early-spring slates
    # when the current season has no games yet.
    season = end_dt.year
    log = _fetch_batter_game_log(player_id, season)
    if log is None or log.empty:
        log = _fetch_batter_game_log(player_id, season - 1)
    if log is None or log.empty:
        return out

    # Only games strictly before the slate date (don't include today's PAs
    # mid-game when this is called during a live slate).
    log = log[pd.to_datetime(log["date"]).dt.date < end_dt].copy()
    if log.empty:
        return out

    def _window_stats(window_df):
        if window_df is None or window_df.empty:
            return dict(empty)
        ab  = int(window_df["ab"].sum())
        h   = int(window_df["h"].sum())
        bb  = int(window_df["bb"].sum())
        hbp = int(window_df["hbp"].sum())
        sf  = int(window_df["sf"].sum())
        k   = int(window_df["k"].sum())
        pa  = int(window_df["pa"].sum())
        avg = (h / ab) if ab > 0 else None
        obp_den = ab + bb + hbp + sf
        obp = ((h + bb + hbp) / obp_den) if obp_den > 0 else None
        k_pct = (100.0 * k / pa) if pa > 0 else None
        return {
            "avg": round(avg, 3) if avg is not None else None,
            "obp": round(obp, 3) if obp is not None else None,
            "k_pct": round(k_pct, 1) if k_pct is not None else None,
            "games": int(len(window_df)),
            "pa": pa,
        }

    out["L5"]  = _window_stats(log.tail(5))
    out["L10"] = _window_stats(log.tail(10))
    cutoff_30 = end_dt - timedelta(days=30)
    last30 = log[pd.to_datetime(log["date"]).dt.date >= cutoff_30]
    out["L30D"] = _window_stats(last30)
    return out


def _fmt_form_avg(v) -> str:
    """Render an AVG value as '.333' or '—' when missing."""
    if v is None:
        return "—"
    try:
        f = float(v)
    except Exception:
        return "—"
    if pd.isna(f):
        return "—"
    s = f"{f:.3f}"
    return s[1:] if s.startswith("0.") else s


def _form_blend_adjustment(l10: dict) -> float:
    """Modest matchup-score nudge from L10 AVG. Capped at +/- 4 points so
    recent form can break ties without overpowering Savant/park/pitcher signal."""
    if not l10:
        return 0.0
    avg = l10.get("avg")
    pa = l10.get("pa") or 0
    if avg is None or pa < 10:
        return 0.0
    delta = float(avg) - 0.250  # league-ish average
    return max(-4.0, min(4.0, delta * 40.0))


@st.cache_data(ttl=1800, show_spinner=False)
def get_batter_hr_context(player_id, end_date_iso: str) -> dict:
    """Compute HR-context snapshot for one batter, derived from MLB StatsAPI gameLog.

    Returns dict with:
      last_hr_date: ISO 'YYYY-MM-DD' of the most recent game with HR>=1, or None.
      days_since_last_hr: int days between end_date_iso and last_hr_date, or None.
      hr_last_10: total HR over the batter's last 10 played games strictly before
        end_date_iso. None when no game log available.
      games_in_last_10: number of games used (<= 10).
      season_hr: season HR total from the same game log (so it matches the source).
    """
    out = {
        "last_hr_date": None,
        "days_since_last_hr": None,
        "hr_last_10": None,
        "games_in_last_10": 0,
        "season_hr": None,
    }
    if not player_id:
        return out
    try:
        end_dt = pd.to_datetime(end_date_iso).date()
    except Exception:
        end_dt = today_ct()

    season = end_dt.year
    log = _fetch_batter_game_log(player_id, season)
    used_prev = False
    if log is None or log.empty:
        log = _fetch_batter_game_log(player_id, season - 1)
        used_prev = True
    if log is None or log.empty:
        return out
    if "hr" not in log.columns:
        return out

    log = log.copy()
    log["date"] = pd.to_datetime(log["date"]).dt.date
    log = log[log["date"] < end_dt]
    if log.empty:
        return out

    try:
        out["season_hr"] = int(log["hr"].sum())
    except Exception:
        out["season_hr"] = None

    last10 = log.tail(10)
    try:
        out["hr_last_10"] = int(last10["hr"].sum())
        out["games_in_last_10"] = int(len(last10))
    except Exception:
        out["hr_last_10"] = None
        out["games_in_last_10"] = int(len(last10))

    hr_games = log[log["hr"] >= 1]
    if not hr_games.empty:
        last_hr_dt = hr_games["date"].max()
        out["last_hr_date"] = last_hr_dt.isoformat() if last_hr_dt else None
        if last_hr_dt:
            try:
                out["days_since_last_hr"] = int((end_dt - last_hr_dt).days)
            except Exception:
                out["days_since_last_hr"] = None
    return out


def _fmt_last_hr(hr_ctx: dict) -> str:
    """Render the last-HR date as '2026-05-08 (4d ago)' or '— none yet'."""
    if not hr_ctx:
        return "—"
    d = hr_ctx.get("last_hr_date")
    if not d:
        return "— none yet"
    days = hr_ctx.get("days_since_last_hr")
    if days is None:
        return str(d)
    if days <= 0:
        return f"{d} (today)"
    return f"{d} ({days}d ago)"


def _fmt_hr_last10(hr_ctx: dict) -> str:
    """Render HR-in-last-10 as 'N HR / G games' or '—'."""
    if not hr_ctx:
        return "—"
    n = hr_ctx.get("hr_last_10")
    g = hr_ctx.get("games_in_last_10") or 0
    if n is None:
        return "—"
    return f"{int(n)} HR / last {int(g)}G"


@st.cache_data(ttl=900, show_spinner=False)
def get_home_runs_on_date(date_iso: str) -> pd.DataFrame:
    """Return every home run hit across MLB on the given date.

    Pulls the slate via MLB StatsAPI /schedule then per-game iterates the live
    feed's allPlays for result.eventType == "home_run". Robust to per-game
    fetch failures (skips that game; never raises). Empty DataFrame on total
    failure or when no games occurred / are completed yet.

    Columns: date, game_pk, away_abbr, home_abbr, inning, half,
             batter_id, batter, batter_team_abbr, pitcher, hr_distance,
             launch_speed, description.
    """
    if not date_iso:
        return pd.DataFrame()
    cols = ["date", "game_pk", "away_abbr", "home_abbr", "inning", "half",
            "batter_id", "batter", "batter_team_abbr", "pitcher",
            "hr_distance", "launch_speed", "description"]
    try:
        sched_url = "https://statsapi.mlb.com/api/v1/schedule"
        r = requests.get(
            sched_url,
            params={"sportId": 1, "date": date_iso, "hydrate": "team"},
            timeout=15,
        )
        r.raise_for_status()
        sched = r.json() or {}
    except Exception:
        return pd.DataFrame(columns=cols)

    games = []
    for d in sched.get("dates", []) or []:
        for g in d.get("games", []) or []:
            games.append(g)
    if not games:
        return pd.DataFrame(columns=cols)

    rows = []
    for g in games:
        gpk = g.get("gamePk")
        if not gpk:
            continue
        # Skip games that haven't actually been played (scheduled / pre-game) —
        # the live feed exists but allPlays is empty, so this is just a small
        # efficiency win, not required for correctness.
        state = (g.get("status") or {}).get("abstractGameState", "")
        if state and state.lower() in ("preview",):
            continue
        away_name = (g.get("teams", {}).get("away", {}).get("team", {}) or {}).get("name", "")
        home_name = (g.get("teams", {}).get("home", {}).get("team", {}) or {}).get("name", "")
        away_abbr = TEAM_INFO.get(away_name, {}).get("abbr", away_name[:3].upper())
        home_abbr = TEAM_INFO.get(home_name, {}).get("abbr", home_name[:3].upper())
        try:
            fr = requests.get(
                f"https://statsapi.mlb.com/api/v1.1/game/{gpk}/feed/live",
                timeout=20,
            )
            fr.raise_for_status()
            feed = fr.json() or {}
        except Exception:
            continue
        live = feed.get("liveData") or {}
        plays = (live.get("plays") or {}).get("allPlays") or []
        for play in plays:
            result = play.get("result") or {}
            ev = (result.get("eventType") or result.get("event") or "").lower()
            if ev not in ("home_run", "home run"):
                continue
            matchup = play.get("matchup") or {}
            about = play.get("about") or {}
            half = (about.get("halfInning") or "").lower()
            inning = about.get("inning")
            batter = (matchup.get("batter") or {})
            pitcher = (matchup.get("pitcher") or {})
            batter_team = away_abbr if half == "top" else home_abbr
            # Try to grab launch speed / hit distance from the playEvents (the
            # final pitch usually carries the hitData payload). Missing data
            # is fine — just leave as None.
            hit_speed = None
            hit_dist = None
            for ev_p in (play.get("playEvents") or []):
                hd = ev_p.get("hitData") or {}
                if hd:
                    hit_speed = hd.get("launchSpeed", hit_speed)
                    hit_dist = hd.get("totalDistance", hit_dist)
            rows.append({
                "date": date_iso,
                "game_pk": gpk,
                "away_abbr": away_abbr,
                "home_abbr": home_abbr,
                "inning": inning,
                "half": half,
                "batter_id": batter.get("id"),
                "batter": batter.get("fullName") or "",
                "batter_team_abbr": batter_team,
                "pitcher": pitcher.get("fullName") or "",
                "hr_distance": hit_dist,
                "launch_speed": hit_speed,
                "description": result.get("description") or "",
            })
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------------
# Day vs Night HR splits — MLB StatsAPI statSplits (sitCodes=d,n)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=21600, show_spinner=False)
def get_day_night_hr_splits(season: int) -> pd.DataFrame:
    """Fetch every hitter's Day Games / Night Games split for the given season
    from the official MLB StatsAPI (statSplits, sitCodes=d,n). One call returns
    every player who has logged a PA in that situation.

    Returns a wide DataFrame keyed by player with columns:
        player_id, player, team_abbr, team_id,
        day_pa, day_ab, day_hr, day_avg, day_obp, day_slg, day_ops, day_hr_rate,
        night_pa, night_ab, night_hr, night_avg, night_obp, night_slg,
        night_ops, night_hr_rate,
        total_pa, total_hr, day_share_hr, split_edge_hr_rate

    Returns an empty DataFrame on any network/parse failure so the UI can
    degrade gracefully without crashing the whole app.

    Why this endpoint: statSplits is published by MLB itself, so the day/night
    classification matches the schedule's authoritative dayNight field — no need
    to scan ~2,400 game feeds. Single HTTP call, cacheable for hours.
    """
    cols = [
        "player_id", "player", "team_abbr", "team_id",
        "day_pa", "day_ab", "day_hr", "day_avg", "day_obp", "day_slg",
        "day_ops", "day_hr_rate",
        "night_pa", "night_ab", "night_hr", "night_avg", "night_obp",
        "night_slg", "night_ops", "night_hr_rate",
        "total_pa", "total_hr", "day_share_hr", "split_edge_hr_rate",
    ]
    if not season:
        return pd.DataFrame(columns=cols)
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/stats",
            params={
                "stats": "statSplits",
                "group": "hitting",
                "sportId": 1,
                "season": int(season),
                "sitCodes": "d,n",
                # 'All' returns rookies/call-ups too; default 'Qualified' would
                # miss most prop-relevant low-PA bench bats.
                "playerPool": "All",
                "limit": 5000,
            },
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json() or {}
    except Exception:
        return pd.DataFrame(columns=cols)

    splits = ((payload.get("stats") or [{}])[0] or {}).get("splits") or []
    if not splits:
        return pd.DataFrame(columns=cols)

    def _num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    # Accumulate per player_id; a player can have both a 'd' and an 'n' row,
    # and (rarely, mid-trade) two team rows per situation — sum stats across
    # those, keep the most recent team_abbr we saw.
    by_pid: dict = {}
    for s in splits:
        code = (s.get("split") or {}).get("code", "")
        if code not in ("d", "n"):
            continue
        player = s.get("player") or {}
        pid = player.get("id")
        if not pid:
            continue
        team = s.get("team") or {}
        team_name = team.get("name", "")
        team_abbr = TEAM_INFO.get(team_name, {}).get("abbr", team_name[:3].upper())
        stat = s.get("stat") or {}
        pa = _num(stat.get("plateAppearances")) or 0
        ab = _num(stat.get("atBats")) or 0
        hr = _num(stat.get("homeRuns")) or 0
        # MLB returns avg/obp/slg/ops as strings like ".367"; coerce to float.
        avg = _num(stat.get("avg"))
        obp = _num(stat.get("obp"))
        slg = _num(stat.get("slg"))
        ops = _num(stat.get("ops"))

        bucket = by_pid.setdefault(pid, {
            "player_id": int(pid),
            "player": player.get("fullName") or "",
            "team_abbr": team_abbr,
            "team_id": team.get("id"),
            "day_pa": 0.0, "day_ab": 0.0, "day_hr": 0.0,
            "day_avg": None, "day_obp": None, "day_slg": None, "day_ops": None,
            "night_pa": 0.0, "night_ab": 0.0, "night_hr": 0.0,
            "night_avg": None, "night_obp": None, "night_slg": None, "night_ops": None,
        })
        # Prefer non-empty team_abbr if a later row carries it.
        if team_abbr:
            bucket["team_abbr"] = team_abbr
            bucket["team_id"] = team.get("id") or bucket["team_id"]

        prefix = "day" if code == "d" else "night"
        bucket[f"{prefix}_pa"] += pa
        bucket[f"{prefix}_ab"] += ab
        bucket[f"{prefix}_hr"] += hr
        # Rate stats: only overwrite if currently None (statSplits usually has
        # one row per player per code so this is fine; in the rare two-team
        # case the per-team rate isn't representative anyway and we recompute
        # AVG ourselves from totals below).
        for k, v in [("avg", avg), ("obp", obp), ("slg", slg), ("ops", ops)]:
            key = f"{prefix}_{k}"
            if bucket[key] is None:
                bucket[key] = v

    if not by_pid:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(by_pid.values())

    def _rate(num, denom):
        return (num / denom) if (denom and denom > 0) else None

    # Recompute AVG from totals when we have multi-team aggregates; HR rate as
    # HR/PA which is the prop-friendly denominator.
    df["day_avg"] = df.apply(
        lambda r: _rate(r["day_hr"] + 0, r["day_ab"]) if (r["day_avg"] is None and r["day_ab"]) else r["day_avg"],
        axis=1,
    )
    df["day_hr_rate"] = df.apply(lambda r: _rate(r["day_hr"], r["day_pa"]), axis=1)
    df["night_hr_rate"] = df.apply(lambda r: _rate(r["night_hr"], r["night_pa"]), axis=1)
    df["total_pa"] = df["day_pa"] + df["night_pa"]
    df["total_hr"] = df["day_hr"] + df["night_hr"]
    df["day_share_hr"] = df.apply(
        lambda r: _rate(r["day_hr"], r["total_hr"]), axis=1
    )
    # Split edge = day HR-rate minus night HR-rate. Positive = day-bias hitter.
    df["split_edge_hr_rate"] = df.apply(
        lambda r: (
            (r["day_hr_rate"] or 0) - (r["night_hr_rate"] or 0)
            if (r["day_pa"] and r["night_pa"]) else None
        ),
        axis=1,
    )

    # Cast counting stats to int for clean display.
    for c in ("day_pa", "day_ab", "day_hr", "night_pa", "night_ab", "night_hr",
              "total_pa", "total_hr"):
        df[c] = df[c].fillna(0).astype(int)

    return df[cols].reset_index(drop=True)


def find_pitcher_row(df, pitcher_name, pitcher_id=None):
    """Locate a pitcher row in pitchers_df. ID-first when pitcher_id is provided
    (the slate's probable-pitcher id from the schedule), then fall back to
    name_key, then a last-name "contains" match. ID-first matching keeps the
    Matchup board keyed to the selected slate game's actual probable pitcher
    even when two pitchers share a name in the season CSV."""
    if df is None or df.empty: return None
    if pitcher_id is not None and "player_id" in df.columns:
        try:
            pid = int(pitcher_id)
            id_match = df[pd.to_numeric(df["player_id"], errors="coerce") == pid]
            if not id_match.empty:
                return id_match.iloc[0]
        except (TypeError, ValueError):
            pass
    if not pitcher_name or pitcher_name == "TBD": return None
    key = clean_name(pitcher_name)
    exact = df[df["name_key"] == key]
    if not exact.empty: return exact.iloc[0]
    last = key.split(" ")[-1]
    contains = df[df["name_key"].str.contains(last, na=False)]
    if not contains.empty: return contains.iloc[0]
    return None

# ===========================================================================
# Metric computation: Matchup, Test Score, Ceiling, Zone Fit, HR Form, kHR
# ===========================================================================
def platoon_value(bat_side, pitch_hand):
    b = str(bat_side).upper(); p = str(pitch_hand).upper()
    if b == "S" and p in ("L", "R"): return 1.0
    if b in ("L", "R") and p in ("L", "R"):
        return 0.8 if b != p else -0.35
    return 0.0

def matchup_score(b_row, p_row, lineup_spot, weather, park_factor, bat_side, opp_pitch_hand):
    """Primary 0-200 matchup score (used as 'Matchup' column)."""
    hr      = safe_float(b_row.get("HR")       if b_row is not None else None, 10)
    iso     = safe_float(b_row.get("ISO")      if b_row is not None else None, 0.170)
    xslg    = safe_float(b_row.get("xSLG")     if b_row is not None else None, 0.420)
    barrel  = safe_float(b_row.get("Barrel%")  if b_row is not None else None, 8.0)
    hardhit = safe_float(b_row.get("HardHit%") if b_row is not None else None, 38.0)

    p_xslg    = safe_float(p_row.get("xSLG")    if p_row is not None else None, 0.420)
    p_barrel  = safe_float(p_row.get("Barrel%") if p_row is not None else None, 8.0)
    p_hardhit = safe_float(p_row.get("HardHit%")if p_row is not None else None, 38.0)
    p_k       = safe_float(p_row.get("K%")      if p_row is not None else None, 22.0)

    temp_f   = safe_float(weather.get("temp_f"), 72)
    wind_mph = safe_float(weather.get("wind_mph"), 8)
    rain_pct = safe_float(weather.get("rain_pct"), 0)

    split_boost = platoon_value(bat_side, opp_pitch_hand) * 7
    weather_bonus = max(0, temp_f - 68) * 0.28 + max(0, wind_mph - 7) * 0.18 - rain_pct * 0.03
    park_bonus = (park_factor - 100) * 0.50
    slot_bonus = max(0, 10 - safe_float(lineup_spot, 9)) * 1.15

    score = (
        hr * 0.75 + iso * 155 + xslg * 28 + barrel * 2.1 + hardhit * 0.65
        + p_xslg * 25 + p_barrel * 1.8 + p_hardhit * 0.45 - p_k * 0.45
        + split_boost + weather_bonus + park_bonus + slot_bonus
    )
    return round(score, 2)

def test_score(b_row, p_row):
    """Confidence 0-100. How signal-rich is this matchup based on data quality + edge size?"""
    if b_row is None: return 35.0
    barrel  = safe_float(b_row.get("Barrel%"), 8.0)
    hardhit = safe_float(b_row.get("HardHit%"), 38.0)
    xwoba_b = safe_float(b_row.get("xwOBA"), 0.320)
    iso     = safe_float(b_row.get("ISO"), 0.170)

    p_xwoba = safe_float(p_row.get("xwOBA") if p_row is not None else None, 0.320)
    p_brl   = safe_float(p_row.get("Barrel%") if p_row is not None else None, 8.0)

    edge = (xwoba_b - 0.320) * 80 + (p_xwoba - 0.320) * 80 \
           + (barrel - 7) * 1.6 + (hardhit - 36) * 0.4 \
           + (iso - 0.150) * 80 + (p_brl - 7) * 1.0
    return round(max(0, min(100, 50 + edge)), 1)

def ceiling_score(b_row, weather, park_factor):
    """Upside: best-case TB/HR potential given raw power, park, conditions."""
    if b_row is None: return 35.0
    barrel  = safe_float(b_row.get("Barrel%"), 8.0)
    hardhit = safe_float(b_row.get("HardHit%"), 38.0)
    iso     = safe_float(b_row.get("ISO"), 0.170)
    xslg    = safe_float(b_row.get("xSLG"), 0.420)
    fb      = safe_float(b_row.get("FB%"), 35.0)
    pull    = safe_float(b_row.get("Pull%"), 38.0)

    temp_f   = safe_float(weather.get("temp_f"), 72)
    wind_mph = safe_float(weather.get("wind_mph"), 8)

    base = barrel * 3.6 + hardhit * 0.55 + iso * 110 + xslg * 25 + (fb - 30) * 0.4 + (pull - 35) * 0.3
    base += (park_factor - 100) * 0.7 + max(0, temp_f - 68) * 0.35 + max(0, wind_mph - 7) * 0.25
    return round(max(0, min(100, base)), 1)

def zone_fit(b_row, p_row, bat_side, opp_pitch_hand):
    """0-1 score: how well batter's hot zones align with pitcher's weakest pitch tendencies.
    Uses pull%/fb%/ld% as a proxy when literal zone data isn't present."""
    if b_row is None: return 0.04
    pull = safe_float(b_row.get("Pull%"), 38.0)
    fb   = safe_float(b_row.get("FB%"), 35.0)
    ld   = safe_float(b_row.get("LD%"), 22.0)

    # pitcher arsenal proxy: if pitcher gives up barrels, fit is higher
    p_brl = safe_float(p_row.get("Barrel%") if p_row is not None else None, 8.0)
    p_hh  = safe_float(p_row.get("HardHit%")if p_row is not None else None, 38.0)

    # base fit
    fit = (pull - 35) * 0.0014 + (fb - 32) * 0.0018 + (ld - 20) * 0.0020 \
          + (p_brl - 7) * 0.0030 + (p_hh - 36) * 0.0010
    fit += platoon_value(bat_side, opp_pitch_hand) * 0.020
    fit = max(0.000, min(0.200, fit + 0.045))
    return round(fit, 3)

def hr_form_pct(b_row):
    """Recent HR-rate proxy as percentage 0-100. Uses HR / PA scaled with barrel weighting.
    Returns (pct_value, trend_arrow) — trend is up if barrel+hardhit are above their thresholds."""
    if b_row is None: return (40.0, "→")
    hr  = safe_float(b_row.get("HR"), 0)
    pa  = safe_float(b_row.get("pa"), 1)
    if pa <= 0: pa = 1
    barrel = safe_float(b_row.get("Barrel%"), 8.0)
    hardhit = safe_float(b_row.get("HardHit%"), 38.0)
    raw = (hr / pa) * 1100 + (barrel - 7) * 2.2 + (hardhit - 36) * 0.4
    pct = max(0, min(100, raw + 30))

    # Trend arrow based on signal alignment
    if barrel >= 11 and hardhit >= 42: arrow = "↑"
    elif barrel <= 6 or hardhit <= 33: arrow = "↓"
    else: arrow = "→"
    return (round(pct, 0), arrow)

def k_adj_hr(b_row, p_row, ceiling):
    """K-adjusted HR likelihood: ceiling × (1 - K%) - punishes high-K matchups."""
    p_k = safe_float(p_row.get("K%") if p_row is not None else None, 22.0)
    b_k = safe_float(b_row.get("K%") if b_row is not None else None, 22.0)
    combined_k = (p_k + b_k) / 2
    factor = max(0.55, 1.0 - (combined_k - 18) * 0.012)
    return round(ceiling * factor, 1)

def pitcher_vulnerability(p_row):
    if p_row is None: return (60, "ok", "OK")
    p_xslg = safe_float(p_row.get("xSLG"), 0.420)
    p_barrel = safe_float(p_row.get("Barrel%"), 8.0)
    p_hardhit = safe_float(p_row.get("HardHit%"), 38.0)
    p_k = safe_float(p_row.get("K%"), 22.0)
    p_hr = safe_float(p_row.get("HR"), 0)
    score = (
        (p_xslg - 0.380) * 120 + (p_barrel - 7.0) * 3.0 + (p_hardhit - 35.0) * 0.6
        - (p_k - 22.0) * 0.8 + p_hr * 0.4 + 60
    )
    score = max(0, min(100, score))
    if score >= 78: return (round(score,1), "elite",  "Highly Vulnerable")
    if score >= 65: return (round(score,1), "strong", "Exploitable")
    if score >= 50: return (round(score,1), "ok",     "Average")
    return (round(score,1), "avoid", "Tough")

# ===========================================================================
# Build the matchup table for one team's lineup
# ===========================================================================
def _build_pitcher_arsenal_set(arsenal_p, opp_pitcher_id, min_usage=10.0):
    """Return set of pitch_type codes the opposing pitcher uses meaningfully."""
    if arsenal_p is None or arsenal_p.empty or not opp_pitcher_id:
        return set()
    try: pid = int(opp_pitcher_id)
    except Exception: return set()
    sub = arsenal_p[arsenal_p["player_id"] == pid]
    if sub.empty or "pitch_type" not in sub.columns:
        return set()
    if "pitch_usage" in sub.columns:
        sub = sub[sub["pitch_usage"].fillna(0) >= float(min_usage)]
    return {str(x).strip().upper() for x in sub["pitch_type"].dropna().tolist() if str(x).strip()}

def _crushes_cell(arsenal_b, player_id, opp_pitches: set, top_n: int = 2, min_pa: int = 15):
    """Compact text cell describing the pitch types this batter crushes.
    If a crush pitch overlaps the opposing pitcher's arsenal, prefix with 🔥.
    Falls back to '—' when sample is insufficient."""
    crush = hitter_pitch_crush(arsenal_b, player_id, top_n=top_n, min_pa=min_pa)
    if crush is None or crush.empty:
        return "—"
    parts = []
    overlap_any = False
    for _, cr in crush.iterrows():
        pt = str(cr.get("pitch_type", "")).strip().upper()
        if not pt: continue
        label = PITCH_NAME_MAP.get(pt, pt)
        woba = cr.get("woba")
        try:
            woba_f = float(woba)
            woba_str = f"{woba_f:.3f}".lstrip("0") if 0 <= woba_f < 1 else f"{woba_f:.3f}"
        except Exception:
            woba_str = "—"
        is_overlap = pt in opp_pitches
        if is_overlap: overlap_any = True
        prefix = "🔥 " if is_overlap else ""
        parts.append(f"{prefix}{label} {woba_str}")
    if not parts:
        return "—"
    return " · ".join(parts)

# Canonical HR-focused batter metric set. Every user-facing batter section
# (Matchup heat-map, Top 3 Hitters card, Hot/Cold slate leaderboards) pulls
# from this single source so display columns stay aligned and no section
# can drift back to the retired Test Score / Ceiling / Zone Fit / HR Form /
# kHR categories. Keys match the short internal column names used by the
# heat-map board; MATCHUP_HEATMAP_DISPLAY_LABELS owns the friendly headers.
HR_METRIC_KEYS = [
    # OPS is included so build_matchup_table-derived views (Top 3 Hitters card,
    # Hot/Cold leaderboards, HR Milestones table) automatically carry the
    # canonical OPS column without each call-site reaching back into the raw
    # batter row.
    "OPS",
    "ISO", "Brl/BIP%", "FB%", "GB%", "EV", "HH%",
    "HR/FB%", "PullAir%", "LA", "xwOBA", "SweetSpot%",
]

def _league_avg_from(batters_df, col, default):
    if batters_df is None or batters_df.empty or col not in batters_df.columns:
        return default
    s = pd.to_numeric(batters_df[col], errors="coerce").dropna()
    if s.empty:
        return default
    return float(s.median())

def _hr_league_table(batters_df):
    """League-average fallbacks for every HR-focused metric, computed from
    the qualified-batter slice so the proxies track the current season."""
    return {
        "K%":         _league_avg_from(batters_df, "K%",          22.0),
        "BB%":        _league_avg_from(batters_df, "BB%",          8.0),
        "ISO":        _league_avg_from(batters_df, "ISO",          0.155),
        "xwOBA":      _league_avg_from(batters_df, "xwOBA",        0.318),
        "Barrel%":    _league_avg_from(batters_df, "Barrel%",      8.0),
        "Pull%":      _league_avg_from(batters_df, "Pull%",       40.0),
        "SweetSpot%": _league_avg_from(batters_df, "SweetSpot%", 33.0),
        "FB%":        _league_avg_from(batters_df, "FB%",         34.0),
        "GB%":        _league_avg_from(batters_df, "GB%",         44.0),
        "HardHit%":   _league_avg_from(batters_df, "HardHit%",   38.0),
        "EV":         _league_avg_from(batters_df, "EV",          89.0),
        "LA":         _league_avg_from(batters_df, "LA",          12.0),
        # OPS league median (~.730) — used as the fallback when a batter row
        # has no OPS/OBP/SLG available so cards never collapse to "—".
        "OPS":        _league_avg_from(batters_df, "OPS",          0.730),
    }

def compute_hr_metrics(b_row, lg):
    """Compute the canonical HR-focused metric dict for one hitter.

    Returns the same short keys used in HEATMAP_THRESHOLDS / the heat-map
    board so every batter section can render identical values. Falls back
    to the league-average table `lg` (built via _hr_league_table) when the
    Savant row is missing a field. HR/FB% and PullAir% are derived; the
    rest are direct Savant fields rounded for display.
    """
    def _g(key, default=None):
        if b_row is None: return default
        v = b_row.get(key)
        try:
            if v is None or pd.isna(v): return default
            return float(v)
        except Exception:
            return default

    iso     = _g("ISO", lg["ISO"])
    xwoba   = _g("xwOBA", lg["xwOBA"])
    barrel  = _g("Barrel%", lg["Barrel%"])
    pull    = _g("Pull%", lg["Pull%"])
    sweet   = _g("SweetSpot%", lg["SweetSpot%"])
    fb      = _g("FB%", lg["FB%"])
    gb      = _g("GB%", lg["GB%"])
    hh      = _g("HardHit%", lg["HardHit%"])
    ev      = _g("EV", lg["EV"])
    la      = _g("LA", lg["LA"])
    # Canonical OPS read with OBP+SLG fallback, then league-median fallback.
    ops     = _g("OPS")
    if ops is None:
        obp_v = _g("OBP")
        slg_v = _g("SLG")
        if obp_v is not None and slg_v is not None:
            ops = obp_v + slg_v
    if ops is None:
        ops = lg.get("OPS", 0.730)

    # BIP estimate for HR/FB% denominator: SwingsComp-derived if available,
    # else PA × (1 - K%/100 - BB%/100), else a low-PA placeholder.
    pa = _g("pa")
    bip = _g("BIP")
    if bip is None:
        kp = _g("K%", lg["K%"]) or lg["K%"]
        bbp = _g("BB%", lg["BB%"]) or lg["BB%"]
        if pa is not None:
            bip = max(0.0, pa * (1 - kp / 100.0 - bbp / 100.0))
        else:
            bip = 12.0

    # HR/FB% = home runs per fly ball. Fall back to league-typical ~13%
    # when HR/FB%/BIP not available so the cell isn't blank for low-PA.
    hr_total = _g("HR")
    if hr_total is not None and fb and bip and fb > 0 and bip > 0:
        fb_count = fb / 100.0 * bip
        hr_fb = round(hr_total / fb_count * 100.0, 1) if fb_count > 0 else 13.0
    else:
        hr_fb = 13.0

    # PullAir% proxy = Pull% × FB% / 100 — rate of balls pulled in the air.
    if pull is not None and fb is not None:
        pull_air = round(pull * fb / 100.0, 1)
    else:
        pull_air = None

    return {
        "ISO":        round(iso, 3) if iso is not None else None,
        "Brl/BIP%":   round(barrel, 1) if barrel is not None else None,
        "FB%":        round(fb, 1) if fb is not None else None,
        "GB%":        round(gb, 1) if gb is not None else None,
        "EV":         round(ev, 1) if ev is not None else None,
        "HH%":        round(hh, 1) if hh is not None else None,
        "HR/FB%":     hr_fb,
        "PullAir%":   pull_air,
        "LA":         round(la, 1) if la is not None else None,
        "xwOBA":      round(xwoba, 3) if xwoba is not None else None,
        "SweetSpot%": round(sweet, 1) if sweet is not None else None,
        "OPS":        round(ops, 3) if ops is not None else None,
    }


def build_matchup_table(lineup_df, batters_df, pitchers_df, opp_pitcher_name, weather, park_factor,
                       arsenal_b=None, arsenal_p=None, opp_pitcher_id=None, slate_date=None):
    """The main heatmap-ready dataframe powering the Top 3 Hitters card and
    the Hot/Cold slate leaderboards. Display columns are aligned with the
    Matchup heat-map board so every batter section sees the same numbers.
    Ceiling / Zone Fit / kHR / HR Form / Test Score are still computed and
    carried alongside as internal fields for the AI HR / Sleepers / parlay
    scoring engines, but are not displayed in any batter-data section."""
    cols = ["Spot", "Hitter", "Team", "Matchup", "Crushes"] + HR_METRIC_KEYS + ["Likely"]
    if lineup_df.empty:
        return pd.DataFrame(columns=cols)
    # ID-first pitcher match keeps this row tied to the *selected slate game's*
    # probable pitcher rather than any pitcher with the same display name.
    p_row = find_pitcher_row(pitchers_df, opp_pitcher_name, pitcher_id=opp_pitcher_id)
    opp_pitches = _build_pitcher_arsenal_set(arsenal_p, opp_pitcher_id)
    lg = _hr_league_table(batters_df)
    try:
        slate_iso = pd.to_datetime(slate_date).strftime("%Y-%m-%d") if slate_date else today_ct().strftime("%Y-%m-%d")
    except Exception:
        slate_iso = today_ct().strftime("%Y-%m-%d")
    rows = []
    for _, r in lineup_df.iterrows():
        b_row = find_player_row(
            batters_df, r["name_key"], r["team"],
            player_id=r.get("player_id") if "player_id" in r.index else None,
        )
        opp_hand = r.get("opposing_pitch_hand", "")
        m   = matchup_score(b_row, p_row, r["lineup_spot"], weather, park_factor, r["bat_side"], opp_hand)
        ts  = test_score(b_row, p_row)
        cl  = ceiling_score(b_row, weather, park_factor)
        zf  = zone_fit(b_row, p_row, r["bat_side"], opp_hand)
        hrf, arrow = hr_form_pct(b_row)
        khr = k_adj_hr(b_row, p_row, cl)
        pid = r.get("player_id") if "player_id" in r.index else None
        crushes = _crushes_cell(arsenal_b, pid, opp_pitches) if arsenal_b is not None else "—"
        hr_metrics = compute_hr_metrics(b_row, lg)
        try:
            hr_ctx = get_batter_hr_context(pid, slate_iso) if pid else {}
        except Exception:
            hr_ctx = {}
        _lkl, _lkl_reason = _likely_with_reason(m, cl, b_row=b_row, p_row=p_row, khr=khr, hr_form_pct_val=hrf)
        row = {
            "Spot": int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
            "Hitter": r["player_name"],
            "Team": norm_team(r["team"]),
            "Matchup": m,
            "Crushes": crushes,
            "Likely": _lkl,
            "_LikelyReason": _lkl_reason,
            # Internal-only fields powering the AI HR / Sleepers / parlay engines.
            # Underscore-prefixed so style_matchup_table / leaderboards hide them.
            "_TestScore": ts,
            "_Ceiling": cl,
            "_ZoneFit": zf,
            "_HRFormPct": hrf,
            "_HRFormArrow": arrow,
            "_kHR": khr,
            "_HRSeason": safe_float(b_row.get("HR") if b_row is not None else None, 0),
            "_Barrel%": safe_float(b_row.get("Barrel%") if b_row is not None else None, 8.0),
            "_HardHit%": safe_float(b_row.get("HardHit%") if b_row is not None else None, 38.0),
            "_Pull%": safe_float(b_row.get("Pull%") if b_row is not None else None, 40.0),
            "_xISO": safe_float(b_row.get("xISO") if b_row is not None else None, 0.155),
            "_PA": safe_float(b_row.get("pa") if b_row is not None else None, 0),
            "_player_id": pid,
            "_bat_side": r["bat_side"] or "",
            "_opp_pitch_hand": opp_hand or "",
            "_OppPitcherName": r.get("opposing_pitcher", "") or "",
            # HR context (last HR date + HR over batter's last 10 games),
            # populated from MLB StatsAPI gameLog. Carried as underscore-prefixed
            # internal fields so existing styler/leaderboard helpers don't
            # accidentally include them; the slate-builder lifts the public-
            # display copies below into the visible leaderboard columns.
            "_LastHRDate": hr_ctx.get("last_hr_date") if hr_ctx else None,
            "_DaysSinceLastHR": hr_ctx.get("days_since_last_hr") if hr_ctx else None,
            "_HRLast10": hr_ctx.get("hr_last_10") if hr_ctx else None,
            "_HRLast10Games": hr_ctx.get("games_in_last_10") if hr_ctx else 0,
            "_HRSeasonFromLog": hr_ctx.get("season_hr") if hr_ctx else None,
            "Last HR": _fmt_last_hr(hr_ctx) if hr_ctx else "—",
            "HR L10G": _fmt_hr_last10(hr_ctx) if hr_ctx else "—",
        }
        row.update(hr_metrics)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Spot").reset_index(drop=True)
    return df

def build_rolling_table(lineup_df, batters_df, window):
    """Rolling-window batter snapshot. Real rolling data isn't in CSVs, so we derive
    a 'recency' view weighted toward power/contact metrics — column suffix shows the window."""
    cols = ["Spot", "Hitter", "Team", f"Form ({window}d)", "Power", "Contact", "Trend"]
    if lineup_df.empty: return pd.DataFrame(columns=cols)
    rows = []
    # weight scales by window: shorter window = more variance, longer = smoother
    w_var = {"7": 1.20, "15": 1.05, "30": 0.92}.get(str(window), 1.0)
    for _, r in lineup_df.iterrows():
        b = find_player_row(batters_df, r["name_key"], r["team"])
        barrel  = safe_float(b.get("Barrel%") if b is not None else None, 8.0)
        hardhit = safe_float(b.get("HardHit%") if b is not None else None, 38.0)
        iso     = safe_float(b.get("ISO") if b is not None else None, 0.170)
        avg     = safe_float(b.get("AVG") if b is not None else None, 0.250)
        kpct    = safe_float(b.get("K%") if b is not None else None, 22.0)

        form  = max(0, min(100, (barrel * 2.4 + hardhit * 0.55 + iso * 130 + (avg - 0.240) * 220 - (kpct - 20) * 0.5) * w_var + 30))
        power = max(0, min(100, barrel * 5 + iso * 110 + 10))
        contact = max(0, min(100, (avg - 0.200) * 350 + (40 - min(40, kpct)) * 1.4))
        trend = "↑" if barrel >= 11 and hardhit >= 42 else ("↓" if barrel <= 6 else "→")
        rows.append({
            "Spot": int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
            "Hitter": r["player_name"],
            "Team": norm_team(r["team"]),
            f"Form ({window}d)": round(form, 1),
            "Power": round(power, 1),
            "Contact": round(contact, 1),
            "Trend": trend,
        })
    df = pd.DataFrame(rows)
    if not df.empty: df = df.sort_values("Spot").reset_index(drop=True)
    return df

# ===========================================================================
# Heatmap styling — red→amber→green like the reference site
# ===========================================================================
def heat_color(value, low, high, reverse=False):
    """Return a CSS background-color in red→yellow→green spectrum."""
    try:
        v = float(value)
    except Exception:
        return ""
    if pd.isna(v): return ""
    rng = high - low
    if rng <= 0: return ""
    pct = max(0.0, min(1.0, (v - low) / rng))
    if reverse: pct = 1.0 - pct
    # piecewise: 0=red(#fecaca) → 0.5=amber(#fde68a) → 1=green(#86efac)
    if pct < 0.5:
        # red → amber
        t = pct * 2
        r = int(254 + (253 - 254) * t)
        g = int(202 + (230 - 202) * t)
        b = int(202 + (138 - 202) * t)
    else:
        # amber → green
        t = (pct - 0.5) * 2
        r = int(253 + (134 - 253) * t)
        g = int(230 + (239 - 230) * t)
        b = int(138 + (172 - 138) * t)
    return f"background-color: rgb({r},{g},{b}); color: #0f172a; font-weight: 800;"

def heat_score(value):
    """Heatmap for 0-100 scale columns (Test Score, Ceiling, Form, Power, Contact)."""
    return heat_color(value, 35, 90)

def heat_matchup(value):
    """Heatmap for the 0-200 Matchup column."""
    return heat_color(value, 95, 145)

def heat_pct(value):
    """Heatmap for percent-of-PA stats (Barrel%, HardHit%)."""
    return heat_color(value, 6, 14)

def heat_hardhit(value):
    return heat_color(value, 32, 48)

def heat_zone_fit(value):
    return heat_color(value, 0.030, 0.090)

def heat_iso(value):
    return heat_color(value, 0.130, 0.230)

def heat_hr_form_num(value):
    return heat_color(value, 25, 75)

def style_matchup_table(df):
    """Style the slate-wide leaderboard. Uses the canonical HR-focused metric
    set: ISO, BARREL%, FLYBALL%, Ground Ball %, EV, Hard Hit %, HR/FB%,
    PULL AIR%, LA, xwOBA, Sweet Spot% — matching the Matchup heat-map board.
    Test Score / Ceiling / Zone Fit / kHR / HR Form / Barrel% / HardHit% /
    raw HR columns are intentionally not displayed; they are carried on the
    builder dataframe as underscore-prefixed internals for AI HR scoring.
    """
    if df.empty: return df
    show_cols = [c for c in df.columns if not str(c).startswith("_")]
    styler = df[show_cols].style.format({
        "Matchup":    "{:.1f}",
        "ISO":        "{:.3f}",
        "Brl/BIP%":   "{:.1f}%",
        "FB%":        "{:.1f}%",
        "GB%":        "{:.1f}%",
        "EV":         "{:.1f}",
        "HH%":        "{:.1f}%",
        "HR/FB%":     "{:.1f}%",
        "PullAir%":   "{:.1f}%",
        "LA":         "{:.1f}°",
        "xwOBA":      "{:.3f}",
        "SweetSpot%": "{:.1f}%",
    })
    base_text = [
        {"selector": "th", "props": [("color", "#7dd3fc"), ("font-size", "12px"),
                                     ("font-weight", "800"), ("background-color", "#1a2744"),
                                     ("text-transform", "uppercase"), ("letter-spacing", "0.04em"),
                                     ("padding", "8px 10px")]},
        {"selector": "td", "props": [("color", "#e2e8f0"), ("font-size", "13px"),
                                     ("font-weight", "700"), ("padding", "6px 10px")]},
    ]
    styler = styler.set_table_styles(base_text)
    # Apply the same heat-map color ramp used by the Matchup heat-map board
    # so the slate leaderboard and the per-game board read consistently.
    def _ramp_styler(col):
        def _f(v):
            rgb, txt = _heatmap_color_for(col, v)
            if rgb is None: return ""
            return f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); color: {txt}; font-weight: 800;"
        return _f
    for col in df.columns:
        if col in HEATMAP_THRESHOLDS and col in show_cols:
            styler = styler.map(_ramp_styler(col), subset=[col])
    return styler

def style_rolling_table(df):
    if df.empty: return df
    styler = df.style.format(precision=1)
    base_text = [
        {"selector": "th", "props": [("color", "#7dd3fc"), ("font-size", "12px"),
                                     ("font-weight", "800"), ("background-color", "#1a2744"),
                                     ("text-transform", "uppercase"), ("letter-spacing", "0.04em")]},
        {"selector": "td", "props": [("color", "#e2e8f0"), ("font-size", "13px"), ("font-weight", "700")]},
    ]
    styler = styler.set_table_styles(base_text)
    for c in df.columns:
        if c.startswith("Form") or c in ("Power", "Contact"):
            styler = styler.map(heat_score, subset=[c])
    return styler

# ===========================================================================
# Matchup Heat-Map Board — horizontally-scrollable stat board
# ===========================================================================
# Color ramp: dark green = best, light green → yellow → orange → red = worst.
# Each column has its own (low, high) thresholds and a `reverse` flag for
# metrics where lower = better (e.g. SwStr%/Whiff%, GB% for power hitters).
# LA uses an "optimal range" curve that peaks at ~14 deg (sweet-spot zone)
# and falls off in either direction — flatter or popup launches go orange/red.
HEATMAP_THRESHOLDS = {
    # name           low      high    reverse  fmt
    "Matchup":      (95.0,    150.0,  False, "{:.1f}"),
    "ISO":          (0.130,   0.250,  False, "{:.3f}"),
    "Brl/BIP%":     (4.0,     14.0,   False, "{:.1f}%"),
    "FB%":          (28.0,    45.0,   False, "{:.1f}%"),
    "GB%":          (35.0,    50.0,   True,  "{:.1f}%"),
    "EV":           (86.0,    93.0,   False, "{:.1f}"),
    "HH%":          (32.0,    48.0,   False, "{:.1f}%"),
    "HR/FB%":       (8.0,     22.0,   False, "{:.1f}%"),
    "PullAir%":     (10.0,    28.0,   False, "{:.1f}%"),
    "LA":           (None,    None,   False, "{:.1f}°"),  # optimal-range, custom
    "xwOBA":        (0.290,   0.380,  False, "{:.3f}"),
    "SweetSpot%":   (28.0,    40.0,   False, "{:.1f}%"),
    # OPS is the universal "is this a good batter right now?" signal. The
    # 0.650 → 0.900 band maps roughly to replacement-level → MVP-tier so the
    # heat ramp colors fall in line with the rest of the rate stats.
    "OPS":          (0.650,   0.900,  False, "{:.3f}"),
}

# Friendly display headers for the Matchup heat-map board. Internal column
# names stay short so the rest of the codebase keeps working; only the visible
# <th> labels get the long human-readable text.
MATCHUP_HEATMAP_DISPLAY_LABELS = {
    "Matchup":     "Matchup",
    "ISO":         "ISO",
    "Brl/BIP%":    "BARREL%",
    "FB%":         "FLYBALL%",
    "GB%":         "Ground Ball %",
    "EV":          "Exit Velocity",
    "HH%":         "Hard hit %",
    "HR/FB%":      "HR/FB%",
    "PullAir%":    "PULL AIR%",
    "LA":          "LAUNCH ANGLE",
    "xwOBA":       "Xwoba %",
    "SweetSpot%":  "Sweet Spot%",
    "OPS":         "OPS",
    "Likely":      "Likely",
}

# Compact labels shown in the mobile card grid where horizontal space is at a
# premium. Each label has to fit inside a ~70px tile without wrapping more
# than one line, so abbreviations beat the long desktop headers.
MATCHUP_HEATMAP_MOBILE_LABELS = {
    "Matchup":     "MTCH",
    "ISO":         "ISO",
    "Brl/BIP%":    "BRL%",
    "FB%":         "FB%",
    "GB%":         "GB%",
    "EV":          "EV",
    "HH%":         "HH%",
    "HR/FB%":      "HR/FB",
    "PullAir%":    "PULL",
    "LA":          "LA",
    "xwOBA":       "xwOBA",
    "SweetSpot%":  "SWT%",
    "OPS":         "OPS",
    "Likely":      "LIKELY",
}

def _heatmap_rgb(pct):
    """Map pct in [0,1] to an RGB tuple along red→orange→yellow→light-green→dark-green."""
    pct = max(0.0, min(1.0, float(pct)))
    # 5-stop ramp matching the screenshots
    stops = [
        (0.00, (220,  53,  69)),   # red
        (0.25, (245, 130,  48)),   # orange
        (0.50, (250, 204,  21)),   # yellow
        (0.75, (134, 239, 172)),   # light green
        (1.00, ( 21, 128,  61)),   # dark green
    ]
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if pct <= p1:
            t = (pct - p0) / (p1 - p0) if p1 > p0 else 0.0
            return tuple(int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
    return stops[-1][1]

def _heatmap_color_for(col, value):
    """Return (background_rgb, text_color) tuple for one cell, or (None, None) if blank."""
    try:
        v = float(value)
    except Exception:
        return (None, None)
    if pd.isna(v):
        return (None, None)
    spec = HEATMAP_THRESHOLDS.get(col)
    if spec is None:
        return (None, None)
    low, high, reverse, _ = spec
    if col == "LA":
        # Optimal launch-angle window centered at ~14°, gives best in 10-18 range,
        # falls off toward 0 (grounders) and >25 (popups).
        center, span = 14.0, 14.0
        dist = abs(v - center) / span
        pct = max(0.0, 1.0 - dist)
    else:
        rng = (high - low)
        if rng <= 0:
            return (None, None)
        pct = (v - low) / rng
        if reverse:
            pct = 1.0 - pct
        pct = max(0.0, min(1.0, pct))
    rgb = _heatmap_rgb(pct)
    # readable text color depending on luminance
    lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0
    text = "#0f172a" if lum > 0.55 else "#ffffff"
    return (rgb, text)

_LIKELY_FALLBACK_REASONS = {
    "HR 🔥":           "Elite barrel + pitcher HR weakness",
    "Long Shot HR 💥": "Power profile, tougher matchup",
    "Strikeout ❌":    "High combined K%",
    "HRR 💎":          "Contact + run production profile",
    "BASES 🧭":        "OPS/SLG/ISO points to bases",
    "—":               "",
}


def _likely_with_reason(matchup, ceiling, b_row=None, p_row=None, khr=None, hr_form_pct_val=None):
    """Return ``(label, reason)`` for the Heat Maps Matchup Likely chip.

    ``label`` is the same string returned by :func:`_likely_label` (kept
    backward-compatible). ``reason`` is a compact stat-driven blurb shown
    under the chip; falls back to a generic line when batter/pitcher rows
    aren't supplied.
    """
    try:
        m_val = float(matchup)
    except Exception:
        m_val = None
    try:
        c_val = float(ceiling)
    except Exception:
        c_val = None

    # No live rows → degrade gracefully to the old behavior so non-heatmap
    # callers don't crash.
    if b_row is None and p_row is None:
        if m_val is None or c_val is None:
            return "—", ""
        if m_val >= 145 and c_val >= 75:
            lbl = "HR 🔥"
        elif m_val >= 130 and c_val >= 65:
            lbl = "Long Shot HR 💥"
        elif m_val >= 115:
            lbl = "BASES 🧭"
        elif m_val >= 100:
            lbl = "HRR 💎"
        else:
            lbl = "Strikeout ❌"
        return lbl, _LIKELY_FALLBACK_REASONS.get(lbl, "")

    def _bf(row, key, default):
        if row is None:
            return default
        try:
            v = row.get(key)
            if v is None or pd.isna(v):
                return default
            return float(v)
        except Exception:
            return default

    # Batter pieces
    b_k       = _bf(b_row, "K%",         22.0)
    b_bb      = _bf(b_row, "BB%",         8.0)
    b_barrel  = _bf(b_row, "Barrel%",     8.0)
    b_hardhit = _bf(b_row, "HardHit%",   38.0)
    b_iso     = _bf(b_row, "ISO",         0.155)
    b_xwoba   = _bf(b_row, "xwOBA",       0.318)
    b_xslg    = _bf(b_row, "xSLG",        0.410)
    b_slg     = _bf(b_row, "SLG",         b_xslg)
    b_obp     = _bf(b_row, "OBP",         0.320)
    # Canonical OPS read; fall back to OBP+SLG when the source row only
    # carries the components. Both BASES and HRR buckets use this as a
    # primary on-base + slug signal so the chip reflects the OPS column.
    b_ops     = _bf(b_row, "OPS",         b_obp + b_slg)
    b_pullair = (_bf(b_row, "Pull%", 40.0) * _bf(b_row, "FB%", 34.0)) / 100.0

    # Pitcher pieces — vulnerability + K rate
    p_k       = _bf(p_row, "K%",         22.0)
    p_xslg    = _bf(p_row, "xSLG",        0.410)
    p_barrel  = _bf(p_row, "Barrel%",     8.0)
    p_hardhit = _bf(p_row, "HardHit%",   38.0)
    p_hr      = _bf(p_row, "HR",          0.0)

    # Composite scores 0..100-ish
    combined_k = (b_k + p_k) / 2.0
    # Pitcher hr-vulnerability: high xSLG/Barrel-against + HR allowed = juicy.
    pitcher_vuln = (
        (p_xslg - 0.380) * 140
        + (p_barrel - 7.0) * 3.0
        + (p_hardhit - 36.0) * 0.5
        + p_hr * 0.4
        - (p_k - 22.0) * 0.6
    )
    # Batter HR engine: barrel/HH/ISO/PullAir — the inputs that actually drive HRs.
    hr_engine = (
        (b_barrel - 7.0) * 4.0
        + (b_hardhit - 36.0) * 0.8
        + (b_iso - 0.155) * 180
        + (b_pullair - 14.0) * 1.2
    )
    if hr_form_pct_val is not None:
        try:
            hr_engine += (float(hr_form_pct_val) - 40.0) * 0.25
        except Exception:
            pass

    # 1) HR 🔥 — both engines firing, K risk under control, K-adj HR confirms.
    khr_val = None
    try:
        khr_val = float(khr) if khr is not None else None
    except Exception:
        khr_val = None
    hr_call = (
        hr_engine >= 14
        and pitcher_vuln >= 8
        and combined_k <= 26
        and (m_val is None or m_val >= 130)
        and (c_val is None or c_val >= 65)
        and (khr_val is None or khr_val >= 55)
    )
    if hr_call:
        parts = []
        # OPS leads the HR reason when the bat is clearly raking — an MVP-tier
        # OPS in front of barrel/ISO communicates the "complete hitter" signal
        # better than any single rate stat.
        if b_ops >= 0.900:
            parts.append(f"OPS {b_ops:.3f}")
        if b_barrel >= 10.0:
            parts.append(f"{b_barrel:.0f}% barrel")
        elif b_barrel >= 8.0:
            parts.append("strong barrel")
        if b_iso >= 0.200:
            parts.append(f"{b_iso:.3f} ISO")
        if p_xslg >= 0.430 or p_barrel >= 9.0:
            parts.append("vs HR-prone arm")
        elif p_k <= 20.0:
            parts.append("low-K pitcher")
        reason = " + ".join(parts[:2]) if parts else _LIKELY_FALLBACK_REASONS["HR 🔥"]
        return "HR 🔥", reason

    # 2) Strikeout — combined K% genuinely elevated and not offset by elite contact.
    if combined_k >= 26 and b_barrel < 9.0 and b_iso < 0.170:
        reason = f"Combined K% {combined_k:.0f}"
        if p_k >= 26.0:
            reason += f" · pitcher {p_k:.0f}%"
        return "Strikeout ❌", reason

    # 3) HRR (Hits + Runs + RBIs) — contact-leaning hitters with on-base/xwOBA
    #    strength and lower K risk; not pure power but threat across H/R/RBI.
    #    Checked BEFORE Long Shot HR so a high-xwOBA / low-K contact bat doesn't
    #    get tagged as a HR long shot when power isn't the dominant signal.
    # HRR also fires when OPS alone is well above the league bar — an .820+
    # OPS with reasonable K risk reliably produces hits/runs/RBIs even when
    # the xwOBA sample is still warming up.
    hrr_call = (
        (b_xwoba >= 0.330 or b_ops >= 0.820)
        and combined_k <= 24
        and (b_barrel >= 7.0 or b_hardhit >= 38.0 or b_ops >= 0.820)
        and b_iso < 0.200
    )
    if hrr_call:
        parts = []
        if b_ops >= 0.820:
            parts.append(f"OPS {b_ops:.3f}")
        parts.append(f"xwOBA {b_xwoba:.3f}")
        if b_hardhit >= 40.0:
            parts.append(f"HH% {b_hardhit:.0f}")
        if combined_k <= 20.0:
            parts.append("low K risk")
        reason = " · ".join(parts[:2])
        return "HRR 💎", reason

    # 4) Long Shot HR — real power profile but matchup/form short of outright HR.
    long_shot = (
        (hr_engine >= 8 or b_iso >= 0.180 or b_barrel >= 10.0)
        and combined_k <= 30
        and (c_val is None or c_val >= 55)
    )
    if long_shot:
        parts = []
        if b_iso >= 0.180:
            parts.append(f"ISO {b_iso:.3f}")
        if b_barrel >= 10.0:
            parts.append(f"barrel {b_barrel:.0f}%")
        if p_k >= 25.0 or pitcher_vuln < 8:
            parts.append("tougher arm")
        reason = " · ".join(parts[:2]) if parts else _LIKELY_FALLBACK_REASONS["Long Shot HR 💥"]
        return "Long Shot HR 💥", reason

    # 5) BASES — default positive bucket: at least 1+ total bases likely
    #    (decent SLG/ISO/xwOBA, not flagged as a K).
    # OPS >= 0.700 is the league-average "decent bat" threshold — any of OPS,
    # SLG, ISO, or xwOBA above its respective bar is enough to expect bases.
    bases_call = (
        (b_ops >= 0.700 or b_slg >= 0.380 or b_iso >= 0.140 or b_xwoba >= 0.310)
        and combined_k < 28
    )
    if bases_call:
        parts = []
        if b_ops >= 0.800:
            parts.append(f"OPS {b_ops:.3f}")
        elif b_slg >= 0.420:
            parts.append(f"SLG {b_slg:.3f}")
        elif b_iso >= 0.140:
            parts.append(f"ISO {b_iso:.3f}")
        if b_xwoba >= 0.330:
            parts.append(f"xwOBA {b_xwoba:.3f}")
        reason = " · ".join(parts[:2]) if parts else _LIKELY_FALLBACK_REASONS["BASES 🧭"]
        return "BASES 🧭", reason

    # Anything left = tough K-prone matchup.
    reason = f"Combined K% {combined_k:.0f}" if combined_k > 0 else _LIKELY_FALLBACK_REASONS["Strikeout ❌"]
    return "Strikeout ❌", reason


def _likely_label(matchup, ceiling, b_row=None, p_row=None, khr=None, hr_form_pct_val=None):
    """Backward-compatible wrapper that returns only the Likely label string.

    See :func:`_likely_with_reason` for the (label, reason) version used by
    the Heat Maps Matchup UI.
    """
    return _likely_with_reason(matchup, ceiling, b_row=b_row, p_row=p_row,
                                khr=khr, hr_form_pct_val=hr_form_pct_val)[0]

def build_matchup_heatmap_board(lineup_df, batters_df, pitchers_df, opp_pitcher_name,
                                weather, park_factor,
                                arsenal_b=None, arsenal_p=None, opp_pitcher_id=None,
                                slate_date=None, home_abbr=None, venue=None,
                                side=None, opp_abbr=None):
    """Build the wide horizontal heat-map stat board for one team's lineup.

    Columns mirror the screenshot reference, with sensible fallbacks when a
    Statcast field isn't populated (rookies, low-PA samples).

    Fallback ladder for each cell:
      1. Real Savant value for the matched hitter (current season).
      2. Prior-season value (already merged into batters_df at startup via
         combine_first), for hitters whose current sample is too small.
      3. League-average proxy from the qualified-batter slice of batters_df,
         used only for cells that are STILL NaN after steps 1-2. This last
         step keeps every starting hitter from rendering as a long row of
         empty dashes when Savant simply hasn't published a value yet.
    """
    cols = ["Spot", "Hitter", "Team", "Crushes", "Matchup", "OPS", "ISO",
            "Brl/BIP%", "FB%", "GB%", "EV", "HH%", "HR/FB%", "PullAir%", "LA",
            "xwOBA", "SweetSpot%", "Likely"]
    if lineup_df.empty:
        return pd.DataFrame(columns=cols)

    # Compute league-avg fallbacks once per call from the full batters_df so
    # the proxies adjust automatically as the season progresses (they lock
    # to the actual median of qualified hitters loaded from Savant).
    def _league_avg(col, default):
        if batters_df is None or batters_df.empty or col not in batters_df.columns:
            return default
        s = pd.to_numeric(batters_df[col], errors="coerce").dropna()
        if s.empty:
            return default
        return float(s.median())

    _LG = {
        "K%":         _league_avg("K%",          22.0),
        "BB%":        _league_avg("BB%",          8.0),
        "ISO":        _league_avg("ISO",          0.155),
        "xwOBA":      _league_avg("xwOBA",        0.318),
        "xSLG":       _league_avg("xSLG",         0.410),
        "xOBP":       _league_avg("xOBP",         0.320),
        "Whiff%":     _league_avg("Whiff%",      24.0),
        "Swing%":     _league_avg("Swing%",      47.0),
        "Barrel%":    _league_avg("Barrel%",      8.0),
        "Pull%":      _league_avg("Pull%",       40.0),
        "SweetSpot%": _league_avg("SweetSpot%", 33.0),
        "FB%":        _league_avg("FB%",         34.0),
        "GB%":        _league_avg("GB%",         44.0),
        "HardHit%":   _league_avg("HardHit%",   38.0),
        "EV":         _league_avg("EV",          89.0),
        "LA":         _league_avg("LA",          12.0),
        # OPS = OBP + SLG. League-median fallback (~.730) keeps the heat-map
        # cell shaded near neutral for rookies/low-PA hitters with no value.
        "OPS":        _league_avg("OPS",         0.730),
    }
    # ID-first pitcher match keeps this row tied to the *selected slate game's*
    # probable pitcher rather than any pitcher with the same display name.
    p_row = find_pitcher_row(pitchers_df, opp_pitcher_name, pitcher_id=opp_pitcher_id)
    opp_pitches = _build_pitcher_arsenal_set(arsenal_p, opp_pitcher_id)
    try:
        slate_iso = pd.to_datetime(slate_date).strftime("%Y-%m-%d") if slate_date else today_ct().strftime("%Y-%m-%d")
    except Exception:
        slate_iso = today_ct().strftime("%Y-%m-%d")
    rows = []
    for _, r in lineup_df.iterrows():
        b_row = find_player_row(
            batters_df, r["name_key"], r["team"],
            player_id=r.get("player_id") if "player_id" in r.index else None,
        )
        pid = r.get("player_id") if "player_id" in r.index else None
        crushes = _crushes_cell(arsenal_b, pid, opp_pitches) if arsenal_b is not None else "—"
        try:
            form = get_batter_recent_form(pid, slate_iso) if pid else {}
        except Exception:
            form = {}
        l5 = form.get("L5", {}) if isinstance(form, dict) else {}
        l10 = form.get("L10", {}) if isinstance(form, dict) else {}
        l30 = form.get("L30D", {}) if isinstance(form, dict) else {}
        form_blend = _form_blend_adjustment(l10)
        try:
            hr_ctx = get_batter_hr_context(pid, slate_iso) if pid else {}
        except Exception:
            hr_ctx = {}
        opp_hand = r.get("opposing_pitch_hand", "")
        m   = matchup_score(b_row, p_row, r["lineup_spot"], weather, park_factor, r["bat_side"], opp_hand) + form_blend
        ts  = test_score(b_row, p_row)
        cl  = ceiling_score(b_row, weather, park_factor)
        zf  = zone_fit(b_row, p_row, r["bat_side"], opp_hand)
        hrf, hr_trend = hr_form_pct(b_row)
        khr = k_adj_hr(b_row, p_row, cl)

        def _g(key, default=None):
            if b_row is None: return default
            v = b_row.get(key)
            try:
                if v is None or pd.isna(v): return default
                return float(v)
            except Exception:
                return default

        # Pitches column = total swings_competitive (from bat-tracking) when
        # available, else fall back to plate appearances * 3.9 (league avg
        # pitches/PA) as a proxy. BIP from bat-tracking when available, else
        # estimated as PA * (1 - K%/100 - BB%/100). For lineup hitters with
        # neither bat-tracking nor PA on file (rookies just called up), use
        # a league-typical placeholder so the row is filled rather than blank.
        pa = _g("pa")
        sc = _g("SwingsComp")
        if sc is not None and sc > 0:
            pitches = sc
        elif pa is not None:
            pitches = pa * 3.9
        else:
            # Lineup-typical: ~4 PA/game * ~3.9 pitches/PA ≈ 15.6 pitches/game.
            # Anchor at 50 to land mid-scale on the heat ramp (200–1800).
            pitches = 50.0

        bip = _g("BIP")
        if bip is None:
            kp = _g("K%", _LG["K%"]) or _LG["K%"]
            bbp = _g("BB%", _LG["BB%"]) or _LG["BB%"]
            if pa is not None:
                bip = max(0.0, pa * (1 - kp / 100.0 - bbp / 100.0))
            else:
                # No PA on file — assume a typical starting-hitter sample
                # (~50 BIP/month) so the cell shows a neutral value.
                bip = 12.0

        iso     = _g("ISO", _LG["ISO"])
        xwoba   = _g("xwOBA", _LG["xwOBA"])
        # OPS is sourced from the canonical column populated by
        # standardize_columns. When the source row only has OBP+SLG we still
        # derive OPS on the fly so heat-map cells never collapse to "—".
        ops_val = _g("OPS")
        if ops_val is None:
            _obp = _g("OBP")
            _slg = _g("SLG")
            if _obp is not None and _slg is not None:
                ops_val = _obp + _slg
        if ops_val is None:
            ops_val = _LG["OPS"]

        barrel  = _g("Barrel%", _LG["Barrel%"])
        pull    = _g("Pull%", _LG["Pull%"])
        brl_bip = barrel  # Savant's barrel_batted_rate IS Brl/BIP%

        sweet   = _g("SweetSpot%", _LG["SweetSpot%"])
        fb      = _g("FB%", _LG["FB%"])
        gb      = _g("GB%", _LG["GB%"])
        hh      = _g("HardHit%", _LG["HardHit%"])
        ev      = _g("EV", _LG["EV"])
        la      = _g("LA", _LG["LA"])

        # HR/FB% = home runs per fly ball. HR comes from the Savant batter
        # row; fly-ball count is estimated from FB% × BIP. Falls back to a
        # league-typical ~13% when either input is missing so the cell isn't
        # blank for low-PA hitters.
        hr_total = _g("HR")
        if hr_total is not None and fb and bip and fb > 0 and bip > 0:
            fb_count = fb / 100.0 * bip
            hr_fb = round(hr_total / fb_count * 100.0, 1) if fb_count > 0 else 13.0
        else:
            hr_fb = 13.0

        # PullAir% proxy = Pull% × FB% / 100. Approximates the rate of balls
        # pulled in the air — the swing path most predictive of HR power.
        if pull is not None and fb is not None:
            pull_air = round(pull * fb / 100.0, 1)
        else:
            pull_air = None

        form_line = (
            f"L5 {_fmt_form_avg(l5.get('avg'))} | "
            f"L10 {_fmt_form_avg(l10.get('avg'))} | "
            f"30D {_fmt_form_avg(l30.get('avg'))}"
        )
        hr_last_line = _fmt_last_hr(hr_ctx)
        hr_l10_line = _fmt_hr_last10(hr_ctx)
        _lkl, _lkl_reason = _likely_with_reason(m, cl, b_row=b_row, p_row=p_row, khr=khr, hr_form_pct_val=hrf)
        rows.append({
            "Spot": int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
            "Hitter": r["player_name"],
            "Team": norm_team(r["team"]),
            "Crushes": crushes,
            "_PlayerId": int(pid) if pid is not None and not (isinstance(pid, float) and pd.isna(pid)) else None,
            "_BatSide": r.get("bat_side", ""),
            "_OppPitchHand": opp_hand or "",
            "_OppPitcherId": int(opp_pitcher_id) if opp_pitcher_id else None,
            "_OppPitcherName": opp_pitcher_name or "",
            "_SlateDate": slate_iso,
            # Live slate venue/home — used by the player detail HR Park row.
            # Without these the modal has to guess from team abbr, which
            # mis-attributes the park when the batter is away (their own
            # team is not the home team).
            "_HomeAbbr": home_abbr or "",
            "_Venue": venue or "",
            "Loc": ("vs" if (side == "home" or (home_abbr and str(r.get("team", "")).upper() == str(home_abbr).upper())) else "@") if (side or home_abbr) else "",
            "Opp": opp_abbr or "",
            "_Form": form_line,
            "_FormL5_AVG": l5.get("avg"),
            "_FormL10_AVG": l10.get("avg"),
            "_FormL30_AVG": l30.get("avg"),
            "_LastHR": hr_last_line,
            "_HRLast10": hr_l10_line,
            "_LastHRDate": hr_ctx.get("last_hr_date") if hr_ctx else None,
            "_HRLast10N": hr_ctx.get("hr_last_10") if hr_ctx else None,
            "Matchup": m,
            "OPS": round(ops_val, 3) if ops_val is not None else None,
            "ISO": round(iso, 3) if iso is not None else None,
            "Brl/BIP%": round(brl_bip, 1) if brl_bip is not None else None,
            "FB%": round(fb, 1) if fb is not None else None,
            "GB%": round(gb, 1) if gb is not None else None,
            "EV": round(ev, 1) if ev is not None else None,
            "HH%": round(hh, 1) if hh is not None else None,
            "HR/FB%": hr_fb,
            "PullAir%": pull_air,
            "LA": round(la, 1) if la is not None else None,
            "xwOBA": round(xwoba, 3) if xwoba is not None else None,
            "SweetSpot%": round(sweet, 1) if sweet is not None else None,
            "Likely": _lkl,
            "_LikelyReason": _lkl_reason,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Spot").reset_index(drop=True)
    return df

# Columns that are never shown as their own heat-map cell — Spot collapses
# into the row label; _HR Trend is rendered alongside HR Form for the arrow;
# the _Form / _LastHR / _HRLast10 / _LikelyReason fields are surfaced inside
# the Hitter cell or the LIKELY tile.
_MATCHUP_HIDDEN_COLS = (
    "Spot", "_HR Trend", "_Form", "_FormL5_AVG", "_FormL10_AVG", "_FormL30_AVG",
    "_LastHR", "_HRLast10", "_LastHRDate", "_HRLast10N", "_LikelyReason",
    "_PlayerId", "_BatSide", "_OppPitchHand", "_OppPitcherId", "_OppPitcherName", "_SlateDate",
    "_HomeAbbr", "_Venue", "Loc", "Opp",
)


def _matchup_display_cols(df):
    return [c for c in df.columns if c not in _MATCHUP_HIDDEN_COLS]


def render_matchup_heatmap_html(df):
    """Render the wide heat-map board as an HTML table that scrolls horizontally
    on mobile + desktop. The first two columns (Spot, Hitter) are sticky so
    the player stays visible while you swipe through the stat columns.

    Returns only the desktop table block — the per-player interactive cards
    are emitted by :func:`render_matchup_board_with_sort` so a real
    Streamlit button can be fused into each card's footer.
    """
    if df is None or df.empty:
        return '<div class="mhm-empty">Lineup not posted yet.</div>'

    # Numeric columns in display order (everything except identifiers/Likely).
    numeric_cols = [c for c in df.columns if c in HEATMAP_THRESHOLDS]
    display_cols = _matchup_display_cols(df)

    css = """
<style>
/* Heat-map table: new dark skin (global .mhm-* classes supplement these) */
.mhm-wrap { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch;
  border: 1px solid rgba(255,255,255,0.07); border-radius: 0 0 5px 5px;
  background: #0a1628; margin: 0 0 12px 0; }
.mhm-wrap::-webkit-scrollbar { height: 7px; }
.mhm-wrap::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.09); border-radius: 4px; }
.mhm-wrap::-webkit-scrollbar-track { background: transparent; }
.mhm-table { border-collapse: separate; border-spacing: 0; width: max-content; min-width: 100%;
  font-family: 'DM Mono', 'DM Sans', monospace; }
.mhm-table th { position: sticky; top: 0; z-index: 3; background: #081526; color: #3a5a7a;
  font-size: 0.6rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em;
  padding: 7px 10px; text-align: center; white-space: nowrap;
  border-bottom: 1px solid rgba(255,255,255,0.07); }
.mhm-table td { padding: 6px 10px; text-align: center; font-size: 0.78rem;
  font-weight: 600; color: #b0c8e4; white-space: nowrap;
  border-bottom: 1px solid rgba(255,255,255,0.04); }
.mhm-table tr:last-child td { border-bottom: none; }
.mhm-table tr:hover td { background: rgba(0,200,150,0.04) !important; }
.mhm-col-hitter { position: sticky; left: 0; z-index: 2; background: #0a1628 !important;
  text-align: left !important; min-width: 170px;
  border-right: 1px solid rgba(255,255,255,0.06); }
.mhm-table th.mhm-col-hitter { z-index: 4; background: #081526 !important; }
.mhm-table tr:hover td.mhm-col-hitter { background: rgba(0,200,150,0.06) !important; }
.mhm-hitter-name { font-weight: 700; color: #e2eeff; font-family: 'DM Sans', sans-serif; }
.mhm-hitter-meta { font-size: 0.6rem; color: #3a5a7a; font-weight: 500; margin-top: 1px; }
.mhm-hitter-crushes { font-size: 0.6rem; color: #00c896; font-weight: 600; margin-top: 2px;
  white-space: normal; line-height: 1.15; max-width: 220px; }
.mhm-hitter-form { font-size: 0.6rem; color: #7dd3fc; font-weight: 600; margin-top: 2px;
  white-space: normal; line-height: 1.15; max-width: 220px; }
.mhm-hitter-hr { font-size: 0.6rem; color: #fca5a5; font-weight: 600; margin-top: 2px;
  white-space: normal; line-height: 1.15; max-width: 220px; }
.mhm-likely { padding: 2px 7px; border-radius: 3px; font-size: 0.64rem; font-weight: 700;
  background: rgba(255,255,255,0.06); color: #b0c8e4; display: inline-block;
  font-family: 'DM Sans', sans-serif; }
.mhm-likely-reason { font-size: 0.58rem; font-weight: 500; color: #3a5a7a;
  margin-top: 3px; line-height: 1.2; max-width: 180px; white-space: normal;
  text-align: center; margin-left: auto; margin-right: auto; }
.mhm-tile-reason { font-size: 0.56rem; font-weight: 500; color: #3a5a7a;
  margin-top: 3px; line-height: 1.1; text-align: center; padding: 0 4px; white-space: normal; }
.mhm-na { background-color: rgba(255,255,255,0.03) !important; color: #3a5a7a !important; font-weight: 600; }
.mhm-empty { padding: 14px 16px; color: #3a5a7a; font-weight: 500; font-style: italic; font-size: 0.82rem; }
.mhm-trend { display: inline-block; margin-left: 3px; font-size: 0.72rem;
  font-weight: 700; line-height: 1; vertical-align: baseline; }
.mhm-trend-up   { color: #10b981; }
.mhm-trend-down { color: #ef4444; }
.mhm-trend-flat { color: #3a5a7a; }
@media (max-width: 700px) { .mhm-wrap { display: none; } }
</style>
"""

    # Build header row — use friendly display labels for stat columns
    # (Matchup heat-map only; the underlying df keeps short internal names).
    header_cells = []
    for c in display_cols:
        cls = "mhm-col-hitter" if c == "Hitter" else ""
        label = MATCHUP_HEATMAP_DISPLAY_LABELS.get(c, c)
        header_cells.append(f'<th class="{cls}">{label}</th>')

    # Build desktop-table body rows. The per-player interactive cards (same
    # numbers, same heat colors) are rendered separately by
    # render_matchup_player_card_html below so the orchestrator can fuse a
    # Streamlit button into each card's footer.
    body_rows = []
    for _, r in df.iterrows():
        cells = []
        for c in display_cols:
            v = r.get(c)
            if c == "Hitter":
                spot = r.get("Spot", "")
                team = r.get("Team", "")
                crushes = r.get("Crushes", "")
                if crushes is None or (isinstance(crushes, float) and pd.isna(crushes)):
                    crushes = ""
                crushes_str = str(crushes).strip()
                crushes_html = (
                    f'<div class="mhm-hitter-crushes" title="Pitch types this hitter crushes (🔥 = in opposing pitcher arsenal)">'
                    f'💥 {crushes_str}</div>'
                ) if crushes_str and crushes_str != "—" else ""
                form_line = r.get("_Form", "")
                if form_line is None or (isinstance(form_line, float) and pd.isna(form_line)):
                    form_line = ""
                form_line = str(form_line).strip()
                form_html = (
                    f'<div class="mhm-hitter-form" title="Recent batting AVG — L5 games / L10 games / last 30 days (MLB StatsAPI gameLog)">'
                    f'📈 Form: {form_line}</div>'
                ) if form_line else ""
                last_hr = r.get("_LastHR", "")
                if last_hr is None or (isinstance(last_hr, float) and pd.isna(last_hr)):
                    last_hr = ""
                last_hr = str(last_hr).strip()
                hr_l10 = r.get("_HRLast10", "")
                if hr_l10 is None or (isinstance(hr_l10, float) and pd.isna(hr_l10)):
                    hr_l10 = ""
                hr_l10 = str(hr_l10).strip()
                hr_lines = []
                if last_hr and last_hr != "—":
                    hr_lines.append(f"💣 Last HR: {last_hr}")
                if hr_l10 and hr_l10 != "—":
                    hr_lines.append(f"🔟 {hr_l10}")
                hr_html = (
                    f'<div class="mhm-hitter-hr" title="Most recent home run date and HR over the batter\'s last 10 played games (MLB StatsAPI gameLog)">'
                    + " · ".join(hr_lines) + '</div>'
                ) if hr_lines else ""
                cells.append(
                    f'<td class="mhm-col-hitter">'
                    f'<div class="mhm-hitter-name">{spot}. {v}</div>'
                    f'<div class="mhm-hitter-meta">{team}</div>'
                    f'{crushes_html}'
                    f'{form_html}'
                    f'{hr_html}'
                    f'</td>'
                )
                continue
            if c == "Team" or c == "Crushes":
                # Already rendered alongside Hitter — skip dedicated cell.
                continue
            if c == "Likely":
                reason = r.get("_LikelyReason", "")
                if reason is None or (isinstance(reason, float) and pd.isna(reason)):
                    reason = ""
                reason = str(reason).strip()
                if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "—":
                    cells.append('<td class="mhm-na">—</td>')
                else:
                    reason_html = f'<div class="mhm-likely-reason">{reason}</div>' if reason else ""
                    cells.append(
                        f'<td><span class="mhm-likely">{v}</span>{reason_html}</td>'
                    )
                continue
            spec = HEATMAP_THRESHOLDS.get(c)
            fmt = spec[3] if spec else "{}"
            if v is None or (isinstance(v, float) and pd.isna(v)):
                cells.append('<td class="mhm-na">—</td>')
                continue
            try:
                txt = fmt.format(float(v))
            except Exception:
                txt = str(v)
            if c == "HR Form":
                trend = r.get("_HR Trend")
                arrow_map = {"↑": ("mhm-trend-up", "↑"),
                             "↓": ("mhm-trend-down", "↓"),
                             "→": ("mhm-trend-flat", "→")}
                arrow_cls, arrow_glyph = arrow_map.get(
                    trend if isinstance(trend, str) else "",
                    ("mhm-trend-flat", "→"),
                )
                txt = f'{txt} <span class="mhm-trend {arrow_cls}">{arrow_glyph}</span>'
            rgb, text_color = _heatmap_color_for(c, v)
            if rgb is None:
                cells.append(f'<td>{txt}</td>')
            else:
                style = (f'background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]}); '
                         f'color: {text_color}; font-weight: 800;')
                cells.append(f'<td style="{style}">{txt}</td>')
        body_rows.append(f'<tr>{"".join(cells)}</tr>')

    # Filter out the "Team" and "Crushes" columns from header (both are rendered
    # in the Hitter cell as sub-lines so the table stays mobile-friendly).
    header_cells_filtered = [
        h for h, c in zip(header_cells, display_cols) if c not in ("Team", "Crushes")
    ]
    table_html = (
        f'{css}<div class="mhm-wrap"><table class="mhm-table">'
        f'<thead><tr>{"".join(header_cells_filtered)}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        f'</table></div>'
    )
    return table_html


def render_matchup_player_card_html(row, display_cols):
    """Render a hitter as a horizontal scouting intelligence row.

    Replaces the old 4-col heat-map tile grid with a compact horizontal strip:
    [spot] [name / meta / crush notes] [5 key metric blocks] [tier + score + likely]

    The row element is left open-bottomed so the orchestrator can fuse a
    Streamlit CTA button below it (same positioning mechanism as before).
    """
    r = row
    spot = r.get("Spot", "")
    hitter_name = r.get("Hitter", "")
    team = r.get("Team", "")
    bat_side = r.get("_bat_side", "") or r.get("_BatSide", "") or r.get("Bat", "") or ""
    bat_label = format_batter_stance(bat_side, "")
    opp_pitcher_name = r.get("_OppPitcherName", "") or r.get("_opposing_pitcher", "") or ""
    opp_pitch_hand = r.get("_opp_pitch_hand", "") or r.get("_OppPitchHand", "") or ""
    opp_hand_label = format_pitcher_hand(opp_pitch_hand, "")

    crushes = r.get("Crushes", "")
    if crushes is None or (isinstance(crushes, float) and pd.isna(crushes)):
        crushes = ""
    crushes_str = str(crushes).strip()

    form_line = r.get("_Form", "")
    if form_line is None or (isinstance(form_line, float) and pd.isna(form_line)):
        form_line = ""
    form_line = str(form_line).strip()

    last_hr = r.get("_LastHR", "")
    if last_hr is None or (isinstance(last_hr, float) and pd.isna(last_hr)):
        last_hr = ""
    last_hr = str(last_hr).strip()

    crush_html = (
        f'<span class="scout-player-crush">&#8623; {crushes_str}</span>'
        if crushes_str and crushes_str != "—" else ""
    )
    form_html = (
        f'<span class="scout-player-form">{form_line}</span>'
        if form_line else ""
    )
    hr_note = ""
    if last_hr and last_hr != "—":
        hr_note = f'<span class="scout-player-form" style="color:#fca5a5;">HR: {last_hr}</span>'

    meta_parts = [team]
    if bat_label:
        meta_parts.append(f"Bats {bat_label}")
    if opp_pitcher_name:
        meta_parts.append(
            f"vs {opp_pitcher_name}"
            + (f" ({opp_hand_label})" if opp_hand_label else "")
        )
    meta_str = " &middot; ".join(p for p in meta_parts if p)
    hand_badges = []
    if bat_label:
        hand_badges.append(f'<span class="scout-hand-badge">{bat_label}</span>')
    if opp_hand_label:
        hand_badges.append(f'<span class="scout-vs-hand">vs {opp_hand_label}</span>')
    hand_badges_html = (
        f'<div class="scout-hand-row">{"".join(hand_badges)}</div>'
        if hand_badges else ""
    )

    # Tier from matchup score
    matchup_v = r.get("Matchup")
    _mv_safe = float(matchup_v) if (
        matchup_v is not None and not (isinstance(matchup_v, float) and pd.isna(matchup_v))
    ) else 0
    tier_key, tier_label = score_tier(_mv_safe)
    matchup_disp = f"{_mv_safe:.1f}" if _mv_safe else "—"

    # 7-metric heat-map strip: barrel, flyball, EV, hard-hit, HR/FB, ISO, pull-air
    STRIP_COLS = [
        ("ISO",      "ISO",    "{:.3f}"),
        ("Brl/BIP%", "BARREL", "{:.1f}"),
        ("FB%",      "FLYBALL","{:.1f}"),
        ("EV",       "EV",     "{:.1f}"),
        ("HH%",      "HARDHIT","{:.1f}"),
        ("HR/FB%",   "HR/FB",  "{:.1f}"),
        ("PullAir%", "PULLAIR","{:.1f}"),
    ]
    metric_tiles = []
    for col, label, fmt in STRIP_COLS:
        v = r.get(col)
        spec = HEATMAP_THRESHOLDS.get(col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            metric_tiles.append(
                f'<div class="scout-metric">'
                f'<span class="scout-metric-label">{label}</span>'
                f'<span class="scout-metric-val" style="color:#4e6a8a;">—</span>'
                f'<div class="scout-metric-bar"></div>'
                f'</div>'
            )
            continue
        try:
            txt = fmt.format(float(v))
        except Exception:
            txt = str(v)
        if col == "HR Form":
            trend = r.get("_HR Trend")
            arrow_map = {
                "↑": ("mhm-trend-up", "&#8593;"),
                "↓": ("mhm-trend-down", "&#8595;"),
                "→": ("mhm-trend-flat", "&#8594;"),
            }
            arrow_cls, arrow_glyph = arrow_map.get(
                trend if isinstance(trend, str) else "", ("mhm-trend-flat", "&#8594;")
            )
            txt = f'{txt}&thinsp;<span class="mhm-trend {arrow_cls}">{arrow_glyph}</span>'

        rgb, text_color = _heatmap_color_for(col, v)
        if rgb:
            bar_color = f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"
            val_style = (
                f"color:{text_color};background:rgb({rgb[0]},{rgb[1]},{rgb[2]});"
                "padding:1px 3px;border-radius:2px;font-size:0.73rem;"
            )
        else:
            bar_color = "#1e3a5c"
            val_style = ""

        bar_pct = 50
        if spec:
            lo, hi = spec[0], spec[1]
            try:
                pct = max(0, min(100, (float(v) - lo) / max(hi - lo, 0.001) * 100))
                bar_pct = int(100 - pct) if spec[2] else int(pct)
            except Exception:
                bar_pct = 50

        metric_tiles.append(
            f'<div class="scout-metric">'
            f'<span class="scout-metric-label">{label}</span>'
            f'<span class="scout-metric-val" style="{val_style}">{txt}</span>'
            f'<div class="scout-metric-bar">'
            f'<div class="scout-metric-bar-fill" style="width:{bar_pct}%;background:{bar_color};opacity:0.65;"></div>'
            f'</div>'
            f'</div>'
        )

    # Likely outcome
    likely_v = r.get("Likely", "")
    if likely_v is None or (isinstance(likely_v, float) and pd.isna(likely_v)):
        likely_v = ""
    likely_str = str(likely_v).strip()
    likely_reason = r.get("_LikelyReason", "")
    if likely_reason is None or (isinstance(likely_reason, float) and pd.isna(likely_reason)):
        likely_reason = ""
    likely_reason = str(likely_reason).strip()

    tier_cls = f"tier-{tier_key}"
    likely_html = (
        f'<span class="scout-likely">{likely_str}</span>'
        if likely_str and likely_str != "—" else ""
    )
    reason_html = (
        f'<span class="scout-likely-reason">{likely_reason}</span>'
        if likely_reason else ""
    )

    return (
        f'<div class="scout-row {tier_cls}">'
        f'<div class="scout-spot">{spot}</div>'
        f'<div class="scout-player">'
        f'<span class="scout-player-name">{hitter_name}</span>'
        f'{hand_badges_html}'
        f'<span class="scout-player-meta">{meta_str}</span>'
        f'{crush_html}{form_html}{hr_note}'
        f'</div>'
        f'<div class="scout-metrics">{"".join(metric_tiles)}</div>'
        f'<div class="scout-outcome">'
        f'<span class="tier {tier_cls}">{tier_label}</span>'
        f'<span class="scout-matchup-score">{matchup_disp}</span>'
        f'<span class="scout-matchup-label">SCORE</span>'
        f'{likely_html}'
        f'{reason_html}'
        f'</div>'
        f'</div>'
    )

# Columns the user can sort the heat-map board by, in display order.
# "Lineup Spot" maps to the default Spot ordering (1-9) — exposed as the first
# option so the existing default behavior is preserved.
MATCHUP_SORTABLE_COLUMNS = [
    "Lineup Spot", "Hitter",
    "Matchup", "ISO", "Brl/BIP%", "FB%", "GB%", "EV", "HH%", "HR/FB%",
    "PullAir%", "LA", "xwOBA", "SweetSpot%", "Likely",
]

def sort_matchup_board(df, sort_col, descending):
    """Return a copy of the heat-map board sorted by `sort_col`.

    Numeric columns (everything in HEATMAP_THRESHOLDS) sort by their numeric
    value with NaNs pushed to the bottom regardless of direction so blank
    cells never crowd the top of a high-to-low view. "Lineup Spot" restores
    the original 1-9 batting order. "Hitter" / "Likely" sort as strings.
    """
    if df is None or df.empty:
        return df
    if sort_col == "Lineup Spot" or sort_col not in df.columns:
        return df.sort_values("Spot").reset_index(drop=True)
    if sort_col in HEATMAP_THRESHOLDS:
        numeric = pd.to_numeric(df[sort_col], errors="coerce")
        out = df.assign(_sort=numeric).sort_values(
            "_sort", ascending=not descending, na_position="last"
        ).drop(columns=["_sort"]).reset_index(drop=True)
        return out
    # Non-numeric (Hitter, Likely) — string sort, blanks last.
    return df.sort_values(
        sort_col, ascending=not descending, na_position="last", key=lambda s: s.astype(str)
    ).reset_index(drop=True)

# ===========================================================================
# Player detail dialog — premium dark-themed modal opened by tapping a tile
# ===========================================================================
from services.player_detail import (
    fetch_batter_game_log as _pd_fetch_batter_game_log,
    build_split_windows as _pd_build_split_windows,
    compute_pitcher_rating as _pd_compute_pitcher_rating,
    build_bvp_rows as _pd_build_bvp_rows,
    compute_player_grade as _pd_compute_player_grade,
    format_game_log_rows as _pd_format_game_log_rows,
    headshot_url as _pd_headshot_url,
    heatmap_style_for as _pd_heatmap_style_for,
    classify_pitcher_tier as _pd_classify_pitcher_tier,
    filter_log_for_split as _pd_filter_log_for_split,
    split_label_to_key as _pd_split_label_to_key,
)

# HR Due Indicator landed in services/player_detail.py in this PR. Wrap in a
# defensive import so a stale deployment never bricks the whole app — if the
# helper is missing we fall back to a no-op returning a "no data" payload.
try:
    from services.player_detail import compute_hr_due_indicator as _pd_compute_hr_due
except ImportError:
    def _pd_compute_hr_due(**_kwargs):
        return {"score": 0, "total": 6, "label": "Cold", "criteria": []}

# Defensive imports for the two helpers added most recently (PR #54).
# If a deployment ever runs against a stale services/player_detail.py
# (e.g. before Streamlit Cloud finishes picking up the redeploy), these
# names would otherwise raise ImportError at module import and crash the
# whole app. Local fallbacks preserve UI behavior: team_logo_url -> None
# drops the chip to its existing text-only path, and short_opp_abbr
# returns a trimmed uppercase string matching the published contract.
try:
    from services.player_detail import team_logo_url as _pd_team_logo_url
except ImportError:
    def _pd_team_logo_url(_abbr):
        return None
try:
    from services.player_detail import short_opp_abbr as _pd_short_opp_abbr
except ImportError:
    def _pd_short_opp_abbr(team_abbr, max_len=3):
        if not team_abbr:
            return ""
        s = str(team_abbr).strip().upper()
        return s[: max(1, int(max_len))] if s else ""


def _pd_hm_cell(metric: str, value, formatted: str) -> str:
    """Wrap one metric value in a heat-map-colored ``<span>``.

    ``metric`` selects the threshold band (see ``services.player_detail``).
    ``formatted`` is the already-stringified display value ("—", "27.5%", etc.).
    Neutral cells render without color so empty/unknown metrics stay readable.
    """
    style = _pd_heatmap_style_for(metric, value)
    if not style["css_class"]:
        return formatted
    return (
        f'<span class="pdc-hm {style["css_class"]}">'
        f'{formatted}</span>'
    )


@st.cache_data(ttl=900, show_spinner=False)
def _player_detail_game_log_cached(player_id: int, season: int) -> list:
    """Cached per-player game-log fetch — keyed (player_id, season) so a
    typical slate triggers ~18 calls once and reuses them across every dialog
    open."""
    return _pd_fetch_batter_game_log(player_id, season)


def _fmt_pct(v, places=1):
    if v is None: return "—"
    try:
        return f"{float(v):.{places}f}%"
    except Exception:
        return "—"


def _fmt_slg(v):
    if v is None: return "—"
    try:
        s = f"{float(v):.3f}"
        return s[1:] if s.startswith("0.") else s
    except Exception:
        return "—"


def _build_player_detail_payload(player_row, pitcher_row_df, slate_date):
    """Assemble everything the detail dialog needs for one batter.

    Returns dict with keys: header, splits, bvp_rows, rating, game_log_rows,
    recent_chart.
    """
    pid = player_row.get("_PlayerId")
    name = player_row.get("Hitter", "")
    team = player_row.get("Team", "")
    spot = player_row.get("Spot", "")
    bat_side = player_row.get("_BatSide", "")
    opp_pid = player_row.get("_OppPitcherId")
    opp_name = player_row.get("_OppPitcherName", "")
    slate_iso = player_row.get("_SlateDate") or (
        pd.to_datetime(slate_date).strftime("%Y-%m-%d") if slate_date else today_ct().strftime("%Y-%m-%d")
    )
    try:
        season = int(slate_iso[:4])
    except Exception:
        season = today_ct().year

    # Game log (cached). Falls back to prior season for early-spring slates.
    game_log = _player_detail_game_log_cached(int(pid), season) if pid else []
    if not game_log:
        game_log = _player_detail_game_log_cached(int(pid), season - 1) if pid else []
        log_season = season - 1
    else:
        log_season = season

    try:
        end_dt = date.fromisoformat(slate_iso[:10])
    except Exception:
        end_dt = today_ct()

    splits = _pd_build_split_windows(game_log, log_season, end_dt)

    # Opposing pitcher — pitcher_row_df is the slate's pitchers_df.
    pitch_hand = ""
    p_dict = None
    if opp_pid and pitcher_row_df is not None and not pitcher_row_df.empty:
        try:
            p_row = find_pitcher_row(pitcher_row_df, opp_name, pitcher_id=int(opp_pid))
            if p_row is not None and not (hasattr(p_row, "empty") and p_row.empty):
                # Convert pandas Series to dict for the pure helper
                if hasattr(p_row, "to_dict"):
                    p_dict = p_row.to_dict()
                else:
                    p_dict = dict(p_row)
        except Exception:
            p_dict = None
    if opp_pid:
        try:
            pitch_hand = _fetch_pitcher_throws(int(opp_pid)) or ""
        except Exception:
            pitch_hand = ""

    rating = _pd_compute_pitcher_rating(p_dict)
    bvp_rows = _pd_build_bvp_rows(
        batter_row=None, pitcher_row=p_dict, season_splits=splits,
        bat_side=bat_side, pitch_hand=pitch_hand,
    )
    log_rows = _pd_format_game_log_rows(game_log, limit=10)

    # Recent chart inputs: last 10 hits-per-game (for green bar chart strip).
    recent_slice = game_log[-10:] if game_log else []
    recent = [r.get("h", 0) for r in recent_slice]
    recent_opps = [
        {"abbr": r.get("opponent") or "",
         "logo": _pd_team_logo_url(r.get("opponent") or ""),
         "is_home": bool(r.get("is_home"))}
        for r in recent_slice
    ]
    if recent:
        avg_h = sum(recent) / len(recent)
        sorted_r = sorted(recent)
        mid = len(sorted_r) // 2
        median_h = sorted_r[mid] if len(sorted_r) % 2 == 1 else (sorted_r[mid - 1] + sorted_r[mid]) / 2
    else:
        avg_h = median_h = None

    opp_team_abbr = str(player_row.get("Opp") or "").strip().upper()

    # HR Due Indicator — six-criterion composite ("is this batter overdue?").
    # Inputs come from data we already have on the slate row + opposing pitcher
    # row + game log; the helper degrades gracefully when any one signal is
    # missing so the card never crashes.
    own_team = str(player_row.get("Team") or "").strip().upper()
    loc = str(player_row.get("Loc") or "").strip()
    # Resolve the home team for this specific matchup. Prefer the explicit
    # ``_HomeAbbr`` plumbed in from the slate game row — that is the ground
    # truth for "whose park is this game played at." Only fall back to a
    # Loc-based inference (or the opposing-team guess) when the slate field
    # is absent, because the batter's own team is not always the home team.
    explicit_home = str(player_row.get("_HomeAbbr") or "").strip().upper()
    if explicit_home:
        home_abbr_for_park = explicit_home
    elif loc == "vs":
        home_abbr_for_park = own_team
    elif loc == "@":
        home_abbr_for_park = opp_team_abbr or own_team
    else:
        # No location info at all — leave blank rather than guessing the
        # opponent's park, which is the bug we were trying to fix.
        home_abbr_for_park = ""
    try:
        park_factor_val = DEFAULT_PARK_FACTORS.get(home_abbr_for_park) if home_abbr_for_park else None
    except Exception:
        park_factor_val = None
    # Prefer an explicit live venue/stadium name from the slate row — the
    # ``_Venue`` field is plumbed straight from ``game_row["venue"]`` (MLB
    # StatsAPI ``gameData.venue.name``). Common live field names are also
    # accepted so upstream feeds that publish under ``Venue``/``Stadium``/
    # ``Ballpark``/``Park`` (and lower-case variants) still resolve. The HR
    # Due helper will fall back to its team-abbr -> ballpark mapping only
    # when none of these are present.
    park_name_val = None
    if hasattr(player_row, "get"):
        for _vk in ("_Venue", "Venue", "Stadium", "Ballpark", "Park",
                    "venue", "stadium", "ballpark", "park",
                    "venue_name", "VenueName"):
            _vv = player_row.get(_vk)
            if _vv:
                park_name_val = str(_vv).strip()
                if park_name_val:
                    break
    # Opposing pitcher fields — the helper reads Barrel% first (primary HR
    # signal) and falls back to HR/9 if barrel data is absent. Forward the
    # pitcher row as-is so the helper can probe its preferred set of column
    # names (Barrel%, Brl/BIP%, barrel_batted_rate, etc.), and explicitly
    # surface HR/9 derived from HR/IP when it isn't precomputed.
    p_hr9 = None
    if p_dict is not None:
        for key in ("HR/9", "hr9", "homeRunsPer9"):
            v = p_dict.get(key)
            if v is not None:
                p_hr9 = v
                break
    hr_due = _pd_compute_hr_due(
        game_log=game_log,
        season_barrel_pct=player_row.get("Brl/BIP%") or player_row.get("Barrel%"),
        season_la=player_row.get("LA"),
        recent_la=None,
        opp_pitcher_row=(
            {**(p_dict or {}), "HR/9": p_hr9} if p_dict is not None else None
        ),
        park_factor=park_factor_val,
        park_name=park_name_val,
        home_team=home_abbr_for_park,
    )

    return {
        "header": {
            "name": name, "team": team, "spot": spot,
            "bat_side": bat_side, "pitch_hand": pitch_hand,
            "opp_pitcher": opp_name,
            "opp_team": opp_team_abbr,
            "slate_date": slate_iso,
            "player_id": int(pid) if pid else None,
            "headshot": _pd_headshot_url(pid),
        },
        "splits": splits,
        "bvp_rows": bvp_rows,
        "rating": rating,
        "game_log_rows": log_rows,
        "game_log": game_log,
        "log_season": log_season,
        "slate_iso": slate_iso,
        "recent": {"values": recent, "avg": avg_h, "median": median_h,
                   "opponents": recent_opps},
        "hr_due": hr_due,
        # Carry the batter's own heat-map metrics so the dialog can show a
        # "what the tile said" recap (keeps numbers consistent w/ the board).
        "tile": {
            "Matchup": player_row.get("Matchup"),
            "OPS": player_row.get("OPS"),
            "ISO": player_row.get("ISO"),
            "Brl/BIP%": player_row.get("Brl/BIP%"),
            "HR/FB%": player_row.get("HR/FB%"),
            "xwOBA": player_row.get("xwOBA"),
            "Likely": player_row.get("Likely"),
            "LikelyReason": player_row.get("_LikelyReason"),
            "LastHR": player_row.get("_LastHR"),
            "Form": player_row.get("_Form"),
        },
    }


_PLAYER_DETAIL_CSS = """
<style>
/* ---------------------------------------------------------------
   Modal-scoped dark surface. The app's Streamlit theme is "light"
   (light purple background, near-black text). The player detail
   dialog rendered by st.dialog inherits that light surface, which
   made the dark-card content look mismatched and the Streamlit
   chrome (radio chips, helper text) appear as dark-on-dark or
   washed-out gray. We force the dialog container itself to a
   premium dark surface and recolor every Streamlit-rendered text
   node inside it to light slate so the modal reads as one cohesive
   dark card matching the screenshots.
   --------------------------------------------------------------- */
div[data-testid="stDialog"] > div > div,
div[role="dialog"] {
  background: #0b1220 !important;
  color: #f8fafc !important;
}
div[data-testid="stDialog"] [data-testid="stMarkdownContainer"] *,
div[role="dialog"] [data-testid="stMarkdownContainer"] *,
div[data-testid="stDialog"] label, div[role="dialog"] label,
div[data-testid="stDialog"] p, div[role="dialog"] p,
div[data-testid="stDialog"] span, div[role="dialog"] span {
  color: #f8fafc;
}
/* Streamlit modal title bar (the "Player detail" header) */
div[data-testid="stDialog"] h1, div[role="dialog"] h1,
div[data-testid="stDialog"] h2, div[role="dialog"] h2,
div[data-testid="stDialog"] h3, div[role="dialog"] h3,
div[data-testid="stDialog"] header, div[role="dialog"] header {
  color: #f8fafc !important;
}
/* Radio (chip toggle) above the detail card. Streamlit renders
   each option as a label inside a div[role=radiogroup]; the
   default option labels are near-black on the light theme. */
div[data-testid="stDialog"] div[role="radiogroup"] label,
div[role="dialog"] div[role="radiogroup"] label,
div[data-testid="stDialog"] div[role="radiogroup"] label p,
div[role="dialog"] div[role="radiogroup"] label p {
  color: #e2e8f0 !important; font-weight: 800;
}
div[data-testid="stDialog"] div[role="radiogroup"] label:has(input:checked) p,
div[role="dialog"] div[role="radiogroup"] label:has(input:checked) p {
  color: #7dd3fc !important;
}
/* Streamlit dataframe / table fallbacks inside the dialog. */
div[data-testid="stDialog"] [data-testid="stTable"] *,
div[role="dialog"] [data-testid="stTable"] *,
div[data-testid="stDialog"] [data-testid="stDataFrame"] *,
div[role="dialog"] [data-testid="stDataFrame"] * {
  color: #e2e8f0 !important;
}
/* Expander / accordion labels inside the dialog (if used). */
div[data-testid="stDialog"] details summary,
div[role="dialog"] details summary,
div[data-testid="stDialog"] [data-testid="stExpander"] *,
div[role="dialog"] [data-testid="stExpander"] * {
  color: #e2e8f0 !important;
}
/* Close button (X) — keep it visible on the dark surface. */
div[data-testid="stDialog"] button[aria-label="Close"],
div[role="dialog"] button[aria-label="Close"] {
  color: #f8fafc !important;
}

/* ---------------------------------------------------------------
   Player detail card body. All text is white or bright slate on
   dark cards; muted/helper text uses #cbd5e1 (slate-300) so even
   the lightest helper line is readable. Sky-blue accent
   (#38bdf8 / #7dd3fc / #93c5fd) is reserved for labels and chips.
   --------------------------------------------------------------- */
.pdc-root { color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.pdc-root * { box-sizing: border-box; }
.pdc-card { background: linear-gradient(180deg, #111827 0%, #0b1220 100%); border-radius: 18px;
  padding: 14px 16px; margin: 10px 0; border: 1px solid #1e293b; box-shadow: 0 4px 18px rgba(0,0,0,.35);
  color: #f8fafc; }
.pdc-header-row { display:flex; align-items:center; gap: 14px; }
.pdc-avatar-wrap { flex: 0 0 72px; position: relative; }
.pdc-avatar {
  width: 72px; height: 72px; border-radius: 50%;
  background: linear-gradient(180deg, #1e293b 0%, #0b1220 100%);
  border: 2px solid #38bdf8;
  box-shadow: 0 4px 14px rgba(56,189,248,.35);
  display:flex; align-items:center; justify-content:center;
  overflow: hidden;
  color: #38bdf8; font-weight: 900; font-size: 1.4rem; letter-spacing: .02em;
}
.pdc-avatar img { width: 100%; height: 100%; object-fit: cover; display:block; }
.pdc-avatar-num {
  position: absolute; bottom: -4px; right: -4px;
  min-width: 26px; height: 26px; padding: 0 6px;
  border-radius: 999px;
  background: linear-gradient(180deg, #facc15 0%, #ca8a04 100%);
  color: #0f172a; font-weight: 900; font-size: .78rem;
  display:flex; align-items:center; justify-content:center;
  border: 2px solid #0b1220;
  box-shadow: 0 2px 6px rgba(0,0,0,.45);
}
.pdc-header-body { flex: 1 1 auto; min-width: 0; }
.pdc-name-row { display:flex; align-items:center; gap: 8px; flex-wrap: wrap; }
.pdc-name { font-size: 1.25rem; font-weight: 900; color:#f8fafc; }
.pdc-meta { font-size: .78rem; color:#cbd5e1; font-weight: 700; margin-top: 2px; }
.pdc-hand-pills { display:flex; gap:6px; flex-wrap:wrap; margin-top:7px; }
.pdc-hand-pill {
  display:inline-flex; align-items:center; justify-content:center;
  padding:4px 10px; border-radius:999px;
  background:#facc15; color:#0f172a !important;
  border:1px solid rgba(250,204,21,.75);
  font-size:.7rem; font-weight:900; letter-spacing:.05em;
  text-transform:uppercase; line-height:1.15;
  box-shadow:0 1px 3px rgba(0,0,0,.45);
}
.pdc-hand-pill.pitch {
  background:rgba(125,211,252,.18); color:#e0f2fe !important;
  border-color:rgba(125,211,252,.45);
}
/* Composite slate grade badge — A best, D worst. Colors mirror the heat-map
   palette but sit on a distinct rounded pill so users read it as a verdict,
   not a metric value. */
.pdc-grade {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: .68rem;
  font-weight: 900;
  letter-spacing: .04em;
  text-transform: uppercase;
  line-height: 1.2;
  box-shadow: 0 1px 2px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.08);
}
.pdc-grade-A { background:#15803d; color:#ecfdf5; }
.pdc-grade-B { background:#0369a1; color:#e0f2fe; }
.pdc-grade-C { background:#ca8a04; color:#0f172a; }
.pdc-grade-D { background:#b91c1c; color:#fef2f2; }
.pdc-next { display:flex; align-items:center; justify-content:space-between; padding: 10px 12px;
  background: rgba(56,189,248,.12); border:1px solid rgba(56,189,248,.35); border-radius: 12px;
  margin-top: 10px; }
.pdc-next-left  { font-size:.85rem; font-weight:800; color:#f8fafc; }
.pdc-next-right { font-size:.72rem; font-weight:900; color:#fde68a; }
.pdc-section-title { font-size:.8rem; font-weight:900; color:#7dd3fc; text-transform:uppercase;
  letter-spacing:.08em; margin: 12px 0 6px 0; display:flex; align-items:center; gap:8px; }
.pdc-section-title::before { content:""; width: 4px; height: 14px; background:#38bdf8; border-radius:3px; display:inline-block; }
.pdc-rating { display:flex; gap: 14px; align-items:stretch; }
.pdc-rating-score { flex: 0 0 92px; background:#0b1220; border:1px solid #1e293b; border-radius: 14px;
  padding: 12px; display:flex; flex-direction:column; align-items:center; justify-content:center; }
.pdc-rating-score .num  { font-size: 1.8rem; font-weight:900; line-height:1; }
.pdc-rating-score .tier { font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.06em; margin-top:6px; color:#e2e8f0; }
.pdc-rating.tier-Juicy  .num { color:#22c55e; }
.pdc-rating.tier-Risky  .num { color:#4ade80; }
.pdc-rating.tier-Average .num { color:#facc15; }
.pdc-rating.tier-Above-Avg .num { color:#fb923c; }
.pdc-rating.tier-Elite  .num { color:#ef4444; }
.pdc-rating-body { flex: 1 1 auto; }
.pdc-rating-name { font-size:.95rem; font-weight:800; color:#f8fafc; }
.pdc-rating-bullets { margin: 6px 0 0 0; padding: 0; list-style: none; }
.pdc-rating-bullets li { font-size:.74rem; color:#e2e8f0; font-weight:700; padding-left: 14px; position:relative; line-height:1.4; }
.pdc-rating-bullets li::before { content:"•"; position:absolute; left:0; color:#38bdf8; }
/* Horizontal scroll wrapper for the BvP table — keeps every column
   reachable on narrow phones while the parent .pdc-card itself stays
   overflow-hidden so the modal corners don't break. table-layout:fixed
   gives every numeric column an equal share with min-content fallback so
   the last column (BB%) never gets clipped or pushed off-screen. */
.pdc-table-wrap {
  width:100%; overflow-x:auto; -webkit-overflow-scrolling:touch;
  margin: 0 -4px; padding: 0 4px;
}
.pdc-table {
  width:100%; min-width: 320px;
  border-collapse: separate; border-spacing: 0;
  font-size:.74rem; table-layout: fixed;
}
.pdc-table col.col-split { width: 30%; }
.pdc-table col.col-num   { width: 14%; }
.pdc-table th { color:#7dd3fc; font-weight:900; text-transform:uppercase; letter-spacing:.04em;
  font-size:.62rem; padding: 6px 4px; text-align:right; border-bottom: 1px solid #1e293b;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pdc-table th:first-child, .pdc-table td:first-child { text-align:left; }
.pdc-table td { color:#f8fafc; padding: 7px 4px; font-weight:700; text-align:right;
  border-bottom: 1px solid rgba(30,41,59,.6);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pdc-table tr:last-child td { border-bottom: none; }
.pdc-table .row-label { color:#f8fafc; font-weight:800; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }
.pdc-table .row-label .proxy { color:#bae6fd; font-size:.56rem; font-weight:800; margin-left:4px;
  text-transform:uppercase; letter-spacing:.04em; }
@media (max-width: 640px) {
  .pdc-table { font-size:.68rem; }
  .pdc-table th { font-size:.58rem; padding: 6px 3px; letter-spacing:.02em; }
  .pdc-table td { padding: 6px 3px; }
  .pdc-table col.col-split { width: 26%; }
  .pdc-table col.col-num   { width: 14.8%; }
}
.pdc-chips { display:flex; gap:6px; overflow-x:auto; padding: 4px 0 8px 0; margin: 0 -2px;
  -webkit-overflow-scrolling: touch; }
.pdc-chip { flex: 0 0 auto; min-width: 64px; background:#0b1220; border:1px solid #1e293b;
  border-radius: 12px; padding: 8px 10px; text-align:center; }
.pdc-chip .lab { font-size:.66rem; font-weight:900; color:#93c5fd;
  text-transform:uppercase; letter-spacing:.06em; }
.pdc-chip .val { font-size:.98rem; font-weight:900; color:#f8fafc; margin-top: 3px; }
.pdc-chip.is-active { background: rgba(56,189,248,.18); border-color: #38bdf8; }
.pdc-chip.is-active .lab { color:#bae6fd; }
.pdc-chip.is-active .val { color:#7dd3fc; }
.pdc-recent { display:flex; flex-direction:column; gap:8px; }
.pdc-recent-pills { display:flex; gap:8px; }
.pdc-pill { background:#0b1220; border:1px solid #1e293b; border-radius: 999px; padding: 4px 10px;
  font-size:.7rem; font-weight:800; color:#e2e8f0; }
.pdc-pill .num { color:#7dd3fc; margin-left:4px; }
.pdc-bars { display:flex; gap:6px; align-items:flex-end; min-height: 64px; padding: 6px 0 0 0; }
.pdc-bar { flex: 1 1 0; background: linear-gradient(180deg, #22c55e 0%, #16a34a 100%);
  border-radius: 4px 4px 0 0; min-height: 4px; position:relative; }
.pdc-bar.empty { background:#1e293b; }
.pdc-bar .v { position:absolute; top:-14px; left:50%; transform: translateX(-50%);
  font-size:.62rem; color:#f8fafc; font-weight:900; }
.pdc-log-table { width:100%; border-collapse: separate; border-spacing: 0;
  background:#0b1220; border-radius: 12px; overflow:hidden; border:1px solid #1e293b; }
.pdc-log-table th { background:#0f172a; color:#7dd3fc; font-size:.6rem; font-weight:900;
  text-transform:uppercase; letter-spacing:.06em; padding: 8px 6px; text-align:right;
  border-bottom: 1px solid #1e293b; }
.pdc-log-table th:first-child, .pdc-log-table td:first-child { text-align:left; }
.pdc-log-table td { color:#f8fafc; font-size:.74rem; font-weight:700; padding: 7px 6px;
  text-align:right; border-bottom: 1px solid rgba(30,41,59,.6); }
.pdc-log-table tr:last-child td { border-bottom: none; }
.pdc-log-opp { color:#bae6fd; font-size:.62rem; font-weight:700; }
.pdc-empty { color:#cbd5e1; font-size:.82rem; font-weight:700; font-style: italic; padding: 8px 4px; }
.pdc-recap { display:grid; grid-template-columns: repeat(4, 1fr); gap:6px; }
.pdc-recap-tile { background:#0b1220; border:1px solid #1e293b; border-radius: 10px;
  padding: 6px 4px; text-align:center; }
.pdc-recap-tile .lab { font-size:.62rem; font-weight:900; color:#93c5fd;
  text-transform:uppercase; letter-spacing:.06em; }
.pdc-recap-tile .val { font-size:.9rem; font-weight:900; color:#f8fafc; margin-top:2px; }
.pdc-likely { padding: 4px 10px; border-radius: 999px; background: rgba(56,189,248,.22);
  color:#bae6fd; font-weight: 800; font-size:.74rem; display:inline-block; margin-top:6px; }
.pdc-likely-reason { color:#cbd5e1; font-size:.7rem; font-weight:700; margin-top:4px; line-height:1.35; }

/* ---------------------------------------------------------------
   Heat-map metric bands. Applied as inline pills around numeric
   values inside the modal so each cell visually communicates
   strength (green) / neutral (yellow) / weakness (red) at a glance.
   Padding and border-radius are kept tight so the colored chip
   doesn't break the table grid; text colors are chosen for
   luminance contrast against each background.
   --------------------------------------------------------------- */
.pdc-hm {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 900;
  font-size: inherit;
  line-height: 1.2;
  letter-spacing: 0;
  min-width: 36px;
  text-align: center;
  box-shadow: 0 1px 2px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06);
}
.pdc-hm-good { background:#15803d !important; color:#ecfdf5 !important; }
.pdc-hm-okay { background:#ca8a04 !important; color:#0f172a !important; }
.pdc-hm-bad  { background:#b91c1c !important; color:#fef2f2 !important; }
/* Recap tiles host the .pdc-hm pill as their value — give the chip a
   slightly larger footprint so the tile reads as one colored block. */
.pdc-recap-tile .pdc-hm { padding: 4px 10px; min-width: 50px; }
/* Chip values (Splits) — recolor the whole chip body when banded. */
.pdc-chip.pdc-hm-good { background:#15803d; border-color:#22c55e; }
.pdc-chip.pdc-hm-good .lab { color:#dcfce7; }
.pdc-chip.pdc-hm-good .val { color:#ecfdf5; }
.pdc-chip.pdc-hm-okay { background:#ca8a04; border-color:#facc15; }
.pdc-chip.pdc-hm-okay .lab { color:#1f2937; }
.pdc-chip.pdc-hm-okay .val { color:#0f172a; }
.pdc-chip.pdc-hm-bad  { background:#b91c1c; border-color:#ef4444; }
.pdc-chip.pdc-hm-bad  .lab { color:#fee2e2; }
.pdc-chip.pdc-hm-bad  .val { color:#fef2f2; }
/* Pitcher rating tile coloring — already handled by tier classes; we
   add complementary text contrast so the "tier" caption stays legible. */
.pdc-rating-score.pdc-hm-good { background:#14532d; border-color:#22c55e; }
.pdc-rating-score.pdc-hm-okay { background:#78350f; border-color:#facc15; }
.pdc-rating-score.pdc-hm-bad  { background:#7f1d1d; border-color:#ef4444; }

/* ---------------------------------------------------------------
   Modal sizing + scrolling. The dialog body must scroll on phones
   so the Game Log at the bottom isn't clipped by the viewport. We
   cap the dialog at ~90vh and let the inner scroll container
   handle overflow with momentum on iOS. Bottom padding gives the
   user space below the last row so it doesn't sit flush against
   the safe-area inset on notch devices.
   --------------------------------------------------------------- */
div[data-testid="stDialog"] > div > div,
div[role="dialog"] {
  max-height: 92vh !important;
  overflow-y: auto !important;
  -webkit-overflow-scrolling: touch;
  /* Respect iOS notches / browser chrome at the top and bottom of the
     scroll area so the sticky close bar and the last game-log row
     don't sit under the status bar or home-indicator. */
  padding-top: env(safe-area-inset-top, 0px) !important;
  padding-bottom: env(safe-area-inset-bottom, 0px) !important;
}
div[data-testid="stDialog"] [data-testid="stVerticalBlock"],
div[role="dialog"]         [data-testid="stVerticalBlock"] {
  padding-bottom: 32px;
}
.pdc-root { padding-bottom: 96px; }
.pdc-root, .pdc-card { overflow-x: hidden; }
.pdc-modal-tail { height: 64px; }

/* ---------------------------------------------------------------
   Sticky "Back to Matchup" close bar. iOS Safari's native dialog
   X button drifts above the viewport once the user scrolls deep
   into the game log, leaving no way to dismiss the modal without
   hitting the device back gesture. We render our own button at
   the top of the dialog content (Streamlit st.button immediately
   after a .pdc-close-bar marker) and pin its element-container to
   the top of the dialog scroll container so it stays in reach.

   The selectors mirror the same pattern used by the matchup CTA:
   target the stElementContainer that immediately follows the one
   carrying our marker class. This survives Streamlit wrapper
   changes between versions. */
.pdc-close-bar { display:block; height:0; margin:0 !important; padding:0 !important; }
div[data-testid="stElementContainer"]:has(.pdc-close-bar) + div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]),
div[data-testid="element-container"]:has(.pdc-close-bar)  + div[data-testid="element-container"]:has(div[data-testid="stButton"]) {
  position: sticky !important;
  top: 0;
  z-index: 50;
  margin: 0 0 12px 0 !important;
  padding: 8px 0 !important;
  background: linear-gradient(180deg, #0b1220 0%, rgba(11,18,32,.96) 80%, rgba(11,18,32,0) 100%) !important;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}
/* The actual button: high-contrast gold-on-dark to match the
   theme's gold CTA accent and to remain readable on the dark
   surface. */
div[data-testid="stElementContainer"]:has(.pdc-close-bar) + div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) button,
div[data-testid="element-container"]:has(.pdc-close-bar)  + div[data-testid="element-container"]:has(div[data-testid="stButton"]) button {
  width: 100% !important;
  min-height: 44px !important;
  background: linear-gradient(180deg, #fde047 0%, #ca8a04 100%) !important;
  color: #0b0b0b !important;
  border: 1px solid #a16207 !important;
  border-radius: 12px !important;
  font-weight: 900 !important;
  letter-spacing: .04em !important;
  text-transform: uppercase !important;
  font-size: .95rem !important;
  box-shadow: 0 4px 12px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.55) !important;
  cursor: pointer !important;
}
div[data-testid="stElementContainer"]:has(.pdc-close-bar) + div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) button *,
div[data-testid="element-container"]:has(.pdc-close-bar)  + div[data-testid="element-container"]:has(div[data-testid="stButton"]) button * {
  color: #0b0b0b !important;
}
/* Make sure the native Streamlit X close button stays in front of
   our sticky bar AND gets a slightly larger tap target on phones,
   so a user who wants it still has it. */
div[data-testid="stDialog"] button[aria-label="Close"],
div[role="dialog"] button[aria-label="Close"] {
  position: sticky;
  z-index: 60 !important;
  min-width: 40px; min-height: 40px;
}
@media (max-width: 640px) {
  div[data-testid="stElementContainer"]:has(.pdc-close-bar) + div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]),
  div[data-testid="element-container"]:has(.pdc-close-bar)  + div[data-testid="element-container"]:has(div[data-testid="stButton"]) {
    top: 0;
    padding: 10px 0 !important;
  }
  div[data-testid="stElementContainer"]:has(.pdc-close-bar) + div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) button,
  div[data-testid="element-container"]:has(.pdc-close-bar)  + div[data-testid="element-container"]:has(div[data-testid="stButton"]) button {
    min-height: 48px !important;
    font-size: 1rem !important;
  }
}

/* Game-log: capped height + scroll so a long L20/Season window is
   reachable without pushing other sections off-screen on phones. */
.pdc-log-scroll {
  max-height: 56vh;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.pdc-log-opp-logo {
  width: 14px; height: 14px; vertical-align: middle;
  margin-right: 4px; display: inline-block;
}
.pdc-log-opp-abbr { vertical-align: middle; }

/* Bar chart columns carry a polished, mobile-first opponent chip
   below each bar. The chip is logo-only by default; if the image
   fails to load (rare) JS swaps in a clean abbreviation chip via
   the .pdc-bar-opp--text class. The @ / vs context lives in the
   `title` tooltip so the strip never wraps on phones. */
.pdc-bar-col {
  flex: 1 1 0; min-width: 0; display:flex; flex-direction:column;
  align-items:stretch;
}
.pdc-bar-track {
  flex: 1 1 auto; min-height: 56px; display:flex; align-items:flex-end;
}
.pdc-bar-track .pdc-bar { width: 100%; }
.pdc-bar-opp {
  height: 26px; margin-top: 6px;
  display:flex; align-items:center; justify-content:center;
  background:#0b1220; border:1px solid #1e293b; border-radius: 6px;
  padding: 2px; overflow:hidden;
}
.pdc-bar-opp img {
  width: 100%; height: 100%; max-width: 22px; max-height: 22px;
  object-fit: contain; display:block;
}
.pdc-bar-opp-fallback {
  display:none;
  font-size: .56rem; font-weight: 900; color:#bae6fd;
  letter-spacing: .02em; line-height: 1;
}
/* JS adds .pdc-bar-opp--text when an image fails so we can swap
   the chip into a clean text pill rather than re-flowing layout. */
.pdc-bar-opp--text { background:#0f172a; border-color:#1e3a5f; }
.pdc-bar-opp--text img { display:none; }
.pdc-bar-opp--text .pdc-bar-opp-fallback { display:inline-block; }
/* Subtle home/away indicator — a 2px accent on the bottom border of
   the chip. Avoids text clutter while still encoding venue context. */
.pdc-bar-opp.is-home  { border-bottom-color: #22c55e; }
.pdc-bar-opp.is-away  { border-bottom-color: #38bdf8; }

/* Narrow phones (≤380px): shrink the gap so 10 bars fit cleanly
   without overflow, and let the chip ride a touch smaller. */
@media (max-width: 380px) {
  .pdc-bars { gap: 4px; }
  .pdc-bar-opp { height: 22px; margin-top: 4px; padding: 1px; }
  .pdc-bar-opp img { max-width: 18px; max-height: 18px; }
  .pdc-bar-opp-fallback { font-size: .5rem; }
}

/* ---------------------------------------------------------------
   HR Due Indicator — premium dark card with a red/pink accent
   border + faint glow, six criterion rows each with a green check
   (or muted dot for miss / missing). The score line is the
   marquee element ("6 / 6 Due 🔥") and uses a red->pink gradient
   to read as the urgency tile from the screenshot.
   --------------------------------------------------------------- */
.pdc-hrdue {
  position: relative;
  background: linear-gradient(180deg, #1a0a10 0%, #120608 100%);
  border: 1px solid rgba(244, 63, 94, .55);
  border-radius: 16px;
  padding: 16px 16px 14px;
  margin: 14px 0;
  box-shadow: 0 0 0 1px rgba(244, 63, 94, .12),
              0 12px 28px -10px rgba(244, 63, 94, .35);
}
.pdc-hrdue-title {
  font-size: .72rem;
  letter-spacing: .14em;
  color: #fda4af;
  text-transform: uppercase;
  font-weight: 800;
  margin-bottom: 4px;
}
.pdc-hrdue-score {
  display: flex; align-items: baseline; gap: 8px;
  margin-bottom: 12px;
}
.pdc-hrdue-score .num {
  font-size: 2.4rem; font-weight: 900;
  background: linear-gradient(180deg, #fb7185 0%, #f43f5e 100%);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
  line-height: 1; letter-spacing: -.02em;
}
.pdc-hrdue-score .den {
  font-size: 1rem; font-weight: 700; color: #fda4af; opacity: .9;
}
.pdc-hrdue-score .lbl {
  font-size: 1.1rem; font-weight: 800; color: #fecdd3;
  margin-left: 6px;
}
.pdc-hrdue-list {
  display: flex; flex-direction: column; gap: 10px;
}
.pdc-hrdue-item {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15, 23, 42, .55);
  border: 1px solid rgba(34, 197, 94, .30);
}
.pdc-hrdue-item.is-miss {
  border-color: rgba(100, 116, 139, .25);
  background: rgba(15, 23, 42, .40);
}
.pdc-hrdue-item.is-missing {
  border-color: rgba(100, 116, 139, .18);
  background: rgba(15, 23, 42, .30);
  opacity: .80;
}
.pdc-hrdue-check {
  flex: 0 0 auto;
  width: 18px; height: 18px;
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 900; line-height: 1;
  color: #22c55e;
  font-size: 1.05rem;
  margin-top: 2px;
}
.pdc-hrdue-item.is-miss .pdc-hrdue-check { color: #475569; }
.pdc-hrdue-item.is-missing .pdc-hrdue-check { color: #334155; }
.pdc-hrdue-body { flex: 1 1 auto; min-width: 0; }
.pdc-hrdue-h    {
  font-size: 1rem; font-weight: 800; color: #f8fafc;
  margin-bottom: 2px;
}
.pdc-hrdue-d    {
  font-size: .85rem; color: #94a3b8;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  line-height: 1.35; word-break: break-word;
}
.pdc-hrdue-item.is-missing .pdc-hrdue-d { color: #64748b; font-style: italic; }
@media (max-width: 380px) {
  .pdc-hrdue { padding: 14px 12px 12px; }
  .pdc-hrdue-score .num { font-size: 2rem; }
  .pdc-hrdue-item { padding: 8px 10px; gap: 10px; }
  .pdc-hrdue-h { font-size: .95rem; }
  .pdc-hrdue-d { font-size: .78rem; }
}

/* White/purple/gold Scout Report makeover. This modal CSS is injected after
   the global app theme, so repeat the brand overrides here for readability. */
div[data-testid="stDialog"] > div > div,
div[role="dialog"] {
  background: linear-gradient(135deg, #fff8e7 0%, #ffffff 55%, #f3e8ff 100%) !important;
  color: #1f123d !important;
}
div[data-testid="stDialog"] [data-testid="stMarkdownContainer"] *,
div[role="dialog"] [data-testid="stMarkdownContainer"] *,
div[data-testid="stDialog"] label, div[role="dialog"] label,
div[data-testid="stDialog"] p, div[role="dialog"] p,
div[data-testid="stDialog"] span, div[role="dialog"] span,
div[data-testid="stDialog"] h1, div[role="dialog"] h1,
div[data-testid="stDialog"] h2, div[role="dialog"] h2,
div[data-testid="stDialog"] h3, div[role="dialog"] h3,
div[data-testid="stDialog"] header, div[role="dialog"] header {
  color: #1f123d !important;
  font-weight: 800;
}
.pdc-root { color:#1f123d !important; }
.pdc-card,
.pdc-rating-score,
.pdc-log-table,
.pdc-chip,
.pdc-recap-tile,
.pdc-hrdue,
.pdc-hrdue-item,
.pdc-next {
  background: #ffffff !important;
  color: #1f123d !important;
  border-color: rgba(91,33,182,.24) !important;
  box-shadow: 0 10px 28px rgba(59,31,107,.14) !important;
}
.pdc-name,
.pdc-rating-name,
.pdc-section-title,
.pdc-table .row-label,
.pdc-log-table td,
.pdc-recap-tile .val,
.pdc-chip .val,
.pdc-hrdue-h,
.pdc-hrdue-score .lbl {
  color: #3b1f6b !important;
  font-weight: 900 !important;
}
.pdc-meta,
.pdc-next-left,
.pdc-next-right,
.pdc-rating-bullets li,
.pdc-empty,
.pdc-table td,
.pdc-log-table th,
.pdc-log-opp,
.pdc-hrdue-d,
.pdc-hrdue-title,
.pdc-hrdue-score .den {
  color: #5b4b79 !important;
  font-weight: 800 !important;
}
.pdc-table th,
.pdc-recap-tile .lab,
.pdc-chip .lab {
  color: #5b21b6 !important;
  font-weight: 900 !important;
}
.pdc-hand-pill {
  background:#facc15 !important;
  color:#1f123d !important;
  border-color:#f59e0b !important;
}
.pdc-hand-pill.pitch {
  background:#3b1f6b !important;
  color:#ffffff !important;
  border-color:#5b21b6 !important;
}
</style>
"""


def _render_player_detail_html(payload: dict, active_chip: str) -> str:
    """Build the dark detail card HTML markup for one player payload.

    Active split chip drives the dynamic sections: the "selected window"
    recap, the recent-hits chart, and the game-log table all recompute for
    the chosen scope (L5 / L10 / L20 / Season / TwoYear / H2H) so the modal
    is truly interactive instead of showing static numbers.
    """
    h = payload["header"]
    tile = payload["tile"]
    rating = payload["rating"]
    splits = payload["splits"]
    bvp_rows = payload["bvp_rows"]
    log_rows = payload["game_log_rows"]
    recent = payload["recent"]
    game_log = payload.get("game_log") or []
    log_season = int(payload.get("log_season") or date.today().year)
    slate_iso = payload.get("slate_iso") or ""

    # Resolve active split -> rows we render the dynamic sections from.
    try:
        end_dt = date.fromisoformat(slate_iso[:10]) if slate_iso else date.today()
    except Exception:
        end_dt = date.today()
    active_rows = _pd_filter_log_for_split(
        game_log, active_chip, log_season, end_dt,
        opp_team=h.get("opp_team"),
    )
    active_key = _pd_split_label_to_key(active_chip)
    # Aggregates for the *selected* window. Re-uses the canonical buckets
    # when the active chip maps cleanly to one of them (faster, same math),
    # and falls back to a fresh aggregation for H2H / unmapped chips.
    if active_chip == "H2H":
        # Aggregate the H2H subset on the fly so H2H shows real numbers
        # instead of the same L10 figures.
        from services.player_detail import aggregate_window as _pd_agg_window
        active_agg = _pd_agg_window(active_rows)
    else:
        active_agg = splits.get(active_key) or {}
    # Whether the H2H tab found any true head-to-head games — drives the
    # "proxy" badge so the user knows when we fell back to L10.
    h2h_actual = False
    if active_chip == "H2H" and h.get("opp_team"):
        opp_norm = str(h.get("opp_team") or "").upper()
        h2h_actual = any(
            str(r.get("opponent") or "").upper() == opp_norm for r in active_rows
        )

    # Header card.
    meta_bits = []
    if h.get("team"): meta_bits.append(h["team"])
    if h.get("bat_side"): meta_bits.append(f"Bats {format_batter_stance(h['bat_side'])}")
    meta = " • ".join(meta_bits)
    next_right = ""
    if h.get("pitch_hand"):
        next_right = format_pitcher_hand(h["pitch_hand"], h["pitch_hand"])
    batter_stance_label = format_batter_stance(h.get("bat_side"), "")
    pitcher_hand_label = format_pitcher_hand(h.get("pitch_hand"), "")
    hand_pills = []
    if batter_stance_label:
        hand_pills.append(f'<span class="pdc-hand-pill">Bats {batter_stance_label}</span>')
    if pitcher_hand_label:
        hand_pills.append(f'<span class="pdc-hand-pill pitch">vs {pitcher_hand_label}</span>')
    hand_pills_html = (
        f'<div class="pdc-hand-pills">{"".join(hand_pills)}</div>'
        if hand_pills else ""
    )
    next_pitcher_html = ""
    if h.get("opp_pitcher"):
        next_pitcher_html = (
            f'<div class="pdc-next">'
            f'<div class="pdc-next-left">Next: vs {h["opp_pitcher"]}</div>'
            f'<div class="pdc-next-right">{next_right} • {h["slate_date"]}</div>'
            f'</div>'
        )

    likely_html = ""
    if tile.get("Likely") and str(tile["Likely"]) != "—":
        reason = tile.get("LikelyReason") or ""
        likely_html = (
            f'<div class="pdc-likely">{tile["Likely"]}</div>'
            f'<div class="pdc-likely-reason">{reason}</div>'
        )

    # Avatar block — MLB headshot if we have a player_id, otherwise the
    # batter's initials as a CSS-only fallback so we never show a broken
    # image or block initial render on a network request.
    headshot = h.get("headshot")
    initials = "".join(
        part[0] for part in str(h.get("name", "") or "").split() if part
    )[:2].upper() or "?"
    if headshot:
        # onerror swap to initials if the CDN 404s for a given player.
        avatar_inner = (
            f'<img src="{headshot}" alt="{h["name"]} headshot" loading="lazy" '
            f'referrerpolicy="no-referrer" '
            f'onerror="this.style.display=\'none\';this.parentElement.innerText=\'{initials}\';">'
        )
    else:
        avatar_inner = initials

    # Player number — we only show a chip if the lineup spot is real.
    # No fake jersey numbers.
    spot_val = h.get("spot")
    has_spot = False
    try:
        if spot_val is not None and not (isinstance(spot_val, float) and pd.isna(spot_val)):
            spot_int = int(spot_val)
            if 1 <= spot_int <= 9:
                has_spot = True
    except Exception:
        has_spot = False
    num_chip = (
        f'<div class="pdc-avatar-num" title="Lineup spot">#{spot_int}</div>'
        if has_spot else ""
    )

    # Composite letter grade (A-D) shown next to the player's name. Inputs
    # come from data the modal already has on hand: matchup score, opposing
    # pitcher rating, plus recent-form metrics. compute_player_grade returns
    # ``available: False`` when nothing is present so we hide the badge in
    # that case rather than showing a fake "C".
    grade = _pd_compute_player_grade(
        matchup=tile.get("Matchup"),
        pitcher_score=rating.get("score") if rating.get("available") else None,
        ops=tile.get("OPS"),
        iso=tile.get("ISO"),
        hr_pct=tile.get("HR%"),
        barrel_pct=tile.get("Brl/BIP%") or tile.get("Barrel%"),
        xwoba=tile.get("xwOBA"),
    )
    if grade.get("available"):
        grade_badge_html = (
            f'<span class="pdc-grade {grade["css_class"]}" '
            f'title="Composite slate grade (A best, D worst)">'
            f'Grade {grade["grade"]}</span>'
        )
    else:
        grade_badge_html = ""

    # HR Due Indicator block — rendered between the header card and the
    # slate snapshot recap so it reads as the marquee call-out the same way
    # the screenshot does. Built from payload["hr_due"]; tolerant of empty.
    hr_due = payload.get("hr_due") or {}
    hr_due_items_html = ""
    for c in (hr_due.get("criteria") or []):
        state = c.get("state", "missing")
        if state == "hit":
            mark = "✓"
            state_cls = ""
        elif state == "miss":
            mark = "×"
            state_cls = "is-miss"
        else:
            mark = "•"
            state_cls = "is-missing"
        title = c.get("title", "")
        detail = c.get("detail", "")
        hr_due_items_html += (
            f'<div class="pdc-hrdue-item {state_cls}">'
            f'<span class="pdc-hrdue-check" aria-hidden="true">{mark}</span>'
            f'<div class="pdc-hrdue-body">'
            f'<div class="pdc-hrdue-h">{title}</div>'
            f'<div class="pdc-hrdue-d">{detail}</div>'
            f'</div></div>'
        )
    if hr_due.get("criteria"):
        hr_due_html = (
            '<div class="pdc-hrdue">'
            '<div class="pdc-hrdue-title">HR Due Indicator</div>'
            '<div class="pdc-hrdue-score">'
            f'<span class="num">{int(hr_due.get("score", 0))}</span>'
            f'<span class="den">/ {int(hr_due.get("total", 6))}</span>'
            f'<span class="lbl">{hr_due.get("label", "")}</span>'
            '</div>'
            f'<div class="pdc-hrdue-list">{hr_due_items_html}</div>'
            '</div>'
        )
    else:
        hr_due_html = ""

    header_card = (
        f'<div class="pdc-card">'
        f'<div class="pdc-header-row">'
        f'  <div class="pdc-avatar-wrap">'
        f'    <div class="pdc-avatar">{avatar_inner}</div>'
        f'    {num_chip}'
        f'  </div>'
        f'  <div class="pdc-header-body">'
        f'    <div class="pdc-name-row">'
        f'      <span class="pdc-name">{h["name"]}</span>'
        f'      {grade_badge_html}'
        f'    </div>'
        f'    {hand_pills_html}'
        f'    <div class="pdc-meta">{meta}</div>'
        f'    {likely_html}'
        f'  </div>'
        f'</div>'
        f'{next_pitcher_html}'
        f'</div>'
    )

    # Heat-map recap (tile snapshot — same numbers the tile showed).
    # ``metric`` is the internal-name lookup used by the heat-map classifier
    # so the green/yellow/red banding matches the rest of the modal and the
    # original heat-map board.
    def _r(label, val, fmt="{:.3f}", *, metric: str | None = None):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return f'<div class="pdc-recap-tile"><div class="lab">{label}</div><div class="val">—</div></div>'
        try:
            v_str = fmt.format(float(val))
        except Exception:
            v_str = str(val)
        val_html = _pd_hm_cell(metric, val, v_str) if metric else v_str
        return f'<div class="pdc-recap-tile"><div class="lab">{label}</div><div class="val">{val_html}</div></div>'

    recap_html = (
        '<div class="pdc-section-title">Slate Snapshot</div>'
        '<div class="pdc-card">'
        '<div class="pdc-recap">'
        + _r("MTCH", tile.get("Matchup"), "{:.1f}", metric="Matchup")
        + _r("OPS", tile.get("OPS"), metric="OPS")
        + _r("ISO", tile.get("ISO"), metric="ISO")
        + _r("BRL%", tile.get("Brl/BIP%"), "{:.1f}%", metric="Brl/BIP%")
        + _r("HR/FB", tile.get("HR/FB%"), "{:.1f}%", metric="HR/FB%")
        + _r("xwOBA", tile.get("xwOBA"), metric="xwOBA")
        + '</div>'
        + (f'<div class="pdc-meta" style="margin-top:8px;">📈 {tile.get("Form","")}</div>' if tile.get("Form") else "")
        + (f'<div class="pdc-meta">💣 {tile.get("LastHR","")}</div>' if tile.get("LastHR") else "")
        + '</div>'
    )

    # Pitcher rating. The rating is *vulnerability for the pitcher*, so a
    # "Juicy" tier is GOOD for the hitter — we map tier->band accordingly
    # via classify_pitcher_tier so the colored ring matches the rest of
    # the modal's heat-map semantics.
    if rating.get("available"):
        bullets_html = "".join(f"<li>{b}</li>" for b in rating.get("bullets", []))
        pitcher_band = _pd_classify_pitcher_tier(rating.get("tier"))
        score_cls = f"pdc-hm-{pitcher_band}" if pitcher_band != "neutral" else ""
        rating_html = (
            '<div class="pdc-section-title">Opposing Pitcher Ratings</div>'
            f'<div class="pdc-card pdc-rating tier-{rating.get("tier","Average").replace(" ","-")}">'
            f'<div class="pdc-rating-score {score_cls}">'
            f'<div class="num">{rating["score"]}</div>'
            f'<div class="tier">{rating["tier"]}</div>'
            f'</div>'
            f'<div class="pdc-rating-body">'
            f'<div class="pdc-rating-name">{h.get("opp_pitcher") or "Opposing pitcher"}</div>'
            f'<ul class="pdc-rating-bullets">{bullets_html}</ul>'
            f'</div>'
            f'</div>'
        )
    else:
        rating_html = (
            '<div class="pdc-section-title">Opposing Pitcher Ratings</div>'
            '<div class="pdc-card"><div class="pdc-empty">No opposing pitcher data available.</div></div>'
        )

    # BvP table.
    def _bvp_cell(v, kind):
        if v is None: return "—"
        if kind == "pa":
            try: return str(int(v))
            except Exception: return "—"
        if kind == "slg":
            return _fmt_slg(v)
        return _fmt_pct(v)
    bvp_body = []
    for row in bvp_rows:
        proxy_tag = '<span class="proxy">proxy</span>' if not row.get("actual") else ""
        h_pct  = row.get("H%")
        slg    = row.get("SLG")
        hr_pct = row.get("HR%")
        bb_pct = row.get("BB%")
        bvp_body.append(
            f'<tr>'
            f'<td class="row-label">{row["label"]}{proxy_tag}</td>'
            f'<td>{_bvp_cell(row.get("PA"),"pa")}</td>'
            f'<td>{_pd_hm_cell("H%",  h_pct,  _bvp_cell(h_pct, "pct"))}</td>'
            f'<td>{_pd_hm_cell("SLG", slg,    _bvp_cell(slg,   "slg"))}</td>'
            f'<td>{_pd_hm_cell("HR%", hr_pct, _bvp_cell(hr_pct, "pct"))}</td>'
            f'<td>{_pd_hm_cell("BB%", bb_pct, _bvp_cell(bb_pct, "pct"))}</td>'
            f'</tr>'
        )
    bvp_html = (
        '<div class="pdc-section-title">Batter vs Pitcher Matchup</div>'
        '<div class="pdc-card">'
        '<div class="pdc-table-wrap">'
        '<table class="pdc-table">'
        '<colgroup>'
        '<col class="col-split"/>'
        '<col class="col-num"/><col class="col-num"/><col class="col-num"/>'
        '<col class="col-num"/><col class="col-num"/>'
        '</colgroup>'
        '<thead><tr>'
        '<th>Split</th><th>PA</th><th>H%</th><th>SLG</th><th>HR%</th><th>BB%</th>'
        '</tr></thead>'
        f'<tbody>{"".join(bvp_body)}</tbody>'
        '</table>'
        '</div>'
        '</div>'
    )

    # Splits chips. Each chip shows the AVG for its window so the user can
    # compare scopes at a glance; tapping a chip (via the radio above) makes
    # the dynamic sections below recompute for that window.
    season_year = log_season
    chip_specs = [
        ("H2H",       splits.get("L10")),
        ("L5",        splits.get("L5")),
        ("L10",       splits.get("L10")),
        ("L20",       splits.get("L20")),
        (str(season_year), splits.get("Season")),
        ("’25-’26",   splits.get("TwoYear")),
    ]
    chip_html = []
    for label, agg in chip_specs:
        is_active = "is-active" if label == active_chip else ""
        val = "—"
        avg_v = None
        if agg and agg.get("PA"):
            if agg.get("AVG") is not None:
                avg_v = agg["AVG"]
                s = f"{avg_v:.3f}"
                val = s[1:] if s.startswith("0.") else s
        band_cls = _pd_heatmap_style_for("AVG", avg_v)["css_class"]
        chip_html.append(
            f'<div class="pdc-chip {is_active} {band_cls}">'
            f'<div class="lab">{label}</div>'
            f'<div class="val">{val}</div>'
            f'</div>'
        )
    chips_html = (
        '<div class="pdc-section-title">Splits — AVG by window</div>'
        '<div class="pdc-card">'
        f'<div class="pdc-chips">{"".join(chip_html)}</div>'
        '</div>'
    )

    # Dynamic "selected window" recap — drives the interactive feel.
    # Tapping H2H / L5 / L10 / L20 / Season / TwoYear re-renders this block
    # with that scope's PA, AVG, OPS, HR%, BB%, K%, SLG and a banded color
    # for each so the modal is genuinely responsive instead of static.
    def _fmt3(v):
        if v is None or (isinstance(v, float) and v != v):
            return "—"
        s = f"{float(v):.3f}"
        return s[1:] if s.startswith("0.") else s
    games_n = int(active_agg.get("games") or 0)
    pa_n = int(active_agg.get("PA") or 0)
    proxy_badge = ""
    if active_chip == "H2H" and not h2h_actual:
        proxy_badge = ('<span class="pdc-likely" style="margin-left:8px;'
                       'background:rgba(202,138,4,.25);color:#fde68a;">'
                       'L10 proxy — no H2H history</span>')
    elif active_chip == "H2H" and h2h_actual:
        proxy_badge = ('<span class="pdc-likely" style="margin-left:8px;'
                       'background:rgba(34,197,94,.20);color:#bbf7d0;">'
                       f'True H2H · {games_n} games vs {h.get("opp_team","")}</span>')
    selected_recap = (
        f'<div class="pdc-section-title">Selected — {active_chip}{proxy_badge}</div>'
        '<div class="pdc-card">'
        '<div class="pdc-recap">'
        + _r("G", games_n, "{:.0f}")
        + _r("PA", pa_n, "{:.0f}")
        + _r("AVG", active_agg.get("AVG"), metric="AVG")
        + _r("OPS", active_agg.get("OPS"), metric="OPS")
        + _r("HR%", active_agg.get("HR%"), "{:.1f}%", metric="HR%")
        + _r("K%",  active_agg.get("K%"),  "{:.1f}%", metric="K%")
        + '</div></div>'
    )

    # Recent bars — show the *selected* window's hits-per-game with the
    # opponent's team logo under each bar. Cap at 10 bars so each column
    # has enough room on narrow phones (~360px) to render a readable logo
    # without crowding. The chip below each bar is logo-only; the
    # @/vs prefix lives in the `title` tooltip to keep the strip clean.
    if active_rows:
        chart_rows = active_rows[-10:]
        vals = [int(r.get("h") or 0) for r in chart_rows]
        chart_opps = [
            {"abbr": (r.get("opponent") or ""),
             "logo": _pd_team_logo_url(r.get("opponent") or ""),
             "is_home": bool(r.get("is_home"))}
            for r in chart_rows
        ]
        if vals:
            chart_avg = sum(vals) / len(vals)
            sorted_v = sorted(vals)
            mid = len(sorted_v) // 2
            chart_median = (sorted_v[mid] if len(sorted_v) % 2 == 1
                            else (sorted_v[mid - 1] + sorted_v[mid]) / 2)
        else:
            chart_avg = chart_median = None
    else:
        vals = (recent.get("values") or [])[-10:]
        chart_opps = (recent.get("opponents") or [])[-10:]
        chart_avg = recent.get("avg")
        chart_median = recent.get("median")
    max_v = max(vals) if vals else 1
    bar_html = []
    for i, v in enumerate(vals):
        pct = (v / max_v * 100) if max_v else 0
        cls = "empty" if v == 0 else ""
        opp = chart_opps[i] if i < len(chart_opps) else {}
        abbr_short = _pd_short_opp_abbr(opp.get("abbr"))
        logo = opp.get("logo")
        is_home = bool(opp.get("is_home"))
        loc = "vs" if is_home else "@"
        ha_cls = "is-home" if is_home else "is-away"
        # Tooltip is the only place we keep "@LAD 2 H" context — the chip
        # itself stays icon-only so the strip never wraps on mobile.
        tip = (f"{loc} {abbr_short} · {v} H" if abbr_short
               else f"{v} H")
        if logo:
            opp_chip = (
                f'<div class="pdc-bar-opp {ha_cls}" title="{tip}" aria-label="{tip}">'
                f'<img src="{logo}" alt="{abbr_short}" loading="lazy" '
                f'referrerpolicy="no-referrer" '
                f'onerror="this.onerror=null;this.style.display=\'none\';'
                f'this.parentElement.classList.add(\'pdc-bar-opp--text\');"/>'
                f'<span class="pdc-bar-opp-fallback">{abbr_short}</span>'
                f'</div>'
            )
        elif abbr_short:
            opp_chip = (
                f'<div class="pdc-bar-opp pdc-bar-opp--text {ha_cls}" '
                f'title="{tip}" aria-label="{tip}">'
                f'<span class="pdc-bar-opp-fallback">{abbr_short}</span>'
                f'</div>'
            )
        else:
            opp_chip = '<div class="pdc-bar-opp" aria-hidden="true"></div>'
        bar_html.append(
            f'<div class="pdc-bar-col" title="{tip}">'
            f'<div class="pdc-bar-track">'
            f'<div class="pdc-bar {cls}" style="height:{max(6, pct)}%"><span class="v">{v}</span></div>'
            f'</div>'
            f'{opp_chip}'
            f'</div>'
        )
    avg_str = f"{chart_avg:.2f}" if chart_avg is not None else "—"
    med_str = f"{chart_median:.1f}" if chart_median is not None else "—"
    chart_title = f"Recent — Hits ({active_chip})" if vals else "Recent — Hits"
    if vals:
        recent_html = (
            f'<div class="pdc-section-title">{chart_title}</div>'
            '<div class="pdc-card pdc-recent">'
            '<div class="pdc-recent-pills">'
            f'<div class="pdc-pill">Avg<span class="num">{avg_str}</span></div>'
            f'<div class="pdc-pill">Median<span class="num">{med_str}</span></div>'
            f'<div class="pdc-pill">N<span class="num">{len(vals)}</span></div>'
            '</div>'
            f'<div class="pdc-bars">{"".join(bar_html)}</div>'
            '</div>'
        )
    else:
        recent_html = (
            f'<div class="pdc-section-title">{chart_title}</div>'
            '<div class="pdc-card"><div class="pdc-empty">No games in this window yet.</div></div>'
        )

    # Game log table — filtered to the active split. Adds a K column
    # (batter strikeouts) the user explicitly asked for, plus a small team
    # logo next to each opponent abbreviation.
    scope_log = _pd_format_game_log_rows(active_rows, limit=20)
    log_body = []
    for r in scope_log:
        opp_abbr = r.get("opp") or ""
        opp_logo = r.get("opp_logo")
        if opp_logo:
            opp_html = (
                f'<img src="{opp_logo}" class="pdc-log-opp-logo" '
                f'alt="{opp_abbr}" loading="lazy" '
                f'referrerpolicy="no-referrer" '
                f'onerror="this.style.display=\'none\';"/>'
                f'<span class="pdc-log-opp-abbr">{r["opp_label"]}</span>'
            )
        else:
            opp_html = f'<span class="pdc-log-opp-abbr">{r["opp_label"]}</span>'
        log_body.append(
            f'<tr>'
            f'<td><div>{r["date_short"]}</div>'
            f'<div class="pdc-log-opp">{opp_html} {r["score"]}</div></td>'
            f'<td>{r["ab"]}</td>'
            f'<td>{r["h"]}</td>'
            f'<td>{r["hr"]}</td>'
            f'<td>{r["tb"]}</td>'
            f'<td>{r["rbi"]}</td>'
            f'<td>{r["k"]}</td>'
            f'</tr>'
        )
    if log_body:
        log_html = (
            f'<div class="pdc-section-title">Game Log — {active_chip}</div>'
            '<div class="pdc-card" style="padding: 0;">'
            '<div class="pdc-log-scroll">'
            '<table class="pdc-log-table">'
            '<thead><tr>'
            '<th>Date</th><th>AB</th><th>H</th><th>HR</th><th>TB</th><th>RBI</th><th>K</th>'
            '</tr></thead>'
            f'<tbody>{"".join(log_body)}</tbody>'
            '</table>'
            '</div>'
            '</div>'
        )
    else:
        log_html = (
            f'<div class="pdc-section-title">Game Log — {active_chip}</div>'
            '<div class="pdc-card"><div class="pdc-empty">No games in this window yet.</div></div>'
        )

    return (
        _PLAYER_DETAIL_CSS
        + '<div class="pdc-root">'
        + header_card
        + hr_due_html
        + recap_html
        + rating_html
        + bvp_html
        + chips_html
        + selected_recap
        + recent_html
        + log_html
        + '<div class="pdc-modal-tail"></div>'
        + '</div>'
    )


@st.dialog("Player detail", width="large")
def _open_player_detail_dialog(payload_key: str):
    """Streamlit dialog that renders one player's detail card.

    The actual payload is stashed on session_state under ``payload_key`` so
    the dialog re-renders cheaply on chip toggles without re-fetching the
    game log on every interaction.
    """
    payload = st.session_state.get(payload_key)
    if not payload:
        st.write("No player selected.")
        return

    # Sticky "Back to Matchup" close button at the top of the dialog. iOS
    # users reported the native Streamlit X drifts off-screen once they
    # scroll the long card, so we render our own always-visible close
    # control inside the modal body. It sits in a wrapper div with the
    # `.pdc-close-bar` marker so the CSS in _PLAYER_DETAIL_CSS can pin
    # the following Streamlit button element to the top of the dialog
    # scroll container.
    st.markdown('<div class="pdc-close-bar"></div>', unsafe_allow_html=True)
    if st.button(
        "← Back to Matchup",
        key=f"{payload_key}__close",
        use_container_width=True,
        type="secondary",
    ):
        # Clear active payload pointer and rerun — Streamlit dismisses the
        # dialog when the script reruns after a widget event inside it.
        st.session_state.pop("_pdc_active_key", None)
        st.rerun()

    # Dynamic season label so the chip year doesn't go stale once the
    # calendar flips. Falls back to the slate-date year on the payload.
    try:
        season_label = str(int(payload.get("log_season") or date.today().year))
    except Exception:
        season_label = str(date.today().year)
    chips = ["H2H", "L5", "L10", "L20", season_label, "’25-’26"]
    active = st.radio(
        "Window",
        chips,
        index=2,
        horizontal=True,
        label_visibility="collapsed",
        key=f"{payload_key}__chip",
    )
    st.markdown(_render_player_detail_html(payload, active), unsafe_allow_html=True)


# The global CSS (injected at startup) already contains the .scout-cta-btn
# button overlay rules. This constant provides only the host wrapper
# that the renderer injects once per lineup board.
_MATCHUP_CTA_CSS = '<div class="scout-cta-host"></div>'


def _render_interactive_player_cards(sorted_df, key_prefix, pitchers_df, slate_date):
    """Render each hitter as a horizontal scouting intelligence row with a
    teal 'SCOUT REPORT' CTA button fused to its bottom edge.

    The scouting row (built by render_matchup_player_card_html) is left
    open-bottomed. A visible .scout-cta-btn label sits below it; the actual
    Streamlit button is then pulled up via negative-margin CSS from the global
    stylesheet to overlay the label so the user's click registers on Streamlit.
    """
    if sorted_df is None or sorted_df.empty:
        return

    display_cols = _matchup_display_cols(sorted_df)

    # Open the scout-rows container
    rows_html_parts = []
    for idx, (_, row) in enumerate(sorted_df.iterrows()):
        pid = row.get("_PlayerId")
        name = row.get("Hitter", "")

        row_html = render_matchup_player_card_html(row, display_cols)
        st.markdown(
            f'<div class="scout-rows">{row_html}</div>'
            f'<div class="scout-cta-btn">SCOUT REPORT</div>'
            f'<div class="mhm-cta-click"></div>',
            unsafe_allow_html=True,
        )

        label = "SCOUT REPORT"
        btn_key = f"pdc_open_{key_prefix}_{idx}_{pid or name}"
        if st.button(label, key=btn_key, use_container_width=True):
            payload_key = f"_pdc_payload_{key_prefix}_{idx}"
            st.session_state[payload_key] = _build_player_detail_payload(
                row.to_dict(), pitchers_df, slate_date,
            )
            st.session_state["_pdc_active_key"] = payload_key
            _open_player_detail_dialog(payload_key)


def render_matchup_board_with_sort(board_df, key_prefix, label, *,
                                   pitchers_df=None, slate_date=None):
    """Render sort controls (column + direction) above a matchup heat-map
    board, then render the sorted board. `key_prefix` must be unique per
    board on the page so Streamlit widget state stays isolated (e.g.
    "away_NYY_BOS" vs "home_NYY_BOS").

    The board is shown two ways: a scrollable desktop heat-map table for
    quick scanning, and (when ``pitchers_df`` is supplied) a grid of
    interactive per-player cards where each original heat-map card has a
    "View Matchup Card" button fused to its footer.
    """
    if board_df is None or board_df.empty:
        st.markdown(render_matchup_heatmap_html(board_df), unsafe_allow_html=True)
        return
    available = [c for c in MATCHUP_SORTABLE_COLUMNS
                 if c == "Lineup Spot" or c in board_df.columns]
    c1, c2 = st.columns([3, 2])
    with c1:
        sort_col = st.selectbox(
            f"Sort {label} by",
            available,
            index=0,
            key=f"mhm_sort_col_{key_prefix}",
            format_func=lambda c: MATCHUP_HEATMAP_DISPLAY_LABELS.get(c, c),
        )
    with c2:
        direction = st.radio(
            "Direction",
            ["High → Low", "Low → High"],
            index=0,
            horizontal=True,
            key=f"mhm_sort_dir_{key_prefix}",
            label_visibility="visible",
        )
    descending = direction.startswith("High")
    sorted_df = sort_matchup_board(board_df, sort_col, descending)
    # Desktop heat-map table for quick scanning (hidden on phones).
    st.markdown(render_matchup_heatmap_html(sorted_df), unsafe_allow_html=True)
    # Interactive per-player cards with the CTA fused to each card's footer.
    if pitchers_df is not None:
        _render_interactive_player_cards(sorted_df, key_prefix, pitchers_df, slate_date)

# ===========================================================================
# Slate pitchers (Baseball Savant CSV joined by player_id +
#                MLB Stats API for handedness only)
# ===========================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pitcher_throws(player_id: int) -> str:
    """Cheap MLB Stats API hit just for handedness ('L'/'R'), since the Savant
    CSV doesn't include it. Cached for an hour."""
    if not player_id:
        return ""
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people",
            params={"personIds": int(player_id)},
            headers=HEADERS, timeout=15,
        )
        r.raise_for_status()
        people = r.json().get("people", [])
        if not people:
            return ""
        return people[0].get("pitchHand", {}).get("code", "") or ""
    except Exception:
        return ""

def _safe_pid(value) -> int:
    """Coerce a raw MLB player/pitcher id to int, returning 0 for anything
    that isn't a real id. Sources include pandas Series (NaN), API rows
    (None, ""), and slate dicts ("TBD"). Float-NaN is the common offender
    because it's truthy in Python but blows up int()."""
    if value is None:
        return 0
    try:
        if pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pitcher_throws_batch(player_ids: tuple) -> dict:
    """Batch-fetch handedness for a tuple of pitcher IDs in a single MLB
    StatsAPI /people call. Returns {player_id: 'L'|'R'|''}. Deduped and
    cached as a frozen tuple key so the slate view never makes one request
    per displayed hitter. Empty/zero/NaN IDs are filtered out."""
    ids = sorted({_safe_pid(p) for p in player_ids})
    ids = [i for i in ids if i]
    if not ids:
        return {}
    out: dict = {pid: "" for pid in ids}
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people",
            params={"personIds": ",".join(str(p) for p in ids)},
            headers=HEADERS, timeout=20,
        )
        r.raise_for_status()
        for person in r.json().get("people", []):
            pid = person.get("id")
            if pid in out:
                out[pid] = person.get("pitchHand", {}).get("code", "") or ""
    except Exception:
        pass
    return out

@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_pitcher_season_stats(player_id: int, season: int) -> dict:
    """Fall back to MLB StatsAPI per-pitcher season pitching stats when the
    Savant leaderboard has no row for this pitcher (e.g. first big-league
    appearance, very small sample, recent call-up). StatsAPI exposes K%, BB%,
    IP, ERA, WHIP, BAA from box-score totals — nowhere near the depth of
    Statcast but a real number is always better than a blank.

    Returns a dict with canonical app keys ('K%', 'BB%', etc.) so callers can
    treat it as a partial Savant row."""
    if not player_id:
        return {}
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{int(player_id)}/stats",
            params={"stats": "season", "group": "pitching", "season": int(season)},
            headers=HEADERS, timeout=15,
        )
        r.raise_for_status()
        stats = r.json().get("stats", [])
        if not stats:
            return {}
        splits = stats[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0].get("stat", {}) or {}

        def _pct(s_val):
            if s_val in (None, "", "-.--"):
                return None
            try:
                # StatsAPI returns "23.4" for percentages, sometimes ".234"
                x = float(s_val)
                return x * 100.0 if x <= 1.0 else x
            except Exception:
                return None

        def _f(s_val):
            if s_val in (None, "", "-.--"):
                return None
            try: return float(s_val)
            except Exception: return None

        ip = _f(s.get("inningsPitched"))
        bf = s.get("battersFaced")
        try: bf = int(bf) if bf is not None else None
        except Exception: bf = None
        out = {
            "K%":   _pct(s.get("strikeoutsPer9Inn")) and None,  # we'll compute below
            "BB%":  _pct(s.get("walksPer9Inn")) and None,
            "K/9":  _f(s.get("strikeoutsPer9Inn")),
            "BB/9": _f(s.get("walksPer9Inn")),
            "HR/9": _f(s.get("homeRunsPer9")),
            "IP":   ip,
            "ERA":  _f(s.get("era")),
            "WHIP": _f(s.get("whip")),
            "BAA":  _f(s.get("avg")),
            "BABIP":_f(s.get("babip")),
            "BF":   bf,
        }
        # Compute K% / BB% from raw counts since StatsAPI doesn't ship them
        # directly. Falls back to per-9 rates when batters-faced isn't published.
        try:
            so = int(s.get("strikeOuts") or 0)
            bb = int(s.get("baseOnBalls") or 0)
            hr = int(s.get("homeRuns") or 0)
            h  = int(s.get("hits") or 0)
            if bf and bf > 0:
                out["K%"]  = round(100.0 * so / bf, 1)
                out["BB%"] = round(100.0 * bb / bf, 1)
            # HR/9 fallback when StatsAPI doesn't ship the rate directly.
            if out.get("HR/9") is None and ip and float(ip) > 0:
                out["HR/9"] = round(9.0 * hr / float(ip), 2)
            # BABIP fallback if StatsAPI omitted it. AB ≈ BF − BB.
            if out.get("BABIP") is None and bf:
                ab_est = bf - bb
                denom = ab_est - so - hr
                if denom > 0:
                    out["BABIP"] = round((h - hr) / denom, 3)
        except Exception:
            pass
        return out
    except Exception:
        return {}

def _coerce_pct_or_decimal(v):
    """Savant CSV percentages can come through as either '0.265' (decimal-of-1)
    or '26.5' (already a percent). Normalize so anything <= 1.0 is treated as a
    fraction and scaled up. Strings like '.265' are also handled."""
    if v is None or pd.isna(v) or v == "":
        return None
    try:
        x = float(v)
    except Exception:
        return None
    return x

def _slate_pitcher_lookup(pitcher_stats_df: pd.DataFrame, player_id: int) -> dict:
    """Return the standardized stat row for one pitcher, keyed by player_id."""
    if pitcher_stats_df is None or pitcher_stats_df.empty or not player_id:
        return {}
    if "player_id" not in pitcher_stats_df.columns:
        return {}
    try:
        match = pitcher_stats_df[pitcher_stats_df["player_id"].astype("Int64") == int(player_id)]
    except Exception:
        return {}
    if match.empty:
        return {}
    return match.iloc[0].to_dict()

def build_slate_pitcher_row(game_row, side, pitcher_stats_df):
    """Build one display row for the Slate Pitchers table.

    Data hierarchy per pitcher:
      1. Baseball Savant 2026 leaderboard (Statcast / xwOBA / Whiff% / Barrel%)
      2. MLB StatsAPI 2026 season pitching totals (K%, BB%, IP, ERA, WHIP)
         used to fill K%/BB% blanks when a pitcher has no Savant row yet.
      3. No data — emit None so the renderer shows '—', never a fake constant.
    """
    pid = game_row.get(f"{side}_probable_id")
    pname = game_row.get(f"{side}_probable") or ""
    if not pid or pname in ("", "TBD"):
        return None
    stat = _slate_pitcher_lookup(pitcher_stats_df, pid)
    throws = _fetch_pitcher_throws(int(pid))

    # standardize_columns has already mapped raw Savant columns to canonical
    # names where possible: K%, BB%, xwOBA, wOBA, Whiff%, Swing%, Barrel%,
    # HardHit%, FB%, GB%, SweetSpot%.
    def _g(key):
        if not stat:
            return None
        v = stat.get(key)
        return _coerce_pct_or_decimal(v)

    pa     = _g("pa")
    k_pct  = _g("K%")
    bb_pct = _g("BB%")
    woba   = _g("wOBA")
    xwoba  = _g("xwOBA")
    whiff  = _g("Whiff%")
    swing  = _g("Swing%")
    barrel = _g("Barrel%")
    hardhit = _g("HardHit%")
    fb_pct = _g("FB%")
    gb_pct = _g("GB%")

    have_savant = bool(stat) and (xwoba is not None or whiff is not None or k_pct is not None)

    # StatsAPI: ALWAYS fetched for the per-pitcher season totals (IP, ERA,
    # WHIP) — these don't appear in the Savant leaderboard but are baseline
    # prop context every prop bettor expects. K%/BB% are only used as a
    # fallback when Savant doesn't have them, since Savant's percent is
    # computed off plate appearances and is the more accurate one.
    season_year = 2026
    api_stats = _fetch_pitcher_season_stats(int(pid), season_year)
    if k_pct is None:
        k_pct = api_stats.get("K%")
    if bb_pct is None:
        bb_pct = api_stats.get("BB%")

    ip    = api_stats.get("IP")
    era   = api_stats.get("ERA")
    whip  = api_stats.get("WHIP")
    bf    = api_stats.get("BF")
    k9    = api_stats.get("K/9")
    bb9   = api_stats.get("BB/9")
    hr9   = api_stats.get("HR/9")
    babip = api_stats.get("BABIP")

    # xSLG / wOBA from Savant (decimal form, e.g. 0.412). Used by the HR-target
    # classifier alongside Barrel%/HardHit%/FB%/HR/9 — all of these are the
    # canonical HR-allowed signals already flowing into the app.
    xslg  = _g("xSLG")

    # K-BB% (small-sample skill signal): publish whenever both rates are known.
    kbb_pct = (k_pct - bb_pct) if (k_pct is not None and bb_pct is not None) else None

    # SwStr% (true): swing-rate × whiff-on-swing. Only computable from Savant.
    sw_str = (swing / 100.0) * (whiff / 100.0) * 100.0 if (swing is not None and whiff is not None) else None

    # ---- composite scores (higher = stronger pitcher) ----
    # Returns None when the input is missing so we never blend a real number
    # with a placeholder 50.0 — the score is either grounded in real data or
    # it's '—'.
    def _norm(v, lo, hi, reverse=False):
        if v is None:
            return None
        try:
            x = float(v)
        except Exception:
            return None
        x = max(lo, min(hi, x))
        pct = (x - lo) / (hi - lo) * 100.0
        return 100.0 - pct if reverse else pct

    def _weighted(parts):
        """Weighted average over the parts that aren't None.
        parts is a list of (weight, normalized_value_or_None).
        Returns (score, weight_used) so callers can require >= 0.6 coverage
        before publishing the score."""
        num = 0.0; den = 0.0
        for w, v in parts:
            if v is None: continue
            num += w * v; den += w
        if den == 0:
            return None, 0.0
        return num / den, den

    # Convert xwoba like 0.265 -> 265 for normalization.
    xwoba_scaled = (xwoba * 1000.0) if (xwoba is not None and xwoba <= 1.0) else xwoba

    # Pitch Score: 35% xwOBA-against (lower good) + 25% K-BB% (higher) +
    # 20% Whiff% (higher) + 20% Barrel%-against (lower good). Require ≥60%
    # weight to publish so a starter with only K%/BB% from StatsAPI can't
    # generate a misleading "63.0" Pitch Score.
    kbb_norm = None
    if k_pct is not None and bb_pct is not None:
        kbb_norm = _norm(k_pct - bb_pct, 5.0, 25.0)
    parts = [
        (0.35, _norm(xwoba_scaled, 250.0, 360.0, reverse=True)),
        (0.25, kbb_norm),
        (0.20, _norm(whiff, 18.0, 33.0)),
        (0.20, _norm(barrel, 4.0, 12.0, reverse=True)),
    ]
    raw_pitch, w_pitch = _weighted(parts)
    pitch_score = round(raw_pitch, 1) if (raw_pitch is not None and w_pitch >= 0.60) else None

    k_parts = [
        (0.55, _norm(k_pct, 18.0, 32.0)),
        (0.45, _norm(whiff, 18.0, 33.0)),
    ]
    raw_k, w_k = _weighted(k_parts)
    # K% alone (StatsAPI fallback) is enough; require any input.
    strikeout_score = round(raw_k, 1) if (raw_k is not None and w_k >= 0.45) else None

    if side == "away":
        team_id = game_row["away_id"]; team_abbr = game_row["away_abbr"]; loc_marker = "@"
        opp_abbr = game_row["home_abbr"]
    else:
        team_id = game_row["home_id"]; team_abbr = game_row["home_abbr"]; loc_marker = "vs"
        opp_abbr = game_row["away_abbr"]

    def _r(v, n=1):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try: return round(float(v), n)
        except Exception: return None

    # Sample size + source classification.
    if have_savant:
        try:
            pa_int = int(pa) if pa is not None else 0
        except Exception:
            pa_int = 0
        if pa_int >= 75:
            source_tag = "Savant"
        elif pa_int >= 25:
            source_tag = "Savant·sm"   # small Statcast sample
        else:
            source_tag = "Savant·xs"   # very small Statcast sample
    elif api_stats:
        source_tag = "StatsAPI"
    else:
        source_tag = "No sample"

    sample = None
    if pa is not None:
        try: sample = int(pa)
        except Exception: sample = None
    if sample is None and bf is not None:
        sample = int(bf)

    return {
        "_logo": logo_url(team_id) if team_id else "",
        "_player_id": int(pid),
        "_source_tag": source_tag,
        "Loc": loc_marker,
        "Team": team_abbr,
        "Pitcher": pname,
        "Throws": throws,
        "Opp": opp_abbr,
        "Game": game_row["short_label"],
        "Time": game_row["time_short"],
        "Pitch Score": pitch_score,
        "Strikeout Score": strikeout_score,
        "xwOBA": _r(xwoba, 3),
        "wOBA": _r(woba, 3),
        "K%": _r(k_pct, 1),
        "BB%": _r(bb_pct, 1),
        "K-BB%": _r(kbb_pct, 1),
        "K/9": _r(k9, 2),
        "BB/9": _r(bb9, 2),
        "HR/9": _r(hr9, 2),
        "xSLG": _r(xslg, 3),
        "BABIP": _r(babip, 3),
        "Whiff%": _r(whiff, 1),
        "SwStr%": _r(sw_str, 1),
        "Barrel%": _r(barrel, 1),
        "HH%": _r(hardhit, 1),
        "FB%": _r(fb_pct, 1),
        "GB%": _r(gb_pct, 1),
        "IP": _r(ip, 1),
        "ERA": _r(era, 2),
        "WHIP": _r(whip, 2),
        "PA": sample,
        "Source": source_tag,
    }

@st.cache_data(ttl=900, show_spinner=False)
def build_slate_pitcher_table(_schedule_df, _pitcher_stats_df):
    """One row per probable starter (away + home) for the slate."""
    out = []
    for _, g in _schedule_df.iterrows():
        for side in ("away", "home"):
            row = build_slate_pitcher_row(g, side, _pitcher_stats_df)
            if row:
                out.append(row)
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.sort_values("Pitch Score", ascending=False, na_position="last").reset_index(drop=True)
    return df

def _heat_bg(v, lo, hi, reverse=False):
    """Return a CSS color for a numeric value on a green→yellow→red gradient.
    Higher = green by default; pass reverse=True to invert."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "#f8fafc"
    if hi == lo:
        t = 0.5
    else:
        t = (x - lo) / (hi - lo)
        t = max(0.0, min(1.0, t))
    if reverse:
        t = 1.0 - t
    # 0.0 = red (#fca5a5), 0.5 = amber (#fde68a), 1.0 = green (#86efac)
    if t < 0.5:
        # red -> amber
        f = t / 0.5
        r = int(252 + (253 - 252) * f); g = int(165 + (230 - 165) * f); b = int(165 + (138 - 165) * f)
    else:
        f = (t - 0.5) / 0.5
        r = int(253 + (134 - 253) * f); g = int(230 + (239 - 230) * f); b = int(138 + (172 - 138) * f)
    return f"rgb({r},{g},{b})"

SLATE_PITCHER_HEATMAP = {
    # column -> (lo, hi, reverse) where reverse=True means lower is greener
    "Pitch Score":     (45.0, 75.0, False),
    "Strikeout Score": (45.0, 75.0, False),
    "xwOBA":           (0.260, 0.340, True),
    "wOBA":            (0.260, 0.340, True),
    "K%":              (18.0, 32.0,   False),
    "K/9":             (6.0,  12.0,   False),
    "BB%":             (5.0,  11.0,   True),
    "BB/9":            (2.0,  4.5,    True),
    "K-BB%":           (5.0,  25.0,   False),
    "BABIP":           (0.260, 0.330, True),
    "Whiff%":          (20.0, 32.0,   False),
    "SwStr%":          (9.0,  15.0,   False),
    "Barrel%":         (4.0,  12.0,   True),
    "HH%":             (32.0, 45.0,   True),
    "FB%":             (30.0, 48.0,   False),
    "GB%":             (38.0, 55.0,   False),
    "ERA":             (2.50, 5.50,   True),
    "WHIP":            (1.00, 1.50,   True),
}

SLATE_PITCHER_FORMAT = {
    "Pitch Score": "{:.1f}", "Strikeout Score": "{:.1f}",
    "xwOBA": "{:.3f}", "wOBA": "{:.3f}",
    "K%": "{:.1f}", "BB%": "{:.1f}",
    "K/9": "{:.2f}", "BB/9": "{:.2f}",
    "K-BB%": "{:+.1f}", "BABIP": "{:.3f}",
    "Whiff%": "{:.1f}", "SwStr%": "{:.1f}",
    "Barrel%": "{:.1f}", "HH%": "{:.1f}",
    "FB%": "{:.1f}", "GB%": "{:.1f}",
    "PA": "{:.0f}", "IP": "{:.1f}",
    "ERA": "{:.2f}", "WHIP": "{:.2f}",
}

def render_slate_pitcher_html(df, schedule_df=None):
    """Render a custom HTML table with team logos in the Team cell and a
    green/red heatmap on the metric columns. Mirrors the design in the
    user-supplied screenshot. When schedule_df is provided, the Pitcher
    cell becomes a link that deep-links the user to that game's Pitcher
    Zones section in the Games view."""
    if df.empty:
        return "<div class='sp-empty'>No probable starters posted yet.</div>"

    # Map game_pk / short_label -> schedule index so we can deep-link.
    label_to_idx = {}
    if schedule_df is not None and not schedule_df.empty:
        for i, srow in schedule_df.reset_index(drop=True).iterrows():
            label_to_idx[str(srow.get("short_label", ""))] = i

    # Preferred display order. Anything in the DataFrame that isn't listed
    # here gets appended at the end (keeps the table forward-compatible if
    # we add new metrics later).
    PREFERRED = [
        "Team", "Pitcher", "Throws", "Opp", "Time", "Source",
        "Pitch Score", "Strikeout Score",
        "xwOBA", "wOBA", "K%", "BB%",
        "Whiff%", "SwStr%",
        "Barrel%", "HH%", "FB%", "GB%",
        "ERA", "WHIP", "IP", "PA", "Game",
    ]
    available = [c for c in df.columns if not c.startswith("_") and c != "Loc"]
    # Hide any column that is fully blank across the slate so unpopulated metrics
    # don't render as a wall of "—". Identity/context columns are always kept so
    # rows still anchor visually even when their numeric metrics are empty.
    _always_keep = {"Team", "Pitcher", "Throws", "Opp", "Time", "Game", "Source"}
    def _col_has_data(col):
        if col in _always_keep:
            return True
        s = df[col]
        try:
            non_null = s.dropna()
        except Exception:
            return True
        if non_null.empty:
            return False
        # Treat empty strings as missing for object columns.
        if non_null.dtype == object:
            non_null = non_null[non_null.astype(str).str.strip() != ""]
            if non_null.empty:
                return False
        return True
    available = [c for c in available if _col_has_data(c)]
    show_cols = [c for c in PREFERRED if c in available] + [c for c in available if c not in PREFERRED]
    css = (
        "<style>"
        ".sp-wrap { overflow-x:auto; border-radius:14px; border:1px solid rgba(255,255,255,.07); "
        "  background:#0b1220; box-shadow: 0 2px 8px rgba(0,0,0,.35); margin: 6px 0 14px 0; }"
        ".sp-table { border-collapse: separate; border-spacing:0; width:100%; "
        "  font-size: 0.86rem; color:#e2e8f0; font-family: inherit; }"
        ".sp-table thead th { background:#1a2744; color:#7dd3fc; font-weight:800; "
        "  text-align:center; padding: 10px 8px; border-bottom:1px solid rgba(255,255,255,.07); "
        "  position: sticky; top: 0; z-index: 1; white-space: nowrap; }"
        ".sp-table thead th.sp-sort { color:#ffffff; }"
        ".sp-table tbody td { padding: 8px 8px; border-bottom:1px solid rgba(255,255,255,.05); "
        "  text-align:center; white-space: nowrap; }"
        ".sp-table tbody tr:hover td { background:rgba(0,200,150,.04); }"
        ".sp-team-cell { display:flex; align-items:center; gap:8px; justify-content:flex-start; "
        "  text-align:left; padding-left:6px; }"
        ".sp-team-cell img { width:24px; height:24px; object-fit:contain; }"
        ".sp-loc { color:#7dd3fc; font-weight:800; width: 28px; }"
        ".sp-pitcher { text-align:left; font-weight:700; }"
        ".sp-pitcher-link { color:#e2e8f0; text-decoration:none; border-bottom: 1px dashed rgba(255,255,255,.3); }"
        ".sp-pitcher-link:hover { color:#00c896; border-bottom-color:#00c896; }"
        ".sp-pitcher-link:hover::after { color:#00c896; }"
        ".sp-num { font-variant-numeric: tabular-nums; font-weight:700; }"
        ".sp-na { color:#4e6a8a; }"
        ".sp-empty { padding:14px 18px; color:#94a3b8; background:rgba(255,255,255,.03); border-radius:14px; "
        "  border:1px dashed rgba(255,255,255,.1); }"
        ".sp-src { display:inline-block; padding: 2px 8px; border-radius: 999px; "
        "  font-size: .68rem; font-weight: 800; letter-spacing: .04em; "
        "  text-transform: uppercase; line-height: 1.4; }"
        ".sp-src-savant   { background:rgba(34,197,94,.18); color:#4ade80; border:1px solid rgba(34,197,94,.3); }"
        ".sp-src-savant-sm{ background:rgba(250,204,21,.12); color:#fde68a; border:1px solid rgba(250,204,21,.3); }"
        ".sp-src-savant-xs{ background:rgba(251,146,60,.12); color:#fb923c; border:1px solid rgba(251,146,60,.3); }"
        ".sp-src-statsapi { background:rgba(59,130,246,.12); color:#93c5fd; border:1px solid rgba(59,130,246,.3); }"
        ".sp-src-none     { background:rgba(255,255,255,.06); color:#94a3b8; border:1px solid rgba(255,255,255,.1); }"
        "</style>"
    )

    # Build header
    head_cells = []
    for c in show_cols:
        cls = "sp-sort" if c == "Pitch Score" else ""
        label = ("↓ " + c) if c == "Pitch Score" else c
        head_cells.append(f'<th class="{cls}">{label}</th>')
    thead = "<thead><tr>" + "".join(head_cells) + "</tr></thead>"

    body_rows = []
    for _, r in df.iterrows():
        cells = []
        loc = str(r.get("Loc", ""))
        for c in show_cols:
            v = r.get(c)
            if c == "Team":
                logo = r.get("_logo", "")
                logo_img = f'<img src="{logo}" alt="{v}" />' if logo else ""
                cells.append(
                    f'<td><div class="sp-team-cell">'
                    f'<span class="sp-loc">{loc}</span>{logo_img}'
                    f'<span>{v}</span></div></td>'
                )
                continue
            if c == "Pitcher":
                cells.append(f'<td class="sp-pitcher">{v}</td>')
                continue
            if c in ("Throws", "Game", "Time", "Opp"):
                display_v = format_pitcher_hand(v) if c == "Throws" else v
                cells.append(f"<td>{display_v if display_v not in (None, '') else '<span class=\"sp-na\">—</span>'}</td>")
                continue
            if c == "Source":
                tag = str(v or "")
                cls_map = {
                    "Savant":     "sp-src-savant",
                    "Savant·sm":  "sp-src-savant-sm",
                    "Savant·xs":  "sp-src-savant-xs",
                    "StatsAPI":   "sp-src-statsapi",
                    "No sample":  "sp-src-none",
                }
                cls = cls_map.get(tag, "sp-src-none")
                title_map = {
                    "Savant":     "Baseball Savant 2026 (Statcast, ≥75 BF)",
                    "Savant·sm":  "Baseball Savant 2026 (small sample, 25–74 BF)",
                    "Savant·xs":  "Baseball Savant 2026 (very small sample, <25 BF)",
                    "StatsAPI":   "MLB StatsAPI 2026 season totals (no Statcast yet)",
                    "No sample":  "No 2026 sample available",
                }
                title = title_map.get(tag, "")
                cells.append(
                    f'<td><span class="sp-src {cls}" title="{title}">{tag or "—"}</span></td>'
                )
                continue
            # Numeric / heatmap column.
            if v is None or (isinstance(v, float) and pd.isna(v)):
                cells.append('<td class="sp-num"><span class="sp-na">—</span></td>')
                continue
            fmt = SLATE_PITCHER_FORMAT.get(c, "{}")
            try:
                txt = fmt.format(float(v))
            except Exception:
                txt = str(v)
            heat = SLATE_PITCHER_HEATMAP.get(c)
            if heat:
                lo, hi, rev = heat
                bg = _heat_bg(v, lo, hi, reverse=rev)
                cells.append(f'<td class="sp-num" style="background:{bg};">{txt}</td>')
            else:
                cells.append(f'<td class="sp-num">{txt}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    tbody = "<tbody>" + "".join(body_rows) + "</tbody>"

    return css + f'<div class="sp-wrap"><table class="sp-table">{thead}{tbody}</table></div>'

# ---------------------------------------------------------------------------
# Mobile-first Slate Pitchers dashboard
#
# Renders a vertically-stacked card per pitcher (no left-to-right scrolling),
# emphasizing the small-sample-stable skill metrics: K%, K/9, BB%, K-BB%, WHIP,
# BABIP. Tier badges flag pitchers who project as "K Dominator", "Command Edge",
# "Traffic Limiter" or "Matchup Boost" based on the metric mix. Designed to
# replace the wide heatmap table on phones.
# ---------------------------------------------------------------------------

def _sp_pct_bar(value, lo, hi, reverse=False):
    """Map a value to a 0-100 'goodness' percent. Higher % = better pitcher.
    reverse=True flips it (lower raw value = better, e.g. BB%, BABIP, WHIP)."""
    if value is None:
        return None
    try:
        x = float(value)
    except Exception:
        return None
    if hi == lo:
        return 50.0
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    if reverse:
        t = 1.0 - t
    return round(t * 100.0, 1)

def _sp_is_hr_target(row: dict):
    """Classify whether a starting pitcher is an HR-target (i.e. opposing hitters
    are likely to slug / hit HRs off them tonight). Returns (level, reason) where
    level ∈ {None, "soft", "hard"}.

    Uses whatever live HR-allowed signals are present on the row:
      • HR/9        — direct rate of HRs surrendered (StatsAPI live season totals)
      • Barrel%     — opponent barrel rate vs this pitcher (Savant)
      • HH%         — opponent hard-hit rate vs this pitcher (Savant)
      • FB%         — opponent fly-ball rate vs this pitcher (Savant); HRs are
                       almost all fly balls, so FB-prone arms are at risk.
      • xSLG        — opponent expected SLG vs this pitcher (Savant)
      • ERA / WHIP  — overall run-prevention context (StatsAPI)

    Lower-is-better for ALL of these — they're "allowed" metrics on a pitcher.
    Missing signals are simply skipped (graceful degradation). Need at least
    one HR-allowed signal to fire."""

    def _f(v):
        try:
            if v is None: return None
            x = float(v)
            if x != x:  # NaN
                return None
            return x
        except Exception:
            return None

    hr9    = _f(row.get("HR/9"))
    barrel = _f(row.get("Barrel%"))
    hh     = _f(row.get("HH%"))
    fb     = _f(row.get("FB%"))
    xslg   = _f(row.get("xSLG"))
    era    = _f(row.get("ERA"))
    whip   = _f(row.get("WHIP"))

    # Per-signal HR-vulnerability flags. Thresholds are pitcher-allowed values.
    flags = []
    severity = 0  # how many "hard" (clearly bad) signals fire
    if hr9 is not None:
        if hr9 >= 1.50:    flags.append(f"HR/9 {hr9:.2f}"); severity += 1
        elif hr9 >= 1.20:  flags.append(f"HR/9 {hr9:.2f}")
    if barrel is not None:
        if barrel >= 10.0:   flags.append(f"{barrel:.0f}% barrel"); severity += 1
        elif barrel >= 8.0:  flags.append(f"{barrel:.0f}% barrel")
    if hh is not None:
        if hh >= 42.0:   flags.append(f"{hh:.0f}% HH"); severity += 1
        elif hh >= 38.0: flags.append(f"{hh:.0f}% HH")
    if fb is not None and fb >= 40.0:
        # FB% over 40 is a noticeable fly-ball lean; HRs ride on fly balls.
        flags.append(f"{fb:.0f}% FB")
        if fb >= 45.0:
            severity += 1
    if xslg is not None:
        # Decimal form (e.g. 0.430). Anything ≥ .430 is in HR-prone territory.
        if xslg >= 0.450:   flags.append(f"xSLG {xslg:.3f}"); severity += 1
        elif xslg >= 0.420: flags.append(f"xSLG {xslg:.3f}")
    if era is not None and era >= 5.00:
        flags.append(f"ERA {era:.2f}")
    if whip is not None and whip >= 1.40:
        flags.append(f"WHIP {whip:.2f}")

    if not flags:
        return None, ""
    # Require at least one HR-specific signal (HR/9, Barrel%, xSLG) to tag.
    has_direct_hr_signal = any(
        flag for flag in flags
        if flag.startswith("HR/9") or "barrel" in flag or flag.startswith("xSLG")
    )
    if not has_direct_hr_signal:
        return None, ""
    level = "hard" if severity >= 2 else "soft"
    return level, " · ".join(flags[:3])


def _sp_compute_tiers(row: dict) -> list:
    """Return a list of (label, tone) tier callouts for one pitcher row.
    tone ∈ {good, warn, info, bad}. Empty list when nothing qualifies."""
    tiers = []
    k_pct  = row.get("K%")
    bb_pct = row.get("BB%")
    kbb    = row.get("K-BB%")
    whip   = row.get("WHIP")
    babip  = row.get("BABIP")
    whiff  = row.get("Whiff%")
    k9     = row.get("K/9")

    def _ge(v, t):
        try: return v is not None and float(v) >= t
        except Exception: return False
    def _le(v, t):
        try: return v is not None and float(v) <= t
        except Exception: return False

    # HR Target — opposing hitters profile as having HR upside vs this arm.
    # Surfaced first because it's the headline call most prop bettors want.
    hr_level, hr_reason = _sp_is_hr_target(row)
    if hr_level == "hard":
        tiers.append((f"💣 HR Target · {hr_reason}", "bad"))
    elif hr_level == "soft":
        tiers.append((f"💥 HR Target · {hr_reason}", "warn"))

    # K Dominator — bat-missing engine.
    if _ge(k_pct, 27.0) or _ge(k9, 10.0) or _ge(whiff, 30.0):
        tiers.append(("⚡ K Dominator", "good"))
    # Command Edge — limits free passes AND misses bats.
    if _le(bb_pct, 6.5) and (_ge(k_pct, 22.0) or _ge(kbb, 17.0)):
        tiers.append(("🎯 Command Edge", "good"))
    # Traffic Limiter — WHIP based: keeps the bases clear.
    if _le(whip, 1.15):
        tiers.append(("🛡️ Traffic Limiter", "good"))
    # Matchup Boost — K-BB% leans heavily positive (the most predictive small-sample mix).
    if _ge(kbb, 18.0):
        tiers.append(("📈 Matchup Boost", "good"))
    # Luck filter — BABIP context, not skill. Flag suspiciously low/high.
    if _le(babip, 0.260):
        tiers.append(("🍀 BABIP Luck", "warn"))   # may regress worse
    elif _ge(babip, 0.330):
        tiers.append(("☁️ BABIP Unlucky", "info"))  # may regress better
    # Fade warning — high walks + soft K.
    if _ge(bb_pct, 10.0) and (k_pct is None or _le(k_pct, 19.0)):
        tiers.append(("⚠️ Fade Risk", "warn"))
    return tiers

# Mobile-first metric panel: which metrics get a chip on the card, and the
# (lo, hi, reverse) goodness scale for the colored progress bar behind them.
#
# Directionality note: a pitcher's `K%`, `K/9`, `K-BB%`, `Whiff%` are "strength"
# metrics — higher is better, so reverse=False maps high→green. Every other
# metric here is an "allowed/risk" metric (BB%, WHIP, BABIP, HR/9, Barrel%,
# HH%, ERA) — lower is better for the pitcher, so reverse=True flips the
# gradient so low values still read green.
SP_CARD_METRICS = [
    # (key, label, fmt, lo, hi, reverse)
    ("K%",      "K%",      "{:.1f}",  18.0, 32.0,   False),
    ("K/9",     "K/9",     "{:.2f}",  6.0,  12.0,   False),
    ("BB%",     "BB%",     "{:.1f}",  5.0,  11.0,   True),
    ("K-BB%",   "K-BB%",   "{:+.1f}", 5.0,  25.0,   False),
    ("WHIP",    "WHIP",    "{:.2f}",  1.00, 1.50,   True),
    ("HR/9",    "HR/9",    "{:.2f}",  0.80, 1.60,   True),   # HR-allowed rate
    ("Barrel%", "Barrel%", "{:.1f}",  4.0,  12.0,   True),   # opponent barrel rate
    ("HH%",     "HH%",     "{:.1f}",  32.0, 45.0,   True),   # opponent hard-hit
    ("BABIP",   "BABIP",   "{:.3f}",  0.260, 0.330, True),   # context only
]

def render_slate_pitcher_dashboard(df, schedule_df=None):
    """Mobile-first stacked-card dashboard for the slate's probable pitchers.

    Replaces the wide heatmap table. Each pitcher gets a self-contained card:
    header (team logo, name, throws, opponent, time, source chip + tier badges),
    a 2-column grid of metric chips (K%, K/9, BB%, K-BB%, WHIP, BABIP) with a
    colored fill that encodes 'goodness' for that metric (higher % bar = better
    pitcher; BB%/BABIP/WHIP bars are reversed so low values still read green).

    The card grid is 2-column on phones, expanding to 3 columns above ~720px.
    Nothing scrolls horizontally."""
    if df is None or df.empty:
        return "<div class='spd-empty'>No probable starters posted yet.</div>"

    css = (
        "<style>"
        ".spd-wrap { display:grid; grid-template-columns: 1fr; gap: 14px; "
        "  margin: 8px 0 14px 0; }"
        "@media (min-width: 720px) { .spd-wrap { grid-template-columns: repeat(2, 1fr); } }"
        "@media (min-width: 1100px) { .spd-wrap { grid-template-columns: repeat(3, 1fr); } }"
        ".spd-card { background: linear-gradient(180deg, #111827 0%, #0b1220 100%); "
        "  border:1px solid #1f2937; border-radius: 16px; padding: 14px; "
        "  color:#e5e7eb; box-shadow: 0 4px 14px rgba(0,0,0,.25); "
        "  display:flex; flex-direction:column; gap:10px; min-width:0; }"
        ".spd-head { display:flex; align-items:center; gap:10px; min-width:0; }"
        ".spd-logo { width:36px; height:36px; object-fit:contain; flex:0 0 auto; "
        "  background:#0f172a; border-radius:8px; padding:3px; }"
        ".spd-id { display:flex; flex-direction:column; min-width:0; flex:1 1 auto; }"
        ".spd-name { font-weight:800; font-size:1.02rem; line-height:1.15; "
        "  color:#f8fafc; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
        ".spd-sub { font-size:.78rem; color:#94a3b8; margin-top:2px; "
        "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
        ".spd-score { font-variant-numeric: tabular-nums; font-weight:800; "
        "  font-size:1.1rem; color:#facc15; text-align:right; flex:0 0 auto; "
        "  padding-left:6px; }"
        ".spd-score small { display:block; font-size:.65rem; color:#94a3b8; "
        "  font-weight:700; letter-spacing:.06em; text-transform:uppercase; }"
        ".spd-tiers { display:flex; flex-wrap:wrap; gap:6px; }"
        ".spd-tier { display:inline-block; padding: 3px 8px; border-radius:999px; "
        "  font-size:.7rem; font-weight:800; letter-spacing:.02em; "
        "  border:1px solid transparent; }"
        ".spd-tier.good { background: rgba(16,185,129,.14); color:#6ee7b7; "
        "  border-color: rgba(110,231,183,.35); }"
        ".spd-tier.warn { background: rgba(244,114,182,.12); color:#fda4af; "
        "  border-color: rgba(253,164,175,.35); }"
        ".spd-tier.info { background: rgba(96,165,250,.12); color:#93c5fd; "
        "  border-color: rgba(147,197,253,.35); }"
        ".spd-tier.bad { background: rgba(239,68,68,.18); color:#fca5a5; "
        "  border-color: rgba(248,113,113,.45); }"
        ".spd-src { font-size:.62rem; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; padding:2px 7px; border-radius:999px; "
        "  border:1px solid #334155; color:#cbd5e1; background:#0f172a; }"
        ".spd-grid { display:grid; grid-template-columns: 1fr 1fr; gap: 8px; }"
        "@media (min-width: 480px) { .spd-grid { grid-template-columns: 1fr 1fr 1fr; } }"
        ".spd-chip { position:relative; background:#0f172a; border:1px solid #1f2937; "
        "  border-radius:10px; padding:8px 10px; overflow:hidden; }"
        ".spd-chip-fill { position:absolute; left:0; top:0; bottom:0; width:0%; "
        "  background: linear-gradient(90deg, rgba(16,185,129,.22), rgba(16,185,129,.06)); "
        "  z-index:0; }"
        ".spd-chip.bad { border-color: rgba(248,113,113,.45); }"
        ".spd-chip.bad .spd-chip-fill { background: linear-gradient(90deg, "
        "  rgba(248,113,113,.22), rgba(248,113,113,.06)); }"
        ".spd-chip.mid { border-color: rgba(250,204,21,.45); }"
        ".spd-chip.mid .spd-chip-fill { background: linear-gradient(90deg, "
        "  rgba(250,204,21,.22), rgba(250,204,21,.06)); }"
        ".spd-chip.good { border-color: rgba(110,231,183,.40); }"
        ".spd-chip-label { position:relative; z-index:1; font-size:.7rem; "
        "  color:#94a3b8; font-weight:700; letter-spacing:.04em; "
        "  text-transform:uppercase; }"
        ".spd-chip-val { position:relative; z-index:1; font-size:1.05rem; "
        "  font-weight:800; color:#f8fafc; font-variant-numeric: tabular-nums; "
        "  margin-top:2px; }"
        # Tone the metric value text green/yellow/red so the chip directly
        # signals 'good/ok/bad' for that pitcher metric at a glance.
        ".spd-chip.good .spd-chip-val { color:#6ee7b7; }"
        ".spd-chip.mid  .spd-chip-val { color:#fde68a; }"
        ".spd-chip.bad  .spd-chip-val { color:#fca5a5; }"
        ".spd-chip-na .spd-chip-val { color:#64748b; }"
        ".spd-foot { font-size:.72rem; color:#94a3b8; display:flex; "
        "  justify-content:space-between; gap:8px; flex-wrap:wrap; }"
        ".spd-foot b { color:#e2e8f0; }"
        ".spd-empty { padding:14px 18px; color:#94a3b8; background:#0f172a; "
        "  border:1px dashed #334155; border-radius:14px; }"
        "</style>"
    )

    cards = []
    for _, r in df.iterrows():
        rd = r.to_dict() if hasattr(r, "to_dict") else dict(r)
        logo = rd.get("_logo") or ""
        name = str(rd.get("Pitcher") or "—")
        throws = rd.get("Throws") or ""
        throws_label = format_pitcher_hand(throws, "")
        team = rd.get("Team") or ""
        opp  = rd.get("Opp") or ""
        loc  = rd.get("Loc") or ""
        time_ = rd.get("Time") or ""
        src   = rd.get("Source") or ""
        pscore = rd.get("Pitch Score")
        ip = rd.get("IP")
        era = rd.get("ERA")
        pa = rd.get("PA")

        # Header sub: TEAM (throws) loc OPP · time
        sub_bits = [team]
        if throws_label:
            sub_bits[-1] += f" ({throws_label})"
        if opp:
            sub_bits.append(f"{loc or 'vs'} {opp}")
        if time_:
            sub_bits.append(str(time_))
        sub = " · ".join([b for b in sub_bits if b])

        score_html = ""
        if pscore is not None:
            try:
                score_html = (
                    f'<div class="spd-score">{float(pscore):.0f}'
                    f'<small>Pitch Score</small></div>'
                )
            except Exception:
                score_html = ""

        # Tier badges
        tiers = _sp_compute_tiers(rd)
        tier_html = ""
        if tiers or src:
            chips = []
            for label, tone in tiers:
                chips.append(f'<span class="spd-tier {tone}">{label}</span>')
            if src:
                chips.append(f'<span class="spd-src" title="Data source">{src}</span>')
            tier_html = '<div class="spd-tiers">' + "".join(chips) + "</div>"

        # Metric chips
        chip_html_parts = []
        for key, label, fmt, lo, hi, rev in SP_CARD_METRICS:
            v = rd.get(key)
            pct = _sp_pct_bar(v, lo, hi, reverse=rev)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                chip_html_parts.append(
                    f'<div class="spd-chip spd-chip-na">'
                    f'<div class="spd-chip-fill" style="width:0%"></div>'
                    f'<div class="spd-chip-label">{label}</div>'
                    f'<div class="spd-chip-val">—</div></div>'
                )
                continue
            try:
                txt = fmt.format(float(v))
            except Exception:
                txt = str(v)
            tone_cls = "good"
            if pct is not None:
                if pct < 33:
                    tone_cls = "bad"
                elif pct < 66:
                    tone_cls = "mid"
            chip_html_parts.append(
                f'<div class="spd-chip {tone_cls}">'
                f'<div class="spd-chip-fill" style="width:{pct or 0}%"></div>'
                f'<div class="spd-chip-label">{label}</div>'
                f'<div class="spd-chip-val">{txt}</div></div>'
            )

        # Foot: IP / ERA / PA context
        foot_bits = []
        if ip is not None:
            try: foot_bits.append(f"<b>IP</b> {float(ip):.1f}")
            except Exception: pass
        if era is not None:
            try: foot_bits.append(f"<b>ERA</b> {float(era):.2f}")
            except Exception: pass
        if pa is not None:
            try: foot_bits.append(f"<b>BF/PA</b> {int(pa)}")
            except Exception: pass
        foot_html = '<div class="spd-foot">' + " · ".join(foot_bits) + "</div>" if foot_bits else ""

        logo_html = (
            f'<img class="spd-logo" src="{logo}" alt="{team}" />'
            if logo else
            '<div class="spd-logo" style="background:#0f172a;"></div>'
        )

        cards.append(
            '<div class="spd-card">'
            '<div class="spd-head">'
            f'{logo_html}'
            f'<div class="spd-id"><div class="spd-name">{name}</div>'
            f'<div class="spd-sub">{sub}</div></div>'
            f'{score_html}'
            '</div>'
            f'{tier_html}'
            f'<div class="spd-grid">{"".join(chip_html_parts)}</div>'
            f'{foot_html}'
            '</div>'
        )

    return css + '<div class="spd-wrap">' + "".join(cards) + "</div>"


def render_slate_pitcher_explainer():
    """Static explanation panel: what each metric means, what stabilizes
    fast in small samples, and which categories are most predictive."""
    return (
        "<style>"
        ".spx-wrap { background: linear-gradient(180deg, #0f172a 0%, #0b1220 100%); "
        "  color:#e5e7eb; border:1px solid #1f2937; border-radius:16px; "
        "  padding:14px 16px; margin: 6px 0 14px 0; }"
        ".spx-title { font-weight:800; font-size:1.0rem; letter-spacing:.02em; "
        "  color:#f8fafc; margin-bottom:8px; display:flex; align-items:center; gap:8px; }"
        ".spx-title .spx-pill { font-size:.65rem; padding:2px 8px; border-radius:999px; "
        "  background:#1e293b; color:#94a3b8; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; }"
        ".spx-grid { display:grid; grid-template-columns: 1fr; gap: 8px; }"
        "@media (min-width: 640px) { .spx-grid { grid-template-columns: 1fr 1fr; } }"
        ".spx-row { background:#111827; border:1px solid #1f2937; border-radius:10px; "
        "  padding:10px 12px; }"
        ".spx-row .k { font-weight:800; color:#facc15; font-size:.85rem; }"
        ".spx-row .tag { font-size:.62rem; padding:2px 7px; border-radius:999px; "
        "  margin-left:6px; font-weight:800; letter-spacing:.05em; "
        "  text-transform:uppercase; }"
        ".spx-row .tag.top   { background: rgba(250,204,21,.16); color:#fde68a; }"
        ".spx-row .tag.fast  { background: rgba(110,231,183,.14); color:#6ee7b7; }"
        ".spx-row .tag.skill { background: rgba(147,197,253,.14); color:#93c5fd; }"
        ".spx-row .tag.luck  { background: rgba(253,164,175,.14); color:#fda4af; }"
        ".spx-row .d { color:#cbd5e1; font-size:.86rem; margin-top:4px; line-height:1.35; }"
        ".spx-note { color:#94a3b8; font-size:.78rem; margin-top:10px; line-height:1.4; }"
        "</style>"
        '<div class="spx-wrap">'
        '<div class="spx-title">📖 How to read these metrics '
        '<span class="spx-pill">small-sample guide</span></div>'
        '<div class="spx-grid">'
        '<div class="spx-row"><span class="k">K-BB%</span>'
        '<span class="tag top">Top predictor</span>'
        '<div class="d">Strikeouts minus walks per batter faced. The single most '
        'predictive small-sample skill stat — it captures bat-missing ability and '
        'command in one number. Anything <b>≥ 18%</b> is elite.</div></div>'
        '<div class="spx-row"><span class="k">K%</span>'
        '<span class="tag fast">Stabilizes fast</span>'
        '<div class="d">Strikeout rate per batter. Stabilizes around <b>~60 BF</b>, '
        'making it one of the first usable signals on a new arm. <b>≥ 27%</b> = '
        'truly hard to make contact against.</div></div>'
        '<div class="spx-row"><span class="k">BB%</span>'
        '<span class="tag fast">Stabilizes fast</span>'
        '<div class="d">Walk rate. Stabilizes around <b>~120 BF</b>. Low walks '
        '(<b>≤ 6.5%</b>) mean batters have to earn their way on — a big driver of '
        'WHIP and run prevention.</div></div>'
        '<div class="spx-row"><span class="k">K/9</span>'
        '<span class="tag fast">Stabilizes fast</span>'
        '<div class="d">Strikeouts per nine innings. A useful headline number for '
        'prop bets and starter quality. <b>≥ 10</b> is bat-missing territory; '
        '&lt; 7 puts the offense in play.</div></div>'
        '<div class="spx-row"><span class="k">WHIP</span>'
        '<span class="tag skill">Overall skill</span>'
        '<div class="d">Walks + hits per inning. A blended measure of how often '
        'a pitcher lets baserunners on. Best starters live <b>≤ 1.10</b>; over '
        '1.40 is traffic city. Includes some hit luck — pair with BABIP.</div></div>'
        '<div class="spx-row"><span class="k">BABIP</span>'
        '<span class="tag luck">Luck filter</span>'
        '<div class="d">Opponent batting average on balls in play. League norm is '
        '<b>~.295</b>. Pitchers running <b>&lt; .260</b> are usually due for '
        'regression worse; <b>&gt; .330</b> are often pitching better than the '
        'surface stats show.</div></div>'
        '</div>'
        '<div class="spx-note">📊 <b>How hard is this pitcher to bat against?</b> '
        'Lead with K-BB% and K/9 for bat-missing ability, then check BB% and WHIP '
        'for whether the bases stay clear. Use BABIP last — as a luck check, not '
        'a skill grade. Whiff% / SwStr% (shown on the card chips when Statcast '
        'data exists) confirms the K% signal is driven by real swing-and-miss, '
        'not weak opposing lineups.</div>'
        '</div>'
    )
    """Compact two-up cards summarizing both starters in the current game.
    Each card pulls Pitch Score, Strikeout Score and the key Statcast headline
    metrics (xwOBA-against, K%, Whiff%, Barrel%, HH%) from the Slate Pitchers
    builder. Used at the top of the per-game Matchup tab."""
    css = (
        "<style>"
        ".spc-row { display:flex; gap:12px; flex-wrap:wrap; margin: 4px 0 14px 0; }"
        ".spc-card { flex:1 1 0; min-width: 280px; "
        "  background:linear-gradient(180deg,#111827 0%,#0b1220 100%); "
        "  border:2px solid rgba(255,255,255,.08); "
        "  border-radius:14px; padding: 12px 14px; box-shadow: 0 2px 12px rgba(0,0,0,.45); "
        "  position: relative; }"
        ".spc-tier-elite  { border-color:rgba(22,163,74,.5); }"
        ".spc-tier-strong { border-color:rgba(132,204,22,.4); }"
        ".spc-tier-ok     { border-color:rgba(250,204,21,.4); }"
        ".spc-tier-soft   { border-color:rgba(249,115,22,.4); }"
        ".spc-tier-poor   { border-color:rgba(239,68,68,.4); }"
        ".spc-tag { position:absolute; top:-10px; left:14px; background:#1a2744; color:#7dd3fc; "
        "  font-size:.68rem; font-weight:800; padding:3px 9px; border-radius:999px; "
        "  letter-spacing:.06em; border:1px solid rgba(125,211,252,.2); }"
        ".spc-head { display:flex; align-items:center; gap:10px; margin-top:2px; }"
        ".spc-logo { width: 36px; height: 36px; object-fit: contain; flex: 0 0 36px; }"
        ".spc-name { font-size: 1.02rem; font-weight: 900; color:#ffffff; line-height:1.1; "
        "  text-shadow:0 1px 3px rgba(0,0,0,.5); }"
        ".spc-name a { color: inherit; text-decoration: none; border-bottom: 1px dashed rgba(255,255,255,.3); }"
        ".spc-name a:hover { color:#00c896; border-bottom-color:#00c896; }"
        ".spc-meta { color:#7dd3fc; font-size:.78rem; font-weight:700; letter-spacing:.02em; }"
        ".spc-scores { display:flex; gap:14px; align-items:flex-end; margin-top:8px; }"
        ".spc-bigscore { display:flex; flex-direction:column; }"
        ".spc-bigscore .lab { color:#94a3b8; font-size:.62rem; font-weight:800; "
        "  text-transform:uppercase; letter-spacing:.08em; }"
        ".spc-bigscore .val { font-size: 1.55rem; font-weight: 900; color:#00c896; line-height:1; }"
        ".spc-pill { display:inline-block; padding: 3px 10px; border-radius: 999px; "
        "  font-weight: 800; font-size: .78rem; }"
        ".spc-pill.elite  { background:rgba(34,197,94,.18); color:#4ade80; border:1px solid rgba(34,197,94,.3); }"
        ".spc-pill.strong { background:rgba(0,200,150,.15); color:#34d399; border:1px solid rgba(0,200,150,.3); }"
        ".spc-pill.ok     { background:rgba(250,204,21,.12); color:#fde68a; border:1px solid rgba(250,204,21,.3); }"
        ".spc-pill.soft   { background:rgba(251,146,60,.12); color:#fb923c; border:1px solid rgba(251,146,60,.3); }"
        ".spc-pill.poor   { background:rgba(239,68,68,.12); color:#fca5a5; border:1px solid rgba(239,68,68,.3); }"
        ".spc-stats { display:grid; grid-template-columns: repeat(5, 1fr); gap:8px; margin-top:12px; }"
        ".spc-stat .lab { color:#94a3b8; font-size:.62rem; font-weight:800; "
        "  text-transform:uppercase; letter-spacing:.06em; }"
        ".spc-stat .val { color:#e2e8f0; font-size: .98rem; font-weight: 800; }"
        ".spc-empty { color:#64748b; font-size:.85rem; font-style:italic; }"
        "</style>"
    )

    def _tier_for(score):
        if score is None: return ("ok", "—")
        try: s = float(score)
        except: return ("ok", "—")
        if s >= 70: return ("elite",  "Elite")
        if s >= 60: return ("strong", "Strong")
        if s >= 50: return ("ok",     "Average")
        if s >= 40: return ("soft",   "Soft")
        return ("poor", "Poor")

    def _fmt(v, n=1, suffix=""):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        try:
            return f"{float(v):.{n}f}{suffix}"
        except Exception:
            return str(v)

    def _card(label, row):
        if not row:
            return (
                f'<div class="spc-card"><div class="spc-tag">{label}</div>'
                f'<div class="spc-empty" style="margin-top:8px;">No probable starter posted yet.</div></div>'
            )
        tier_cls, tier_label = _tier_for(row.get("Pitch Score"))
        name = row.get("Pitcher", "")
        name_html = name
        logo = row.get("_logo", "")
        logo_html = f'<img class="spc-logo" src="{logo}" alt=""/>' if logo else ""
        throws = format_pitcher_hand(row.get("Throws"), "?")
        team = row.get("Team", "")
        return (
            f'<div class="spc-card spc-tier-{tier_cls}">'
            f'<div class="spc-tag">{label}</div>'
            f'<div class="spc-head">{logo_html}'
            f'<div><div class="spc-name">{name_html} '
            f'<span style="color:#7dd3fc; font-weight:700; font-size:.82rem;">({throws})</span></div>'
            f'<div class="spc-meta">{team}</div></div></div>'
            f'<div class="spc-scores">'
            f'<div class="spc-bigscore"><span class="lab">Pitch Score</span>'
            f'<span class="val">{_fmt(row.get("Pitch Score"), 1)}</span></div>'
            f'<span class="spc-pill {tier_cls}">{tier_label}</span>'
            f'<div class="spc-bigscore"><span class="lab">Strikeout Score</span>'
            f'<span class="val">{_fmt(row.get("Strikeout Score"), 1)}</span></div>'
            f'</div>'
            f'<div class="spc-stats">'
            f'<div class="spc-stat"><div class="lab">xwOBA</div><div class="val">{_fmt(row.get("xwOBA"), 3)}</div></div>'
            f'<div class="spc-stat"><div class="lab">K%</div><div class="val">{_fmt(row.get("K%"), 1, "%")}</div></div>'
            f'<div class="spc-stat"><div class="lab">Whiff%</div><div class="val">{_fmt(row.get("Whiff%"), 1, "%")}</div></div>'
            f'<div class="spc-stat"><div class="lab">Barrel%</div><div class="val">{_fmt(row.get("Barrel%"), 1, "%")}</div></div>'
            f'<div class="spc-stat"><div class="lab">HH%</div><div class="val">{_fmt(row.get("HH%"), 1, "%")}</div></div>'
            f'</div>'
            f'</div>'
        )

    return css + (
        '<div class="spc-row">'
        + _card("Away SP", away_row)
        + _card("Home SP", home_row)
        + '</div>'
    )

# ============== HR Sleepers data layer ==============
#
# A "sleeper" = a batter whose underlying power-profile (Barrel%, HardHit%,
# ISO, FB%, Pull%, EV) and tonight's game context (matchup score, ceiling,
# park, weather, opposing pitcher) say HR-upside, but who flies under the
# radar (low season HR total, lower lineup spot, lower-priced).
#
# Sleeper Score (0-100):
#   Power signal     60%  (Barrel% 22% + HardHit% 14% + ISO 10% + FB% 7% + Pull% 7%)
#   Matchup signal   25%  (Matchup 10% + Ceiling 8% + kHR 7%)
#   Sleeper bonus    15%  (lower season HR total + lower lineup spot earn boost)
#
# All inputs are clipped/normalized to a 0-100 scale before weighting.
def _norm(val, lo, hi, default=50.0):
    """Linear-scale a metric to 0-100, clipped. Returns default on NaN."""
    try:
        v = float(val)
        if pd.isna(v):
            return default
    except Exception:
        return default
    if hi <= lo:
        return default
    return max(0.0, min(100.0, (v - lo) / (hi - lo) * 100.0))

def _sleeper_bonus(hr_total, lineup_spot):
    """Bigger boost for low-HR-total + lower lineup spot.
    HR total: 0 HR -> 100, 5 HR -> 80, 10 HR -> 55, 15 HR -> 30, 20+ HR -> 10.
    Lineup spot: 1 -> 0, 2 -> 10, 3 -> 25, 4 -> 45, 5 -> 60, 6 -> 75, 7-9 -> 90.
    """
    try: hr = float(hr_total)
    except: hr = 10.0
    if pd.isna(hr): hr = 10.0
    if hr <= 0:    hr_b = 100
    elif hr <= 5:  hr_b = 100 - (hr * 4)         # 5 HR -> 80
    elif hr <= 10: hr_b = 80 - (hr - 5) * 5      # 10 HR -> 55
    elif hr <= 15: hr_b = 55 - (hr - 10) * 5     # 15 HR -> 30
    elif hr <= 25: hr_b = 30 - (hr - 15) * 2     # 25 HR -> 10
    else:          hr_b = 10

    try: spot = int(lineup_spot)
    except: spot = 5
    spot_table = {1:0, 2:10, 3:25, 4:45, 5:60, 6:75, 7:90, 8:90, 9:90}
    spot_b = spot_table.get(spot, 50)

    return 0.55 * hr_b + 0.45 * spot_b

def park_weather_indicator(weather: dict, park_factor, home_abbr: str) -> dict:
    """Map ballpark + weather into a Good/OK/Bad indicator for HR scoring.

    Drives off compute_weather_impact()'s hr_pct (already used elsewhere on
    the Weather Impact card), so the verdict here matches the rest of the app.

    Thresholds (HR boost vs. league-average):
        hr_pct >=  6  -> Good   (🟢)  hot park + favorable weather
        -3 <= hr_pct <  6 -> OK   (🟡)  neutral / mixed signals
        hr_pct <  -3 -> Bad      (🔴)  pitcher-friendly park or HR-suppressing weather
    """
    try:
        imp = compute_weather_impact(weather or {}, park_factor, home_abbr or "")
    except Exception:
        return {"label": "OK", "tier": "ok", "hr_pct": 0,
                "icon": "🟡", "tooltip": "Park/weather signal unavailable"}
    hr_pct = int(imp.get("hr_pct", 0) or 0)
    if hr_pct >= 6:
        tier, label, icon = "good", "Good", "🟢"
    elif hr_pct < -3:
        tier, label, icon = "bad", "Bad", "🔴"
    else:
        tier, label, icon = "ok", "OK", "🟡"
    sky = imp.get("sky") or ""
    wind_label = imp.get("wind_label") or ""
    temp = imp.get("temp")
    wind = imp.get("wind")
    bits = []
    if temp is not None:
        try: bits.append(f"{int(round(float(temp)))}°F")
        except Exception: pass
    if wind:
        try:
            w = int(round(float(wind)))
            if wind_label and wind_label not in ("unknown", "roof closed"):
                bits.append(f"wind {w} mph {wind_label}")
            elif w > 0:
                bits.append(f"wind {w} mph")
        except Exception: pass
    if sky:
        bits.append(sky)
    tip = f"Park/weather HR {hr_pct:+d}%"
    if bits:
        tip += " · " + " · ".join(bits)
    return {"label": label, "tier": tier, "hr_pct": hr_pct,
            "icon": icon, "tooltip": tip}

def build_hr_sleepers_table(_schedule_df, _batters_df, _pitchers_df,
                             slate_date_iso: str = ""):
    """Score every posted-lineup batter for HR sleeper potential and return a
    sorted DataFrame ready for rendering.

    slate_date_iso (YYYY-MM-DD) is the selected slate date — used to anchor
    the L5/L10/30D recent-form snapshot per batter so the score reflects
    today's opponent / probable pitcher / park / weather AND each batter's
    latest form (via _form_blend_adjustment). Falls back gracefully when
    recent form is unavailable."""
    rows = []
    for _, g in _schedule_df.iterrows():
        # Live overlay so a slate-wide HR/RBI scoring pass sees the
        # current pitcher once a game is in progress, not the pregame
        # probable that's already been pulled.
        g = apply_live_pitcher_overlay(g)
        try:
            cc = build_game_context(g)
        except Exception:
            continue
        bpw = park_weather_indicator(
            cc.get("weather", {}), g.get("park_factor"), g.get("home_abbr", "")
        )
        for side, lineup_df, opp_pitcher, opp_pid in (
            ("away", cc["away_lineup"], g["home_probable"], g.get("home_probable_id")),
            ("home", cc["home_lineup"], g["away_probable"], g.get("away_probable_id")),
        ):
            if lineup_df is None or lineup_df.empty:
                continue
            # ID-first pitcher match (mirrors PR #21) so the same-name
            # pitcher in the season CSV doesn't shadow tonight's actual
            # probable pitcher.
            p_row = find_pitcher_row(_pitchers_df, opp_pitcher, pitcher_id=opp_pid)
            for _, r in lineup_df.iterrows():
                b = find_player_row(_batters_df, r["name_key"], r["team"],
                                    player_id=r.get("player_id"))
                if b is None:
                    continue
                opp_hand = r.get("opposing_pitch_hand", "")
                # Reuse the existing scoring stack so sleeper rankings stay
                # consistent with the per-game model.
                m_score = matchup_score(b, p_row, r["lineup_spot"], cc["weather"],
                                         g["park_factor"], r["bat_side"], opp_hand)
                # Recent-form blend: nudge matchup score by L10 AVG vs league.
                # Uses the slate date so the snapshot is locked to the
                # selected slate and never leaks across days.
                form = None
                if slate_date_iso and r.get("player_id") is not None:
                    try:
                        form = get_batter_recent_form(int(r["player_id"]), slate_date_iso)
                    except Exception:
                        form = None
                if form:
                    m_score = float(m_score) + _form_blend_adjustment(form.get("L10") or {})
                c_score = ceiling_score(b, cc["weather"], g["park_factor"])
                khr     = k_adj_hr(b, p_row, c_score)

                hr_total = safe_float(b.get("HR"), 0)
                pa       = safe_float(b.get("pa"), 0) or safe_float(b.get("PA"), 0)
                barrel   = safe_float(b.get("Barrel%"),  np.nan)
                hh       = safe_float(b.get("HardHit%"), np.nan)
                iso      = safe_float(b.get("ISO"),      np.nan)
                fb       = safe_float(b.get("FB%"),      np.nan)
                pull     = safe_float(b.get("Pull%"),    np.nan)
                ev       = safe_float(b.get("EV"),       np.nan)
                xiso     = safe_float(b.get("xISO"),     np.nan)
                # OPS is included on every sleeper row so downstream views
                # (HR Sleepers card, AI HR Parlay reasons, Round Robin stats
                # line) can read it directly. get_ops_value falls back to
                # OBP+SLG when the precomputed OPS is missing.
                ops      = get_ops_value(b)
                # Pull the canonical HR-focused metric set so the Sleepers
                # display, the slate leaderboards, and the Matchup heat-map
                # all show identical values for the same hitter.
                _hr_metrics = compute_hr_metrics(b, _hr_league_table(_batters_df))

                # Normalize each input to 0-100
                n_barrel = _norm(barrel, 4.0, 18.0)
                n_hh     = _norm(hh,     30.0, 55.0)
                n_iso    = _norm(iso,    0.100, 0.280)
                n_fb     = _norm(fb,     20.0, 45.0)
                n_pull   = _norm(pull,   30.0, 50.0)
                n_match  = _norm(m_score, 80.0, 140.0)
                n_ceil   = _norm(c_score, 80.0, 140.0)
                n_khr    = _norm(khr,     0.4,  1.6)
                # 0-100 OPS index anchored on .620 (replacement) → .950 (MVP),
                # mirroring the band used by build_targets_table so OPS reads
                # the same across HR Sleepers, TB, RBI2, and HRR scoring.
                n_ops    = _norm(ops if ops is not None else np.nan, 0.620, 0.950)
                bonus    = _sleeper_bonus(hr_total, r.get("lineup_spot", 9))

                # 5% OPS bonus: small enough to keep raw HR-power signals
                # (Barrel%, ISO, FB%) in the driver's seat, but large enough
                # that an .850+ OPS reliably nudges qualified sleepers up the
                # board over identical-power bats with weaker plate skills.
                power_part   = (0.22 * n_barrel + 0.14 * n_hh + 0.10 * n_iso
                                + 0.07 * n_fb + 0.07 * n_pull)
                matchup_part = 0.10 * n_match + 0.08 * n_ceil + 0.07 * n_khr
                bonus_part   = 0.15 * bonus
                ops_part     = 0.05 * n_ops
                sleeper      = round(power_part + matchup_part + bonus_part + ops_part, 1)

                rows.append({
                    "_player_id": r.get("player_id"),
                    "Hitter":   r["player_name"],
                    "Team":     norm_team(r["team"]),
                    "Bat":      r["bat_side"] or "",
                    "Spot":     int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
                    "Game":     g["short_label"],
                    "Opp SP":   opp_pitcher,
                    "Sleeper Score": sleeper,
                    "HR (Season)":   int(hr_total) if not pd.isna(hr_total) else 0,
                    "Barrel%":  barrel if not pd.isna(barrel) else None,
                    "HardHit%": hh     if not pd.isna(hh)     else None,
                    "ISO":      iso    if not pd.isna(iso)    else None,
                    "xISO":     xiso   if not pd.isna(xiso)   else None,
                    "FB%":      fb     if not pd.isna(fb)     else None,
                    "Pull%":    pull   if not pd.isna(pull)   else None,
                    "EV":       ev     if not pd.isna(ev)     else None,
                    "OPS":      ops    if ops is not None     else None,
                    "Matchup":  m_score,
                    "Ceiling":  c_score,
                    "kHR":      khr,
                    "PA":       pa,
                    "BPW Label":  bpw["label"],
                    "BPW Tier":   bpw["tier"],
                    "BPW HR%":    bpw["hr_pct"],
                    "BPW Icon":   bpw["icon"],
                    "BPW Tip":    bpw["tooltip"],
                    # Canonical HR-focused metrics (same keys / values as the
                    # Matchup heat-map board and the slate leaderboards).
                    "Brl/BIP%":   _hr_metrics.get("Brl/BIP%"),
                    "GB%":        _hr_metrics.get("GB%"),
                    "HH%":        _hr_metrics.get("HH%"),
                    "HR/FB%":     _hr_metrics.get("HR/FB%"),
                    "PullAir%":   _hr_metrics.get("PullAir%"),
                    "LA":         _hr_metrics.get("LA"),
                    "xwOBA":      _hr_metrics.get("xwOBA"),
                    "SweetSpot%": _hr_metrics.get("SweetSpot%"),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("Sleeper Score", ascending=False).reset_index(drop=True)
    return df

# ============== Shared mobile-card system ==============
#
# Every Apps & Generators category that historically rendered a wide table
# (HR Sleepers, TB 1.5+, HRR 1.5+, RBI Edge, Hot/Cold Batters, HR Milestones,
# Day vs Night HR, Ballpark Weather, daily HR finder) also emits a parallel
# mobile-card grid using the helpers below. CSS hides one or the other based
# on viewport: tables on desktop (≥640px), cards on phones (<640px). This
# gives us the Slate-Pitchers-style look everywhere without horizontal
# scrolling, while preserving the existing desktop tables verbatim.
#
# Visual language matches `render_slate_pitcher_dashboard` (purple/gold
# brand + dark gradient cards + colored stat chips).

MOBILE_CARDS_CSS = (
    "<style>"
    # Wrapper that switches between desktop table and mobile card grid.
    ".mc-desktop { display:block; }"
    ".mc-mobile  { display:none; }"
    "@media (max-width: 640px) {"
    "  .mc-desktop { display:none !important; }"
    "  .mc-mobile  { display:block !important; }"
    "}"
    # Modern-minimal mode: render the dark player-card grid on every
    # viewport, replacing the wide white desktop table entirely. Used by
    # the Apps & Generators leaderboards (Hot / Cold / HR Milestones /
    # Day vs Night HR) where the desktop dataframe was redundant clutter.
    ".mc-always { display:block !important; }"
    ".mc-always .mc-grid { grid-template-columns: 1fr; }"
    "@media (min-width: 720px) {"
    "  .mc-always .mc-grid { grid-template-columns: repeat(2, 1fr); }"
    "}"
    "@media (min-width: 1080px) {"
    "  .mc-always .mc-grid { grid-template-columns: repeat(3, 1fr); }"
    "}"
    ".mc-grid { display:grid; grid-template-columns: 1fr; gap: 12px; "
    "  margin: 6px 0 12px 0; }"
    "@media (min-width: 480px) and (max-width: 640px) {"
    "  .mc-grid { grid-template-columns: repeat(2, 1fr); }"
    "}"
    ".mc-card { background: linear-gradient(180deg, #15102b 0%, #0b0820 100%); "
    "  border:1px solid #2a1e4a; border-radius:14px; padding:12px 13px; "
    "  color:#e9e6f5; box-shadow: 0 4px 12px rgba(0,0,0,.30); "
    "  display:flex; flex-direction:column; gap:8px; min-width:0; }"
    ".mc-head { display:flex; align-items:flex-start; gap:8px; min-width:0; }"
    ".mc-rank { font-variant-numeric: tabular-nums; font-weight:900; "
    "  font-size:.78rem; color:#fcd34d; background:#3b1f6b; "
    "  border:1px solid #5b3aa0; padding:2px 7px; border-radius:8px; "
    "  flex:0 0 auto; line-height:1.3; }"
    ".mc-id { display:flex; flex-direction:column; min-width:0; flex:1 1 auto; }"
    ".mc-name { font-weight:800; font-size:1.0rem; line-height:1.15; "
    "  color:#f8fafc; word-break:break-word; }"
    ".mc-sub { font-size:.74rem; color:#a3a0c4; margin-top:2px; "
    "  word-break:break-word; }"
    ".mc-score { font-variant-numeric: tabular-nums; font-weight:900; "
    "  font-size:1.05rem; color:#fcd34d; text-align:right; flex:0 0 auto; "
    "  padding-left:6px; line-height:1.05; }"
    ".mc-score small { display:block; font-size:.6rem; color:#a3a0c4; "
    "  font-weight:700; letter-spacing:.06em; text-transform:uppercase; "
    "  margin-top:2px; }"
    ".mc-tiers { display:flex; flex-wrap:wrap; gap:5px; }"
    ".mc-tier { display:inline-block; padding: 2px 8px; border-radius:999px; "
    "  font-size:.66rem; font-weight:800; letter-spacing:.03em; "
    "  border:1px solid transparent; text-transform:uppercase; }"
    ".mc-tier.elite  { background: rgba(16,185,129,.18); color:#86efac; "
    "  border-color: rgba(110,231,183,.40); }"
    ".mc-tier.strong { background: rgba(132,204,22,.16); color:#bef264; "
    "  border-color: rgba(190,242,100,.40); }"
    ".mc-tier.ok     { background: rgba(250,204,21,.14); color:#fde68a; "
    "  border-color: rgba(253,224,71,.40); }"
    ".mc-tier.soft   { background: rgba(249,115,22,.16); color:#fdba74; "
    "  border-color: rgba(253,186,116,.40); }"
    ".mc-tier.poor   { background: rgba(239,68,68,.18); color:#fecaca; "
    "  border-color: rgba(252,165,165,.40); }"
    ".mc-tier.gold   { background: rgba(252,211,77,.16); color:#fde68a; "
    "  border-color: rgba(253,224,71,.45); }"
    ".mc-tier.info   { background: rgba(139,92,246,.20); color:#ddd6fe; "
    "  border-color: rgba(196,181,253,.40); }"
    ".mc-tier.warn   { background: rgba(244,114,182,.14); color:#fda4af; "
    "  border-color: rgba(253,164,175,.35); }"
    ".mc-grid2 { display:grid; grid-template-columns: 1fr 1fr; gap: 6px; }"
    ".mc-grid3 { display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; }"
    ".mc-chip { background:#1c1340; border:1px solid #2a1e4a; "
    "  border-radius:9px; padding:6px 8px; min-width:0; }"
    ".mc-chip.good { border-color: rgba(110,231,183,.35); "
    "  background: linear-gradient(180deg, rgba(16,185,129,.10), #1c1340); }"
    ".mc-chip.bad  { border-color: rgba(252,165,165,.35); "
    "  background: linear-gradient(180deg, rgba(239,68,68,.10), #1c1340); }"
    ".mc-chip.mid  { border-color: rgba(253,224,71,.35); "
    "  background: linear-gradient(180deg, rgba(250,204,21,.10), #1c1340); }"
    ".mc-chip-label { font-size:.62rem; color:#a3a0c4; "
    "  font-weight:700; letter-spacing:.04em; text-transform:uppercase; "
    "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
    ".mc-chip-val { font-size:.95rem; font-weight:800; color:#f8fafc; "
    "  font-variant-numeric: tabular-nums; margin-top:1px; "
    "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
    ".mc-chip-val.na { color:#6b7290; }"
    ".mc-foot { font-size:.72rem; color:#a3a0c4; display:flex; "
    "  flex-wrap:wrap; gap:6px 10px; }"
    ".mc-foot b { color:#e9e6f5; }"
    ".mc-empty { padding:14px 16px; color:#a3a0c4; background:#15102b; "
    "  border:1px dashed #2a1e4a; border-radius:14px; text-align:center; }"
    # Auto-hide ANY wide dataframe / heatmap-table-wrap on phones if a sibling
    # .mc-mobile exists. We do NOT touch dataframes that have no mobile twin.
    "@media (max-width: 640px) {"
    "  div[data-testid='stHorizontalBlock'] { flex-wrap: wrap !important; }"
    "  div[data-testid='stHorizontalBlock'] > div { "
    "    min-width: 0 !important; width: 100% !important; "
    "    flex: 1 1 100% !important; }"
    "}"
    "</style>"
)


def _mc_tier_from_score(score, thresholds=(75, 65, 55)):
    """Map a 0-100 score to (css-class, label). Higher = better."""
    if score is None:
        return ("ok", "—")
    try:
        s = float(score)
    except Exception:
        return ("ok", "—")
    e, st_, ok = thresholds
    if s >= e:  return ("elite",  "Elite")
    if s >= st_: return ("strong", "Strong")
    if s >= ok: return ("ok",     "Average")
    return ("soft", "Soft")


def _mc_chip_tone(v, lo, hi, reverse=False):
    """Return ('good'|'mid'|'bad') from a value relative to a metric's
    expected range. `reverse=True` for stats where lower is better."""
    if v is None:
        return "mid"
    try:
        x = float(v)
    except Exception:
        return "mid"
    if hi == lo:
        return "mid"
    pct = (x - lo) / (hi - lo)
    if reverse:
        pct = 1.0 - pct
    if pct >= 0.66: return "good"
    if pct <= 0.33: return "bad"
    return "mid"


def _mc_fmt(v, fmt):
    if v is None:
        return None
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
    except Exception:
        pass
    try:
        return fmt.format(float(v))
    except Exception:
        s = str(v).strip()
        return s if s else None


def _mc_chip(label, val, tone="mid"):
    """Build a single stat chip. val=None renders an em-dash."""
    if val is None:
        return (
            '<div class="mc-chip">'
            f'<div class="mc-chip-label">{label}</div>'
            f'<div class="mc-chip-val na">—</div></div>'
        )
    return (
        f'<div class="mc-chip {tone}">'
        f'<div class="mc-chip-label">{label}</div>'
        f'<div class="mc-chip-val">{val}</div></div>'
    )


def _mc_card(*, rank=None, name="", sub="", score=None, score_label="Score",
             tiers=None, chips_html="", foot_html=""):
    """Assemble one card. `tiers` is a list of (label, css-class)."""
    rank_html = (
        f'<span class="mc-rank">#{rank}</span>' if rank is not None else ""
    )
    score_html = ""
    if score is not None:
        try:
            score_html = (
                f'<div class="mc-score">{float(score):.1f}'
                f'<small>{score_label}</small></div>'
            )
        except Exception:
            score_html = ""
    tier_html = ""
    if tiers:
        tier_html = (
            '<div class="mc-tiers">' +
            "".join(f'<span class="mc-tier {cls}">{lab}</span>'
                    for lab, cls in tiers) +
            '</div>'
        )
    return (
        '<div class="mc-card">'
        '<div class="mc-head">'
        f'{rank_html}'
        f'<div class="mc-id"><div class="mc-name">{name}</div>'
        f'<div class="mc-sub">{sub}</div></div>'
        f'{score_html}'
        '</div>'
        f'{tier_html}'
        f'{chips_html}'
        f'{foot_html}'
        '</div>'
    )


def render_hr_sleepers_html(df):
    """Render the HR Sleepers table with tiered borders and badge tier pills.
    Uses the same gold-on-dark-green visual language as the rest of the app.
    """
    if df is None or df.empty:
        return '<div style="padding:14px;color:#64748b;">No sleeper candidates yet — lineups may not be posted.</div>'

    css = (
        "<style>"
        ".hrs-wrap { margin: 6px 0 14px 0; }"
        ".hrs-table { width:100%; border-collapse: separate; border-spacing: 0; "
        "  font-size:.92rem; background:#0b1220; border-radius:12px; overflow:hidden; "
        "  box-shadow: 0 2px 10px rgba(0,0,0,.4); border:1px solid rgba(255,255,255,.07); }"
        ".hrs-table th { background:#3b1f6b; color:#fcd34d; text-align:left; "
        "  font-weight:800; padding:9px 10px; letter-spacing:.03em; font-size:.78rem; "
        "  text-transform:uppercase; }"
        ".hrs-table td { padding:8px 10px; border-bottom:1px solid rgba(255,255,255,.06); "
        "  color:#e2e8f0; }"
        ".hrs-table tr:nth-child(even) td { background:rgba(255,255,255,.03); }"
        ".hrs-pill { display:inline-block; padding:3px 9px; border-radius:999px; "
        "  font-weight:800; font-size:.74rem; letter-spacing:.04em; }"
        ".hrs-pill.elite  { background:rgba(34,197,94,.18); color:#4ade80; border:1px solid rgba(34,197,94,.3); }"
        ".hrs-pill.strong { background:rgba(0,200,150,.15); color:#34d399; border:1px solid rgba(0,200,150,.3); }"
        ".hrs-pill.ok     { background:rgba(250,204,21,.12); color:#fde68a; border:1px solid rgba(250,204,21,.3); }"
        ".hrs-pill.soft   { background:rgba(251,146,60,.12); color:#fb923c; border:1px solid rgba(251,146,60,.3); }"
        ".hrs-score { font-weight:900; font-size:1.05rem; color:#00c896; }"
        ".hrs-name { font-weight:800; color:#ffffff; text-shadow:0 1px 2px rgba(0,0,0,.4); }"
        ".hrs-meta { color:#7dd3fc; font-size:.78rem; }"
        ".hrs-num { font-variant-numeric: tabular-nums; }"
        "</style>"
    )

    def _tier(score):
        if score is None: return ("ok", "—")
        try: s = float(score)
        except: return ("ok", "—")
        if s >= 75: return ("elite",  "Elite")
        if s >= 65: return ("strong", "Strong")
        if s >= 55: return ("ok",     "Average")
        return ("soft", "Soft")

    def _fmt_pct(v, n=1):
        if v is None or (isinstance(v, float) and pd.isna(v)): return "—"
        try: return f"{float(v):.{n}f}%"
        except: return "—"

    def _fmt_num(v, n=3):
        if v is None or (isinstance(v, float) and pd.isna(v)): return "—"
        try: return f"{float(v):.{n}f}"
        except: return "—"

    def _fmt_deg(v):
        if v is None or (isinstance(v, float) and pd.isna(v)): return "—"
        try: return f"{float(v):.1f}°"
        except: return "—"

    rows_html = []
    for i, r in df.iterrows():
        tier_cls, tier_label = _tier(r.get("Sleeper Score"))
        rows_html.append(
            "<tr>"
            f'<td class="hrs-num">{i+1}</td>'
            f'<td><div class="hrs-name">{r.get("Hitter","")}</div>'
            f'<div class="hrs-meta">{r.get("Team","")} · Bat {format_batter_stance(r.get("Bat",""))} · Spot {r.get("Spot","")}</div></td>'
            f'<td class="hrs-meta">{r.get("Game","")}<br/><span style="color:#475569;">vs {r.get("Opp SP","")}</span></td>'
            f'<td><span class="hrs-score">{r.get("Sleeper Score",0):.1f}</span> '
            f'<span class="hrs-pill {tier_cls}" style="margin-left:6px;">{tier_label}</span></td>'
            f'<td class="hrs-num">{_fmt_num(r.get("Matchup"), 1)}</td>'
            f'<td class="hrs-num">{fmt_ops(r.get("OPS"))}</td>'
            f'<td class="hrs-num">{_fmt_num(r.get("ISO"), 3)}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("Brl/BIP%"))}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("FB%"))}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("GB%"))}</td>'
            f'<td class="hrs-num">{_fmt_num(r.get("EV"), 1)}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("HH%"))}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("HR/FB%"))}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("PullAir%"))}</td>'
            f'<td class="hrs-num">{_fmt_deg(r.get("LA"))}</td>'
            f'<td class="hrs-num">{_fmt_num(r.get("xwOBA"), 3)}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("SweetSpot%"))}</td>'
            "</tr>"
        )

    # ---- Mobile card grid (same data, vertical card layout) ----
    mobile_cards = []
    for i, r in df.iterrows():
        tier_cls, tier_label = _tier(r.get("Sleeper Score"))
        chip_parts = [
            _mc_chip("Matchup", _mc_fmt(r.get("Matchup"), "{:.1f}"),
                     _mc_chip_tone(r.get("Matchup"), 80, 140)),
            # OPS leads the chip stack on mobile so the universal bat-quality
            # signal is immediately visible (same band as the heat-map /
            # target-table chips: .650 → .900).
            _mc_chip("OPS", _mc_fmt(r.get("OPS"), "{:.3f}"),
                     _mc_chip_tone(r.get("OPS"), 0.650, 0.900)),
            _mc_chip("ISO", _mc_fmt(r.get("ISO"), "{:.3f}"),
                     _mc_chip_tone(r.get("ISO"), 0.130, 0.260)),
            _mc_chip("Barrel%", _mc_fmt(r.get("Brl/BIP%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("Brl/BIP%"), 5.0, 14.0)),
            _mc_chip("HardHit%", _mc_fmt(r.get("HH%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("HH%"), 32.0, 50.0)),
            _mc_chip("FB%", _mc_fmt(r.get("FB%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("FB%"), 28.0, 45.0)),
            _mc_chip("HR/FB%", _mc_fmt(r.get("HR/FB%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("HR/FB%"), 6.0, 18.0)),
            _mc_chip("Pull Air%", _mc_fmt(r.get("PullAir%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("PullAir%"), 8.0, 22.0)),
            _mc_chip("xwOBA", _mc_fmt(r.get("xwOBA"), "{:.3f}"),
                     _mc_chip_tone(r.get("xwOBA"), 0.300, 0.400)),
            _mc_chip("Exit Velo", _mc_fmt(r.get("EV"), "{:.1f}"),
                     _mc_chip_tone(r.get("EV"), 86.0, 94.0)),
        ]
        opp_sp = r.get("Opp SP", "") or "—"
        foot = (
            f'<div class="mc-foot"><b>{r.get("Game","")}</b>'
            f' · <span>vs {opp_sp}</span>'
            f' · Spot {r.get("Spot","")}</div>'
        )
        mobile_cards.append(_mc_card(
            rank=i + 1,
            name=r.get("Hitter", ""),
            sub=f'{r.get("Team","")} · Bats {format_batter_stance(r.get("Bat",""))}',
            score=r.get("Sleeper Score"),
            score_label="Sleeper",
            tiers=[(tier_label, tier_cls)],
            chips_html='<div class="mc-grid2">' + "".join(chip_parts) + "</div>",
            foot_html=foot,
        ))
    mobile_html = (
        '<div class="mc-mobile"><div class="mc-grid">'
        + "".join(mobile_cards) +
        '</div></div>'
    )

    return MOBILE_CARDS_CSS + css + (
        '<div class="mc-desktop">'
        '<div class="hrs-wrap"><table class="hrs-table">'
        '<thead><tr>'
        '<th>#</th><th>Hitter</th><th>Game</th><th>Sleeper</th>'
        '<th>Matchup</th><th>OPS</th><th>ISO</th><th>Barrel%</th><th>Flyball%</th>'
        '<th>GB%</th><th>Exit Velo</th><th>Hard Hit%</th><th>HR/FB%</th>'
        '<th>Pull Air%</th><th>Launch Angle</th><th>xwOBA</th><th>Sweet Spot%</th>'
        '</tr></thead><tbody>'
        + "".join(rows_html) +
        '</tbody></table></div>'
        '</div>'
        + mobile_html
    )

# ============== Total Bases & HRR Targets data layer ==============
#
# Over 1.5 Total Bases (TB) angle: batters likely to either grab an XBH or
# multiple singles. Heavier weight on contact quality + power, lighter on
# raw HR profile.
#   TB Score (0-100) =
#     Contact 35%   (xBA 12 + AVG 10 + K%-inverse 8 + LD% 5)
#     Power   30%   (xSLG 12 + ISO 10 + Barrel% 8)
#     PA opp. 15%   (Spot 1-5 favored)
#     Matchup 20%   (Matchup 12 + Ceiling 8)
#
# Over 1.5 HRR (Hits + Runs + RBI) angle: combines on-base + scoring/RBI
# opportunity. Heavier on getting-on + lineup-spot context.
#   HRR Score (0-100) =
#     OnBase  35%   (xBA 12 + xOBP 13 + AVG 10)
#     Power   20%   (xSLG 10 + ISO 6 + Barrel% 4)
#     Spot    25%   (Spots 1-5 strongly favored — they bat more & drive runs)
#     Matchup 20%   (Matchup 12 + Ceiling 8)
#
# Both reuse build_game_context, find_player_row, find_pitcher_row,
# matchup_score and ceiling_score from the existing scoring stack.

def _spot_pa_weight(lineup_spot):
    """Score for # of PAs a spot is likely to see (TB angle).
    Spot 1 -> 100, 2 -> 95, 3 -> 90, 4 -> 80, 5 -> 65, 6 -> 50,
    7 -> 35, 8 -> 25, 9 -> 15."""
    table = {1:100, 2:95, 3:90, 4:80, 5:65, 6:50, 7:35, 8:25, 9:15}
    try: return table.get(int(lineup_spot), 30)
    except: return 30

def _spot_hrr_weight(lineup_spot):
    """Score for HRR opportunity (Hits+Runs+RBI). Top-of-order gets runs,
    middle gets RBI, both matter. Spots 2-5 get the highest combined score.
    1 -> 90, 2 -> 100, 3 -> 100, 4 -> 95, 5 -> 85, 6 -> 65,
    7 -> 45, 8 -> 30, 9 -> 20."""
    table = {1:90, 2:100, 3:100, 4:95, 5:85, 6:65, 7:45, 8:30, 9:20}
    try: return table.get(int(lineup_spot), 40)
    except: return 40

def _spot_rbi_weight(lineup_spot):
    """Score for 2+ RBI opportunity. Heart-of-order spots crush this — they
    bat with runners on base most often. Spots 3-5 are king, 2/6 are decent,
    1 is weak (leads off the inning), bottom of order is dead.
    1 -> 50, 2 -> 80, 3 -> 100, 4 -> 100, 5 -> 95, 6 -> 70,
    7 -> 45, 8 -> 25, 9 -> 15."""
    table = {1:50, 2:80, 3:100, 4:100, 5:95, 6:70, 7:45, 8:25, 9:15}
    try: return table.get(int(lineup_spot), 30)
    except: return 30

def _summarize_weather_short(weather: dict) -> str:
    """Compact one-cell weather string for prop tables.
    e.g. '78°F · W 12 mph out · 10% rain' or 'Dome' when applicable."""
    if not weather:
        return "—"
    temp = weather.get("temp_f")
    wind = weather.get("wind_mph")
    wind_dir = weather.get("wind_dir_deg")
    rain = weather.get("rain_pct")
    bits = []
    if temp is not None:
        try:
            bits.append(f"{int(round(float(temp)))}°F")
        except Exception:
            pass
    if wind is not None and float(wind) >= 1:
        try:
            wmph = int(round(float(wind)))
            wdir_label = ""
            if wind_dir is not None:
                d = float(wind_dir)
                # Compass to 8-pt
                dirs = ["N","NE","E","SE","S","SW","W","NW"]
                wdir_label = dirs[int(((d + 22.5) % 360) // 45)]
            bits.append(f"{wdir_label} {wmph} mph".strip())
        except Exception:
            pass
    if rain is not None:
        try:
            rp = int(round(float(rain)))
            if rp >= 20:
                bits.append(f"{rp}% rain")
        except Exception:
            pass
    if not bits:
        return "—"
    return " · ".join(bits)


def build_targets_table(_schedule_df, _batters_df, _pitchers_df, mode="tb",
                         slate_date_iso: str = ""):
    """Build a target-prop sleeper table for either:
       mode="tb"   -> Over 1.5 Total Bases
       mode="hrr"  -> Over 1.5 Hits+Runs+RBI
       mode="rbi2" -> 2+ RBI
    Returns a sorted DataFrame ready for rendering.

    slate_date_iso (YYYY-MM-DD) is the selected slate date. When provided
    and a per-batter player_id is present, the matchup score is nudged by
    each batter's L10 AVG vs league (same _form_blend_adjustment used in
    Matchup Data). The snapshot is keyed by (player_id, slate date) so it
    never leaks across days or opponents."""
    rows = []
    for _, g in _schedule_df.iterrows():
        g = apply_live_pitcher_overlay(g)
        try:
            cc = build_game_context(g)
        except Exception:
            continue
        for side, lineup_df, opp_pitcher, opp_pid in (
            ("away", cc["away_lineup"], g["home_probable"], g.get("home_probable_id")),
            ("home", cc["home_lineup"], g["away_probable"], g.get("away_probable_id")),
        ):
            if lineup_df is None or lineup_df.empty:
                continue
            # ID-first pitcher match: keeps the score tied to tonight's
            # actual probable pitcher even when names collide.
            p_row = find_pitcher_row(_pitchers_df, opp_pitcher, pitcher_id=opp_pid)
            for _, r in lineup_df.iterrows():
                b = find_player_row(_batters_df, r["name_key"], r["team"],
                                    player_id=r.get("player_id"))
                if b is None:
                    continue
                opp_hand = r.get("opposing_pitch_hand", "")
                m_score = matchup_score(b, p_row, r["lineup_spot"], cc["weather"],
                                         g["park_factor"], r["bat_side"], opp_hand)
                # Recent-form blend, same anchor as Matchup Data / HR Sleepers.
                form = None
                if slate_date_iso and r.get("player_id") is not None:
                    try:
                        form = get_batter_recent_form(int(r["player_id"]), slate_date_iso)
                    except Exception:
                        form = None
                if form:
                    m_score = float(m_score) + _form_blend_adjustment(form.get("L10") or {})
                c_score = ceiling_score(b, cc["weather"], g["park_factor"])

                # Pull all source metrics with NaN-safe defaults
                avg     = safe_float(b.get("AVG"),      np.nan)
                xba     = safe_float(b.get("xBA"),      np.nan)
                xobp    = safe_float(b.get("xOBP"),     np.nan)
                xslg    = safe_float(b.get("xSLG"),     np.nan)
                iso     = safe_float(b.get("ISO"),      np.nan)
                barrel  = safe_float(b.get("Barrel%"),  np.nan)
                hh      = safe_float(b.get("HardHit%"), np.nan)
                k_pct   = safe_float(b.get("K%"),       np.nan)
                ld      = safe_float(b.get("LD%"),      np.nan)
                pa      = safe_float(b.get("pa"), 0) or safe_float(b.get("PA"), 0)
                # OPS is the canonical bat-quality scalar surfaced on cards,
                # heat maps, and the target-prop table. Falls back to OBP+SLG
                # when the source row only has the components.
                ops     = safe_float(b.get("OPS"),      np.nan)
                if pd.isna(ops):
                    _obp_v = safe_float(b.get("OBP"), np.nan)
                    _slg_v = safe_float(b.get("SLG"), np.nan)
                    if not pd.isna(_obp_v) and not pd.isna(_slg_v):
                        ops = _obp_v + _slg_v

                # Normalize to 0-100
                n_avg    = _norm(avg,   0.200, 0.320)
                n_xba    = _norm(xba,   0.220, 0.310)
                n_xobp   = _norm(xobp,  0.290, 0.390)
                n_xslg   = _norm(xslg,  0.350, 0.560)
                n_iso    = _norm(iso,   0.100, 0.280)
                n_barrel = _norm(barrel, 4.0, 18.0)
                n_hh     = _norm(hh,    30.0, 55.0)
                n_kinv   = 100.0 - _norm(k_pct, 12.0, 32.0)   # lower K% better
                n_ld     = _norm(ld,    18.0, 28.0)
                n_match  = _norm(m_score, 80.0, 140.0)
                n_ceil   = _norm(c_score, 80.0, 140.0)
                # 0-100 OPS index anchored on .620 (replacement) → .950 (MVP).
                # Used as a small but universal nudge across every prop mode
                # so a strong bat consistently gets surfaced.
                n_ops    = _norm(ops, 0.620, 0.950)

                # Universal OPS bonus: a 5% weight on the normalized OPS so
                # any batter view (TB, 2+ RBI, HRR) reflects overall bat
                # quality without overpowering the mode-specific weights.
                ops_bonus = 0.05 * n_ops
                if mode == "tb":
                    contact_part = 0.12*n_xba + 0.10*n_avg + 0.08*n_kinv + 0.05*n_ld
                    power_part   = 0.12*n_xslg + 0.10*n_iso + 0.08*n_barrel
                    spot_part    = 0.15 * _spot_pa_weight(r.get("lineup_spot", 9))
                    matchup_part = 0.12*n_match + 0.08*n_ceil
                    score = contact_part + power_part + spot_part + matchup_part + ops_bonus
                elif mode == "rbi2":
                    # 2+ RBI: heart-of-order power bats vs vulnerable SP.
                    # Power 40% (xSLG, ISO, Barrel%, HardHit%) ·
                    # Spot 30% (3-5 dominate) ·
                    # Matchup 20% (opp SP / park / weather / ceiling) ·
                    # Contact 10% (xBA — need to actually put the ball in play).
                    power_part   = 0.14*n_xslg + 0.12*n_iso + 0.08*n_barrel + 0.06*n_hh
                    spot_part    = 0.30 * _spot_rbi_weight(r.get("lineup_spot", 9))
                    matchup_part = 0.12*n_match + 0.08*n_ceil
                    contact_part = 0.10*n_xba
                    score = power_part + spot_part + matchup_part + contact_part + ops_bonus
                else:  # hrr
                    onbase_part  = 0.12*n_xba + 0.13*n_xobp + 0.10*n_avg
                    power_part   = 0.10*n_xslg + 0.06*n_iso + 0.04*n_barrel
                    spot_part    = 0.25 * _spot_hrr_weight(r.get("lineup_spot", 9))
                    matchup_part = 0.12*n_match + 0.08*n_ceil
                    score = onbase_part + power_part + spot_part + matchup_part + ops_bonus

                # Lineup status: "Confirmed" once MLB posts the official
                # batting order, "Projected" when we infer from recent games,
                # "Not Posted" if we have nothing.
                lineup_status = (cc["away_status"] if side == "away"
                                 else cc["home_status"]) or "Not Posted"
                # Opposing pitcher handedness — drives platoon edge in props.
                opp_p_hand = (cc["home_pitch_hand"] if side == "away"
                              else cc["away_pitch_hand"]) or ""
                rows.append({
                    "_player_id": r.get("player_id"),
                    "Hitter":   r["player_name"],
                    "Team":     norm_team(r["team"]),
                    "Bat":      r["bat_side"] or "",
                    "Spot":     int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
                    "LineupStatus": lineup_status,
                    "Game":     g["short_label"],
                    "Opp SP":   opp_pitcher,
                    "OppHand":  opp_p_hand,
                    "Park":     g.get("home_abbr", ""),
                    "ParkFactor": g.get("park_factor", 100),
                    "Weather":  _summarize_weather_short(cc.get("weather", {})),
                    "Score":    round(score, 1),
                    "AVG":      avg if not pd.isna(avg) else None,
                    "xBA":      xba if not pd.isna(xba) else None,
                    "xOBP":     xobp if not pd.isna(xobp) else None,
                    "xSLG":     xslg if not pd.isna(xslg) else None,
                    "OPS":      ops if not pd.isna(ops) else None,
                    "ISO":      iso if not pd.isna(iso) else None,
                    "Barrel%":  barrel if not pd.isna(barrel) else None,
                    "HardHit%": hh if not pd.isna(hh) else None,
                    "K%":       k_pct if not pd.isna(k_pct) else None,
                    "LD%":      ld if not pd.isna(ld) else None,
                    "Matchup":  m_score,
                    "Ceiling":  c_score,
                    "PA":       pa,
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Score", ascending=False).reset_index(drop=True)

def render_targets_html(df, mode="tb"):
    """Render the TB or HRR targets table with the same gold-on-dark-green
    visual language as HR Sleepers. mode picks which columns headline."""
    if df is None or df.empty:
        return ('<div style="padding:14px;color:#64748b;">No target candidates yet — '
                'lineups may not be posted.</div>')

    css = (
        "<style>"
        ".tg-wrap { margin: 6px 0 14px 0; }"
        ".tg-table { width:100%; border-collapse: separate; border-spacing: 0; "
        "  font-size:.92rem; background:#0b1220; border-radius:12px; overflow:hidden; "
        "  box-shadow: 0 2px 10px rgba(0,0,0,.4); border:1px solid rgba(255,255,255,.07); }"
        ".tg-table th { background:#3b1f6b; color:#fcd34d; text-align:left; "
        "  font-weight:800; padding:9px 10px; letter-spacing:.03em; font-size:.78rem; "
        "  text-transform:uppercase; }"
        ".tg-table td { padding:8px 10px; border-bottom:1px solid rgba(255,255,255,.06); "
        "  color:#e2e8f0; }"
        ".tg-table tr:nth-child(even) td { background:rgba(255,255,255,.03); }"
        ".tg-pill { display:inline-block; padding:3px 9px; border-radius:999px; "
        "  font-weight:800; font-size:.74rem; letter-spacing:.04em; }"
        ".tg-pill.elite  { background:rgba(34,197,94,.18); color:#4ade80; border:1px solid rgba(34,197,94,.3); }"
        ".tg-pill.strong { background:rgba(0,200,150,.15); color:#34d399; border:1px solid rgba(0,200,150,.3); }"
        ".tg-pill.ok     { background:rgba(250,204,21,.12); color:#fde68a; border:1px solid rgba(250,204,21,.3); }"
        ".tg-pill.soft   { background:rgba(251,146,60,.12); color:#fb923c; border:1px solid rgba(251,146,60,.3); }"
        ".tg-score { font-weight:900; font-size:1.05rem; color:#00c896; }"
        ".tg-name { font-weight:800; color:#ffffff; text-shadow:0 1px 2px rgba(0,0,0,.4); }"
        ".tg-meta { color:#7dd3fc; font-size:.78rem; }"
        ".tg-num { font-variant-numeric: tabular-nums; }"
        # Lineup-status mini-pills shown inline next to player name.
        ".tg-lp { display:inline-block; padding:1px 7px; border-radius:999px; "
        "  font-weight:800; font-size:.66rem; letter-spacing:.04em; "
        "  margin-left:6px; vertical-align: middle; }"
        ".tg-lp.confirmed { background:rgba(34,197,94,.18); color:#4ade80; border:1px solid rgba(34,197,94,.3); }"
        ".tg-lp.projected { background:rgba(59,130,246,.15); color:#93c5fd; border:1px solid rgba(59,130,246,.3); }"
        ".tg-lp.notposted { background:rgba(239,68,68,.12); color:#fca5a5; border:1px solid rgba(239,68,68,.3); }"
        # Park-factor pill colored by neutral / hitter-friendly / pitcher-friendly.
        ".tg-park { display:inline-block; padding:1px 7px; border-radius:6px; "
        "  font-weight:700; font-size:.7rem; }"
        ".tg-park.hot   { background:rgba(239,68,68,.15); color:#fca5a5; border:1px solid rgba(239,68,68,.3); }"
        ".tg-park.neut  { background:rgba(148,163,184,.12); color:#cbd5e1; border:1px solid rgba(148,163,184,.2); }"
        ".tg-park.cold  { background:rgba(59,130,246,.12); color:#93c5fd; border:1px solid rgba(59,130,246,.3); }"
        "</style>"
    )

    def _tier(score):
        if score is None: return ("ok", "—")
        try: s = float(score)
        except: return ("ok", "—")
        if s >= 75: return ("elite",  "Elite")
        if s >= 65: return ("strong", "Strong")
        if s >= 55: return ("ok",     "Average")
        return ("soft", "Soft")

    def _fmt_pct(v, n=1):
        if v is None or (isinstance(v, float) and pd.isna(v)): return "—"
        try: return f"{float(v):.{n}f}%"
        except: return "—"

    def _fmt_num(v, n=3):
        if v is None or (isinstance(v, float) and pd.isna(v)): return "—"
        try: return f"{float(v):.{n}f}"
        except: return "—"

    # Pick column layout per mode — keep clean (8 metric cols max).
    # Every layout now includes a Context column (park · weather) so users
    # see the environmental edge alongside the prop metrics.
    # OPS is included on every prop layout because it is a universal bat-
    # quality signal — relevant whether the prop is total bases, RBIs, or
    # hits+runs+RBIs.
    if mode == "tb":
        # TB: OPS, AVG, xBA, xSLG, ISO, Barrel%, K%, Matchup
        headers = ["#", "Hitter", "Game", "Context", "TB Score", "OPS", "AVG", "xBA", "xSLG", "ISO", "Barrel%", "K%", "Match"]
    elif mode == "rbi2":
        # 2+ RBI: OPS, AVG, xSLG, ISO, Barrel%, HardHit%, K%, Matchup
        headers = ["#", "Hitter", "Game", "Context", "RBI Score", "OPS", "AVG", "xSLG", "ISO", "Barrel%", "HardHit%", "K%", "Match"]
    else:
        # HRR: OPS, AVG, xBA, xOBP, xSLG, ISO, K%, Matchup
        headers = ["#", "Hitter", "Game", "Context", "HRR Score", "OPS", "AVG", "xBA", "xOBP", "xSLG", "ISO", "K%", "Match"]

    def _lineup_pill(status: str) -> str:
        s = (status or "Not Posted").strip()
        if s == "Confirmed": return '<span class="tg-lp confirmed">CONF</span>'
        if s == "Projected": return '<span class="tg-lp projected">PROJ</span>'
        return '<span class="tg-lp notposted">TBD</span>'

    def _park_chip(abbr: str, factor) -> str:
        try:
            f = float(factor)
        except Exception:
            f = 100.0
        cls = "neut"
        if f >= 105: cls = "hot"
        elif f <= 95: cls = "cold"
        return f'<span class="tg-park {cls}">{abbr or "—"} {int(round(f))}</span>'

    def _platoon_marker(bat: str, opp_hand: str) -> str:
        """Return ★ for a clean platoon edge (LHB vs RHP / RHB vs LHP),
        ✓ for switch hitters, '' otherwise."""
        b = (bat or "").upper()[:1]
        h = (opp_hand or "").upper()[:1]
        if b == "S": return ' <span title="Switch hitter" style="color:#16a34a;font-weight:900;">⇄</span>'
        if (b == "L" and h == "R") or (b == "R" and h == "L"):
            return ' <span title="Platoon edge" style="color:#dc2626;font-weight:900;">★</span>'
        return ""

    rows_html = []
    for i, r in df.iterrows():
        tier_cls, tier_label = _tier(r.get("Score"))
        lineup_pill = _lineup_pill(r.get("LineupStatus"))
        opp_hand = (r.get("OppHand") or "").upper()
        opp_hand_chip = (
            f'<span style="color:#475569;font-weight:700;font-size:.72rem;'
            f'margin-left:4px;">({format_pitcher_hand(opp_hand)})</span>' if opp_hand else ""
        )
        platoon = _platoon_marker(r.get("Bat", ""), opp_hand)
        park_chip = _park_chip(r.get("Park", ""), r.get("ParkFactor", 100))
        weather_str = r.get("Weather", "—") or "—"
        common = (
            "<tr>"
            f'<td class="tg-num">{i+1}</td>'
            f'<td><div class="tg-name">{r.get("Hitter","")}{platoon}{lineup_pill}</div>'
            f'<div class="tg-meta">{r.get("Team","")} · Bat {format_batter_stance(r.get("Bat",""))} · Spot {r.get("Spot","")}</div></td>'
            f'<td class="tg-meta">{r.get("Game","")}<br/>'
            f'<span style="color:#475569;">vs {r.get("Opp SP","")}{opp_hand_chip}</span></td>'
            f'<td class="tg-meta">{park_chip}<br/>'
            f'<span style="color:#475569;">{weather_str}</span></td>'
            f'<td><span class="tg-score">{r.get("Score",0):.1f}</span> '
            f'<span class="tg-pill {tier_cls}" style="margin-left:6px;">{tier_label}</span></td>'
        )
        ops_cell = f'<td class="tg-num">{_fmt_num(r.get("OPS"), 3)}</td>'
        if mode == "tb":
            metrics_html = (
                ops_cell +
                f'<td class="tg-num">{_fmt_num(r.get("AVG"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("xBA"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("xSLG"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("ISO"), 3)}</td>'
                f'<td class="tg-num">{_fmt_pct(r.get("Barrel%"))}</td>'
                f'<td class="tg-num">{_fmt_pct(r.get("K%"))}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("Matchup"), 1)}</td>'
            )
        elif mode == "rbi2":
            metrics_html = (
                ops_cell +
                f'<td class="tg-num">{_fmt_num(r.get("AVG"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("xSLG"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("ISO"), 3)}</td>'
                f'<td class="tg-num">{_fmt_pct(r.get("Barrel%"))}</td>'
                f'<td class="tg-num">{_fmt_pct(r.get("HardHit%"))}</td>'
                f'<td class="tg-num">{_fmt_pct(r.get("K%"))}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("Matchup"), 1)}</td>'
            )
        else:
            metrics_html = (
                ops_cell +
                f'<td class="tg-num">{_fmt_num(r.get("AVG"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("xBA"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("xOBP"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("xSLG"), 3)}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("ISO"), 3)}</td>'
                f'<td class="tg-num">{_fmt_pct(r.get("K%"))}</td>'
                f'<td class="tg-num">{_fmt_num(r.get("Matchup"), 1)}</td>'
            )
        rows_html.append(common + metrics_html + "</tr>")

    head_html = "".join(f"<th>{h}</th>" for h in headers)

    # ---- Mobile card grid ----
    if mode == "tb":
        score_label = "TB Score"
    elif mode == "rbi2":
        score_label = "RBI Score"
    else:
        score_label = "HRR Score"

    def _chips_for(r):
        if mode == "tb":
            chips = [
                _mc_chip("Match", _mc_fmt(r.get("Matchup"), "{:.1f}"),
                         _mc_chip_tone(r.get("Matchup"), 80, 140)),
                _mc_chip("OPS", _mc_fmt(r.get("OPS"), "{:.3f}"),
                         _mc_chip_tone(r.get("OPS"), 0.650, 0.900)),
                _mc_chip("xBA", _mc_fmt(r.get("xBA"), "{:.3f}"),
                         _mc_chip_tone(r.get("xBA"), 0.230, 0.310)),
                _mc_chip("xSLG", _mc_fmt(r.get("xSLG"), "{:.3f}"),
                         _mc_chip_tone(r.get("xSLG"), 0.380, 0.520)),
                _mc_chip("ISO", _mc_fmt(r.get("ISO"), "{:.3f}"),
                         _mc_chip_tone(r.get("ISO"), 0.130, 0.260)),
                _mc_chip("Barrel%", _mc_fmt(r.get("Barrel%"), "{:.1f}%"),
                         _mc_chip_tone(r.get("Barrel%"), 5.0, 14.0)),
                _mc_chip("K%", _mc_fmt(r.get("K%"), "{:.1f}%"),
                         _mc_chip_tone(r.get("K%"), 16.0, 30.0, reverse=True)),
            ]
        elif mode == "rbi2":
            chips = [
                _mc_chip("Match", _mc_fmt(r.get("Matchup"), "{:.1f}"),
                         _mc_chip_tone(r.get("Matchup"), 80, 140)),
                _mc_chip("OPS", _mc_fmt(r.get("OPS"), "{:.3f}"),
                         _mc_chip_tone(r.get("OPS"), 0.650, 0.900)),
                _mc_chip("xSLG", _mc_fmt(r.get("xSLG"), "{:.3f}"),
                         _mc_chip_tone(r.get("xSLG"), 0.380, 0.520)),
                _mc_chip("ISO", _mc_fmt(r.get("ISO"), "{:.3f}"),
                         _mc_chip_tone(r.get("ISO"), 0.130, 0.260)),
                _mc_chip("Barrel%", _mc_fmt(r.get("Barrel%"), "{:.1f}%"),
                         _mc_chip_tone(r.get("Barrel%"), 5.0, 14.0)),
                _mc_chip("HardHit%", _mc_fmt(r.get("HardHit%"), "{:.1f}%"),
                         _mc_chip_tone(r.get("HardHit%"), 32.0, 50.0)),
                _mc_chip("K%", _mc_fmt(r.get("K%"), "{:.1f}%"),
                         _mc_chip_tone(r.get("K%"), 16.0, 30.0, reverse=True)),
            ]
        else:  # hrr
            chips = [
                _mc_chip("Match", _mc_fmt(r.get("Matchup"), "{:.1f}"),
                         _mc_chip_tone(r.get("Matchup"), 80, 140)),
                _mc_chip("OPS", _mc_fmt(r.get("OPS"), "{:.3f}"),
                         _mc_chip_tone(r.get("OPS"), 0.650, 0.900)),
                _mc_chip("xBA", _mc_fmt(r.get("xBA"), "{:.3f}"),
                         _mc_chip_tone(r.get("xBA"), 0.230, 0.310)),
                _mc_chip("xOBP", _mc_fmt(r.get("xOBP"), "{:.3f}"),
                         _mc_chip_tone(r.get("xOBP"), 0.300, 0.400)),
                _mc_chip("xSLG", _mc_fmt(r.get("xSLG"), "{:.3f}"),
                         _mc_chip_tone(r.get("xSLG"), 0.380, 0.520)),
                _mc_chip("ISO", _mc_fmt(r.get("ISO"), "{:.3f}"),
                         _mc_chip_tone(r.get("ISO"), 0.130, 0.260)),
                _mc_chip("K%", _mc_fmt(r.get("K%"), "{:.1f}%"),
                         _mc_chip_tone(r.get("K%"), 16.0, 30.0, reverse=True)),
            ]
        return '<div class="mc-grid2">' + "".join(chips) + "</div>"

    mobile_cards = []
    for i, r in df.iterrows():
        tier_cls, tier_label = _tier(r.get("Score"))
        opp_sp = r.get("Opp SP", "") or "—"
        opp_hand = (r.get("OppHand") or "").upper()
        park = r.get("Park", "") or "—"
        try:
            pf = float(r.get("ParkFactor", 100) or 100)
        except Exception:
            pf = 100.0
        weather = (r.get("Weather", "—") or "—")
        lineup = (r.get("LineupStatus") or "Not Posted").strip()
        lineup_pill = {
            "Confirmed": ("CONF", "elite"),
            "Projected": ("PROJ", "info"),
        }.get(lineup, ("TBD", "warn"))
        tiers = [(tier_label, tier_cls), (lineup_pill[0], lineup_pill[1])]
        foot = (
            f'<div class="mc-foot">'
            f'<b>{r.get("Game","")}</b>'
            f' · vs {opp_sp}{f" ({format_pitcher_hand(opp_hand)})" if opp_hand else ""}'
            f' · Park {park} {int(round(pf))}'
            f' · {weather}'
            f'</div>'
        )
        mobile_cards.append(_mc_card(
            rank=i + 1,
            name=r.get("Hitter", ""),
            sub=f'{r.get("Team","")} · Bats {format_batter_stance(r.get("Bat",""))} · Spot {r.get("Spot","")}',
            score=r.get("Score"),
            score_label=score_label,
            tiers=tiers,
            chips_html=_chips_for(r),
            foot_html=foot,
        ))
    mobile_html = (
        '<div class="mc-mobile"><div class="mc-grid">'
        + "".join(mobile_cards) +
        '</div></div>'
    )

    return MOBILE_CARDS_CSS + css + (
        '<div class="mc-desktop">'
        f'<div class="tg-wrap"><table class="tg-table">'
        f'<thead><tr>{head_html}</tr></thead><tbody>'
        + "".join(rows_html) +
        '</tbody></table></div>'
        '</div>'
        + mobile_html
    )

def style_slate_pitcher_table(df):
    """Apply heatmap shading. Higher = better for pitcher (green) on Pitch Score,
    Strikeout Score, K%, K/9, Strike%, FB%*. Lower = better on ERA, WHIP, FIP*,
    BB%, OPS-A, AVG-A, Ball%."""
    if df.empty:
        return df
    show_cols = [c for c in df.columns if not c.startswith("_")]
    styler = df[show_cols].style.format({
        "Pitch Score": "{:.1f}", "Strikeout Score": "{:.1f}",
        "ERA": "{:.2f}", "WHIP": "{:.2f}", "FIP*": "{:.2f}",
        "K%": "{:.1f}", "BB%": "{:.1f}", "K/9": "{:.2f}",
        "Strike%": "{:.1f}", "Ball%": "{:.1f}",
        "OPS-A": "{:.3f}", "AVG-A": "{:.3f}",
        "FB%*": "{:.1f}", "IP": "{:.1f}",
    }, na_rep="—")
    higher_better = [("Pitch Score", 45, 75), ("Strikeout Score", 45, 75),
                     ("K%", 18, 32), ("K/9", 7.0, 12.0),
                     ("Strike%", 60, 68), ("FB%*", 30, 50)]
    lower_better  = [("ERA", 2.5, 5.5), ("WHIP", 1.00, 1.55), ("FIP*", 3.00, 5.50),
                     ("BB%", 5.0, 11.0), ("OPS-A", 0.600, 0.800),
                     ("AVG-A", 0.210, 0.275), ("Ball%", 32, 40)]
    for col, lo, hi in higher_better:
        if col in show_cols:
            styler = styler.map(lambda v, lo=lo, hi=hi: heat_color(v, lo, hi), subset=[col])
    for col, lo, hi in lower_better:
        if col in show_cols:
            styler = styler.map(lambda v, lo=lo, hi=hi: heat_color(v, lo, hi, reverse=True), subset=[col])
    return styler

# ===========================================================================
# UI components
# ===========================================================================
def render_brand_bar(slate_count):
    logo_html = (
        f'<img class="cmd-bar-logo" src="{LOGO_URI}" alt="MLB Edge" />'
        if LOGO_URI else
        '<span class="cmd-bar-logo" style="display:flex;align-items:center;justify-content:center;'
        'font-size:1rem;color:#00c896;">⚾</span>'
    )
    try:
        now_ct = datetime.now(MLB_TZ).strftime("%a %b %-d · %-I:%M %p CT")
    except Exception:
        now_ct = ""
    live_n = sum(1 for v in DATA_SOURCES.values() if v.get("status") == "live")
    total_n = len(DATA_SOURCES)
    health_dot_cls = "warn" if (total_n and live_n < total_n * 0.7) else ""
    health_label = f"{live_n}/{total_n} feeds live" if total_n else "Data live"
    st.markdown(f"""
    <div class="cmd-bar">
        <div class="cmd-bar-left">
            {logo_html}
            <div class="cmd-bar-wordmark">
                <span class="cmd-bar-name">MLB Edge</span>
                <span class="cmd-bar-tag">Matchup Intelligence</span>
            </div>
        </div>
        <div class="cmd-bar-center">
            <div class="cmd-stat">
                <span class="cmd-stat-val">{slate_count}</span>
                <span class="cmd-stat-label">{'Game' if slate_count == 1 else 'Games'}</span>
            </div>
            <div class="cmd-divider"></div>
            <div class="cmd-stat">
                <span class="cmd-stat-val">{live_n}</span>
                <span class="cmd-stat-label">Live Feeds</span>
            </div>
        </div>
        <div class="cmd-bar-right">
            <span class="cmd-time">{now_ct}</span>
            <div class="cmd-health">
                <span class="cmd-health-dot {health_dot_cls}"></span>
                <span class="cmd-health-text">{health_label}</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def _game_phase(detailed_status: str, snap: dict | None) -> str:
    """Classify a schedule row into one of {live, final, postponed, preview}.

    Prefer the live-feed status when available — it's the authority. Fall
    back to the schedule's detailedState text for games we never tried to
    fetch live state for (or where the feed failed)."""
    s = ((snap or {}).get("abstract_status") or "").lower()
    if s == "live":
        return "live"
    if s == "final":
        return "final"
    ds = (detailed_status or "").strip().lower()
    if "final" in ds or "game over" in ds:
        return "final"
    if "in progress" in ds or "warmup" in ds or "delayed" in ds and "start" not in ds:
        return "live"
    if "postponed" in ds or "suspended" in ds or "cancelled" in ds or "canceled" in ds:
        return "postponed"
    return "preview"


def _inning_label(snap: dict | None) -> str:
    """Short inning label like 'Top 6' or 'Bot 3'. Empty when unknown."""
    if not snap:
        return ""
    inn = snap.get("inning")
    half = (snap.get("inning_half") or "").lower()
    if not inn:
        return ""
    half_short = "Top" if half.startswith("t") else ("Bot" if half.startswith("b") else "")
    return f"{half_short} {inn}".strip()


def _pill_score_block(g, snap: dict | None) -> str:
    """The status + score chunk shown inside each game pill.

    Falls back to the original start-time line for preview games so the
    pill keeps its existing pregame look. Postponed/cancelled games show
    a single pill with no score."""
    phase = _game_phase(g.get("status", ""), snap)
    if phase == "preview":
        return f'<span class="time">{g.get("time_short", "")}</span>'
    if phase == "postponed":
        label = (g.get("status") or "Postponed")
        return (
            f'<span class="status-chip postponed">{label}</span>'
        )
    # Live or final — we have a score to show.
    away = (snap or {}).get("away_score")
    home = (snap or {}).get("home_score")
    away_s = "—" if away is None else str(away)
    home_s = "—" if home is None else str(home)
    away_ab = g.get("away_abbr", "")
    home_ab = g.get("home_abbr", "")
    if phase == "final":
        chip = '<span class="status-chip final">Final</span>'
    else:
        sub = _inning_label(snap)
        chip = '<span class="status-chip live">Live' + (
            f' · {sub}' if sub else "") + '</span>'
    return (
        f'<span class="score-line">{away_ab} {away_s}<span class="sep">·</span>'
        f'{home_ab} {home_s}</span>{chip}'
    )


def render_game_carousel(schedule_df, selected_idx):
    """Horizontal scrolling logo carousel. Pills are anchor links that set the
    `?g=<idx>` query parameter, which the app reads to drive game selection.

    Once a game is live or final we replace the start-time line on the pill
    with a compact score + Live/Final chip so the user sees the result
    without opening the game card."""
    if schedule_df.empty:
        return
    pills = []
    for i, g in schedule_df.iterrows():
        active = "active" if i == selected_idx else ""
        away_logo = logo_url(g["away_id"]) if g["away_id"] else ""
        home_logo = logo_url(g["home_id"]) if g["home_id"] else ""
        # Live snapshot is best-effort — failures fall back to pregame look.
        try:
            snap = _live_state_snapshot(g.get("game_pk"))
        except Exception:
            snap = None
        score_block = _pill_score_block(g, snap)
        pills.append(
            f'<a class="game-pill {active}" href="?g={i}" target="_self">'
            f'<span class="logos">'
            f'<img src="{away_logo}" alt="{g["away_abbr"]}" />'
            f'<span class="at">@</span>'
            f'<img src="{home_logo}" alt="{g["home_abbr"]}" />'
            f'</span>'
            f'<span class="matchup-text">{g["away_abbr"]} @ {g["home_abbr"]}</span>'
            f'{score_block}'
            f'</a>'
        )
    st.markdown(
        '<div class="carousel-wrap"><div class="carousel-strip">' + "".join(pills) + '</div></div>',
        unsafe_allow_html=True,
    )


def render_live_ticker(schedule_df):
    """Compact horizontal ticker of live + recently-final games.

    Renders nothing when there are no live or final games on the slate, so
    a pregame morning view doesn't show an empty bar. Pregame games are
    intentionally excluded — those already appear in the pill carousel."""
    if schedule_df is None or schedule_df.empty:
        return
    chunks = []
    for _, g in schedule_df.iterrows():
        try:
            snap = _live_state_snapshot(g.get("game_pk"))
        except Exception:
            snap = None
        phase = _game_phase(g.get("status", ""), snap)
        if phase not in ("live", "final"):
            continue
        away_ab = g.get("away_abbr", "")
        home_ab = g.get("home_abbr", "")
        away = (snap or {}).get("away_score")
        home = (snap or {}).get("home_score")
        away_s = "—" if away is None else str(away)
        home_s = "—" if home is None else str(home)
        sub = ""
        if phase == "live":
            inn = _inning_label(snap)
            if inn:
                sub = f'<span class="inning">{inn}</span>'
        chunks.append(
            f'<span class="game {phase}">'
            f'<span>{away_ab}</span><span class="runs">{away_s}</span>'
            f'<span class="vs">·</span>'
            f'<span>{home_ab}</span><span class="runs">{home_s}</span>'
            f'{sub}</span>'
        )
    if not chunks:
        return
    label = "Live / Final"
    st.markdown(
        f'<div class="live-ticker"><div class="row">'
        f'<span class="label">{label}</span>{"".join(chunks)}</div></div>',
        unsafe_allow_html=True,
    )


def _render_box_score_html(game_row, snap: dict | None) -> str:
    """Compact R/H/E + per-inning box score for the game-header card.

    Empty string for preview/postponed games. For final games the layout is
    a horizontally-scrollable per-inning table with R/H/E columns; for live
    games we render the same table plus an inning + base/out diamond."""
    if not snap:
        return ""
    phase = _game_phase(game_row.get("status", ""), snap)
    if phase not in ("live", "final"):
        return ""
    away_ab = game_row.get("away_abbr", "")
    home_ab = game_row.get("home_abbr", "")
    innings = list(snap.get("innings") or [])
    # Always show at least 9 inning columns for a balanced look; expand if
    # extras were played. Each cell can be a runs int or blank.
    cols = max(9, len(innings))
    header_cells = "".join(f"<th>{i+1}</th>" for i in range(cols))
    def _row(side: str, abbr: str) -> str:
        idx = 0 if side == "away" else 1
        tds = []
        for i in range(cols):
            if i < len(innings):
                v = innings[i][idx]
                tds.append(f"<td>{'' if v is None else v}</td>")
            else:
                tds.append("<td>·</td>")
        runs = snap.get(f"{side}_score")
        hits = snap.get(f"{side}_hits")
        errs = snap.get(f"{side}_errors")
        r_disp = "—" if runs is None else runs
        h_disp = "—" if hits is None else hits
        e_disp = "—" if errs is None else errs
        return (
            f"<tr><td class='team'>{abbr}</td>{''.join(tds)}"
            f"<td class='rhe runs'>{r_disp}</td>"
            f"<td class='rhe'>{h_disp}</td>"
            f"<td class='rhe'>{e_disp}</td></tr>"
        )
    # Status row at the top: chip + meta line + (live-only) diamond.
    if phase == "final":
        chip = '<span class="status-pill final">Final</span>'
        meta_bits = []
        det = (snap.get("detailed_status") or "").strip()
        if det and det.lower() not in ("final", "game over"):
            meta_bits.append(det)
        meta = (
            f'<span class="status-meta">{" · ".join(meta_bits)}</span>'
            if meta_bits else ""
        )
        diamond_html = ""
    else:
        chip = '<span class="status-pill live">Live</span>'
        inn = _inning_label(snap) or ""
        det = (snap.get("detailed_status") or "").strip()
        meta_parts = []
        if inn:
            meta_parts.append(inn)
        if det and det.lower() != "in progress":
            meta_parts.append(det)
        meta = (
            f'<span class="status-meta">{" · ".join(meta_parts)}</span>'
            if meta_parts else ""
        )
        # Build the base-state diamond (only meaningful mid-inning).
        b = snap.get("balls"); s = snap.get("strikes"); o = snap.get("outs")
        count_str = ""
        if b is not None and s is not None:
            count_str = f"{b}-{s}"
        out_str = "" if o is None else f"{o} out{'s' if o != 1 else ''}"
        def _base(on: bool) -> str:
            return f'<span class="base{" on" if on else ""}"></span>'
        # Grid is 3 cols x 2 rows: row 1 = blank, 2nd, blank; row 2 = 3rd, blank, 1st
        bases_html = (
            '<span class="bases">'
            '<span></span>' + _base(bool(snap.get("on_second"))) + '<span></span>'
            + _base(bool(snap.get("on_third"))) + '<span></span>'
            + _base(bool(snap.get("on_first"))) + '</span>'
        )
        bits = []
        if count_str:
            bits.append(count_str)
        if out_str:
            bits.append(out_str)
        bits_html = (" · ".join(bits)) if bits else ""
        diamond_html = (
            f'<span class="diamond">{bases_html}'
            f'<span>{bits_html}</span></span>'
        )
    status_row = (
        f'<div class="status-row">{chip}{meta}{diamond_html}</div>'
    )
    table = (
        f'<div class="scrollwrap"><table>'
        f'<thead><tr><th>Team</th>{header_cells}'
        f'<th>R</th><th>H</th><th>E</th></tr></thead>'
        f'<tbody>{_row("away", away_ab)}{_row("home", home_ab)}</tbody>'
        f'</table></div>'
    )
    return f'<div class="gh-scorebox">{status_row}{table}</div>'

def _impact_tile(label: str, pct: int, sub: str = "") -> str:
    """One of the three Weather-Impact tiles (HR / Runs / K).
    Color: green for boost, red for suppress, amber for neutral.
    Always shows a signed number with %."""
    if pct >= 4:    bg, fg, sign = "#dcfce7", "#15803d", "+"
    elif pct >= 1:  bg, fg, sign = "#ecfdf5", "#16a34a", "+"
    elif pct <= -4: bg, fg, sign = "#fee2e2", "#b91c1c", ""   # "-" already in number
    elif pct <= -1: bg, fg, sign = "#fef2f2", "#dc2626", ""
    else:           bg, fg, sign = "#fef9c3", "#a16207", "+" if pct >= 0 else ""
    return (
        f'<div style="flex:1; background:{bg}; border:1px solid #e2e8f0; '
        f'border-radius:12px; padding:10px 12px; min-width:0;">'
        f'<div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; '
        f'letter-spacing:.08em; font-weight:800;">{label}</div>'
        f'<div style="font-size:1.6rem; font-weight:900; color:{fg}; line-height:1.1; '
        f'margin-top:2px;">{sign}{pct}%</div>'
        f'<div style="font-size:0.7rem; color:#475569; font-weight:700; margin-top:2px; '
        f'min-height:1em;">{sub}</div>'
        f'</div>'
    )

def _avg_bar(label: str, pct: int) -> str:
    """Horizontal 'vs MLB average' bar. 50% center = avg; right of center = above."""
    # map -15..+15 to 0..100
    pos = max(0, min(100, 50 + pct * 3.3))
    # color the right segment green for boost, red for suppress
    if pct >= 1:    fill = "#16a34a"
    elif pct <= -1: fill = "#dc2626"
    else:           fill = "#94a3b8"
    sign = "+" if pct >= 0 else ""
    return (
        f'<div style="display:flex; align-items:center; gap:10px; margin:6px 0;">'
        f'<div style="width:42px; font-size:0.72rem; color:#475569; font-weight:800; '
        f'text-transform:uppercase; flex-shrink:0;">{label}</div>'
        f'<div style="flex:1; position:relative; height:8px; background:#e2e8f0; border-radius:999px;">'
        f'<div style="position:absolute; top:-3px; bottom:-3px; left:50%; width:2px; '
        f'background:#94a3b8;"></div>'
        f'<div style="position:absolute; top:0; bottom:0; '
        f'{"left:50%" if pct >= 0 else f"right:50%"}; width:{abs(pct*3.3)}%; '
        f'background:{fill}; border-radius:999px;"></div>'
        f'</div>'
        f'<div style="width:46px; text-align:right; font-size:0.78rem; font-weight:900; '
        f'color:{fill if pct != 0 else "#475569"};">{sign}{pct}%</div>'
        f'</div>'
    )

def _render_ou_strip(ou: dict) -> str:
    """Render the Historical O/U bar (Under % | Push % | Over %).
    `ou` comes from summarize_park_ou(); shows N=0 placeholder if no data."""
    if not ou or ou.get("n", 0) == 0:
        return (
            '<div style="font-size:0.7rem; color:#94a3b8; font-weight:700; '
            'margin-top:10px;">Historical O/U — not enough completed games at this park yet.</div>'
        )
    line = ou.get("line")
    avg = ou.get("avg")
    n = ou.get("n")
    line_str = (f"line: <span style='color:#e2e8f0; font-weight:900;'>{line:.1f}</span>"
                if line is not None else
                "<span style='color:#94a3b8;'>line: not set · add ODDS_API_KEY in Streamlit secrets</span>")
    avg_str = (f"hist. avg: <span style='color:#e2e8f0; font-weight:900;'>{avg:.1f}</span>"
               if avg is not None else "")
    # If we have a line, draw the Under/Push/Over distribution bar
    if ou.get("under") is not None:
        u, p, o = ou["under"], ou["push"] or 0, ou["over"]
        bar_html = (
            f'<div style="display:flex; height:36px; border-radius:999px; overflow:hidden; '
            f'border:1px solid rgba(255,255,255,.1); margin-top:6px;">'
            f'<div style="flex:{u}; background:linear-gradient(180deg,rgba(239,68,68,.35) 0%,rgba(220,38,38,.5) 100%); '
            f'display:flex; align-items:center; justify-content:center; '
            f'color:#fca5a5; font-weight:900; font-size:0.85rem;">'
            f'<span style="margin-right:6px; opacity:.7; font-size:0.7rem;">UNDER</span>{u}%</div>'
        )
        if p > 0:
            bar_html += (
                f'<div style="flex:{p}; background:rgba(255,255,255,.06); '
                f'display:flex; align-items:center; justify-content:center; '
                f'color:#94a3b8; font-weight:800; font-size:0.72rem;">'
                f'<span style="opacity:.7; font-size:0.6rem; margin-right:3px;">PUSH</span>{p}%</div>'
            )
        bar_html += (
            f'<div style="flex:{o}; background:linear-gradient(180deg,rgba(34,197,94,.35) 0%,rgba(22,163,74,.5) 100%); '
            f'display:flex; align-items:center; justify-content:center; '
            f'color:#4ade80; font-weight:900; font-size:0.85rem;">'
            f'{o}%<span style="margin-left:6px; opacity:.7; font-size:0.7rem;">OVER</span></div>'
            f'</div>'
        )
    else:
        # No line, just show distribution above/below historical avg
        bar_html = ""
    return (
        '<div style="font-size:0.7rem; color:#64748b; text-transform:uppercase; '
        'letter-spacing:.1em; font-weight:800; margin-top:14px; margin-bottom:4px; '
        'display:flex; justify-content:space-between; align-items:baseline; gap:8px;">'
        f'<span>Historical O/U · {n} games</span>'
        f'<span style="text-transform:none; letter-spacing:.02em; font-size:0.78rem; '
        f'color:#475569; font-weight:700;">{line_str}'
        f'{"  /  " + avg_str if avg_str else ""}</span>'
        '</div>'
        + bar_html
    )

def render_weather_impact_card(weather: dict, park_factor, home_abbr: str,
                                ou_summary: dict = None) -> str:
    """Build the Kevin Roth-style Weather Impact panel: chip strip on top,
    three impact tiles, vs-MLB-average bars, and historical O/U strip."""
    imp = compute_weather_impact(weather, park_factor, home_abbr)
    temp = imp["temp"]; wind = imp["wind"]; dew = imp["dew"]
    rain = imp["rain_pct"]
    temp_str = f"{int(round(temp))}°F" if temp is not None else "—"
    wind_val = f"{int(round(float(wind)))}" if wind not in (None, 0) else "0"
    dew_str = f"{int(round(dew))}°" if dew is not None else "—"
    rain_str = f"{int(rain)}%" if rain not in (None, 0) else "0%"
    # tile sub-lines (Kevin's style: "X.X HR/gm these conditions / Y.Y HR/gm park avg")
    pf = float(park_factor) if park_factor is not None else 100.0
    base_hr = 2.37 * (pf / 100.0)  # MLB avg ~2.37 HR/gm scaled by park factor
    hr_today = base_hr * (1 + imp["hr_pct"] / 100.0)
    hr_sub = f"{hr_today:.1f} HR/gm · park avg {base_hr:.1f}"
    runs_sub = f"{9.0 * (1 + imp['runs_pct']/100):.1f} R/gm · mlb 9.0"
    k_sub = f"{16.8 * (1 + imp['k_pct']/100):.1f} K/gm · mlb 16.8"
    # sample dot color
    if imp["sample"] == "Strong signal": dot = "#16a34a"
    elif imp["sample"] == "Moderate signal": dot = "#facc15"
    elif imp["sample"] == "Stable sample": dot = "#22d3ee"
    else: dot = "#94a3b8"
    return (
        '<div style="background:linear-gradient(180deg,#111827 0%,#0b1220 100%); '
        'border:1px solid rgba(255,255,255,.08); border-radius:14px; '
        'padding:0; margin-top:14px; overflow:hidden; '
        'box-shadow:0 2px 12px rgba(0,0,0,.45);">'
        # ---- Top chip strip: temp, dew, wind, rain, sky, sample ----
        '<div style="background:linear-gradient(180deg,#3b1f6b 0%,#1a0b3a 100%); '
        'color:#fef3c7; padding:10px 14px; display:flex; flex-wrap:wrap; gap:10px 18px; '
        'align-items:center; border-bottom:2px solid #facc15;">'
        f'<div style="font-size:1.05rem; font-weight:900;">{temp_str}'
        f'<span style="font-size:0.7rem; opacity:.85; font-weight:800; margin-left:3px;">TEMP</span></div>'
        f'<div style="font-size:0.92rem; font-weight:800; opacity:.92;">'
        f'<span style="opacity:.7; font-size:0.7rem; font-weight:800;">DEW</span> {dew_str}</div>'
        f'<div style="font-size:0.92rem; font-weight:800; opacity:.92;">'
        f'<span style="opacity:.7; font-size:0.7rem; font-weight:800;">WIND</span> {wind_val} mph '
        f'<span style="opacity:.65; font-size:0.78rem;">({imp["wind_label"]})</span></div>'
        f'<div style="font-size:0.92rem; font-weight:800; opacity:.92;">'
        f'<span style="opacity:.7; font-size:0.7rem; font-weight:800;">RAIN</span> {rain_str}</div>'
        f'<div style="margin-left:auto; display:flex; gap:14px; align-items:center;">'
        f'<div style="font-size:0.95rem; font-weight:900; color:#facc15;">'
        f'{imp["sky_icon"]} {imp["sky"]}</div>'
        f'<div style="font-size:0.78rem; font-weight:800; display:flex; align-items:center; gap:6px;">'
        f'<span style="width:9px; height:9px; border-radius:50%; background:{dot}; '
        f'box-shadow:0 0 0 2px rgba(255,255,255,.15);"></span>{imp["sample"]}</div>'
        '</div></div>'
        # ---- "WEATHER IMPACT VS THIS PARK" header ----
        '<div style="padding:12px 14px 4px;">'
        '<div style="font-size:0.7rem; color:#64748b; text-transform:uppercase; '
        'letter-spacing:.1em; font-weight:800; margin-bottom:8px;">'
        'Weather Impact vs This Park</div>'
        # ---- 3 impact tiles ----
        '<div style="display:flex; gap:8px; flex-wrap:wrap;">'
        + _impact_tile("Home Runs", imp["hr_pct"], hr_sub)
        + _impact_tile("Runs",      imp["runs_pct"], runs_sub)
        + _impact_tile("Strikeouts", imp["k_pct"],  k_sub)
        + '</div>'
        # ---- vs MLB average bars ----
        '<div style="font-size:0.7rem; color:#64748b; text-transform:uppercase; '
        'letter-spacing:.1em; font-weight:800; margin-top:14px; margin-bottom:4px;">'
        'vs MLB Average</div>'
        + _avg_bar("HR",   imp["hr_pct"])
        + _avg_bar("RUNS", imp["runs_pct"])
        + _avg_bar("K’s", imp["k_pct"])
        + (_render_ou_strip(ou_summary) if ou_summary is not None else "")
        + '<div style="font-size:0.68rem; color:#94a3b8; font-weight:700; '
        'margin-top:8px; padding-top:6px; border-top:1px dashed rgba(255,255,255,.1);">'
        'Model blends park factor, temp, wind to/from CF, and dew point. Tunable in code — '
        f'weather: <b>{weather.get("source_label", "Open-Meteo")}</b> '
        '(RotoGrinders preferred · Open-Meteo fallback) · MLB StatsAPI · The Odds API.</div>'
        '</div></div>'
    )

def render_game_header(game_row, ctx, weather):
    away_logo = logo_url(game_row["away_id"]) if game_row["away_id"] else ""
    home_logo = logo_url(game_row["home_id"]) if game_row["home_id"] else ""
    def _status_pill(s):
        if s == "Confirmed": return "tier-strong"
        if s == "Projected": return "tier-ok"
        return "tier-avoid"
    away_pill = _status_pill(ctx["away_status"])
    home_pill = _status_pill(ctx["home_status"])
    # Pull park history + book line for the O/U strip on the card.
    # Both fail silently if data is unavailable; the card still renders.
    try:
        totals = get_park_history_totals(
            int(game_row.get("venue_id") or 0),
            int(game_row.get("home_id") or 0),
        )
    except Exception:
        totals = []
    try:
        odds_map = get_odds_totals_map()
        line_today = odds_map.get((game_row.get("away_abbr"), game_row.get("home_abbr")))
    except Exception:
        line_today = None
    ou_summary = summarize_park_ou(totals, line_today)
    weather_card_html = render_weather_impact_card(
        weather, game_row.get("park_factor"), game_row.get("home_abbr", ""),
        ou_summary=ou_summary,
    )
    # Box score: only renders for live + final games; pregame returns "".
    try:
        snap = _live_state_snapshot(game_row.get("game_pk"))
    except Exception:
        snap = None
    box_html = _render_box_score_html(game_row, snap)
    # Build as a single flat string (no leading indentation, no blank lines).
    # st.markdown runs textwrap.dedent + strip; an empty {box_html} on its own
    # line becomes a blank line, which closes the CommonMark HTML block — and
    # the subsequent indented <div>s then render as literal code blocks.
    card_html = (
        '<div class="section-card dark">'
        '<div class="game-header">'
        '<div>'
        '<div class="matchup-display">'
        f'<img src="{away_logo}" alt="{game_row["away_abbr"]}" />'
        f'<div class="team-abbr">{game_row["away_abbr"]}</div>'
        '<span class="vs">@</span>'
        f'<div class="team-abbr">{game_row["home_abbr"]}</div>'
        f'<img src="{home_logo}" alt="{game_row["home_abbr"]}" />'
        '</div>'
        f'<div class="meta">{game_row["game_time_ct"]} · {game_row["venue"]} · {game_row["status"]}</div>'
        '</div>'
        '<div class="probables">'
        '<div class="label">Probables</div>'
        f'<div>{game_row["away_probable"]} <span class="hand">({format_pitcher_hand(ctx["away_pitch_hand"], "?")})</span></div>'
        f'<div>vs {game_row["home_probable"]} <span class="hand">({format_pitcher_hand(ctx["home_pitch_hand"], "?")})</span></div>'
        '</div>'
        '</div>'
        f'{box_html}'
        f'{weather_card_html}'
        '<div class="kpi-row" style="margin-top:10px;">'
        f'<div class="kpi"><span class="tier {away_pill}">{game_row["away_abbr"]}: {ctx["away_status"]}</span></div>'
        f'<div class="kpi"><span class="tier {home_pill}">{game_row["home_abbr"]}: {ctx["home_status"]}</span></div>'
        '</div>'
        '</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Live game state — current pitcher overlay
# ---------------------------------------------------------------------------
# Once a game flips from preview to live, the schedule's pregame
# ``probablePitcher`` becomes stale the moment the starter is pulled. We
# layer the live feed on top of every schedule_df row through this helper so
# pitcher cards, matchup heat-maps, generators, and parlays all see the
# *current* pitcher with no signature changes downstream.
#
# 60s TTL keeps the StatsAPI live feed footprint negligible (~one request
# per active game per minute) while matching live-betting cadence. Pregame
# and final games are also handled — the service uses a longer TTL for
# those internally.
@st.cache_data(ttl=60, show_spinner=False)
def _live_state_snapshot(game_pk):
    """Return a small dict snapshot of the live state for ``game_pk``.

    Returning a dict (not the dataclass) keeps Streamlit's cache happy —
    dataclasses with default_factory don't always hash cleanly across reruns
    and we only need a handful of fields here. ``None`` when unavailable so
    callers can short-circuit to their pregame fallback.
    """
    state = _svc_get_live_game_state(game_pk)
    if state is None:
        return None
    def _p_to_dict(p):
        if p is None:
            return None
        return {
            "player_id": p.player_id,
            "name": p.name,
            "hand": p.hand,
            "is_starter": p.is_starter,
            "pitches_thrown": p.pitches_thrown,
        }
    return {
        "game_pk": state.game_pk,
        "abstract_status": state.abstract_status,
        "detailed_status": state.detailed_status,
        "inning": state.inning,
        "inning_half": state.inning_half,
        "venue": state.venue,
        "away_pitcher": _p_to_dict(state.away_pitcher),
        "home_pitcher": _p_to_dict(state.home_pitcher),
        "away_score": state.away_score,
        "home_score": state.home_score,
        "away_hits": state.away_hits,
        "home_hits": state.home_hits,
        "away_errors": state.away_errors,
        "home_errors": state.home_errors,
        "innings": list(state.innings or []),
        "balls": state.balls,
        "strikes": state.strikes,
        "outs": state.outs,
        "on_first": state.on_first,
        "on_second": state.on_second,
        "on_third": state.on_third,
    }


def apply_live_pitcher_overlay(game_row):
    """Apply current-pitcher live overlay to a schedule row.

    Accepts a dict, pd.Series, or anything supporting ``.to_dict()`` /
    ``.get()`` and returns a dict with the same shape plus possible live
    overrides. Pregame rows pass through unchanged (with a ``*_pitcher_source
    = "probable"`` tag added so the UI can render a consistent freshness
    chip).
    """
    try:
        snap = _live_state_snapshot(game_row.get("game_pk") if hasattr(game_row, "get")
                                    else game_row["game_pk"])
    except Exception:
        snap = None
    if snap is None:
        # No live state — return a plain-dict copy with source tags so
        # downstream callers can treat every row uniformly.
        if hasattr(game_row, "to_dict"):
            out = dict(game_row.to_dict())
        elif isinstance(game_row, dict):
            out = dict(game_row)
        else:
            out = dict(game_row)
        out.setdefault("away_pitcher_source", "probable")
        out.setdefault("home_pitcher_source", "probable")
        return out
    # Rebuild a LiveGameState-like object so the service helper can do the
    # merge in one place. We avoid re-importing the dataclass by calling
    # the service-level helper with a fresh fetch instead. Cheap: the
    # underlying state is already cached in _live_state_snapshot above.
    from services.live_game_state import LiveGameState, LivePitcher
    def _p_from_dict(d):
        if not d:
            return None
        return LivePitcher(
            player_id=d.get("player_id"), name=d.get("name") or "",
            hand=d.get("hand") or "", is_starter=bool(d.get("is_starter", True)),
            pitches_thrown=d.get("pitches_thrown"),
        )
    state = LiveGameState(
        game_pk=int(snap.get("game_pk") or 0),
        abstract_status=snap.get("abstract_status") or "",
        detailed_status=snap.get("detailed_status") or "",
        inning=snap.get("inning"),
        inning_half=snap.get("inning_half") or "",
        venue=snap.get("venue") or "",
        away_pitcher=_p_from_dict(snap.get("away_pitcher")),
        home_pitcher=_p_from_dict(snap.get("home_pitcher")),
    )
    return _svc_apply_live_pitcher(game_row, state=state)


@st.cache_data(ttl=60, show_spinner=False)
def get_lineup_freshness(game_pk):
    """Return ('<provider> · <status> · <age>', provider, status) for a game.

    Backed by services.lineup_service, which chains premium feeds
    (Sportradar / SportsDataIO) when keys are configured and falls back to
    the official MLB StatsAPI live feed. Never raises — empty tuple on
    failure so the banner still renders."""
    try:
        gl = get_lineup_service().get_game(int(game_pk))
        if gl is None:
            return ("", "", "")
        return (_format_lineup_freshness(gl), gl.provider, gl.lineup_status)
    except Exception:
        return ("", "", "")


def render_lineup_banner(team_id, team_abbr, opp_pitcher, status, *,
                          freshness_text: str = "", opp_pitch_hand: str = ""):
    if status == "Confirmed": pill_cls = "tier-strong"
    elif status == "Projected": pill_cls = "tier-ok"
    else: pill_cls = "tier-avoid"
    logo = logo_url(team_id) if team_id else ""
    fresh_html = ""
    if freshness_text:
        fresh_html = (
            f'<span style="margin-left:10px;font-size:.72rem;color:#475569;'
            f'font-weight:700;letter-spacing:.02em;" title="Data source · '
            f'lineup status · last refresh">{freshness_text}</span>'
        )
    # Flat string + no leading whitespace: an empty {fresh_html} on its own
    # indented line would otherwise become a blank line after textwrap.dedent
    # inside st.markdown, closing the HTML block and rendering the rest as
    # literal code.
    hand_label = format_pitcher_hand(opp_pitch_hand, "")
    opp_text = f"vs {opp_pitcher}" + (f" ({hand_label})" if hand_label else "")
    banner_html = (
        '<div class="lineup-banner">'
        f'<img src="{logo}" alt="{team_abbr}" />'
        f'<div class="lineup-title">{team_abbr} Lineup</div>'
        f'<div class="vs-pitcher">{opp_text}</div>'
        f'{fresh_html}'
        f'<div class="badge"><span class="tier {pill_cls}">{status}</span></div>'
        '</div>'
    )
    st.markdown(banner_html, unsafe_allow_html=True)

def render_pitch_mix_block(mix_df: pd.DataFrame, surface_bg: str = "#0a1628") -> str:
    """Build the 'Pitch Mix' mini-table HTML for a pitcher.
    Columns: pitch (emoji+name), Use%, wOBA allowed, Whiff%, RV/100.
    Color-codes wOBA allowed: green = strong (low), red = vulnerable (high)."""
    if mix_df is None or mix_df.empty:
        return (
            '<div style="margin-top:10px; font-size:0.78rem; color:#94a3b8; '
            'font-weight:700;">No pitch-mix data yet — may not have enough '
            "pitches thrown this season.</div>"
        )
    rows_html = []
    for _, r in mix_df.iterrows():
        pt = str(r.get("pitch_type", "")).strip().upper()
        name = PITCH_NAME_MAP.get(pt, str(r.get("pitch_name", pt)))
        emoji = PITCH_EMOJI.get(pt, "⚾")
        use = r.get("pitch_usage")
        woba = r.get("woba")
        whiff = r.get("whiff_percent")
        rv100 = r.get("run_value_per_100")
        # color the wOBA cell: <.300 great (green), >.360 leaky (red)
        try:
            w = float(woba)
            if w <= 0.300: woba_col = "#4ade80"
            elif w <= 0.340: woba_col = "#a3e635"
            elif w <= 0.380: woba_col = "#fbbf24"
            else: woba_col = "#f87171"
            woba_str = f"{w:.3f}"
        except Exception:
            woba_col, woba_str = "#94a3b8", "—"
        use_str = f"{float(use):.0f}%" if pd.notna(use) else "—"
        whiff_str = f"{float(whiff):.0f}%" if pd.notna(whiff) else "—"
        try: rv_str = f"{float(rv100):+.1f}"
        except Exception: rv_str = "—"
        rows_html.append(
            f'<tr>'
            f'<td style="padding:6px 8px; font-weight:800; color:#f1f5f9; white-space:nowrap; font-size:0.86rem;">{emoji} {name}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:800; color:#e2e8f0; font-size:0.86rem;">{use_str}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:900; color:{woba_col}; font-size:0.88rem;">{woba_str}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700; color:#cbd5e1; font-size:0.84rem;">{whiff_str}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700; color:#cbd5e1; font-size:0.84rem;">{rv_str}</td>'
            f'</tr>'
        )
    return (
        '<div style="margin-top:12px; background:rgba(255,255,255,0.05); '
        'border:1px solid rgba(255,255,255,0.12); '
        'border-radius:10px; padding:6px 4px; overflow:hidden;">'
        '<div style="font-size:0.64rem; color:#7dd3fc; text-transform:uppercase; '
        'letter-spacing:.1em; font-weight:900; padding:5px 10px 6px; '
        'border-bottom:1px solid rgba(255,255,255,0.07);">'
        'Pitch Mix — What They Throw &amp; Results Allowed</div>'
        '<table style="width:100%; border-collapse:collapse;">'
        '<thead>'
        '<tr style="border-bottom:1px solid rgba(255,255,255,0.07);">'
        '<th style="text-align:left; padding:5px 8px; font-size:0.64rem; font-weight:900; '
        'color:#94a3b8; text-transform:uppercase; letter-spacing:.08em;">Pitch</th>'
        '<th style="text-align:right; padding:5px 8px; font-size:0.64rem; font-weight:900; '
        'color:#94a3b8; text-transform:uppercase; letter-spacing:.08em;">Use</th>'
        '<th style="text-align:right; padding:5px 8px; font-size:0.64rem; font-weight:900; '
        'color:#94a3b8; text-transform:uppercase; letter-spacing:.08em;">wOBA</th>'
        '<th style="text-align:right; padding:5px 8px; font-size:0.64rem; font-weight:900; '
        'color:#94a3b8; text-transform:uppercase; letter-spacing:.08em;">Whiff</th>'
        '<th style="text-align:right; padding:5px 8px; font-size:0.64rem; font-weight:900; '
        'color:#94a3b8; text-transform:uppercase; letter-spacing:.08em;" '
        'title="Run value per 100 pitches — negative is better for the pitcher">RV/100</th>'
        '</tr>'
        '</thead>'
        '<tbody>' + "".join(rows_html) + '</tbody></table>'
        '<div style="font-size:0.68rem; color:#64748b; padding:5px 10px 3px; '
        'border-top:1px solid rgba(255,255,255,0.06);">'
        'Green wOBA = pitch is dominant · Red = batters punish it.</div>'
        '</div>'
    )

def _pp_tone(v, lo, hi, reverse=False):
    """Map a pitcher metric value to a 'good'/'mid'/'bad' tone class for the
    main-matchup pitcher panel. `reverse=True` flips directionality for
    pitcher-allowed metrics (xwOBA, Barrel%, HardHit%, WHIP, HR/9, ERA,
    xSLG, FB%) where lower is better. Returns "" when the value can't be
    parsed so the cell renders neutral instead of fake-green."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x != x:  # NaN
        return ""
    if hi == lo:
        return "mid"
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    if reverse:
        t = 1.0 - t
    if t < 0.33:
        return "bad"
    if t < 0.66:
        return "mid"
    return "good"


def _hand_code(value) -> str:
    """Normalize handedness-like values to L/R/S for display helpers."""
    try:
        if value is None:
            return ""
        if isinstance(value, float) and value != value:
            return ""
        s = str(value).strip().upper()
        if not s or s in ("NAN", "NONE", "<NA>", "?", "-", "—"):
            return ""
        return s[:1]
    except Exception:
        return ""


def format_batter_stance(bat_side, unknown="—") -> str:
    """Display MLB batter stance as LHB/RHB/SHB while preserving raw codes."""
    return {"L": "LHB", "R": "RHB", "S": "SHB"}.get(
        _hand_code(bat_side), unknown
    )


def format_pitcher_hand(pitch_hand, unknown="—") -> str:
    """Display MLB pitcher throwing hand as LHP/RHP."""
    return {"L": "LHP", "R": "RHP"}.get(_hand_code(pitch_hand), unknown)


def lookup_batter_stance(player_id=None, name=None, team=None, df=None) -> str:
    """Return raw L/R/S batter stance from the loaded batter data."""
    data = df
    if data is None:
        data = globals().get("batters_df")
    if data is None or not isinstance(data, pd.DataFrame) or data.empty:
        return ""
    stance_col = "bat_side" if "bat_side" in data.columns else (
        "Bat" if "Bat" in data.columns else None
    )
    if not stance_col:
        return ""
    if player_id is not None and "player_id" in data.columns:
        try:
            pid = int(float(player_id))
            match = data[pd.to_numeric(data["player_id"], errors="coerce") == pid]
            if not match.empty:
                return _hand_code(match.iloc[0].get(stance_col))
        except Exception:
            pass
    if name and "name_key" in data.columns:
        try:
            key = clean_name(str(name))
            match = data[data["name_key"] == key]
            if team and "team_key" in data.columns:
                team_key = norm_team(team)
                team_match = match[match["team_key"] == team_key]
                if not team_match.empty:
                    match = team_match
            if not match.empty:
                return _hand_code(match.iloc[0].get(stance_col))
        except Exception:
            pass
    return ""


def _safe_str(v, default=""):
    """Coerce arbitrary live-metadata values to a clean string for rendering.

    Why: live overlays may inject ``None``, ``float('nan')``, pandas NA, dicts,
    or other non-string values into fields the renderer drops into an
    HTML/markdown template. A ``TypeError`` mid-template (e.g. ``'replaced ' +
    None``) wipes the whole card and surfaces raw HTML to the user.
    """
    try:
        if v is None:
            return default
        # pandas NA / numpy NaN — comparing NaN to itself is the cheapest check
        # that works without a hard pandas dependency in this helper.
        if isinstance(v, float) and v != v:
            return default
        s = str(v).strip()
        if not s or s.lower() in ("nan", "none", "<na>"):
            return default
        return s
    except Exception:
        return default


# ============================================================================
# Pitcher Breakdown — premium per-pitcher interactive card.
#
# Renders a dark, mobile-first "player card" experience: identity row,
# projection KPI tiles (PROJ K / PROJ IP / ERA / HR ALLOW / WHIP / OPP K RK)
# followed by internal tabs (Arsenal · Opposing Lineup · Game Log · Splits).
# All data sources degrade gracefully — when an API call fails or returns
# nothing, the tab renders a friendly fallback instead of raw HTML.
# ============================================================================

@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_pitcher_game_log(player_id: int, season: int) -> pd.DataFrame:
    """Per-game pitching log for one season from MLB StatsAPI.

    Returns a DataFrame with one row per game (most recent last) with columns:
      date, opp, ip, h, r, er, bb, k, hr, pitches, era_game.
    Empty DataFrame on any failure or unrecognized payload.
    """
    if not player_id:
        return pd.DataFrame()
    try:
        pid = int(player_id)
    except Exception:
        return pd.DataFrame()
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
            params={"stats": "gameLog", "group": "pitching",
                    "season": int(season), "sportId": 1},
            timeout=10,
        )
        r.raise_for_status()
        payload = r.json() or {}
    except Exception:
        return pd.DataFrame()

    splits = []
    for s in (payload.get("stats") or []):
        for sp in (s.get("splits") or []):
            splits.append(sp)
    if not splits:
        return pd.DataFrame()

    rows = []
    for sp in splits:
        stat = sp.get("stat") or {}
        d = sp.get("date") or (sp.get("game") or {}).get("date")
        try:
            dt = pd.to_datetime(d).date() if d else None
        except Exception:
            dt = None
        opp = ""
        try:
            opp_team = sp.get("opponent") or {}
            opp = opp_team.get("abbreviation") or opp_team.get("teamCode") or opp_team.get("name") or ""
        except Exception:
            opp = ""

        def _f(k):
            v = stat.get(k)
            try: return float(v)
            except Exception: return None

        def _i(k):
            v = stat.get(k)
            try: return int(v)
            except Exception: return None

        ip = _f("inningsPitched")
        h = _i("hits") or 0
        r_ = _i("runs") or 0
        er = _i("earnedRuns") or 0
        bb = _i("baseOnBalls") or 0
        k = _i("strikeOuts") or 0
        hr = _i("homeRuns") or 0
        pitches = _i("numberOfPitches") or _i("pitchesThrown")
        era_g = (9.0 * er / float(ip)) if (ip and ip > 0) else None
        rows.append({
            "date": dt, "opp": opp,
            "ip": ip, "h": h, "r": r_, "er": er,
            "bb": bb, "k": k, "hr": hr,
            "pitches": pitches, "era_game": era_g,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.dropna(subset=["date"])
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pitcher_splits_by_hand(player_id: int, season: int) -> dict:
    """Pitcher splits vs LHB / vs RHB and Home/Away for the season.

    Returns dict shaped like:
      {"vsR": {...}, "vsL": {...}, "home": {...}, "away": {...}}
    where each inner dict has keys: pa, avg, obp, slg, ops, k, bb, hr.
    Missing splits become empty dicts so callers can render fallback text.
    """
    out = {"vsR": {}, "vsL": {}, "home": {}, "away": {}}
    if not player_id:
        return out
    try:
        pid = int(player_id)
    except Exception:
        return out

    def _grab(sit_codes: str, key: str):
        try:
            r = requests.get(
                f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                params={"stats": "statSplits", "group": "pitching",
                        "season": int(season), "sitCodes": sit_codes,
                        "sportId": 1},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json() or {}
        except Exception:
            return
        for st_ in (data.get("stats") or []):
            for sp in (st_.get("splits") or []):
                stat = sp.get("stat") or {}
                def _f(k):
                    try: return float(stat.get(k))
                    except Exception: return None
                def _i(k):
                    try: return int(stat.get(k))
                    except Exception: return None
                out[key] = {
                    "pa": _i("plateAppearances") or _i("battersFaced"),
                    "avg": _f("avg"),
                    "obp": _f("obp"),
                    "slg": _f("slg"),
                    "ops": _f("ops"),
                    "k": _i("strikeOuts") or 0,
                    "bb": _i("baseOnBalls") or 0,
                    "hr": _i("homeRuns") or 0,
                    "ip": _f("inningsPitched"),
                }
                return

    # Use MLB StatsAPI 'sitCodes' splits selectors.
    _grab("vr", "vsR")
    _grab("vl", "vsL")
    _grab("h",  "home")
    _grab("a",  "away")
    return out


def _project_pitcher_targets(p_row: dict, game_log_df: pd.DataFrame | None = None) -> dict:
    """Compute per-game projections for a starter from season-rate stats.

    PROJ IP: blends recent-form IP/start (last 5 starts) and season IP/start.
    PROJ K:  IP * K/9 / 9.
    PROJ HR: IP * HR/9 / 9.
    Returns dict with floats or None.
    """
    out = {"PROJ_IP": None, "PROJ_K": None, "PROJ_HR": None}
    if not p_row:
        return out
    def _f(k):
        v = p_row.get(k)
        try:
            if v is None: return None
            x = float(v)
            return None if x != x else x
        except Exception:
            return None
    season_ip = _f("IP")
    k9 = _f("K/9")
    hr9 = _f("HR/9")

    # Recent IP/start from the game log (last 5 starts, if available).
    recent_ip_per_start = None
    try:
        if game_log_df is not None and not game_log_df.empty and "ip" in game_log_df.columns:
            tail = game_log_df.tail(5)
            ips = tail["ip"].dropna()
            if not ips.empty:
                recent_ip_per_start = float(ips.mean())
    except Exception:
        recent_ip_per_start = None

    # Season IP/start (StatsAPI doesn't ship 'starts' here, but the slate
    # row's IP is season-total. We approximate starts from game-log length
    # if available; fall back to assuming ~5.5 IP per start when neither
    # source is usable.)
    season_ip_per_start = None
    try:
        if game_log_df is not None and not game_log_df.empty:
            starts = len(game_log_df)
            if season_ip and starts:
                season_ip_per_start = float(season_ip) / float(starts)
    except Exception:
        season_ip_per_start = None

    if recent_ip_per_start is not None and season_ip_per_start is not None:
        proj_ip = 0.6 * recent_ip_per_start + 0.4 * season_ip_per_start
    elif recent_ip_per_start is not None:
        proj_ip = recent_ip_per_start
    elif season_ip_per_start is not None:
        proj_ip = season_ip_per_start
    else:
        proj_ip = 5.5  # league-average starter floor

    # Clamp to a believable range so a single 1-IP relief blowup or a 9-IP
    # complete game doesn't break the projection.
    proj_ip = max(3.0, min(7.5, float(proj_ip)))
    out["PROJ_IP"] = round(proj_ip, 1)
    if k9 is not None:
        out["PROJ_K"] = round(proj_ip * k9 / 9.0, 1)
    if hr9 is not None:
        out["PROJ_HR"] = round(proj_ip * hr9 / 9.0, 2)
    return out


def _compute_opp_k_rank(schedule_df, opp_abbr: str, pitcher_stats_df=None) -> tuple[int | None, int | None]:
    """Best-effort 'opponent K rank' across the slate.

    Ranks the opponent by how strikeout-prone its lineup is. We don't have
    direct team K% in this app, so we approximate by aggregating the
    starting pitcher K%/Whiff% they're facing tonight isn't useful — what
    we want is opposing-batter K-rate. As a fallback we return (None, None)
    when we can't compute it; callers display '—'. Right now we expose a
    rank within the slate's pitcher K% as a proxy: a team facing a high-K
    pitcher tonight is the same proxy used for the headline KPI tile.

    Returns (rank, total) or (None, None) if not computable.
    """
    # No team-K table is loaded in app.py today — return blank rather than
    # invent a number. The opp-K KPI tile will render '—' which is the
    # intended graceful fallback the user accepted.
    return (None, None)


def _kpi_tile_html(label: str, value: str, sub: str = "", tone: str = "") -> str:
    tone_colors = {
        "good": "#10b981",
        "warn": "#f59e0b",
        "bad":  "#ef4444",
        "":     "#facc15",
    }
    accent = tone_colors.get(tone, "#facc15")
    sub_html = (
        f'<div class="pbd-kpi-sub">{sub}</div>' if sub else ''
    )
    return (
        '<div class="pbd-kpi">'
        f'<div class="pbd-kpi-label">{label}</div>'
        f'<div class="pbd-kpi-value" style="color:{accent};">{value}</div>'
        f'{sub_html}'
        '</div>'
    )


def _fmt_or_dash(v, fmt="{:.2f}"):
    if v is None:
        return "—"
    try:
        if isinstance(v, float) and v != v:
            return "—"
        return fmt.format(float(v))
    except Exception:
        return "—" if v in (None, "") else str(v)


def render_pitcher_breakdown_header(p_row: dict, ranking_label: str = "") -> str:
    """Top of the Pitcher Breakdown card: LIVE PREVIEW pill + title +
    subtitle + identity row with headshot, name, matchup line, ranking
    badge."""
    pname = _safe_str(p_row.get("Pitcher"), "TBD")
    team = _safe_str(p_row.get("Team"), "")
    opp = _safe_str(p_row.get("Opp"), "")
    loc = _safe_str(p_row.get("Loc"), "@")
    throws = _safe_str(p_row.get("Throws"), "?")
    hand_label = format_pitcher_hand(throws, "SP")
    matchup_line = f"{team} {loc} {opp} · {hand_label}"

    pid = p_row.get("_player_id") or p_row.get("player_id")
    headshot = player_headshot_url(pid) if pid else ""
    logo = p_row.get("_logo") or (logo_url(int(pid)) if False else "")
    head_img = (
        f'<img class="pbd-headshot" src="{headshot}" alt="{pname}" '
        f'onerror="this.style.display=\'none\'" />'
        if headshot else
        '<div class="pbd-headshot pbd-headshot-empty">⚾</div>'
    )

    ranking_html = ""
    if ranking_label:
        ranking_html = (
            f'<div class="pbd-rankbadge">{ranking_label}</div>'
        )

    return (
        '<div class="pbd-top">'
        '<div class="pbd-pill">'
        '<span class="pbd-pill-dot"></span>LIVE PREVIEW'
        '</div>'
        '<div class="pbd-title">Pitcher Breakdown</div>'
        '<div class="pbd-subtitle">Arsenal analysis, opposing lineup grid, '
        'recent form, season stats.</div>'
        '<div class="pbd-id-row">'
        f'{head_img}'
        '<div class="pbd-id-info">'
        f'<div class="pbd-id-name">{pname}</div>'
        f'<div class="pbd-id-matchup">{matchup_line}</div>'
        '</div>'
        f'{ranking_html}'
        '</div>'
        '</div>'
    )


def render_pitcher_breakdown_kpis(p_row: dict, proj: dict, opp_k_rank: tuple) -> str:
    """KPI row: PROJ K · PROJ IP · ERA · HR ALLOW · WHIP · OPP K RK."""
    proj_k = proj.get("PROJ_K")
    proj_ip = proj.get("PROJ_IP")
    proj_hr = proj.get("PROJ_HR")
    era = p_row.get("ERA")
    whip = p_row.get("WHIP")

    # Tone hints reuse the same thresholds as the season-stat heatmap so
    # the colors are consistent across the app.
    def _tone(v, lo, hi, reverse=False):
        try: x = float(v)
        except Exception: return ""
        if x != x: return ""
        if reverse:
            if x <= lo: return "good"
            if x >= hi: return "bad"
            return "warn"
        if x >= hi: return "good"
        if x <= lo: return "bad"
        return "warn"

    era_tone  = _tone(era,  5.50, 2.50, reverse=True)
    whip_tone = _tone(whip, 1.50, 1.00, reverse=True)
    k_tone    = _tone(proj_k, 4.0, 7.5)
    ip_tone   = _tone(proj_ip, 4.5, 6.0)
    hr_tone   = _tone(proj_hr, 1.5, 0.5, reverse=True)

    rank, total = opp_k_rank or (None, None)
    opp_k_value = f"#{rank}" if rank is not None else "—"
    opp_k_sub = f"of {total}" if total else "season"

    tiles = [
        _kpi_tile_html("PROJ K",    _fmt_or_dash(proj_k, "{:.1f}"),  "tonight", k_tone),
        _kpi_tile_html("PROJ IP",   _fmt_or_dash(proj_ip, "{:.1f}"), "innings", ip_tone),
        _kpi_tile_html("ERA",       _fmt_or_dash(era, "{:.2f}"),     "season",  era_tone),
        _kpi_tile_html("HR ALLOW",  _fmt_or_dash(proj_hr, "{:.2f}"), "tonight", hr_tone),
        _kpi_tile_html("WHIP",      _fmt_or_dash(whip, "{:.2f}"),    "season",  whip_tone),
        _kpi_tile_html("OPP K RK",  opp_k_value,                      opp_k_sub, ""),
    ]
    return (
        '<div class="pbd-kpi-grid">'
        + "".join(tiles) +
        '</div>'
    )


def _pitcher_breakdown_pitch_score(p_row: dict, proj: dict | None = None) -> float | None:
    """Return a 0-100 pitch score for sorting / chip display.

    Prefers the precomputed ``Pitch Score`` from ``build_slate_pitcher_table``
    (35% xwOBA-against + 25% K-BB% + 20% Whiff% + 20% Barrel%-against). When
    that column is missing or null — common for StatsAPI-only fallback rows —
    derives a stable proxy from available projection + skill metrics:

        30% PROJ_K  normalized over 3.0..8.0  (higher=better)
        20% K-BB%   normalized over 5..25     (higher=better)
        20% ERA     normalized over 2.50..5.50 (lower=better)
        15% WHIP    normalized over 1.00..1.50 (lower=better)
        15% HR/9    normalized over 0.50..1.80 (lower=better)

    Weights stay constant; whichever inputs are present split the score
    proportionally. Returns ``None`` only if no signals are available so
    sorting can push such pitchers to the tail with ``na_position='last'``.
    """
    proj = proj or {}

    def _f(v):
        try:
            if v is None: return None
            x = float(v)
            return None if x != x else x
        except Exception:
            return None

    raw = _f(p_row.get("Pitch Score"))
    if raw is not None:
        return round(raw, 1)

    def _norm(x, lo, hi, reverse=False):
        if x is None: return None
        try:
            v = float(x)
        except Exception:
            return None
        if hi == lo: return 50.0
        t = (v - lo) / (hi - lo)
        t = max(0.0, min(1.0, t))
        if reverse: t = 1.0 - t
        return t * 100.0

    # _norm expects lo<hi. For "lower=better" metrics we pass the natural
    # range and use reverse=True so low raw values map to a high score.
    parts = [
        (0.30, _norm(proj.get("PROJ_K"), 3.0, 8.0)),
        (0.20, _norm(p_row.get("K-BB%"), 5.0, 25.0)),
        (0.20, _norm(p_row.get("ERA"), 2.50, 5.50, reverse=True)),
        (0.15, _norm(p_row.get("WHIP"), 1.00, 1.50, reverse=True)),
        (0.15, _norm(p_row.get("HR/9"), 0.50, 1.80, reverse=True)),
    ]
    num = 0.0
    den = 0.0
    for w, v in parts:
        if v is None: continue
        num += w * v
        den += w
    if den == 0:
        return None
    return round(num / den, 1)


def _pitcher_breakdown_badges(p_row: dict, proj: dict | None = None) -> list:
    """Return up to 4 (label, tone) badges for the Pitcher Breakdown card.

    Reuses ``_sp_compute_tiers`` so classifications stay in sync with the
    rest of the app, but caps the result and applies a priority ordering:
    HR risk first (the headline bettors look for), then strength badges
    (K Dominator, Command Edge, Traffic Limiter, Matchup Boost), then
    softer context (BABIP / Fade Risk). Falls back to a PROJ_K-derived
    Matchup Boost when nothing else fires but the projection is strong.
    """
    tiers = []
    try:
        tiers = _sp_compute_tiers(p_row) or []
    except Exception:
        tiers = []

    def _prio(item):
        label, tone = item
        if "HR Target" in label: return 0
        if tone == "good": return 1
        if tone == "warn": return 2
        return 3
    tiers = sorted(tiers, key=_prio)

    if proj and not any("Matchup Boost" in lbl for lbl, _ in tiers):
        try:
            pk = float(proj.get("PROJ_K") or 0)
            if pk >= 7.5:
                tiers.append((f"📈 Matchup Boost · {pk:.1f} K proj", "good"))
        except Exception:
            pass

    return tiers[:4]


def render_pitcher_breakdown_badges(p_row: dict, proj: dict | None = None,
                                     pitch_score: float | None = None) -> str:
    """Render the compact badge strip + pitch score chip under the KPI grid.

    Returns an empty string when there is nothing meaningful to show, so
    the card collapses cleanly for TBD / no-data rows instead of leaving
    an empty bar.
    """
    badges = _pitcher_breakdown_badges(p_row, proj)
    chips = []
    if pitch_score is not None:
        try:
            ps = float(pitch_score)
        except Exception:
            ps = None
        if ps is None:
            tone = ""
            ps_text = "—"
        else:
            if ps >= 65:   tone = "good"
            elif ps >= 50: tone = "warn"
            else:          tone = "bad"
            ps_text = f"{ps:.1f}"
        chips.append(
            f'<span class="pbd-badge pbd-badge-score pbd-badge-{tone}">'
            f'🎯 Pitch Score · {ps_text}</span>'
        )
    for label, tone in badges:
        chips.append(
            f'<span class="pbd-badge pbd-badge-{tone}">{label}</span>'
        )
    if not chips:
        return ""
    return '<div class="pbd-badges">' + "".join(chips) + '</div>'


def render_pitcher_breakdown_arsenal(mix_df: pd.DataFrame) -> str:
    """Arsenal tab: pitch usage bars + Use% / wOBA / Whiff% / RV/100 detail."""
    if mix_df is None or mix_df.empty:
        return (
            '<div class="pbd-empty">'
            'No pitch-by-pitch Statcast data for this starter yet '
            '(usually means &lt; 50 pitches thrown this season).'
            '</div>'
        )
    rows = []
    max_use = 0.0
    try:
        max_use = float(mix_df["pitch_usage"].fillna(0).max() or 0.0)
    except Exception:
        max_use = 0.0
    if not max_use or max_use <= 0:
        max_use = 100.0
    for _, r in mix_df.iterrows():
        pt = str(r.get("pitch_type", "")).strip().upper()
        name = PITCH_NAME_MAP.get(pt, str(r.get("pitch_name", pt)) or pt or "—")
        emoji = PITCH_EMOJI.get(pt, "⚾")
        use = r.get("pitch_usage")
        woba = r.get("woba")
        whiff = r.get("whiff_percent")
        rv100 = r.get("run_value_per_100")
        velo = r.get("velocity") if "velocity" in r.index else None

        try:
            use_pct = float(use)
        except Exception:
            use_pct = 0.0
        bar_width = max(0.0, min(100.0, (use_pct / max_use) * 100.0))
        use_str = f"{use_pct:.0f}%" if use_pct else "—"

        try:
            w = float(woba)
            if w <= 0.300: woba_col = "#34d399"
            elif w <= 0.340: woba_col = "#a3e635"
            elif w <= 0.380: woba_col = "#f59e0b"
            else: woba_col = "#f87171"
            woba_str = f"{w:.3f}"
        except Exception:
            woba_col, woba_str = "#94a3b8", "—"
        try: whiff_str = f"{float(whiff):.0f}%"
        except Exception: whiff_str = "—"
        try: rv_str = f"{float(rv100):+.1f}"
        except Exception: rv_str = "—"
        try: velo_str = f"{float(velo):.1f} mph"
        except Exception: velo_str = ""

        velo_html = (
            f'<span class="pbd-arsenal-velo">{velo_str}</span>' if velo_str else ''
        )

        rows.append(
            '<div class="pbd-arsenal-row">'
            '<div class="pbd-arsenal-head">'
            f'<span class="pbd-arsenal-name">{emoji} {name}</span>'
            f'{velo_html}'
            f'<span class="pbd-arsenal-use">{use_str}</span>'
            '</div>'
            '<div class="pbd-arsenal-bar-wrap">'
            f'<div class="pbd-arsenal-bar" style="width:{bar_width:.1f}%;"></div>'
            '</div>'
            '<div class="pbd-arsenal-stats">'
            f'<div><span>wOBA</span><b style="color:{woba_col};">{woba_str}</b></div>'
            f'<div><span>Whiff</span><b>{whiff_str}</b></div>'
            f'<div><span>RV/100</span><b>{rv_str}</b></div>'
            '</div>'
            '</div>'
        )
    return '<div class="pbd-arsenal">' + "".join(rows) + '</div>'


def render_pitcher_breakdown_game_log(log_df: pd.DataFrame, n: int = 6) -> str:
    """Game Log tab: most recent starts as compact cards."""
    if log_df is None or log_df.empty:
        return (
            '<div class="pbd-empty">'
            'No game log available for this season yet (StatsAPI returns '
            'nothing for pitchers who haven\'t appeared).'
            '</div>'
        )
    tail = log_df.tail(n).iloc[::-1]  # most recent first
    rows_html = []
    for _, r in tail.iterrows():
        d = r.get("date")
        try:
            d_str = pd.to_datetime(d).strftime("%b %-d") if d else "—"
        except Exception:
            d_str = str(d) if d else "—"
        opp = _safe_str(r.get("opp"), "—") or "—"
        ip = _fmt_or_dash(r.get("ip"), "{:.1f}")
        k = r.get("k") if r.get("k") is not None else "—"
        h = r.get("h") if r.get("h") is not None else "—"
        bb = r.get("bb") if r.get("bb") is not None else "—"
        er = r.get("er") if r.get("er") is not None else "—"
        hr = r.get("hr") if r.get("hr") is not None else "—"
        pitches = r.get("pitches")
        pitches_str = f"{int(pitches)} P" if pitches else "—"
        era_g = r.get("era_game")
        try:
            era_g_str = f"{float(era_g):.2f}"
        except Exception:
            era_g_str = "—"
        # K-line tone (5+ Ks is a good start)
        try:
            k_tone = "good" if int(k) >= 6 else ("warn" if int(k) >= 4 else "bad")
        except Exception:
            k_tone = ""
        try:
            er_tone = "good" if int(er) <= 2 else ("warn" if int(er) <= 4 else "bad")
        except Exception:
            er_tone = ""
        rows_html.append(
            '<div class="pbd-glog-row">'
            '<div class="pbd-glog-date">'
            f'<div class="pbd-glog-d">{d_str}</div>'
            f'<div class="pbd-glog-opp">vs {opp}</div>'
            '</div>'
            '<div class="pbd-glog-stats">'
            f'<div><span>IP</span><b>{ip}</b></div>'
            f'<div><span>K</span><b class="pbd-tone-{k_tone}">{k}</b></div>'
            f'<div><span>H</span><b>{h}</b></div>'
            f'<div><span>BB</span><b>{bb}</b></div>'
            f'<div><span>ER</span><b class="pbd-tone-{er_tone}">{er}</b></div>'
            f'<div><span>HR</span><b>{hr}</b></div>'
            f'<div><span>P</span><b>{pitches_str}</b></div>'
            f'<div><span>ERA</span><b>{era_g_str}</b></div>'
            '</div>'
            '</div>'
        )
    return '<div class="pbd-glog">' + "".join(rows_html) + '</div>'


def render_pitcher_breakdown_lineup(game_pk, opp_team_id, opp_abbr: str) -> str:
    """Opposing Lineup tab: starters with handedness + slot."""
    if not game_pk:
        return (
            '<div class="pbd-empty">'
            'Opposing lineup not yet posted. Check back ~2 hours before first pitch.'
            '</div>'
        )
    try:
        gl = get_lineup_service().get_game(int(game_pk))
    except Exception:
        gl = None
    if gl is None:
        return (
            '<div class="pbd-empty">'
            'Could not load opposing lineup from the lineup service. '
            'Try again in a moment.'
            '</div>'
        )

    # Pick the side that ISN'T this pitcher's team.
    pick = None
    try:
        if opp_team_id and gl.away.team_id == int(opp_team_id):
            pick = gl.away
        elif opp_team_id and gl.home.team_id == int(opp_team_id):
            pick = gl.home
    except Exception:
        pick = None
    if pick is None:
        # Fallback by abbr.
        if opp_abbr and gl.away.team_abbr == opp_abbr:
            pick = gl.away
        elif opp_abbr and gl.home.team_abbr == opp_abbr:
            pick = gl.home
    if pick is None or not pick.starters:
        return (
            '<div class="pbd-empty">'
            f'Opposing lineup for {opp_abbr or "the opponent"} is not posted yet. '
            'MLB typically posts ~2 hours before first pitch.'
            '</div>'
        )

    rows = []
    for p in pick.starters[:9]:
        slot = p.batting_order if p.batting_order else "—"
        nm = _safe_str(p.name, "—")
        pos = _safe_str(p.position, "")
        side = _safe_str(p.bat_side, "?")
        side_class = {"L": "L", "R": "R", "S": "S"}.get(side, "")
        side_label = format_batter_stance(side, "?")
        rows.append(
            '<div class="pbd-lineup-row">'
            f'<div class="pbd-lineup-slot">{slot}</div>'
            '<div class="pbd-lineup-name">'
            f'<div class="pbd-lineup-nm">{nm}</div>'
            f'<div class="pbd-lineup-pos">{pos}</div>'
            '</div>'
            f'<div class="pbd-lineup-hand pbd-hand-{side_class}">{side_label}</div>'
            '</div>'
        )
    head = (
        '<div class="pbd-lineup-head">'
        f'<div>{pick.team_abbr or opp_abbr or "OPP"} Lineup</div>'
        f'<div class="pbd-lineup-status">{pick.status or "expected"}</div>'
        '</div>'
    )
    return '<div class="pbd-lineup">' + head + "".join(rows) + '</div>'


def render_pitcher_breakdown_splits(splits: dict) -> str:
    """Splits tab: vs LHB / vs RHB / Home / Away."""
    if not splits or not any(splits.values()):
        return (
            '<div class="pbd-empty">'
            'No splits data available yet for this starter.'
            '</div>'
        )
    def _card(title, d):
        if not d:
            return (
                f'<div class="pbd-split-card pbd-split-empty">'
                f'<div class="pbd-split-title">{title}</div>'
                '<div class="pbd-split-na">no data</div>'
                '</div>'
            )
        return (
            '<div class="pbd-split-card">'
            f'<div class="pbd-split-title">{title}</div>'
            '<div class="pbd-split-grid">'
            f'<div><span>PA</span><b>{d.get("pa") or "—"}</b></div>'
            f'<div><span>AVG</span><b>{_fmt_or_dash(d.get("avg"), "{:.3f}")}</b></div>'
            f'<div><span>OBP</span><b>{_fmt_or_dash(d.get("obp"), "{:.3f}")}</b></div>'
            f'<div><span>SLG</span><b>{_fmt_or_dash(d.get("slg"), "{:.3f}")}</b></div>'
            f'<div><span>OPS</span><b>{_fmt_or_dash(d.get("ops"), "{:.3f}")}</b></div>'
            f'<div><span>K</span><b>{d.get("k") if d.get("k") is not None else "—"}</b></div>'
            f'<div><span>BB</span><b>{d.get("bb") if d.get("bb") is not None else "—"}</b></div>'
            f'<div><span>HR</span><b>{d.get("hr") if d.get("hr") is not None else "—"}</b></div>'
            '</div>'
            '</div>'
        )
    cards = [
        _card("vs RHB", splits.get("vsR")),
        _card("vs LHB", splits.get("vsL")),
        _card("Home", splits.get("home")),
        _card("Away", splits.get("away")),
    ]
    return '<div class="pbd-splits">' + "".join(cards) + '</div>'


def render_pitcher_breakdown_styles() -> str:
    """Single block of CSS shared by all Pitcher Breakdown components."""
    return (
        "<style>"
        # ---- Outer card / hero ----
        ".pbd-card { background: linear-gradient(180deg, #0b1220 0%, #1a0b2e 60%, #2a0e3e 100%); "
        "  border:1px solid #312e81; border-radius:20px; padding:18px 16px; "
        "  color:#e5e7eb; box-shadow: 0 10px 30px rgba(15,5,40,.45); "
        "  margin: 14px 0 18px 0; }"
        ".pbd-top { margin-bottom:14px; }"
        ".pbd-pill { display:inline-flex; align-items:center; gap:6px; "
        "  background: rgba(250,204,21,.12); color:#facc15; border:1px solid rgba(250,204,21,.45); "
        "  padding:3px 10px; border-radius:999px; font-size:.66rem; font-weight:900; "
        "  letter-spacing:.12em; text-transform:uppercase; }"
        ".pbd-pill-dot { width:6px; height:6px; border-radius:50%; background:#facc15; "
        "  box-shadow:0 0 8px rgba(250,204,21,.7); animation: pbdpulse 1.6s infinite; }"
        "@keyframes pbdpulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }"
        ".pbd-title { font-size:1.35rem; font-weight:900; color:#f8fafc; margin-top:10px; "
        "  letter-spacing:.01em; }"
        ".pbd-subtitle { font-size:.82rem; color:#a78bfa; margin-top:2px; }"
        ".pbd-id-row { display:flex; align-items:center; gap:12px; margin-top:14px; "
        "  padding:10px 12px; background: rgba(15,23,42,.55); border-radius:14px; "
        "  border:1px solid rgba(99,102,241,.25); }"
        ".pbd-headshot { width:54px; height:54px; border-radius:50%; object-fit:cover; "
        "  background:#0f172a; border:2px solid rgba(168,85,247,.45); flex:0 0 auto; }"
        ".pbd-headshot-empty { display:flex; align-items:center; justify-content:center; "
        "  font-size:1.4rem; }"
        ".pbd-id-info { flex:1 1 auto; min-width:0; }"
        ".pbd-id-name { font-size:1.05rem; font-weight:900; color:#f8fafc; "
        "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
        ".pbd-id-matchup { font-size:.78rem; color:#a5b4fc; margin-top:2px; }"
        ".pbd-rankbadge { flex:0 0 auto; padding:6px 10px; border-radius:10px; "
        "  background: linear-gradient(135deg, #facc15 0%, #f59e0b 100%); color:#1f1407; "
        "  font-size:.7rem; font-weight:900; letter-spacing:.04em; text-transform:uppercase; "
        "  box-shadow: 0 4px 12px rgba(245,158,11,.35); white-space:nowrap; }"
        # ---- KPI grid ----
        ".pbd-kpi-grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:8px; "
        "  margin-bottom:14px; }"
        "@media (min-width: 720px) { .pbd-kpi-grid { grid-template-columns: repeat(6, 1fr); } }"
        ".pbd-kpi { background: rgba(15,23,42,.65); border:1px solid rgba(99,102,241,.25); "
        "  border-radius:12px; padding:10px 8px; text-align:center; }"
        ".pbd-kpi-label { font-size:.6rem; font-weight:900; letter-spacing:.08em; "
        "  text-transform:uppercase; color:#94a3b8; }"
        ".pbd-kpi-value { font-size:1.25rem; font-weight:900; margin-top:2px; "
        "  font-variant-numeric: tabular-nums; line-height:1.1; }"
        ".pbd-kpi-sub { font-size:.6rem; color:#94a3b8; margin-top:1px; "
        "  letter-spacing:.04em; }"
        # ---- Badge strip (HR Target, K Dominator, Pitch Score chip, …) ----
        ".pbd-badges { display:flex; flex-wrap:wrap; gap:6px; "
        "  margin: -4px 0 14px 0; }"
        ".pbd-badge { display:inline-flex; align-items:center; gap:4px; "
        "  padding:4px 10px; border-radius:999px; font-size:.72rem; font-weight:800; "
        "  letter-spacing:.02em; line-height:1.2; "
        "  background: rgba(99,102,241,.14); color:#c7d2fe; "
        "  border:1px solid rgba(99,102,241,.35); white-space:nowrap; "
        "  max-width:100%; overflow:hidden; text-overflow:ellipsis; }"
        ".pbd-badge-good { background: rgba(16,185,129,.16); color:#6ee7b7; "
        "  border-color: rgba(16,185,129,.45); }"
        ".pbd-badge-warn { background: rgba(245,158,11,.18); color:#fcd34d; "
        "  border-color: rgba(245,158,11,.5); }"
        ".pbd-badge-bad  { background: rgba(239,68,68,.18); color:#fca5a5; "
        "  border-color: rgba(239,68,68,.5); }"
        ".pbd-badge-info { background: rgba(96,165,250,.16); color:#93c5fd; "
        "  border-color: rgba(96,165,250,.45); }"
        ".pbd-badge-score { background: linear-gradient(135deg, "
        "  rgba(250,204,21,.18), rgba(167,139,250,.18)); "
        "  color:#fde68a; border-color: rgba(250,204,21,.5); }"
        # ---- Sort hint ----
        ".pbd-sort-hint { color:#94a3b8; font-size:.7rem; "
        "  margin: 0 0 8px 2px; letter-spacing:.02em; }"
        # ---- Empty state ----
        ".pbd-empty { padding:14px 16px; color:#a5b4fc; background: rgba(15,23,42,.55); "
        "  border:1px dashed rgba(148,163,184,.35); border-radius:12px; font-size:.85rem; "
        "  line-height:1.4; }"
        # ---- Arsenal ----
        ".pbd-arsenal { display:flex; flex-direction:column; gap:10px; }"
        ".pbd-arsenal-row { background: rgba(15,23,42,.55); border:1px solid rgba(99,102,241,.22); "
        "  border-radius:12px; padding:10px 12px; }"
        ".pbd-arsenal-head { display:flex; align-items:center; gap:8px; "
        "  font-size:.88rem; }"
        ".pbd-arsenal-name { font-weight:800; color:#f8fafc; flex:1 1 auto; "
        "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
        ".pbd-arsenal-velo { color:#a78bfa; font-weight:700; font-size:.74rem; "
        "  background: rgba(167,139,250,.12); padding:2px 8px; border-radius:6px; }"
        ".pbd-arsenal-use { font-weight:900; color:#facc15; font-variant-numeric: tabular-nums; }"
        ".pbd-arsenal-bar-wrap { background: rgba(255,255,255,.06); border-radius:6px; "
        "  height:6px; margin-top:8px; overflow:hidden; }"
        ".pbd-arsenal-bar { height:100%; background: linear-gradient(90deg, #a78bfa 0%, #facc15 100%); "
        "  border-radius:6px; transition: width .25s ease; }"
        ".pbd-arsenal-stats { display:grid; grid-template-columns: repeat(3, 1fr); gap:6px; "
        "  margin-top:8px; }"
        ".pbd-arsenal-stats > div { background: rgba(255,255,255,.04); border-radius:8px; "
        "  padding:4px 6px; text-align:center; }"
        ".pbd-arsenal-stats span { display:block; font-size:.58rem; font-weight:800; "
        "  letter-spacing:.06em; color:#94a3b8; text-transform:uppercase; }"
        ".pbd-arsenal-stats b { font-size:.85rem; color:#f8fafc; font-variant-numeric: tabular-nums; }"
        # ---- Game log ----
        ".pbd-glog { display:flex; flex-direction:column; gap:8px; }"
        ".pbd-glog-row { display:flex; align-items:center; gap:10px; "
        "  background: rgba(15,23,42,.55); border:1px solid rgba(99,102,241,.22); "
        "  border-radius:12px; padding:8px 10px; }"
        ".pbd-glog-date { flex:0 0 auto; text-align:center; min-width:54px; }"
        ".pbd-glog-d { font-weight:900; color:#facc15; font-size:.78rem; "
        "  letter-spacing:.04em; }"
        ".pbd-glog-opp { font-size:.68rem; color:#a5b4fc; margin-top:1px; }"
        ".pbd-glog-stats { display:grid; grid-template-columns: repeat(8, 1fr); gap:4px; "
        "  flex:1 1 auto; min-width:0; }"
        "@media (max-width: 640px) { "
        "  .pbd-glog-stats { grid-template-columns: repeat(4, 1fr); } "
        "}"
        ".pbd-glog-stats > div { text-align:center; }"
        ".pbd-glog-stats span { display:block; font-size:.56rem; font-weight:800; "
        "  letter-spacing:.06em; color:#94a3b8; text-transform:uppercase; }"
        ".pbd-glog-stats b { font-size:.82rem; color:#f8fafc; font-variant-numeric: tabular-nums; }"
        ".pbd-tone-good { color:#34d399 !important; }"
        ".pbd-tone-warn { color:#fbbf24 !important; }"
        ".pbd-tone-bad  { color:#f87171 !important; }"
        # ---- Lineup ----
        ".pbd-lineup { display:flex; flex-direction:column; gap:6px; }"
        ".pbd-lineup-head { display:flex; justify-content:space-between; align-items:baseline; "
        "  font-weight:900; color:#f8fafc; padding:0 4px 6px; font-size:.86rem; "
        "  border-bottom:1px solid rgba(148,163,184,.18); margin-bottom:4px; }"
        ".pbd-lineup-status { font-size:.62rem; color:#a5b4fc; font-weight:800; "
        "  letter-spacing:.06em; text-transform:uppercase; }"
        ".pbd-lineup-row { display:flex; align-items:center; gap:10px; "
        "  background: rgba(15,23,42,.55); border:1px solid rgba(99,102,241,.18); "
        "  border-radius:10px; padding:7px 10px; }"
        ".pbd-lineup-slot { width:24px; height:24px; border-radius:6px; "
        "  background: linear-gradient(135deg, #facc15, #f59e0b); color:#1f1407; "
        "  font-weight:900; display:flex; align-items:center; justify-content:center; "
        "  font-size:.78rem; flex:0 0 auto; }"
        ".pbd-lineup-name { flex:1 1 auto; min-width:0; }"
        ".pbd-lineup-nm { font-weight:800; color:#f8fafc; font-size:.88rem; "
        "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
        ".pbd-lineup-pos { font-size:.66rem; color:#94a3b8; margin-top:1px; "
        "  letter-spacing:.04em; }"
        ".pbd-lineup-hand { width:28px; height:28px; border-radius:50%; "
        "  display:flex; align-items:center; justify-content:center; "
        "  font-weight:900; font-size:.74rem; flex:0 0 auto; "
        "  background:#1e293b; color:#cbd5e1; border:1px solid #334155; }"
        ".pbd-hand-L { background: rgba(96,165,250,.18); color:#93c5fd; "
        "  border-color: rgba(96,165,250,.45); }"
        ".pbd-hand-R { background: rgba(248,113,113,.18); color:#fca5a5; "
        "  border-color: rgba(248,113,113,.45); }"
        ".pbd-hand-S { background: rgba(168,85,247,.18); color:#c4b5fd; "
        "  border-color: rgba(168,85,247,.45); }"
        # ---- Splits ----
        ".pbd-splits { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }"
        "@media (min-width: 720px) { .pbd-splits { grid-template-columns: repeat(4, 1fr); } }"
        ".pbd-split-card { background: rgba(15,23,42,.55); border:1px solid rgba(99,102,241,.22); "
        "  border-radius:12px; padding:10px 12px; }"
        ".pbd-split-empty { opacity:.55; }"
        ".pbd-split-title { font-weight:900; color:#facc15; font-size:.74rem; "
        "  letter-spacing:.08em; text-transform:uppercase; margin-bottom:8px; }"
        ".pbd-split-na { color:#94a3b8; font-size:.78rem; }"
        ".pbd-split-grid { display:grid; grid-template-columns: 1fr 1fr; gap:6px; }"
        ".pbd-split-grid > div { background: rgba(255,255,255,.04); border-radius:8px; "
        "  padding:5px 7px; text-align:center; }"
        ".pbd-split-grid span { display:block; font-size:.58rem; font-weight:800; "
        "  letter-spacing:.06em; color:#94a3b8; text-transform:uppercase; }"
        ".pbd-split-grid b { font-size:.82rem; color:#f8fafc; font-variant-numeric: tabular-nums; }"
        # ---- Picker ----
        ".pbd-picker-label { color:#a78bfa; font-weight:800; font-size:.78rem; "
        "  letter-spacing:.06em; text-transform:uppercase; margin: 6px 0 4px; }"
        # ---- Mobile bottom safe-area / scroll tail ----
        # iOS Safari + Chrome on Android both reserve a strip at the bottom
        # of the viewport for browser chrome (URL bar, gesture indicator,
        # home bar). Without explicit padding the last tab's content sits
        # under that strip and looks cut off. We add a fixed tail spacer
        # plus env(safe-area-inset-bottom) so the final row is always
        # reachable, and let the page scroll naturally underneath it.
        ".pbd-tail { height: calc(96px + env(safe-area-inset-bottom, 0px)); "
        "  width: 100%; pointer-events: none; }"
        "@media (max-width: 720px) {"
        "  .pbd-card { margin-bottom: 8px; }"
        "  .pbd-glog, .pbd-arsenal, .pbd-splits, .pbd-lineup {"
        "    padding-bottom: calc(24px + env(safe-area-inset-bottom, 0px));"
        "  }"
        "}"
        "</style>"
    )


def render_pitcher_panel(label, pitcher_name, pitch_hand, p_row, pitch_mix_df=None,
                          *, pitcher_changed=False, original_name=""):
    # Coerce display-bound inputs defensively. Live overlays can deliver
    # ``None`` / NaN / non-string values into any of these slots, and an
    # exception while building the f-string below blanks the whole card.
    import html as _html
    label_s = _safe_str(label, "SP")
    pitcher_name_s = _safe_str(pitcher_name, "TBD")
    pitch_hand_s = format_pitcher_hand(pitch_hand, "?")
    original_name_s = _safe_str(original_name, "")
    pitcher_changed_b = bool(pitcher_changed)
    score, key, verdict = pitcher_vulnerability(p_row)
    # Live StatsAPI season totals (WHIP, ERA, HR/9) — same data path the
    # Slate Pitchers builder uses, so the numbers stay consistent across the
    # two surfaces. Safe-defaults to "—" when no player_id or no live row.
    live = {}
    if p_row is not None:
        try:
            pid = p_row.get("player_id") if hasattr(p_row, "get") else None
            if pid is None and hasattr(p_row, "__getitem__"):
                try: pid = p_row["player_id"]
                except Exception: pid = None
            if pid is not None:
                live = _fetch_pitcher_season_stats(int(pid), 2026) or {}
        except Exception:
            live = {}

    def _g(key_):
        if p_row is None:
            return None
        try:
            v = p_row.get(key_) if hasattr(p_row, "get") else None
        except Exception:
            v = None
        if v is None:
            return None
        try:
            x = float(v)
            return None if x != x else x
        except Exception:
            return None

    k_v       = _g("K%")
    bb_v      = _g("BB%")
    xwoba_v   = _g("xwOBA")
    barrel_v  = _g("Barrel%")
    hardhit_v = _g("HardHit%")
    whip_v    = live.get("WHIP")
    era_v     = live.get("ERA")
    hr9_v     = live.get("HR/9")
    fb_v      = _g("FB%")
    xslg_v    = _g("xSLG")

    # Compose the row dict the HR-target classifier expects. It only reads keys
    # via .get(), so a plain dict is enough.
    hr_input = {
        "HR/9":    hr9_v,
        "Barrel%": barrel_v,
        "HH%":     hardhit_v,
        "FB%":     fb_v,
        "xSLG":    xslg_v,
        "ERA":     era_v,
        "WHIP":    whip_v,
    }
    hr_level, hr_reason = _sp_is_hr_target(hr_input)

    def _fmt(v, n, suffix=""):
        if v is None: return "—"
        try: return f"{float(v):.{n}f}{suffix}"
        except Exception: return "—"

    k       = _fmt(k_v, 1, "%")
    bb      = _fmt(bb_v, 1, "%")
    era_w   = _fmt(xwoba_v, 3)
    barrel  = _fmt(barrel_v, 1, "%")
    hardhit = _fmt(hardhit_v, 1, "%")
    whip    = _fmt(whip_v, 2)
    era     = _fmt(era_v, 2)
    hr9     = _fmt(hr9_v, 2)

    # Tone classes — same thresholds the Slate Pitchers heatmap uses, so a
    # pitcher reads identically in either view. K% is a strength metric (higher
    # = greener); everything else here is allowed/risk (lower = greener).
    k_tone       = _pp_tone(k_v,       18.0, 32.0)
    bb_tone      = _pp_tone(bb_v,      5.0,  11.0,   reverse=True)
    xwoba_tone   = _pp_tone(xwoba_v,   0.260, 0.340, reverse=True)
    barrel_tone  = _pp_tone(barrel_v,  4.0,  12.0,   reverse=True)
    hardhit_tone = _pp_tone(hardhit_v, 32.0, 45.0,   reverse=True)
    whip_tone    = _pp_tone(whip_v,    1.00, 1.50,   reverse=True)
    era_tone     = _pp_tone(era_v,     2.50, 5.50,   reverse=True)
    hr9_tone     = _pp_tone(hr9_v,     0.80, 1.60,   reverse=True)

    color_bg = {"elite": "#0f1f2e", "strong": "#0f1f2e", "ok": "#0c1a2e", "avoid": "#0f1f2e"}[key]
    border = {"elite": "#ef4444", "strong": "#f59e0b", "ok": "#4e6a8a", "avoid": "#10b981"}[key]

    # Live "PITCHING CHANGE DETECTED" badge — surfaced when the live current
    # pitcher differs from the pregame probable starter. Tooltip names the
    # original probable so a bettor knows what the matchup was keyed on
    # before the change.
    change_badge_html = ""
    replaced_html = ""
    if pitcher_changed_b:
        _orig_esc = _html.escape(original_name_s) if original_name_s else ""
        _title_raw = (
            f"Active pitcher differs from probable starter ({original_name_s} pulled)"
            if original_name_s else
            "Active pitcher differs from probable starter"
        )
        _title = _html.escape(_title_raw, quote=True)
        change_badge_html = (
            f'<span title="{_title}" '
            f'style="display:inline-block; padding:3px 9px; border-radius:999px; '
            f'background:rgba(245,158,11,0.15); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); '
            f'font-size:.7rem; font-weight:900; letter-spacing:.04em; '
            f'margin-left:8px; text-transform:uppercase;">⚠️ Pitching Change</span>'
        )
        if _orig_esc:
            replaced_html = (
                f'<div style="font-size:.72rem; color:#94a3b8; '
                f'font-weight:700; margin-top:2px;">replaced {_orig_esc}</div>'
            )

    # HR-target badge — bad (hard) / warn (soft). Same iconography as the
    # Slate Pitchers compact card so the signal is recognizable across tabs.
    hr_badge_html = ""
    if hr_level == "hard":
        hr_badge_html = (
            f'<span title="HR-target pitcher: {hr_reason}" '
            f'style="display:inline-block; padding:2px 7px; border-radius:3px; '
            f'background:rgba(239,68,68,0.15); color:#ef4444; border:1px solid rgba(239,68,68,0.3); '
            f'font-size:.62rem; font-weight:700; letter-spacing:.06em; '
            f'margin-left:6px; text-transform:uppercase;">HR Target</span>'
        )
    elif hr_level == "soft":
        hr_badge_html = (
            f'<span title="HR-target pitcher: {hr_reason}" '
            f'style="display:inline-block; padding:2px 7px; border-radius:3px; '
            f'background:rgba(245,158,11,0.12); color:#f59e0b; border:1px solid rgba(245,158,11,0.25); '
            f'font-size:.62rem; font-weight:700; letter-spacing:.06em; '
            f'margin-left:6px; text-transform:uppercase;">HR Watch</span>'
        )

    tone_color = {"good": "#4ade80", "mid": "#fbbf24", "bad": "#f87171", "": "#b0c8e4"}
    def _stat_block(lbl, val, tone):
        c = tone_color.get(tone, "#b0c8e4")
        return (
            f'<div>'
            f'<div style="font-size:0.58rem; color:#7dd3fc; text-transform:uppercase; '
            f'font-weight:800; letter-spacing:0.08em;">{lbl}</div>'
            f'<div style="font-weight:800; font-size:0.95rem; color:{c}; font-family:\'DM Mono\',monospace;">{val}</div>'
            f'</div>'
        )

    mix_html = render_pitch_mix_block(pitch_mix_df, surface_bg="#0a1628") if pitch_mix_df is not None else ""
    _tier_key = ('elite' if key == 'elite'
                 else 'strong' if key == 'strong'
                 else 'ok' if key == 'ok' else 'avoid')
    _label_html = _html.escape(label_s)
    _name_html = _html.escape(pitcher_name_s)
    _hand_html = _html.escape(pitch_hand_s)
    _verdict_html = _html.escape(_safe_str(verdict, ""))
    _score_html = _html.escape(_safe_str(score, "—"))
    panel_html = (
        f'<div style="background:{color_bg}; border:1px solid rgba(255,255,255,0.08); '
        f'border-left:4px solid {border}; border-radius:6px; '
        f'padding:10px 12px;">'
        f'<div style="display:flex; justify-content:space-between; align-items:flex-start;">'
        f'<div>'
        f'<div style="font-size:0.6rem; color:#7dd3fc; text-transform:uppercase; '
        f'letter-spacing:0.1em; font-weight:800;">{_label_html}</div>'
        f'<div style="font-size:0.95rem; font-weight:900; color:#ffffff; margin-top:2px; '
        f'text-shadow:0 1px 3px rgba(0,0,0,0.5);">'
        f'{_name_html} '
        f'<span style="color:#94a3b8; font-weight:600; font-size:0.82rem;">({_hand_html})</span>'
        f'{change_badge_html}{hr_badge_html}</div>'
        f'{replaced_html}'
        f'</div>'
        f'<div style="text-align:right;">'
        f'<div style="font-size:1.4rem; font-weight:800; color:#eef4ff; line-height:1; '
        f'font-family:\'DM Mono\',monospace;">{_score_html}</div>'
        f'<span class="tier tier-{_tier_key}">{_verdict_html}</span>'
        f'</div>'
        f'</div>'
        f'<div style="display:grid; grid-template-columns: repeat(4, 1fr); '
        f'gap: 6px 10px; margin-top:10px;">'
        f'{_stat_block("WHIP",    whip,    whip_tone)}'
        f'{_stat_block("HR/9",    hr9,     hr9_tone)}'
        f'{_stat_block("ERA",     era,     era_tone)}'
        f'{_stat_block("xwOBA",   era_w,   xwoba_tone)}'
        f'{_stat_block("K%",      k,       k_tone)}'
        f'{_stat_block("BB%",     bb,      bb_tone)}'
        f'{_stat_block("Barrel%", barrel,  barrel_tone)}'
        f'{_stat_block("HardHit%",hardhit, hardhit_tone)}'
        f'</div>'
        f'{mix_html}'
        f'</div>'
    )
    try:
        st.markdown(panel_html, unsafe_allow_html=True)
    except Exception as _exc:
        # Last-ditch fallback so a render error in one card can't blank the
        # entire Pitcher Vulnerability section. Surface a minimal text card
        # so the user still sees the pitcher and a hint that data is degraded.
        try:
            st.warning(f"Pitcher card render fallback: {label_s} — {pitcher_name_s}")
        except Exception:
            pass

# ===========================================================================
# MAIN
# ===========================================================================
with st.spinner("Loading Baseball Savant data from GitHub..."):
    csvs = load_all_csvs()
batters_df = standardize_columns(csvs.get("batters", pd.DataFrame()))
pitchers_df = standardize_columns(csvs.get("pitchers", pd.DataFrame()))
pitcher_stats_df = standardize_columns(csvs.get("pitcher_stats", pd.DataFrame()))

# Backfill: append non-qualified hitters (rookies, call-ups, bench bats) from
# the min=10-PA leaderboard so daily lineups don't render with empty Statcast
# cells. We only add player_ids that are NOT already in the qualified table —
# this guarantees the qualified Statcast values for established hitters are
# never overwritten by a low-PA proxy. Joined by player_id (ID-first), with
# row-count assertions so a bad backfill cannot accidentally explode the table.
_batters_all_raw = csvs.get("batters_all", pd.DataFrame())
if (
    not batters_df.empty
    and _batters_all_raw is not None
    and not _batters_all_raw.empty
):
    try:
        _batters_all = standardize_columns(_batters_all_raw)
        if (
            "player_id" in batters_df.columns
            and "player_id" in _batters_all.columns
        ):
            _q_ids = pd.to_numeric(batters_df["player_id"], errors="coerce").dropna().astype("Int64")
            _q_idset = set(int(x) for x in _q_ids.tolist())
            _all_pid = pd.to_numeric(_batters_all["player_id"], errors="coerce").astype("Int64")
            _batters_all = _batters_all.assign(player_id=_all_pid)
            _missing = _batters_all[
                _all_pid.notna() & ~_all_pid.isin(_q_idset)
            ].drop_duplicates("player_id")
            _before = len(batters_df)
            if not _missing.empty:
                # Align columns so concat doesn't introduce duplicates / drops.
                _missing = _missing.reindex(columns=batters_df.columns, fill_value=pd.NA)
                batters_df = pd.concat([batters_df, _missing], ignore_index=True)
            _after = len(batters_df)
            # Sanity: cap at ~2,500 hitters total (well above the ~1,400 active
            # major-leaguers in any given season). Anything larger means the
            # CSV has been polluted with duplicate / minor-league rows and we
            # should revert to the qualified-only frame.
            if _after > 2500:
                batters_df = batters_df.iloc[:_before].reset_index(drop=True)
    except Exception:
        # Backfill must never break startup. The qualified leaderboard alone
        # is still a fully usable batters_df.
        pass

# Cell-level prior-season fallback: fill *missing* (NaN) Statcast values on
# current-season rows from each player's prior-season row, joined by
# player_id. This catches main-team batters whose 2026 sample is too small
# for some metrics (e.g. a player with PA but no xwOBA yet because they
# haven't put enough balls in play). NEVER overwrites an existing value —
# only fills gaps. Out-of-band rows (player_ids unique to prior season,
# i.e. retired players) are appended at the end so they're available if a
# lineup unexpectedly references them.
_batters_prev_raw = csvs.get("batters_prev", pd.DataFrame())
if (
    not batters_df.empty
    and _batters_prev_raw is not None
    and not _batters_prev_raw.empty
):
    try:
        _batters_prev = standardize_columns(_batters_prev_raw)
        if (
            "player_id" in batters_df.columns
            and "player_id" in _batters_prev.columns
        ):
            batters_df["player_id"] = pd.to_numeric(
                batters_df["player_id"], errors="coerce"
            ).astype("Int64")
            _batters_prev["player_id"] = pd.to_numeric(
                _batters_prev["player_id"], errors="coerce"
            ).astype("Int64")
            _batters_prev = _batters_prev.dropna(subset=["player_id"]).drop_duplicates(
                "player_id"
            )
            # Align prev columns to batters_df shape; keep extras Untouched
            _prev_aligned = _batters_prev.set_index("player_id").reindex(
                columns=[c for c in batters_df.columns if c != "player_id"]
            )
            _cur_indexed = batters_df.set_index("player_id")
            # Fill only NaN cells in current with prior-season values. combine_first
            # would prefer the *left* table where present, which is what we want.
            _cur_filled = _cur_indexed.combine_first(_prev_aligned)
            # Preserve original row order
            _cur_filled = _cur_filled.reindex(_cur_indexed.index)
            batters_df = _cur_filled.reset_index()
            # Append prior-season players entirely missing from current season
            _cur_idset = set(int(x) for x in _cur_indexed.index.dropna().tolist())
            _prev_only = _batters_prev[
                ~_batters_prev["player_id"].isin(_cur_idset)
            ]
            if not _prev_only.empty:
                _before = len(batters_df)
                _prev_only = _prev_only.reindex(
                    columns=batters_df.columns, fill_value=pd.NA
                )
                batters_df = pd.concat([batters_df, _prev_only], ignore_index=True)
                _after = len(batters_df)
                if _after > _before * 2 + 200:
                    batters_df = batters_df.iloc[:_before].reset_index(drop=True)
    except Exception:
        # Prior-season fallback must never break startup.
        pass

# Merge real per-batter bat-tracking data (avg_bat_speed in mph) into
# batters_df, keyed on player_id. The custom batter leaderboard does NOT
# expose bat speed, so without this merge the lineup tables fall back to a
# constant placeholder. If the bat-tracking CSV is missing or empty, the
# Bat Speed column simply renders as "—" downstream rather than a fake value.
def _prep_bat_tracking_frame(_raw):
    """Return a (player_id, BatSpeed, SwingsComp, BIP) frame from a raw
    bat-tracking CSV, or an empty DataFrame if the columns aren't present."""
    if _raw is None or _raw.empty:
        return pd.DataFrame()
    _bt = _raw.copy()
    _bt.columns = [str(c).strip() for c in _bt.columns]
    if "id" not in _bt.columns or "avg_bat_speed" not in _bt.columns:
        return pd.DataFrame()
    keep = ["id", "avg_bat_speed"]
    rename = {"id": "player_id", "avg_bat_speed": "BatSpeed"}
    if "swings_competitive" in _bt.columns:
        keep.append("swings_competitive")
        rename["swings_competitive"] = "SwingsComp"
    if "batted_ball_events" in _bt.columns:
        keep.append("batted_ball_events")
        rename["batted_ball_events"] = "BIP"
    _bt = _bt[keep].rename(columns=rename)
    _bt["player_id"] = pd.to_numeric(_bt["player_id"], errors="coerce").astype("Int64")
    for _c in ("BatSpeed", "SwingsComp", "BIP"):
        if _c in _bt.columns:
            _bt[_c] = pd.to_numeric(_bt[_c], errors="coerce")
    return _bt.dropna(subset=["player_id"]).drop_duplicates("player_id")

_bat_tracking_df = csvs.get("bat_tracking", pd.DataFrame())
_bat_tracking_prev_df = csvs.get("bat_tracking_prev", pd.DataFrame())
if (
    not batters_df.empty
    and "player_id" in batters_df.columns
    and (
        (_bat_tracking_df is not None and not _bat_tracking_df.empty)
        or (_bat_tracking_prev_df is not None and not _bat_tracking_prev_df.empty)
    )
):
    _bt_cur = _prep_bat_tracking_frame(_bat_tracking_df)
    _bt_prev = _prep_bat_tracking_frame(_bat_tracking_prev_df)
    # Cell-level fill: prefer current-season values, fall back to prior-season
    # only for cells the current frame doesn't cover. This ensures every
    # active hitter that swung a bat in either season has Pitches/BIP filled.
    if not _bt_cur.empty and not _bt_prev.empty:
        _bt = (
            _bt_cur.set_index("player_id")
            .combine_first(_bt_prev.set_index("player_id"))
            .reset_index()
        )
    elif not _bt_cur.empty:
        _bt = _bt_cur
    else:
        _bt = _bt_prev
    if not _bt.empty:
        batters_df["player_id"] = pd.to_numeric(
            batters_df["player_id"], errors="coerce"
        ).astype("Int64")
        # Drop any pre-existing merge cols so the merge cleanly populates them.
        batters_df = batters_df.drop(
            columns=[c for c in ("BatSpeed", "SwingsComp", "BIP") if c in batters_df.columns]
        )
        batters_df = batters_df.merge(
            _bt.drop_duplicates("player_id"), on="player_id", how="left"
        )

# Pitch-arsenal leaderboards (per pitch type) — fetched directly from
# Baseball Savant. Cached for an hour. Used by Top 3 Hitters "Crushes" line
# and by the Pitcher Vulnerability panels' pitch-mix mini-table.
arsenal_pitcher_df = load_pitch_arsenal("pitcher")
arsenal_batter_df  = load_pitch_arsenal("batter")

# Render brand bar FIRST so the date picker isn't pinned to Streamlit's top chrome.
# Use a placeholder count, then re-render after schedule loads.
_brand_bar_slot = st.empty()
_loading_logo = (
    f'<img class="brand-logo" src="{LOGO_URI}" alt="MrBets850" />'
    if LOGO_URI else '<span class="brand-logo" style="display:flex;align-items:center;'
                     'justify-content:center;font-size:1.6rem;">👑</span>'
)
_brand_bar_slot.markdown(
    f'<div class="brand-bar"><div class="brand-left">{_loading_logo}'
    '<div><div class="brand-tag">⚾ THE MLB EDGE</div>'
    '<div class="brand-name">MLB Edge — Matchup Board</div></div></div>'
    '<div class="brand-meta"><div class="big">Loading slate…</div></div></div>',
    unsafe_allow_html=True,
)

# top controls - date picker + refresh button + today button. Visible labels.
st.markdown(
    '<style>'
    '.toolbar-section-title { font-size: 0.78rem; color:#475569; font-weight:800; '
    '  letter-spacing:.06em; text-transform:uppercase; margin: 8px 0 4px 2px; }'
    '.toolbar-spacer-label { font-size: 0.78rem; color:transparent; margin-bottom: 0; user-select:none; }'
    '</style>'
    '<div class="toolbar-section-title">Slate Controls</div>',
    unsafe_allow_html=True,
)
# Compute the rollover-aware default date once per run. If the current
# CT slate has been final for 45+ minutes (and tomorrow has games),
# this advances past today; otherwise it stays on today. The user's
# manual picker selection (stored in session_state) always wins — we
# only use this value when seeding the widget or honoring the "Today"
# button.
_slate_default = auto_default_slate_date()

# Apply any pending "Today" reset BEFORE the date_input widget is instantiated.
# Streamlit forbids writing to st.session_state[<widget_key>] after the widget
# has been created, so we use a one-shot flag and rerun pattern. The reset
# target is the rollover-aware default, so the Today button does the right
# thing late-night (i.e. snaps to tomorrow's slate once the grace period has
# elapsed) instead of taking the user back to a finished slate.
if st.session_state.pop("_reset_to_today", False):
    st.session_state["slate_date_picker"] = _slate_default.slate_date
    st.session_state["_selected_idx"] = 0

top_cols = st.columns([2.2, 1, 1])
with top_cols[0]:
    selected_date = st.date_input(
        "📅 Slate date",
        value=_slate_default.slate_date,
        key="slate_date_picker",
    )
with top_cols[1]:
    st.markdown('<div class="toolbar-spacer-label">.</div>', unsafe_allow_html=True)
    if st.button("🔄 Refresh data", width='stretch', key="refresh_btn"):
        st.cache_data.clear()
        st.rerun()
with top_cols[2]:
    st.markdown('<div class="toolbar-spacer-label">.</div>', unsafe_allow_html=True)
    if st.button("📆 Today", width='stretch', key="today_btn"):
        # Defer the actual session_state write to the next run, BEFORE the
        # widget is recreated, to avoid StreamlitAPIException.
        st.session_state["_reset_to_today"] = True
        try:
            _t = st.query_params.get("token", ""); st.query_params.clear()
            if _t: st.query_params["token"] = _t
        except Exception:
            pass
        st.rerun()

# Small caption when the auto-rollover has moved the default off of
# the literal CT date — gives the user a quick "you're on tomorrow's
# slate because last night's games are done" cue without cluttering
# the toolbar. Only shown when the user hasn't picked a different
# date manually.
if (
    _slate_default.rolled_over
    and selected_date == _slate_default.slate_date
    and selected_date != today_ct()
):
    st.caption(
        "🌙 Auto-rolled to the next slate · prior slate has been final for "
        "45+ minutes. Use the date picker to go back."
    )

try:
    schedule_df = get_schedule(selected_date)
except Exception as e:
    _brand_bar_slot.empty()
    render_brand_bar(0)
    st.error(f"Schedule load failed: {e}")
    st.stop()

# Update the brand bar with the actual slate count
_brand_bar_slot.empty()
render_brand_bar(len(schedule_df))

if batters_df.empty and pitchers_df.empty:
    st.error(f"No CSV data could be loaded. Check https://github.com/{GITHUB_USER}/{GITHUB_REPO}.")

if schedule_df.empty:
    st.warning("No games found for this date.")
    st.stop()

# ---------------------------------------------------------------------------
# Data-freshness banner (only renders when at least one core source is
# stale, errored, or fell back). Lets users know up front that some signals
# may be using older data — no silent staleness.
# ---------------------------------------------------------------------------
def _render_freshness_banner():
    bad = []
    for key, info in DATA_SOURCES.items():
        s = info.get("status", "")
        if s in ("stale", "error", "fallback"):
            bad.append((key, info))
        elif s == "live" and source_is_stale(key):
            bad.append((key, {**info, "status": "stale"}))
    if not bad:
        return
    items_html = []
    for _, info in bad:
        bg, fg, txt = status_pill(info.get("status", ""))
        items_html.append(
            f'<span style="background:{bg};color:{fg};border-radius:999px;'
            f'padding:2px 9px;font-size:.72rem;font-weight:800;letter-spacing:.04em;'
            f'margin-right:6px;">{txt}</span>'
            f'<b>{info.get("label", "")}</b> '
            f'<span style="color:#475569;">— {info.get("detail", "") or "see Data status panel"}</span>'
        )
    st.markdown(
        '<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:12px;'
        'padding:10px 14px;margin:6px 0 12px 0;color:#78350f;font-size:.92rem;'
        'line-height:1.45;">'
        '<div style="font-weight:900;margin-bottom:4px;">⚠️ Data freshness notice</div>'
        + "<br/>".join(items_html) +
        '<div style="margin-top:6px;color:#92400e;font-size:.82rem;">'
        'Open the <b>📊 Data status &amp; sources</b> panel at the bottom for full '
        'per-source timestamps. Some prop scores below may use these signals.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

_render_freshness_banner()

# ===========================================================================
# 🔥 Hero strip: Top 15 — 2+ RBI Plays Tonight
# Always rendered at the very top of the slate. A compact, scrollable
# horizontal carousel of the highest-scoring 2+ RBI candidates across the
# entire slate — so users see the night's best RBI plays before drilling
# into a specific game or view. Falls back gracefully when lineups aren't
# posted yet.
# ===========================================================================
def _render_rbi_hero_strip():
    if batters_df is None or batters_df.empty:
        return
    # Hero strip is a *recommendation* surface, so it must use the same
    # pre-game eligibility rule as the parlay generators — once a game has
    # started its lineup is locked and we can't recommend props from it.
    try:
        _hero_sched = filter_pre_game_schedule(schedule_df)
    except Exception:
        _hero_sched = schedule_df
    if _hero_sched is None or _hero_sched.empty:
        return
    try:
        _slate_iso = pd.to_datetime(selected_date).strftime("%Y-%m-%d")
    except Exception:
        _slate_iso = ""
    try:
        _hero_df = build_targets_table(_hero_sched, batters_df, pitchers_df,
                                        mode="rbi2", slate_date_iso=_slate_iso)
    except Exception:
        return
    if _hero_df is None or _hero_df.empty:
        return
    # Light pre-filter so the strip surfaces realistic 2+ RBI plays:
    # spots 1-6, with at least some power profile.
    _hero_df = _hero_df[_hero_df["Spot"] <= 6]
    _hero_df = _hero_df[_hero_df["ISO"].fillna(-1) >= 0.130]
    _hero_df = _hero_df.head(15).reset_index(drop=True)
    if _hero_df.empty:
        return

    css = (
        "<style>"
        "@keyframes rbiScan { "
        "  0%   { transform: translateX(-100%); opacity:0; } "
        "  20%  { opacity:1; } 80% { opacity:1; } "
        "  100% { transform: translateX(200%); opacity:0; } }"
        ".rbi-hero { margin: 4px 0 16px 0; padding: 16px 16px 12px; "
        "  background: linear-gradient(130deg, #3b0000 0%, #7f1d1d 35%, #b91c1c 70%, #dc2626 100%); "
        "  border-radius: 18px; border: 1px solid rgba(251,191,36,.50); "
        "  box-shadow: 0 8px 28px rgba(127,29,29,.45), 0 0 0 1px rgba(239,68,68,.15); "
        "  position: relative; overflow: hidden; }"
        ".rbi-hero::before { content:''; position:absolute; inset:0; pointer-events:none; "
        "  background-image: radial-gradient(circle, rgba(251,191,36,.04) 1px, transparent 1px); "
        "  background-size: 16px 16px; }"
        ".rbi-hero::after { content:''; position:absolute; top:0; bottom:0; width:35%; "
        "  pointer-events:none; "
        "  background: linear-gradient(90deg, transparent, rgba(255,255,255,.04), transparent); "
        "  animation: rbiScan 5s ease-in-out infinite 1s; }"
        ".rbi-hero-title { color:#fde68a; font-weight:900; font-size:1.15rem; "
        "  letter-spacing:.02em; margin: 0 0 4px 0; position: relative; z-index:1; "
        "  text-shadow: 0 0 16px rgba(251,191,36,.35), 0 1px 2px rgba(0,0,0,.5); }"
        ".rbi-hero-sub { color:#fecaca; font-size:.82rem; margin: 0 0 12px 0; "
        "  position: relative; z-index:1; }"
        ".rbi-hero-rail { display:flex; gap:10px; overflow-x:auto; "
        "  padding: 4px 2px 8px; scroll-snap-type: x mandatory; "
        "  -webkit-overflow-scrolling: touch; position: relative; z-index:1; }"
        ".rbi-hero-rail::-webkit-scrollbar { height:5px; }"
        ".rbi-hero-rail::-webkit-scrollbar-thumb { background:rgba(251,191,36,.50); border-radius:3px; }"
        ".rbi-card { flex: 0 0 auto; min-width: 180px; max-width: 200px; "
        "  background:linear-gradient(180deg,#111827 0%,#0b1220 100%); "
        "  border-radius:14px; padding:11px 13px; "
        "  scroll-snap-align: start; "
        "  box-shadow: 0 4px 14px rgba(0,0,0,.45); "
        "  border: 1px solid rgba(255,255,255,.08); "
        "  transition: transform .15s, box-shadow .15s; }"
        ".rbi-card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,.55); }"
        ".rbi-card-rank { display:inline-block; background: linear-gradient(135deg,#3b1f6b,#1e0b4a); "
        "  color:#facc15; "
        "  font-weight:900; font-size:.70rem; padding:2px 9px; border-radius:999px; "
        "  letter-spacing:.05em; }"
        ".rbi-card-score { float:right; font-weight:900; font-size:1.08rem; "
        "  color:#f87171; font-variant-numeric:tabular-nums; }"
        ".rbi-card-name { font-weight:900; color:#ffffff; font-size:.96rem; "
        "  margin-top:7px; line-height:1.15; text-shadow:0 1px 3px rgba(0,0,0,.5); }"
        ".rbi-card-meta { color:#7dd3fc; font-size:.73rem; margin-top:2px; font-weight:600; }"
        ".rbi-card-game { color:#94a3b8; font-size:.73rem; margin-top:7px; "
        "  border-top:1px solid rgba(255,255,255,.07); padding-top:6px; font-weight:600; }"
        ".rbi-card-stats { display:flex; gap:8px; margin-top:6px; "
        "  font-variant-numeric: tabular-nums; font-size:.72rem; color:#e2e8f0; font-weight:700; }"
        ".rbi-card-stats span b { color:#f87171; }"
        "</style>"
    )

    cards_html = []
    for i, r in _hero_df.iterrows():
        iso = r.get("ISO")
        bar = r.get("Barrel%")
        iso_s  = f"{iso:.3f}" if iso is not None and not pd.isna(iso) else "—"
        bar_s  = f"{bar:.1f}%" if bar is not None and not pd.isna(bar) else "—"
        cards_html.append(
            '<div class="rbi-card">'
            f'<span class="rbi-card-rank">#{i+1}</span>'
            f'<span class="rbi-card-score">{r.get("Score",0):.0f}</span>'
            f'<div class="rbi-card-name">{r.get("Hitter","")}</div>'
            f'<div class="rbi-card-meta">{r.get("Team","")} · Bat {format_batter_stance(r.get("Bat",""))} · Spot {r.get("Spot","")}</div>'
            f'<div class="rbi-card-game">{r.get("Game","")}<br/>vs {r.get("Opp SP","")}</div>'
            f'<div class="rbi-card-stats"><span>ISO <b>{iso_s}</b></span>'
            f'<span>Barrel <b>{bar_s}</b></span></div>'
            '</div>'
        )

    html = (
        css +
        '<div class="rbi-hero">'
        '<div class="rbi-hero-title">🔥 Top 15 — 2+ RBI Plays Tonight</div>'
        '<div class="rbi-hero-sub">Heart-of-order power bats with the best '
        'RBI matchup<span class="mobile-hide-swipe"> — swipe to see all 15</span>. Refreshes by slate date and blends '
        "today's opponent / probable pitcher / lineup spot / park &amp; weather "
        'with each hitter\'s L10 form when available. Started games are '
        'excluded. Open the <b>⚾ RBI Edge Model</b> tab for filters &amp; full table.</div>'
        '<div class="rbi-hero-rail">' + "".join(cards_html) + '</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

# NOTE: The Top 15 — 2+ RBI hero strip is intentionally not rendered at the
# top of the page. Users still get the full 2+ RBI experience via the
# "🔥 2+ RBI" tab in the Apps & Generators pill row below. Keeping the
# function definition above so the underlying logic stays available if we
# want to surface it elsewhere later.

# ===========================================================================
# Top-level tab switcher: "⚾ Games" vs "🥎 Slate Pitchers"
# Implemented as a styled radio so we can toggle large sections of the page
# without re-indenting the entire game flow.
# ===========================================================================
_TOP_VIEW_OPTIONS = [
    "⚾ Games",
    "🔥 Hot Batters",
    "🧊 Cold Batters",
    "💣 HR Milestones",
    "☀️🌙 Day vs Night HR",
    "🥎 Pitcher Breakdown",
    "🎯 Pitcher Weak Spots",
    "💎 HR Sleepers",
    "📊 Total Bases 1.5+",
    "🎯 HRR 1.5+",
    "⚾ RBI Edge Model",
    "🤖 AI HR Parlay",
    "👑 HR Round Robin",
    "🎯 AI K Generator",
    "🌬️ Ballpark Weather",
    "🥎 AI 1+ Hits Parlay",
    "🏟️ Live HR Tracker",
]

st.markdown(
    "<style>"
    # ---- Top-level view tabs: bold, mobile-friendly pill carousel ----
    # Pure HTML anchor pills — no Streamlit radio internals to fight.
    "@keyframes tabRailGlow { "
    "  0%, 100% { box-shadow: 0 2px 12px rgba(20,5,50,.30), inset 0 1px 0 rgba(250,204,21,.08); } "
    "  50%       { box-shadow: 0 4px 20px rgba(20,5,50,.45), inset 0 1px 0 rgba(250,204,21,.16); } "
    "}"
    ".top-tab-row { margin: 8px 0 14px 0; padding: 10px; "
    "  background: linear-gradient(180deg, #0c0420 0%, #14062e 60%, #1a0840 100%); "
    "  border-radius: 18px; border: 1px solid rgba(250,204,21,.28); "
    "  animation: tabRailGlow 5s ease-in-out infinite; "
    "  position: relative; overflow: hidden; }"
    # subtle dot grid in the nav rail background
    ".top-tab-row::before { content:''; position:absolute; inset:0; pointer-events:none; "
    "  background-image: radial-gradient(circle, rgba(250,204,21,.035) 1px, transparent 1px); "
    "  background-size: 16px 16px; }"
    ".top-tab-strip { display:flex; gap:8px; flex-wrap:nowrap; "
    "  justify-content:flex-start; overflow-x:auto; overflow-y:hidden; "
    "  -webkit-overflow-scrolling:touch; scroll-snap-type:x proximity; "
    "  scrollbar-width:thin; scrollbar-color:rgba(250,204,21,.35) transparent; "
    "  padding-bottom:4px; position: relative; z-index: 1; }"
    ".top-tab-strip::-webkit-scrollbar { height:4px; }"
    ".top-tab-strip::-webkit-scrollbar-thumb { background:rgba(250,204,21,.35); border-radius:2px; }"
    ".top-tab-strip::-webkit-scrollbar-track { background:transparent; }"
    ".top-tab-pill { scroll-snap-align:start; flex:0 0 auto; white-space:nowrap; "
    "  background:rgba(255,255,255,.07); padding:11px 20px; min-height:46px; min-width:124px; "
    "  border-radius:12px; border:1px solid rgba(255,255,255,.10); cursor:pointer; "
    "  font-weight:700; font-size:.98rem; color:#cbd5e1; "
    "  text-decoration:none; line-height:1.2; letter-spacing:.01em; "
    "  transition:all .18s cubic-bezier(.22,1,.36,1); "
    "  display:inline-flex; align-items:center; justify-content:center; }"
    ".apps-gen-header { display:flex; align-items:baseline; justify-content:space-between; "
    "  margin: 6px 4px 6px; padding: 0 2px; gap: 10px; flex-wrap:wrap; }"
    ".apps-gen-title { font-weight:900; font-size:1.18rem; color:#facc15; "
    "  letter-spacing:.01em; text-shadow:0 0 16px rgba(250,204,21,.30), 0 1px 0 rgba(0,0,0,.6); }"
    ".apps-gen-sub { font-size:.78rem; color:#8795b8; font-weight:600; }"
    ".top-tab-pill:hover { border-color:rgba(124,58,237,.45); transform:translateY(-2px); "
    "  background:rgba(124,58,237,.15); "
    "  box-shadow:0 4px 14px rgba(124,58,237,.20); color:#e2e8f0; "
    "  text-decoration:none; }"
    ".top-tab-pill.active { "
    "  background:linear-gradient(135deg, #1e0b4a 0%, #3b1f6b 55%, #5b21a8 100%); "
    "  color:#facc15; border-color:rgba(250,204,21,.55); "
    "  box-shadow:0 0 0 1px rgba(250,204,21,.20), 0 6px 20px rgba(20,5,50,.50), "
    "    0 0 12px rgba(250,204,21,.12); "
    "  transform:translateY(-2px); text-decoration:none; font-weight:800; }"
    ".top-tab-pill.active:hover { color:#facc15; }"
    # Mobile: replace horizontal pill carousel with a visible grid of
    # square tiles so every category is in plain sight without
    # horizontal scrolling. Desktop layout (above) is unchanged.
    "@media (max-width: 640px) { "
    "  .top-tab-row { padding:10px; border-radius:16px; } "
    "  .top-tab-strip { display:grid !important; "
    "    grid-template-columns: repeat(2, minmax(0, 1fr)); "
    "    gap:7px; overflow:visible !important; "
    "    flex-wrap:wrap; padding-bottom:0; } "
    "  .top-tab-strip::-webkit-scrollbar { display:none; } "
    "  .top-tab-pill { width:100%; min-width:0; flex:1 1 auto; "
    "    padding:14px 10px; min-height:62px; font-size:.94rem; "
    "    white-space:normal; line-height:1.2; text-align:center; "
    "    border-radius:12px; } "
    "  .apps-gen-title { font-size:1.08rem; } "
    "  .apps-gen-sub { display:none; } "
    "}"
    "@media (max-width: 380px) { "
    "  .top-tab-pill { padding:12px 8px; min-height:58px; font-size:.88rem; } "
    "}"
    ".sp-legend { color:#64748b; font-size:.78rem; margin: 4px 0 12px 0; }"
    ".sp-legend code { background:rgba(255,255,255,.07); padding: 1px 6px; border-radius:6px; "
    "  font-family: inherit; font-weight:700; color:#94a3b8; }"
    "</style>",
    unsafe_allow_html=True,
)

# ---- Deep-link handler: ?view=games&g=<idx> AND ?top_view=<idx> ----
# We drive selection through a plain session_state string (NOT a widget key,
# so it can be freely written at any time).
try:
    _qp = st.query_params
    _qp_view = _qp.get("view", None)
    _qp_top_view = _qp.get("top_view", None)
except Exception:
    _qp_view = None
    _qp_top_view = None

# Pill click → ?top_view=<idx>. Apply, then strip the param so refreshing
# doesn't re-trigger and so the URL stays clean.
if _qp_top_view is not None:
    try:
        _idx = int(_qp_top_view)
        if 0 <= _idx < len(_TOP_VIEW_OPTIONS):
            st.session_state["top_view_tab"] = _TOP_VIEW_OPTIONS[_idx]
    except (TypeError, ValueError):
        pass
    try:
        # Remove just the top_view param, leave others (e.g. g=) intact.
        try:
            del st.query_params["top_view"]
        except Exception:
            # Older Streamlit query_params API — fall back to dict assignment.
            _remaining = {k: v for k, v in dict(_qp).items() if k != "top_view"}
            _t2 = _remaining.pop("token", ""); st.query_params.clear()
            if _t2: st.query_params["token"] = _t2
            for _k, _v in _remaining.items():
                st.query_params[_k] = _v
    except Exception:
        pass

# Existing ?view=games deep-link continues to work.
if _qp_view == "games":
    if "top_view_tab" not in st.session_state:
        st.session_state["top_view_tab"] = "⚾ Games"
    elif st.session_state.get("top_view_tab") != "⚾ Games":
        if not st.session_state.get("_deep_link_consumed"):
            st.session_state["top_view_tab"] = "⚾ Games"
            st.session_state["_deep_link_consumed"] = True
if _qp_view is None:
    st.session_state.pop("_deep_link_consumed", None)

# Default selection on first load.
if "top_view_tab" not in st.session_state:
    st.session_state["top_view_tab"] = _TOP_VIEW_OPTIONS[0]

_view = st.session_state["top_view_tab"]
if _view not in _TOP_VIEW_OPTIONS:
    _view = _TOP_VIEW_OPTIONS[0]
    st.session_state["top_view_tab"] = _view

# Render the pill carousel as anchor links. Clicking a pill navigates to
# ?top_view=<idx> (preserving any other query params like &g=) which the
# handler above translates into the new selection on the next run.
def _build_top_tab_href(idx: int) -> str:
    try:
        _other = {k: v for k, v in dict(st.query_params).items()
                  if k not in ("top_view", "view")}
    except Exception:
        _other = {}
    _parts = [f"top_view={idx}"]
    for _k, _v in _other.items():
        if isinstance(_v, list):
            for _item in _v:
                _parts.append(f"{_k}={_item}")
        else:
            _parts.append(f"{_k}={_v}")
    return "?" + "&".join(_parts)

# ===========================================================================
# ⚾ MLB SLATE TICKER — scrolling scoreboard for today's games
# Shows all games on the current slate with team logos, scores, and status.
# Live games pulse with a red dot; final games show an "F" badge; pregame
# games show the scheduled start time. The strip auto-scrolls horizontally
# so all games are visible even on narrow mobile screens.
# ===========================================================================
def _render_slate_ticker(sched_df):
    """Render a premium auto-scrolling MLB scoreboard ticker."""
    if sched_df is None or sched_df.empty:
        return
    cards = []
    _n_live = 0
    _n_final = 0
    _n_preview = 0
    for _, g in sched_df.iterrows():
        try:
            snap = _live_state_snapshot(g.get("game_pk"))
        except Exception:
            snap = None
        phase = _game_phase(g.get("status", ""), snap)
        away_ab = g.get("away_abbr", "")
        home_ab = g.get("home_abbr", "")
        away_id = g.get("away_id", 0)
        home_id = g.get("home_id", 0)
        away_logo = logo_url(away_id) if away_id else ""
        home_logo = logo_url(home_id) if home_id else ""
        away_score = (snap or {}).get("away_score")
        home_score = (snap or {}).get("home_score")

        if phase == "live":
            _n_live += 1
            inn = _inning_label(snap)
            status_html = (
                '<div class="stk-status live">'
                '<span class="stk-live-dot"></span>'
                f'<span>{inn if inn else "Live"}</span>'
                '</div>'
            )
            away_s = "—" if away_score is None else str(away_score)
            home_s = "—" if home_score is None else str(home_score)
            score_html = (
                f'<div class="stk-scores">'
                f'<span class="stk-score">{away_s}</span>'
                f'<span class="stk-score-sep">-</span>'
                f'<span class="stk-score">{home_s}</span>'
                f'</div>'
            )
        elif phase == "final":
            _n_final += 1
            status_html = '<div class="stk-status final"><span>Final</span></div>'
            away_s = "—" if away_score is None else str(away_score)
            home_s = "—" if home_score is None else str(home_score)
            score_html = (
                f'<div class="stk-scores">'
                f'<span class="stk-score">{away_s}</span>'
                f'<span class="stk-score-sep">-</span>'
                f'<span class="stk-score">{home_s}</span>'
                f'</div>'
            )
        elif phase == "postponed":
            status_html = '<div class="stk-status ppd"><span>PPD</span></div>'
            score_html = ''
        else:
            _n_preview += 1
            time_short = g.get("time_short", "")
            status_html = f'<div class="stk-status preview"><span>{time_short}</span></div>'
            score_html = ''

        cards.append(
            f'<div class="stk-game {phase}">'
            f'<div class="stk-team">'
            f'<img class="stk-logo" src="{away_logo}" alt="{away_ab}" />'
            f'<span class="stk-abbr">{away_ab}</span>'
            f'</div>'
            f'{score_html}'
            f'<div class="stk-team">'
            f'<img class="stk-logo" src="{home_logo}" alt="{home_ab}" />'
            f'<span class="stk-abbr">{home_ab}</span>'
            f'</div>'
            f'{status_html}'
            f'</div>'
        )

    if _n_live > 0:
        summary = f"{_n_live} Live"
        if _n_final:
            summary += f" · {_n_final} Final"
    elif _n_final > 0:
        summary = f"{_n_final} Final"
        if _n_preview:
            summary += f" · {_n_preview} Upcoming"
    else:
        summary = f"{_n_preview} Games Today"

    n = len(cards)
    scroll_dur = max(20, n * 4)
    cards_dup = "".join(cards) + "".join(cards)

    st.markdown(
        "<style>"
        "@keyframes stkScroll { "
        "  0%   { transform: translateX(0); } "
        "  100% { transform: translateX(-50%); } "
        "}"
        "@keyframes stkLivePulse { "
        "  0%, 100% { opacity: 1; } 50% { opacity: .35; } "
        "}"
        ".stk-wrap { "
        "  margin: 2px 0 16px 0; padding: 0; "
        "  background: linear-gradient(180deg, #0a1628 0%, #0e1f38 100%); "
        "  border: 1px solid rgba(255,255,255,.08); border-radius: 14px; "
        "  overflow: hidden; position: relative; "
        "}"
        ".stk-header { "
        "  display: flex; align-items: center; justify-content: space-between; "
        "  padding: 8px 14px 4px; "
        "}"
        ".stk-header-left { display: flex; align-items: center; gap: 8px; }"
        ".stk-header-title { "
        "  font-size: .72rem; font-weight: 800; letter-spacing: .14em; "
        "  text-transform: uppercase; color: #64b5f6; "
        "  font-family: 'DM Mono', monospace; "
        "}"
        ".stk-header-summary { "
        "  font-size: .68rem; font-weight: 600; color: #4a6a8a; "
        "  font-family: 'DM Mono', monospace; "
        "}"
        ".stk-rail { "
        "  overflow: hidden; padding: 6px 0 10px; "
        "}"
        ".stk-strip { "
        "  display: flex; gap: 10px; width: max-content; "
        f"  animation: stkScroll {scroll_dur}s linear infinite; "
        "  padding: 0 10px; "
        "}"
        ".stk-strip:hover { animation-play-state: paused; }"
        ".stk-game { "
        "  flex: 0 0 auto; display: flex; flex-direction: column; "
        "  align-items: center; gap: 3px; "
        "  background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.06); "
        "  border-radius: 10px; padding: 8px 14px 7px; min-width: 88px; "
        "  transition: background .2s, border-color .2s; "
        "}"
        ".stk-game:hover { background: rgba(255,255,255,.06); border-color: rgba(255,255,255,.12); }"
        ".stk-game.live { border-color: rgba(239,68,68,.35); }"
        ".stk-game.live:hover { border-color: rgba(239,68,68,.55); }"
        ".stk-team { display: flex; align-items: center; gap: 5px; }"
        ".stk-logo { width: 18px; height: 18px; object-fit: contain; }"
        ".stk-abbr { "
        "  font-size: .74rem; font-weight: 700; color: #c8ddf0; "
        "  font-family: 'DM Mono', monospace; letter-spacing: .04em; "
        "}"
        ".stk-scores { "
        "  display: flex; align-items: center; gap: 4px; margin: 1px 0; "
        "}"
        ".stk-score { "
        "  font-size: .88rem; font-weight: 800; color: #ffffff; "
        "  font-family: 'DM Mono', monospace; min-width: 14px; text-align: center; "
        "}"
        ".stk-score-sep { color: #3a5a7a; font-weight: 600; font-size: .78rem; }"
        ".stk-game.final .stk-score { color: #7a9bbf; }"
        ".stk-status { "
        "  display: flex; align-items: center; gap: 4px; "
        "  font-size: .58rem; font-weight: 700; letter-spacing: .06em; "
        "  text-transform: uppercase; "
        "  font-family: 'DM Mono', monospace; "
        "}"
        ".stk-status.live { color: #ef4444; }"
        ".stk-status.final { color: #f59e0b; }"
        ".stk-status.preview { color: #4a6a8a; }"
        ".stk-status.ppd { color: #6b7280; }"
        ".stk-live-dot { "
        "  width: 6px; height: 6px; border-radius: 50%; background: #ef4444; "
        "  animation: stkLivePulse 1.6s ease-in-out infinite; flex-shrink: 0; "
        "}"
        "@media (max-width: 640px) { "
        "  .stk-wrap { border-radius: 10px; margin-bottom: 12px; } "
        "  .stk-game { padding: 6px 10px 5px; min-width: 76px; border-radius: 8px; } "
        "  .stk-logo { width: 15px; height: 15px; } "
        "  .stk-abbr { font-size: .68rem; } "
        "  .stk-score { font-size: .80rem; } "
        "  .stk-header { padding: 6px 10px 3px; } "
        "}"
        "</style>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="stk-wrap">'
        '<div class="stk-header">'
        '<div class="stk-header-left">'
        '<span class="stk-header-title">⚾ MLB Scoreboard</span>'
        '</div>'
        f'<span class="stk-header-summary">{summary}</span>'
        '</div>'
        '<div class="stk-rail">'
        f'<div class="stk-strip">{cards_dup}</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

_render_slate_ticker(schedule_df)

_pills_html = []
for _i, _opt in enumerate(_TOP_VIEW_OPTIONS):
    _active = " active" if _opt == _view else ""
    _href = _build_top_tab_href(_i)
    _pills_html.append(
        f'<a class="top-tab-pill{_active}" href="{_href}" target="_self">{_opt}</a>'
    )
st.markdown(
    '<div class="apps-gen-header">'
    '<span class="apps-gen-title">🧰 Apps &amp; Generators</span>'
    '<span class="apps-gen-sub">Tap any tile to switch views</span>'
    '</div>'
    '<div class="top-tab-row"><div class="top-tab-strip">'
    + "".join(_pills_html)
    + '</div></div>',
    unsafe_allow_html=True,
)

if _view == "🥎 Pitcher Breakdown":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🥎 Pitcher Breakdown'
        '</div>'
        '<div style="color:#64748b; font-size:.9rem; margin: -4px 0 10px 0;">'
        'Pick a starter for a premium drill-down: arsenal, opposing lineup, '
        'recent form, and splits.'
        '</div>',
        unsafe_allow_html=True,
    )
    if pitcher_stats_df is None or pitcher_stats_df.empty:
        st.warning(
            "Pitcher stats CSV (`Data:savant_pitcher_stats.csv`) hasn’t loaded yet."
        )
        st.stop()
    with st.spinner("Loading probable starters…"):
        sp_df = build_slate_pitcher_table(schedule_df, pitcher_stats_df)
    # Drop TBD probables — they have no data behind the picker.
    if not sp_df.empty and "Pitcher" in sp_df.columns:
        sp_df = sp_df[sp_df["Pitcher"].astype(str).str.upper() != "TBD"]
    if sp_df.empty:
        st.info("No probable starters posted yet for this slate. Check back closer to first pitch.")
    else:
        # ---- Interactive Pitcher Breakdown card -----------------------
        # Single focus: starter picker + premium dark card + tabs.
        st.markdown(render_pitcher_breakdown_styles(), unsafe_allow_html=True)

        # Ranking labels — "#1 Projected Ks" etc — driven by Strikeout
        # Score so the badge always picks the highest-K projection on
        # the slate.
        _pb_df = sp_df.copy()
        try:
            _pb_df["_pb_rank"] = (
                _pb_df["Strikeout Score"].rank(ascending=False, method="min")
            )
        except Exception:
            _pb_df["_pb_rank"] = 0

        # Order options for the picker. Default is the best Pitch Score
        # first so the slate's top arms are top of mind. Slate order
        # preserves the original schedule ordering from
        # build_slate_pitcher_table; name is a plain alpha sort.
        _PB_ORDER_OPTS = (
            "Pitch Score (best to worst)",
            "Pitch Score (worst to best)",
            "Slate order",
            "Pitcher name (A-Z)",
        )
        st.markdown(
            '<div class="pbd-picker-label">📊 Order picker</div>',
            unsafe_allow_html=True,
        )
        _pb_order = st.selectbox(
            "Order picker by",
            _PB_ORDER_OPTS,
            index=0,
            key="pbd_order",
            label_visibility="collapsed",
        )
        if _pb_order == "Pitch Score (best to worst)":
            _pb_df = _pb_df.sort_values(
                "Pitch Score", ascending=False, na_position="last"
            ).reset_index(drop=True)
            st.markdown(
                '<div class="pbd-sort-hint">'
                'Best Pitch Score first · lower=worse, dashes=insufficient sample'
                '</div>',
                unsafe_allow_html=True,
            )
        elif _pb_order == "Pitch Score (worst to best)":
            _pb_df = _pb_df.sort_values(
                "Pitch Score", ascending=True, na_position="last"
            ).reset_index(drop=True)
            st.markdown(
                '<div class="pbd-sort-hint">'
                'Worst Pitch Score first · lower=worse, dashes=insufficient sample'
                '</div>',
                unsafe_allow_html=True,
            )
        elif _pb_order == "Pitcher name (A-Z)":
            _pb_df = _pb_df.sort_values(
                "Pitcher", ascending=True, na_position="last"
            ).reset_index(drop=True)
        # Slate order: leave the original order from build_slate_pitcher_table.

        _label_options = []
        _row_by_label = {}
        for _, _r in _pb_df.iterrows():
            _nm = str(_r.get("Pitcher", "") or "TBD")
            _team = str(_r.get("Team", "") or "")
            _opp = str(_r.get("Opp", "") or "")
            _label = f"{_nm} — {_team} {_r.get('Loc','')} {_opp}".strip()
            if _label in _row_by_label:
                _label = f"{_label} ({_r.get('Time','')})"
            _label_options.append(_label)
            _row_by_label[_label] = _r.to_dict()

        st.markdown(
            '<div class="pbd-picker-label">🔎 Drill into a starter</div>',
            unsafe_allow_html=True,
        )
        # Preserve the previously selected pitcher across re-sorts when
        # possible; otherwise default to the top of the new ordering.
        _prev_pick = st.session_state.get("pbd_pick")
        _default_idx = 0
        if _prev_pick in _label_options:
            _default_idx = _label_options.index(_prev_pick)
        _selected_label = st.selectbox(
            "Pick a pitcher for full breakdown",
            _label_options,
            index=_default_idx,
            key="pbd_pick",
            label_visibility="collapsed",
        )
        _pb_row = _row_by_label.get(_selected_label, {})

        # Pull supporting data — each call is wrapped so a failure can't
        # blank the whole card.
        _pid = _pb_row.get("_player_id")
        _mix_df = pd.DataFrame()
        try:
            if _pid:
                _mix_df = pitcher_pitch_mix(arsenal_pitcher_df, _pid)
        except Exception:
            _mix_df = pd.DataFrame()

        _log_df = pd.DataFrame()
        try:
            if _pid:
                _log_df = _fetch_pitcher_game_log(int(_pid), 2026)
        except Exception:
            _log_df = pd.DataFrame()

        _splits = {}
        try:
            if _pid:
                _splits = _fetch_pitcher_splits_by_hand(int(_pid), 2026)
        except Exception:
            _splits = {}

        _proj = _project_pitcher_targets(_pb_row, _log_df)

        try:
            _rank_n = int(_pb_row.get("_pb_rank") or 0) or None
        except Exception:
            _rank_n = None
        _rank_label = ""
        if _rank_n and _rank_n <= 3:
            _rank_label = f"#{_rank_n} Projected Ks"

        _opp_abbr = str(_pb_row.get("Opp") or "")
        _game_pk = None
        _opp_team_id = None
        try:
            _sched_match = schedule_df[
                (schedule_df["away_abbr"] == _pb_row.get("Team")) &
                (schedule_df["home_abbr"] == _opp_abbr)
            ]
            if _sched_match.empty:
                _sched_match = schedule_df[
                    (schedule_df["home_abbr"] == _pb_row.get("Team")) &
                    (schedule_df["away_abbr"] == _opp_abbr)
                ]
            if not _sched_match.empty:
                _sched_row = _sched_match.iloc[0]
                _game_pk = _sched_row.get("game_pk")
                if _sched_row.get("away_abbr") == _opp_abbr:
                    _opp_team_id = _sched_row.get("away_id")
                else:
                    _opp_team_id = _sched_row.get("home_id")
        except Exception:
            _game_pk = None
            _opp_team_id = None

        _opp_k_rank = _compute_opp_k_rank(schedule_df, _opp_abbr, pitcher_stats_df)
        _pb_pitch_score = _pitcher_breakdown_pitch_score(_pb_row, _proj)

        st.markdown(
            '<div class="pbd-card">'
            + render_pitcher_breakdown_header(_pb_row, _rank_label)
            + render_pitcher_breakdown_kpis(_pb_row, _proj, _opp_k_rank)
            + render_pitcher_breakdown_badges(_pb_row, _proj, _pb_pitch_score)
            + '</div>',
            unsafe_allow_html=True,
        )

        _tab_arsenal, _tab_opp, _tab_log, _tab_splits = st.tabs(
            ["🎯 Arsenal", "👥 Opposing Lineup", "📅 Game Log", "🔀 Splits"]
        )
        with _tab_arsenal:
            st.markdown(
                render_pitcher_breakdown_arsenal(_mix_df),
                unsafe_allow_html=True,
            )
        with _tab_opp:
            st.markdown(
                render_pitcher_breakdown_lineup(_game_pk, _opp_team_id, _opp_abbr),
                unsafe_allow_html=True,
            )
        with _tab_log:
            st.markdown(
                render_pitcher_breakdown_game_log(_log_df, n=6),
                unsafe_allow_html=True,
            )
        with _tab_splits:
            st.markdown(
                render_pitcher_breakdown_splits(_splits),
                unsafe_allow_html=True,
            )

        # Mobile tail spacer — guarantees the last row of any tab is
        # scrollable above the browser chrome / home indicator strip.
        st.markdown('<div class="pbd-tail"></div>', unsafe_allow_html=True)
    st.stop()

# ============== HR Sleepers view ==============
if _view == "💎 HR Sleepers":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">💎 HR Sleepers</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Overlooked batters with HR upside tonight. Sleeper Score (0-100) blends '
        '<b>Power profile</b> (Barrel%, HardHit%, ISO, FB%, Pull%) · <b>Tonight’s matchup</b> '
        '(opposing SP, park, weather, ceiling) · <b>Sleeper bonus</b> for low-HR-total / '
        'lower-spot bats. Filter to taste.'
        '</div>',
        unsafe_allow_html=True,
    )
    if batters_df.empty:
        st.warning(
            "Batter CSV (`Data:savant_batters.csv.csv`) hasn’t loaded yet."
        )
        st.stop()

    # HR Sleepers is a batter-pick recommendation, so apply the same
    # started-game eligibility rule used by the parlay generators.
    _hrs_sched = filter_pre_game_schedule(schedule_df)
    _hrs_total = int(len(schedule_df))
    _hrs_elig  = int(len(_hrs_sched))
    _hrs_excl  = _hrs_total - _hrs_elig
    if _hrs_sched is None or _hrs_sched.empty:
        st.warning(
            f"All {_hrs_total} games on this slate have already started or "
            "finished. No pre-game matchups remain to score sleepers from."
        )
        st.stop()
    if _hrs_excl > 0:
        st.markdown(
            f'<div style="margin:0 0 10px 0; padding:8px 12px; '
            f'border-left:3px solid #7c3aed; background:#f5f3ff; '
            f'border-radius:6px; color:#065f46; font-size:0.88rem;">'
            f'🕒 Using <b>{_hrs_elig}</b> pre-game matchup'
            f'{"s" if _hrs_elig != 1 else ""}; '
            f'started &amp; completed games excluded ({_hrs_excl} hidden).'
            f'</div>',
            unsafe_allow_html=True,
        )

    try:
        _slate_iso_hrs = pd.to_datetime(selected_date).strftime("%Y-%m-%d")
    except Exception:
        _slate_iso_hrs = ""
    with st.spinner("Scoring sleeper candidates across the slate…"):
        hrs_df = build_hr_sleepers_table(_hrs_sched, batters_df, pitchers_df,
                                          slate_date_iso=_slate_iso_hrs)

    if hrs_df.empty:
        st.info("No lineups posted yet across the slate. Check back closer to first pitch.")
        st.stop()

    # Filters row
    f_cols = st.columns([1.0, 1.0, 1.0, 1.0, 1.6])
    with f_cols[0]:
        _max_hr = st.number_input("Max season HR", min_value=0, max_value=60, value=15, step=1,
                                  key="hrs_max_hr",
                                  help="Hide bats with more season HRs than this. The whole point of a sleeper.")
    with f_cols[1]:
        _min_barrel = st.number_input("Min Barrel%", min_value=0.0, max_value=20.0, value=6.0, step=0.5,
                                      key="hrs_min_barrel",
                                      help="Floor for power profile. League average is ~7%.")
    with f_cols[2]:
        _hide_top3 = st.checkbox("Hide spots 1-3", value=True, key="hrs_hide_top3",
                                 help="Top-of-order bats are not really sleepers.")
    with f_cols[3]:
        _min_pa = st.number_input("Min PA", min_value=0, max_value=700, value=50, step=10,
                                  key="hrs_min_pa",
                                  help="Avoid tiny-sample noise.")
    with f_cols[4]:
        _topn = st.slider("Show top N", min_value=10, max_value=50, value=20, step=5,
                          key="hrs_topn")

    filtered = hrs_df.copy()
    if _max_hr is not None:
        filtered = filtered[filtered["HR (Season)"] <= int(_max_hr)]
    if _min_barrel and _min_barrel > 0:
        filtered = filtered[filtered["Barrel%"].fillna(-1) >= float(_min_barrel)]
    if _hide_top3:
        filtered = filtered[filtered["Spot"] > 3]
    if _min_pa and _min_pa > 0 and "PA" in filtered.columns:
        filtered = filtered[filtered["PA"].fillna(0).astype(float) >= float(_min_pa)]
    filtered = filtered.head(int(_topn)).reset_index(drop=True)

    if filtered.empty:
        st.info("No sleepers match the current filters. Try raising Max season HR or lowering Min Barrel%.")
    else:
        st.markdown(render_hr_sleepers_html(filtered), unsafe_allow_html=True)
        st.markdown(
            '<div style="margin: 6px 0 4px 0; color:#64748b; font-size:.82rem;">'
            'Scoring weights — Power 60% (Barrel% 22 · HardHit% 14 · ISO 10 · FB% 7 · Pull% 7) · '
            'Matchup 25% (Matchup 10 · Ceiling 8 · kHR 7) · Sleeper bonus 15% (low HR total + lower spot). '
            "Scores refresh by slate date and blend today's opponent / probable pitcher, "
            'lineup spot, park &amp; weather, and L10 form when available; started games are excluded.'
            '</div>',
            unsafe_allow_html=True,
        )
        # CSV download
        dl_cols = [c for c in filtered.columns if not c.startswith("_")]
        csv_bytes = filtered[dl_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download HR Sleepers (CSV)",
            data=csv_bytes,
            file_name=f"hr_sleepers_{selected_date}.csv",
            mime="text/csv",
            width='content',
        )
    st.stop()

# ============== Total Bases 1.5+ view ==============
if _view == "📊 Total Bases 1.5+":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '📊 Over 1.5 Total Bases — Top Targets</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Best plays for clearing 1.5 total bases (an XBH or two singles). '
        'TB Score (0-100) blends <b>Contact</b> 35% (xBA, AVG, low K%, LD%) · '
        '<b>Power</b> 30% (xSLG, ISO, Barrel%) · <b>PA opportunity</b> 15% '
        '(spots 1-5 favored) · <b>Tonight’s matchup</b> 20% (opp SP, park, weather, ceiling).'
        '</div>',
        unsafe_allow_html=True,
    )
    if batters_df.empty:
        st.warning("Batter CSV hasn’t loaded yet.")
        st.stop()

    _tb_sched = filter_pre_game_schedule(schedule_df)
    _tb_total = int(len(schedule_df)); _tb_elig = int(len(_tb_sched))
    _tb_excl  = _tb_total - _tb_elig
    if _tb_sched is None or _tb_sched.empty:
        st.warning(
            f"All {_tb_total} games on this slate have already started or "
            "finished. No pre-game matchups remain to score TB targets from."
        )
        st.stop()
    if _tb_excl > 0:
        st.markdown(
            f'<div style="margin:0 0 10px 0; padding:8px 12px; '
            f'border-left:3px solid #7c3aed; background:#f5f3ff; '
            f'border-radius:6px; color:#065f46; font-size:0.88rem;">'
            f'🕒 Using <b>{_tb_elig}</b> pre-game matchup'
            f'{"s" if _tb_elig != 1 else ""}; '
            f'started &amp; completed games excluded ({_tb_excl} hidden).'
            f'</div>',
            unsafe_allow_html=True,
        )
    try:
        _slate_iso_tb = pd.to_datetime(selected_date).strftime("%Y-%m-%d")
    except Exception:
        _slate_iso_tb = ""
    with st.spinner("Scoring TB targets across the slate…"):
        tb_df = build_targets_table(_tb_sched, batters_df, pitchers_df,
                                     mode="tb", slate_date_iso=_slate_iso_tb)

    if tb_df.empty:
        st.info("No lineups posted yet across the slate. Check back closer to first pitch.")
        st.stop()

    # Filters
    f_cols = st.columns([1.0, 1.0, 1.0, 1.0, 1.6])
    with f_cols[0]:
        _max_spot = st.number_input("Max lineup spot", min_value=1, max_value=9, value=6, step=1,
                                    key="tb_max_spot",
                                    help="Spot 6 or better. Bottom of order rarely sees enough PA for 2 TB.")
    with f_cols[1]:
        _min_xba = st.number_input("Min xBA", min_value=0.000, max_value=0.400, value=0.230, step=0.005,
                                   format="%.3f", key="tb_min_xba",
                                   help="Floor for expected batting average.")
    with f_cols[2]:
        _max_k = st.number_input("Max K%", min_value=10.0, max_value=40.0, value=28.0, step=0.5,
                                 key="tb_max_k",
                                 help="K-prone hitters struggle to multi-hit.")
    with f_cols[3]:
        _min_pa = st.number_input("Min PA", min_value=0, max_value=700, value=80, step=10,
                                  key="tb_min_pa")
    with f_cols[4]:
        _topn = st.slider("Show top N", min_value=10, max_value=30, value=15, step=5, key="tb_topn")

    fdf = tb_df.copy()
    fdf = fdf[fdf["Spot"] <= int(_max_spot)]
    if _min_xba and _min_xba > 0:
        fdf = fdf[fdf["xBA"].fillna(-1) >= float(_min_xba)]
    if _max_k and _max_k < 40:
        fdf = fdf[fdf["K%"].fillna(99) <= float(_max_k)]
    if _min_pa and _min_pa > 0:
        fdf = fdf[fdf["PA"].fillna(0).astype(float) >= float(_min_pa)]
    fdf = fdf.head(int(_topn)).reset_index(drop=True)

    if fdf.empty:
        st.info("No targets match the current filters. Try lowering Min xBA or raising Max K%.")
    else:
        st.markdown(render_targets_html(fdf, mode="tb"), unsafe_allow_html=True)
        st.markdown(
            render_source_chips([
                "savant:batters", "savant:pitchers",
                "statsapi:schedule", "statsapi:boxscore",
                "rotogrinders:weather", "openmeteo:weather",
            ]),
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="margin: 6px 0 4px 0; color:#64748b; font-size:.82rem;">'
            'Tiers — Elite ≥75 · Strong ≥65 · Average ≥55 · Soft <55. '
            'Sort: TB Score ↓. '
            'Pills next to a hitter: <b>CONF</b> = MLB has posted the lineup, '
            '<b>PROJ</b> = inferred from recent games, <b>TBD</b> = not yet known. '
            '★ marks a platoon edge (LHB vs RHP / RHB vs LHP). '
            "Scores refresh by slate date and blend today's opponent / probable pitcher, "
            'park &amp; weather, lineup spot, and L10 form when available; started games are excluded.'
            '</div>',
            unsafe_allow_html=True,
        )
        dl_cols = [c for c in fdf.columns if not c.startswith("_")]
        csv_bytes = fdf[dl_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download Total Bases Targets (CSV)",
            data=csv_bytes,
            file_name=f"total_bases_targets_{selected_date}.csv",
            mime="text/csv",
            width='content',
        )
    st.stop()

# ============== HRR 1.5+ view (Hits + Runs + RBI) ==============
if _view == "🎯 HRR 1.5+":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🎯 Over 1.5 H+R+RBI — Top Targets</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Best plays for clearing 1.5 combined Hits + Runs + RBI. '
        'HRR Score (0-100) blends <b>On-base</b> 35% (xBA, xOBP, AVG) · '
        '<b>Power</b> 20% (xSLG, ISO, Barrel%) · <b>Lineup spot</b> 25% '
        '(spots 2-5 strongly favored — they bat more & drive runs) · '
        '<b>Tonight’s matchup</b> 20% (opp SP, park, weather, ceiling).'
        '</div>',
        unsafe_allow_html=True,
    )
    if batters_df.empty:
        st.warning("Batter CSV hasn’t loaded yet.")
        st.stop()

    _hrr_sched = filter_pre_game_schedule(schedule_df)
    _hrr_total = int(len(schedule_df)); _hrr_elig = int(len(_hrr_sched))
    _hrr_excl  = _hrr_total - _hrr_elig
    if _hrr_sched is None or _hrr_sched.empty:
        st.warning(
            f"All {_hrr_total} games on this slate have already started or "
            "finished. No pre-game matchups remain to score HRR targets from."
        )
        st.stop()
    if _hrr_excl > 0:
        st.markdown(
            f'<div style="margin:0 0 10px 0; padding:8px 12px; '
            f'border-left:3px solid #7c3aed; background:#f5f3ff; '
            f'border-radius:6px; color:#065f46; font-size:0.88rem;">'
            f'🕒 Using <b>{_hrr_elig}</b> pre-game matchup'
            f'{"s" if _hrr_elig != 1 else ""}; '
            f'started &amp; completed games excluded ({_hrr_excl} hidden).'
            f'</div>',
            unsafe_allow_html=True,
        )
    try:
        _slate_iso_hrr = pd.to_datetime(selected_date).strftime("%Y-%m-%d")
    except Exception:
        _slate_iso_hrr = ""
    with st.spinner("Scoring HRR targets across the slate…"):
        hrr_df = build_targets_table(_hrr_sched, batters_df, pitchers_df,
                                      mode="hrr", slate_date_iso=_slate_iso_hrr)

    if hrr_df.empty:
        st.info("No lineups posted yet across the slate. Check back closer to first pitch.")
        st.stop()

    # Filters
    f_cols = st.columns([1.0, 1.0, 1.0, 1.0, 1.6])
    with f_cols[0]:
        _max_spot = st.number_input("Max lineup spot", min_value=1, max_value=9, value=6, step=1,
                                    key="hrr_max_spot",
                                    help="Bottom-of-order bats struggle for combined H+R+RBI volume.")
    with f_cols[1]:
        _min_xobp = st.number_input("Min xOBP", min_value=0.000, max_value=0.500, value=0.310, step=0.005,
                                    format="%.3f", key="hrr_min_xobp",
                                    help="Floor for expected on-base — you need to reach base for R/RBI.")
    with f_cols[2]:
        _max_k = st.number_input("Max K%", min_value=10.0, max_value=40.0, value=28.0, step=0.5,
                                 key="hrr_max_k",
                                 help="K-prone hitters struggle for hits + production.")
    with f_cols[3]:
        _min_pa = st.number_input("Min PA", min_value=0, max_value=700, value=80, step=10,
                                  key="hrr_min_pa")
    with f_cols[4]:
        _topn = st.slider("Show top N", min_value=10, max_value=30, value=15, step=5, key="hrr_topn")

    fdf = hrr_df.copy()
    fdf = fdf[fdf["Spot"] <= int(_max_spot)]
    if _min_xobp and _min_xobp > 0:
        fdf = fdf[fdf["xOBP"].fillna(-1) >= float(_min_xobp)]
    if _max_k and _max_k < 40:
        fdf = fdf[fdf["K%"].fillna(99) <= float(_max_k)]
    if _min_pa and _min_pa > 0:
        fdf = fdf[fdf["PA"].fillna(0).astype(float) >= float(_min_pa)]
    fdf = fdf.head(int(_topn)).reset_index(drop=True)

    if fdf.empty:
        st.info("No targets match the current filters. Try lowering Min xOBP or raising Max K%.")
    else:
        st.markdown(render_targets_html(fdf, mode="hrr"), unsafe_allow_html=True)
        st.markdown(
            render_source_chips([
                "savant:batters", "savant:pitchers",
                "statsapi:schedule", "statsapi:boxscore",
                "rotogrinders:weather", "openmeteo:weather",
            ]),
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="margin: 6px 0 4px 0; color:#64748b; font-size:.82rem;">'
            'Tiers — Elite ≥75 · Strong ≥65 · Average ≥55 · Soft <55. '
            'Sort: HRR Score ↓. '
            'Pills: <b>CONF</b>/<b>PROJ</b>/<b>TBD</b> for lineup status. '
            '★ = platoon edge. '
            "Scores refresh by slate date and blend today's opponent / probable pitcher, "
            'park &amp; weather, lineup spot, and L10 form when available; started games are excluded.'
            '</div>',
            unsafe_allow_html=True,
        )
        dl_cols = [c for c in fdf.columns if not c.startswith("_")]
        csv_bytes = fdf[dl_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download HRR Targets (CSV)",
            data=csv_bytes,
            file_name=f"hrr_targets_{selected_date}.csv",
            mime="text/csv",
            width='content',
        )
    st.stop()

# ============== RBI Edge Model view ==============
# Replaces the legacy "🔥 2+ RBI" tab. The new model is implemented in
# rbi_model.py and exposes render_rbi_model_page(). We pass the app's
# already-cached schedule + lineup/weather helpers so the module can use
# Confirmed lineups when posted and fall back to Projected lineups
# (most-used 9 over recent games) before its own demo slate.
if _view == "⚾ RBI Edge Model":
    _rbi_sched = filter_pre_game_schedule(schedule_df) if schedule_df is not None else schedule_df
    render_rbi_model_page(
        schedule_df=_rbi_sched,
        batters_df=batters_df,
        pitchers_df=pitchers_df,
        build_game_context_fn=build_game_context,
        clean_name_fn=clean_name,
        norm_team_fn=norm_team,
    )
    st.stop()

# ============== AI HR Parlay view ==============
# Builds 2-leg and 3-leg HR parlays from the slate's full eligible-lineup pool.
# Scoring layers an AI HR Score on top of the existing HR Sleeper Score: it
# explicitly weights Ceiling (park + weather + raw power) and pitch-zone /
# hitter-zone fit (zone_fit() + opposing pitcher arsenal vs hitter crush
# pitches) so picks favor bats whose hot zones overlap the SP's offerings.
# Selection is weighted-sample with sleeper diversity boost so under-owned
# sleepers can win a slot when their profile holds up — no odds API.
if _view == "🤖 AI HR Parlay":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🤖 AI HR Parlay Generator</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Two- and three-leg home-run parlays built from the slate model. '
        'The <b>AI HR Score</b> blends raw power (Barrel%, HardHit%, ISO, '
        'xSLG, FB%, Pull%, Bat Speed, SweetSpot%) with <b>Ceiling</b> '
        '(park + weather + opposing SP) and <b>pitch-zone fit</b> '
        '(hitter hot zones × pitcher arsenal overlap). '
        'Sleepers stay in the running via weighted sampling — not just '
        'top-N stars. Each leg shows the data-driven reasons it was picked.'
        '</div>',
        unsafe_allow_html=True,
    )

    if batters_df.empty:
        st.warning("Batter CSV (`Data:savant_batters.csv.csv`) hasn’t loaded yet.")
        st.stop()

    # ---- Filter out games that have already started, finished, or whose
    # start time has passed. Once a game is underway the lineup is locked
    # and props can't be reliably bet, so the AI must only recommend bats
    # from pre-game (scheduled / projected / confirmed lineup) slates.
    _COMPLETED_TOKENS = (
        "final", "completed", "game over", "postponed", "cancelled",
        "canceled", "suspended", "forfeit", "if necessary",
    )
    # Statuses that mean the game is already underway (or about to be):
    # treated the same as completed for eligibility — exclude.
    _STARTED_TOKENS = (
        "in progress", "live", "manager challenge", "review",
        "delayed",
    )
    # Pre-game-but-imminent statuses still allowed (lineups posted, but
    # first pitch hasn't happened). We still gate these by start time.
    _PREGAME_TOKENS = (
        "scheduled", "pre-game", "pregame", "pre game",
        "warmup", "warm-up", "warm up",
    )

    def _game_is_eligible(row) -> bool:
        status = str(row.get("status") or "").strip().lower()
        # Hard-exclude completed / canceled / postponed regardless of time.
        if any(tok in status for tok in _COMPLETED_TOKENS):
            return False
        # Hard-exclude games already underway: lineups are locked and
        # in-game props (deep innings) are unreliable to recommend.
        if any(tok in status for tok in _STARTED_TOKENS):
            return False
        # For scheduled/pre-game games, also require the start time to be
        # in the future (UTC). If start time is missing, accept on status.
        gt = row.get("game_time_utc")
        try:
            start_utc = pd.to_datetime(gt, utc=True)
        except Exception:
            start_utc = pd.NaT
        _now = pd.Timestamp.now('UTC')
        now_utc = _now if _now.tzinfo is not None else _now.tz_localize("UTC")
        if any(tok in status for tok in _PREGAME_TOKENS):
            if pd.isna(start_utc):
                return True
            return start_utc > now_utc
        # Unknown status: fall back to start-time check; if the start time
        # has already passed we treat the game as started and exclude it.
        if pd.isna(start_utc):
            return False
        return start_utc > now_utc

    if schedule_df is None or schedule_df.empty:
        st.warning("No games on the slate. Pick a different date or check back later.")
        st.stop()

    _elig_mask = schedule_df.apply(_game_is_eligible, axis=1)
    eligible_schedule_df = schedule_df[_elig_mask].reset_index(drop=True)
    _n_total = int(len(schedule_df))
    _n_elig  = int(len(eligible_schedule_df))
    _n_excl  = _n_total - _n_elig

    if eligible_schedule_df.empty:
        st.warning(
            f"All {_n_total} games on this slate have already started or finished. "
            f"No pre-game matchups remain to build a parlay from."
        )
        st.stop()

    st.markdown(
        f'<div style="margin:0 0 10px 0; padding:8px 12px; '
        f'border-left:3px solid #7c3aed; background:#f5f3ff; '
        f'border-radius:6px; color:#065f46; font-size:0.88rem;">'
        f'🕒 Using <b>{_n_elig}</b> pre-game matchup'
        f'{"s" if _n_elig != 1 else ""}; '
        f'started &amp; completed games excluded'
        f'{f" ({_n_excl} hidden)" if _n_excl > 0 else ""}.'
        f'</div>',
        unsafe_allow_html=True,
    )

    try:
        _slate_iso_aip = pd.to_datetime(selected_date).strftime("%Y-%m-%d")
    except Exception:
        _slate_iso_aip = ""
    with st.spinner("Scoring HR candidates across the slate…"):
        ai_pool_df = build_hr_sleepers_table(eligible_schedule_df, batters_df, pitchers_df,
                                              slate_date_iso=_slate_iso_aip)

    if ai_pool_df is None or ai_pool_df.empty:
        st.info("No lineups posted yet for the upcoming/live games. Check back closer to first pitch.")
        st.stop()

    # Per-game ballpark+weather indicator map keyed by short_label, used as a
    # fallback when a leg dict somehow loses its BPW columns (cache, merges,
    # downstream filters). Guarantees every parlay leg can render the pill.
    _bpw_game_map = {}
    for _, _g in eligible_schedule_df.iterrows():
        try:
            _gc = build_game_context(_g)
            _wx = _gc.get("weather", {}) if isinstance(_gc, dict) else {}
        except Exception:
            _wx = {}
        try:
            _bpw_game_map[str(_g.get("short_label", ""))] = park_weather_indicator(
                _wx, _g.get("park_factor"), _g.get("home_abbr", "")
            )
        except Exception:
            _bpw_game_map[str(_g.get("short_label", ""))] = {
                "label": "OK", "tier": "ok", "hr_pct": 0,
                "icon": "🟡", "tooltip": "Park/weather signal unavailable",
            }

    # ---- Controls (clean, compact, odds-free) ----
    c_cols = st.columns([1.6, 1.0, 1.0, 1.0])
    with c_cols[0]:
        _risk = st.radio(
            "Risk profile",
            ["Safer", "Balanced", "Sleeper Hunt", "Aggressive"],
            index=1,
            horizontal=True,
            key="ai_parlay_risk",
            help=(
                "Safer: heart-of-order, elite power, strict thresholds. "
                "Balanced: default mix of stars and quality sleepers. "
                "Sleeper Hunt: boosts under-owned bats with strong AI HR Score. "
                "Aggressive: opens spots 1-9, deeper variance."
            ),
        )
    with c_cols[1]:
        _avoid_same_game = st.checkbox(
            "Avoid same game", value=True, key="ai_parlay_avoid_game",
            help="Prevent two legs from the same game (correlation risk).",
        )
    with c_cols[2]:
        _avoid_same_team = st.checkbox(
            "Avoid same team", value=True, key="ai_parlay_avoid_team",
            help="Prevent two legs from the same team.",
        )
    with c_cols[3]:
        _max_spot = st.slider(
            "Max lineup spot", min_value=4, max_value=9, value=8, step=1,
            key="ai_parlay_max_spot",
            help="Hide bats batting deeper than this in the order.",
        )

    # ---- AI HR Score: Ceiling + zone-fit aware augmentation -------------
    # We layer Ceiling and pitch-zone signals explicitly on top of the base
    # HR Sleeper Score so the AI tab favors bats whose hot zones overlap
    # the opposing SP arsenal — not just raw power leaderboard.
    PITCH_NAME_FALLBACK = {
        "FF":"4-Seam","FT":"2-Seam","SI":"Sinker","FC":"Cutter",
        "SL":"Slider","ST":"Sweeper","SV":"Slurve","CU":"Curve",
        "KC":"Knuckle Curve","CH":"Change","FS":"Splitter",
        "SC":"Screwball","KN":"Knuckle","FO":"Forkball",
    }
    try:
        _PITCH_LABELS = PITCH_NAME_MAP  # use app's canonical map if defined
    except NameError:
        _PITCH_LABELS = PITCH_NAME_FALLBACK

    def _pitch_label(code: str) -> str:
        if not code:
            return ""
        c = str(code).strip().upper()
        return _PITCH_LABELS.get(c, c)

    @st.cache_data(ttl=300, show_spinner=False)
    def _opposing_sp_arsenal_cached(opp_pitcher_name: str):
        """Return (pitcher_id, set(pitch_codes)) for an opposing SP."""
        if not opp_pitcher_name or str(opp_pitcher_name).upper() == "TBD":
            return (None, set())
        prow = find_pitcher_row(pitchers_df, opp_pitcher_name)
        if prow is None:
            return (None, set())
        pid = prow.get("player_id") if hasattr(prow, "get") else None
        try:
            pid = int(pid) if pid is not None and not pd.isna(pid) else None
        except Exception:
            pid = None
        return (pid, _build_pitcher_arsenal_set(arsenal_pitcher_df, pid))

    def _zone_fit_for_row(row) -> float:
        """Resolve zone_fit() (0..0.20) for an AI pool row, falling back to
        the proxy formula if direct lookup misses."""
        b = find_player_row(batters_df, clean_name(str(row.get("Hitter",""))),
                            row.get("Team",""))
        opp_sp = str(row.get("Opp SP","") or "")
        prow = find_pitcher_row(pitchers_df, opp_sp) if opp_sp else None
        bat_side = row.get("Bat","") or ""
        # Opposing pitcher hand: best-effort from pitcher row
        opp_hand = ""
        if prow is not None:
            for k in ("p_throws","throw_hand","throws","hand"):
                try:
                    v = prow.get(k)
                    if v and not pd.isna(v):
                        opp_hand = str(v)
                        break
                except Exception:
                    continue
        try:
            return float(zone_fit(b, prow, bat_side, opp_hand))
        except Exception:
            return 0.05

    def _hitter_crush_overlap(row):
        """Returns (overlap_codes:list[str], crush_codes:list[str]) — pitch
        codes the hitter punishes most that overlap the opposing SP's used
        arsenal. Empty lists if data is unavailable."""
        pid = row.get("_player_id")
        if pid is None or pd.isna(pid):
            return ([], [])
        try:
            pid_i = int(pid)
        except Exception:
            return ([], [])
        crush_df = hitter_pitch_crush(arsenal_batter_df, pid_i, top_n=3, min_pa=15)
        if crush_df is None or crush_df.empty or "pitch_type" not in crush_df.columns:
            return ([], [])
        crush_codes = [str(x).strip().upper() for x in
                       crush_df["pitch_type"].dropna().tolist() if str(x).strip()]
        opp_sp = str(row.get("Opp SP","") or "")
        _pid, opp_arsenal = _opposing_sp_arsenal_cached(opp_sp)
        if not opp_arsenal:
            return ([], crush_codes)
        overlap = [c for c in crush_codes if c in opp_arsenal]
        return (overlap, crush_codes)

    def _ai_hr_score(row, zone_fit_v: float, has_overlap: bool) -> float:
        """0-100 AI HR Score:
            55%  HR Sleeper Score (already a strong base)
            18%  Ceiling (park + weather + raw power, normalized 80-140)
            10%  Zone Fit (zone_fit() 0..0.20, normalized)
             7%  Pitch-zone overlap bonus (hitter crushes a pitch SP throws)
             5%  Bat Speed lift (>= 73 mph is elite)
             5%  SweetSpot% / xSLG composite
        """
        base   = float(row.get("Sleeper Score") or 0.0)
        ceil   = float(row.get("Ceiling") or 0.0)
        # Ceiling is already 0..100 in this codebase; normalize defensively.
        n_ceil = max(0.0, min(100.0, ceil))
        n_zf   = max(0.0, min(100.0, (float(zone_fit_v) / 0.20) * 100.0))
        overlap_b = 100.0 if has_overlap else 35.0
        bs = row.get("BatSpeed")
        try: bs_f = float(bs) if bs is not None and not pd.isna(bs) else None
        except Exception: bs_f = None
        if bs_f is None:
            n_bs = 50.0
        else:
            n_bs = max(0.0, min(100.0, (bs_f - 67.0) / (76.0 - 67.0) * 100.0))
        ss = row.get("SweetSpot%")
        try: ss_f = float(ss) if ss is not None and not pd.isna(ss) else None
        except Exception: ss_f = None
        if ss_f is None: n_ss = 50.0
        else:           n_ss = max(0.0, min(100.0, (ss_f - 28.0) / (40.0 - 28.0) * 100.0))
        xslg = row.get("xSLG")
        try: xslg_f = float(xslg) if xslg is not None and not pd.isna(xslg) else None
        except Exception: xslg_f = None
        if xslg_f is None: n_xslg = 50.0
        else:              n_xslg = max(0.0, min(100.0, (xslg_f - 0.350) / (0.560 - 0.350) * 100.0))
        comp_pwr = 0.5 * n_ss + 0.5 * n_xslg

        ai = (
            0.55 * base
          + 0.18 * n_ceil
          + 0.10 * n_zf
          + 0.07 * overlap_b
          + 0.05 * n_bs
          + 0.05 * comp_pwr
        )
        return round(max(0.0, min(100.0, ai)), 1)

    # ---- Build full eligible candidate pool (no top-N truncation) -------
    # Bring in extra columns we'll need from batters_df for AI HR Score
    # reasons (Bat Speed, SweetSpot%, xwOBA, K%, LA).
    pool = ai_pool_df.copy()
    extra_b = batters_df.copy() if not batters_df.empty else pd.DataFrame()
    if not extra_b.empty:
        keep_cols = [c for c in
            ["name_key","BatSpeed","SweetSpot%","LA","xwOBA","K%","BB%"]
            if c in extra_b.columns]
        if "name_key" in extra_b.columns and len(keep_cols) > 1:
            extra_b = extra_b[keep_cols].drop_duplicates("name_key")
            pool["__nk"] = pool["Hitter"].astype(str).map(clean_name)
            pool = pool.merge(extra_b, left_on="__nk", right_on="name_key",
                              how="left", suffixes=("", "_b"))
            pool = pool.drop(columns=[c for c in ["__nk","name_key"]
                                      if c in pool.columns])

    # Risk-profile thresholds. "Sleeper Hunt" = lower floor, big bonus on
    # under-owned bats; we apply that bonus during sampling, not here.
    if _risk == "Safer":
        min_score, min_barrel, min_hh = 60.0, 7.5, 36.0
        max_spot = min(_max_spot, 6)
    elif _risk == "Sleeper Hunt":
        min_score, min_barrel, min_hh = 48.0, 6.0, 32.0
        max_spot = _max_spot
    elif _risk == "Aggressive":
        min_score, min_barrel, min_hh = 42.0, 5.0, 30.0
        max_spot = _max_spot
    else:  # Balanced
        min_score, min_barrel, min_hh = 52.0, 6.5, 34.0
        max_spot = _max_spot

    pool = pool[pool["Sleeper Score"].fillna(0) >= float(min_score)]
    pool = pool[pool["Barrel%"].fillna(0)        >= float(min_barrel)]
    pool = pool[pool["HardHit%"].fillna(0)       >= float(min_hh)]
    pool = pool[pool["Spot"].fillna(99).astype(int) <= int(max_spot)]
    pool = pool.reset_index(drop=True)

    if pool.empty or len(pool) < 2:
        st.info(
            f"Not enough qualifying bats for the **{_risk}** profile — "
            f"only {len(pool)} hitter(s) cleared the thresholds. "
            f"Try **Sleeper Hunt** or **Aggressive**, or raise **Max lineup spot**."
        )
        st.stop()

    # ---- Score the entire eligible pool with AI HR Score ----------------
    with st.spinner("Layering Ceiling + pitch-zone fit on every bat…"):
        zf_vals, overlap_lists, crush_lists, ai_scores = [], [], [], []
        for _, r in pool.iterrows():
            zfv = _zone_fit_for_row(r)
            ovl, crush_codes = _hitter_crush_overlap(r)
            zf_vals.append(zfv)
            overlap_lists.append(ovl)
            crush_lists.append(crush_codes)
            ai_scores.append(_ai_hr_score(r, zfv, bool(ovl)))
        pool["Zone Fit"]      = zf_vals
        pool["__overlap"]     = overlap_lists
        pool["__crush"]       = crush_lists
        pool["AI HR Score"]   = ai_scores

    pool = pool.sort_values("AI HR Score", ascending=False).reset_index(drop=True)

    # ---- Reroll controls ------------------------------------------------
    if "ai_parlay_seed" not in st.session_state:
        st.session_state["ai_parlay_seed"] = 1
    if "ai_parlay_generated_at" not in st.session_state:
        st.session_state["ai_parlay_generated_at"] = datetime.now(
            ZoneInfo("America/New_York")
        ).strftime("%Y-%m-%d %I:%M:%S %p ET")

    r_cols = st.columns([1.0, 1.0, 2.0])
    with r_cols[0]:
        if st.button("🎲 Generate New Parlays", key="ai_parlay_reroll",
                     width='stretch',
                     help="Reroll: rebuilds 2-leg & 3-leg tickets via weighted "
                          "sampling on the same eligible pool. Filters stay the same."):
            st.session_state["ai_parlay_seed"] = int(
                st.session_state.get("ai_parlay_seed", 1)
            ) + 1
            st.session_state["ai_parlay_generated_at"] = datetime.now(
                ZoneInfo("America/New_York")
            ).strftime("%Y-%m-%d %I:%M:%S %p ET")
    with r_cols[1]:
        _show_alts = st.checkbox(
            "Show alternate tickets", value=True, key="ai_parlay_show_alts",
            help="Also surface 2 alternate 2-leg and 3-leg tickets from the same pool.",
        )
    with r_cols[2]:
        st.markdown(
            f'<div style="padding-top:6px; color:#475569; font-size:0.86rem;">'
            f'🎟️ <b>Ticket #{int(st.session_state["ai_parlay_seed"])}</b> · '
            f'{len(pool)} eligible hitters · '
            f'Generated {st.session_state["ai_parlay_generated_at"]}'
            f'</div>',
            unsafe_allow_html=True,
        )

    _seed = int(st.session_state["ai_parlay_seed"])

    # Sleeper-hunt boost: under-owned bats (low season HR + lower spot)
    # get extra weight so sleepers can win a slot. Aggressive gets a
    # smaller version of the same boost; Safer doesn't.
    if _risk == "Sleeper Hunt":
        sleeper_bias = 0.55
    elif _risk == "Aggressive":
        sleeper_bias = 0.25
    elif _risk == "Balanced":
        sleeper_bias = 0.12
    else:
        sleeper_bias = 0.0

    def _weighted_sample_legs(_pool, n: int,
                              avoid_same_game: bool, avoid_same_team: bool,
                              seed: int):
        """Weighted sample on AI HR Score with sleeper diversity boost.
        Higher AI HR Score -> higher pick probability; sleeper bias raises
        weight on bats with low season HR + lower lineup spot."""
        if _pool is None or _pool.empty:
            return []
        rng = np.random.default_rng(int(seed))
        scores = _pool["AI HR Score"].fillna(0).to_numpy(dtype=float)
        weights = np.clip(scores, 1.0, None) ** 1.4
        if sleeper_bias > 0:
            hr_szn = _pool["HR (Season)"].fillna(15).to_numpy(dtype=float)
            spot   = _pool["Spot"].fillna(5).to_numpy(dtype=float)
            sleeper_factor = (
                np.clip((20.0 - hr_szn) / 20.0, 0.0, 1.0) * 0.6 +
                np.clip((spot - 2.0)    /  7.0, 0.0, 1.0) * 0.4
            )
            weights = weights * (1.0 + sleeper_bias * sleeper_factor)
        if not np.isfinite(weights).any() or weights.sum() <= 0:
            weights = np.ones_like(scores)

        rows = list(_pool.to_dict("records"))
        idxs = list(range(len(rows)))
        legs, used_games, used_teams = [], set(), set()
        available = idxs.copy()
        avail_w = weights.copy()
        while available and len(legs) < n:
            w = avail_w[available]
            if w.sum() <= 0:
                pick_local = int(rng.integers(0, len(available)))
            else:
                p = w / w.sum()
                pick_local = int(rng.choice(len(available), p=p))
            pick = available.pop(pick_local)
            row = rows[pick]
            g = str(row.get("Game", "") or "")
            t = str(row.get("Team", "") or "")
            if avoid_same_game and g and g in used_games:
                continue
            if avoid_same_team and t and t in used_teams:
                continue
            legs.append(row)
            if g: used_games.add(g)
            if t: used_teams.add(t)

        if len(legs) < n:
            taken_names = {l.get("Hitter") for l in legs}
            for row in rows:
                if len(legs) >= n: break
                if row.get("Hitter") in taken_names: continue
                legs.append(row)
        return legs[:n]

    def _pick_legs(_pool, n, avoid_same_game, avoid_same_team, seed=0):
        return _weighted_sample_legs(_pool, n, avoid_same_game,
                                     avoid_same_team, seed)

    def _reasons_for(row) -> list:
        """Compact, data-driven reasons. Always tries to include Ceiling +
        Zone Fit when notable, then the strongest power/matchup signals."""
        reasons = []
        def _f(v):
            try:
                if v is None: return None
                f = float(v)
                if pd.isna(f): return None
                return f
            except Exception:
                return None

        ceil   = _f(row.get("Ceiling"))
        zfv    = _f(row.get("Zone Fit"))
        barrel = _f(row.get("Barrel%"))
        hh     = _f(row.get("HardHit%"))
        iso    = _f(row.get("ISO"))
        xslg   = _f(row.get("xSLG"))
        ops    = _f(row.get("OPS"))
        fb     = _f(row.get("FB%"))
        pull   = _f(row.get("Pull%"))
        match  = _f(row.get("Matchup"))
        khr    = _f(row.get("kHR"))
        bs     = _f(row.get("BatSpeed"))
        ss     = _f(row.get("SweetSpot%"))
        spot   = row.get("Spot")
        hr_szn = row.get("HR (Season)")
        overlap = list(row.get("__overlap") or [])
        crush_codes = list(row.get("__crush") or [])

        # 1. Ceiling — always prioritize when notable
        if ceil is not None:
            if ceil >= 80:
                reasons.append(f"🏟️ Ceiling <b>{ceil:.0f}</b> — elite park/weather/SP combo")
            elif ceil >= 65:
                reasons.append(f"🏟️ Ceiling <b>{ceil:.0f}</b>")

        # 2. Zone fit / pitch overlap — always priority when strong
        if overlap:
            labs = ", ".join(_pitch_label(c) for c in overlap[:2] if c)
            opp_sp = str(row.get("Opp SP") or "SP")
            reasons.append(f"🎯 Crushes <b>{labs}</b> vs {opp_sp} arsenal")
        elif zfv is not None and zfv >= 0.090:
            reasons.append(f"🎯 Zone fit <b>+</b> ({zfv:.3f}) — hot zones align")
        elif crush_codes and crush_codes[0]:
            reasons.append(f"🎯 Punishes <b>{_pitch_label(crush_codes[0])}</b> profile")

        # 3. Power — Barrel%, HardHit%, ISO, xSLG, OPS
        # OPS sits near the top of the candidate list because it captures both
        # on-base ability and slugging in one number; an .850+ OPS is a strong
        # standalone "this bat is producing" signal independent of barrel/ISO.
        cands = []
        if ops is not None and ops >= 0.900:
            cands.append((ops * 100, f"🔥 OPS <b>{ops:.3f}</b> — MVP-tier bat"))
        elif ops is not None and ops >= 0.800:
            cands.append((ops * 95, f"🔥 OPS <b>{ops:.3f}</b> — premium bat"))
        elif ops is not None and ops >= 0.730:
            cands.append((ops * 80, f"🔥 OPS <b>{ops:.3f}</b>"))
        if barrel is not None and barrel >= 9.0:
            cands.append((barrel, f"💥 Barrel% <b>{barrel:.1f}</b> — elite contact"))
        elif barrel is not None and barrel >= 7.0:
            cands.append((barrel, f"💥 Barrel% <b>{barrel:.1f}</b>"))
        if hh is not None and hh >= 42.0:
            cands.append((hh, f"💪 HardHit% <b>{hh:.1f}</b> — squares up"))
        elif hh is not None and hh >= 38.0:
            cands.append((hh, f"💪 HardHit% <b>{hh:.1f}</b>"))
        if iso is not None and iso >= 0.220:
            cands.append((iso * 100, f"⚡ ISO <b>{iso:.3f}</b> — top-tier raw power"))
        elif iso is not None and iso >= 0.170:
            cands.append((iso * 100, f"⚡ ISO <b>{iso:.3f}</b>"))
        if xslg is not None and xslg >= 0.500:
            cands.append((xslg * 100, f"📈 xSLG <b>{xslg:.3f}</b> — slugger tier"))
        if fb is not None and fb >= 38.0:
            cands.append((fb, f"🚀 FB% <b>{fb:.1f}</b> — gets it airborne"))
        if pull is not None and pull >= 42.0:
            cands.append((pull, f"↗️ Pull% <b>{pull:.1f}</b> — into HR alley"))
        if bs is not None and bs >= 73.0:
            cands.append((bs, f"🏏 Bat Speed <b>{bs:.1f}</b> mph"))
        if ss is not None and ss >= 35.0:
            cands.append((ss, f"🎯 SweetSpot% <b>{ss:.1f}</b>"))
        if match is not None and match >= 110.0:
            cands.append((match, f"🆚 Matchup <b>{match:.0f}</b> vs {row.get('Opp SP','SP')}"))
        if khr is not None and khr >= 1.10:
            cands.append((khr * 60, f"📊 kHR <b>{khr:.2f}</b>"))

        try:
            sp = int(spot)
            if 3 <= sp <= 5:
                cands.append((90, f"📋 <b>{sp}-hole</b> — heart of the order"))
            elif sp <= 2:
                cands.append((85, f"📋 Bats <b>{sp}</b> — extra PA upside"))
        except Exception:
            pass
        try:
            hrn = int(hr_szn)
            if hrn <= 5 and (barrel or 0) >= 7.0:
                cands.append((70, f"😴 Only <b>{hrn}</b> HR on year — under-owned sleeper"))
        except Exception:
            pass

        cands.sort(key=lambda x: -x[0])
        seen = {r.split("<b>")[0] for r in reasons}
        for _, txt in cands:
            key = txt.split("<b>")[0]
            if key in seen: continue
            seen.add(key)
            reasons.append(txt)
            if len(reasons) >= 5:
                break

        if not reasons:
            reasons.append("🔢 Composite AI HR Score above slate threshold")
        return reasons[:5]

    legs_2 = _pick_legs(pool, 2, _avoid_same_game, _avoid_same_team, seed=_seed)
    legs_3 = _pick_legs(pool, 3, _avoid_same_game, _avoid_same_team, seed=_seed * 1000 + 7)
    alt_2_a = _pick_legs(pool, 2, _avoid_same_game, _avoid_same_team, seed=_seed + 101)
    alt_2_b = _pick_legs(pool, 2, _avoid_same_game, _avoid_same_team, seed=_seed + 211)
    alt_3_a = _pick_legs(pool, 3, _avoid_same_game, _avoid_same_team, seed=_seed * 1000 + 313)
    alt_3_b = _pick_legs(pool, 3, _avoid_same_game, _avoid_same_team, seed=_seed * 1000 + 521)

    def _tier(score):
        try: s = float(score)
        except Exception: return ("ok", "Average")
        if s >= 75: return ("elite",  "Elite")
        if s >= 65: return ("strong", "Strong")
        if s >= 55: return ("ok",     "Average")
        return ("soft", "Soft")

    def _fmt_or_dash(v, fmt):
        try:
            if v is None: return "—"
            f = float(v)
            if pd.isna(f): return "—"
            return fmt.format(f)
        except Exception:
            return "—"

    def _compact_stats_line(leg) -> str:
        """One-line compact stats: Ceiling · OPS · Barrel · HH · ISO · Zone fit · Spot.

        OPS sits between Ceiling and the contact metrics so the leg card
        reads as: environment first, overall bat quality next, then the
        HR-specific signals that pushed the leg up the board.
        """
        parts = []
        ceil = leg.get("Ceiling")
        try:
            cf = float(ceil)
            if not pd.isna(cf):
                parts.append(f"Ceiling <b>{cf:.0f}</b>")
        except Exception:
            pass
        op = fmt_ops(get_ops_value(leg))
        if op != "—":
            parts.append(f"OPS <b>{op}</b>")
        b = _fmt_or_dash(leg.get("Barrel%"),  "{:.1f}%")
        if b != "—": parts.append(f"Barrel <b>{b}</b>")
        h = _fmt_or_dash(leg.get("HardHit%"), "{:.1f}%")
        if h != "—": parts.append(f"HH <b>{h}</b>")
        i = _fmt_or_dash(leg.get("ISO"),      "{:.3f}")
        if i != "—": parts.append(f"ISO <b>{i}</b>")
        # Zone fit symbol
        try:
            zfv = float(leg.get("Zone Fit"))
            if not pd.isna(zfv):
                if (leg.get("__overlap") or []) or zfv >= 0.090:
                    parts.append("Zone fit <b>+</b>")
                elif zfv <= 0.040:
                    parts.append("Zone fit <b>−</b>")
        except Exception:
            pass
        # Lineup spot
        try:
            sp = int(leg.get("Spot"))
            if sp <= 9:
                parts.append(f"<b>{sp}-hole</b>")
        except Exception:
            pass
        return " · ".join(parts) if parts else "—"

    def _bpw_for_leg(leg) -> dict:
        """Resolve a Good/OK/Bad ballpark+weather indicator for one parlay leg.

        Order of resolution:
          1. Use BPW columns already on the leg dict (set by build_hr_sleepers_table).
          2. Fall back to the per-game map keyed by short_label.
          3. Final default = neutral OK.

        Returns a dict with str fields safe to interpolate into HTML, including
        a pre-formatted 'hr_str' like "+8%" / "-6%" (or "" if unknown).
        """
        def _is_blank(v):
            try:
                if v is None: return True
                if isinstance(v, float) and pd.isna(v): return True
                if pd.isna(v): return True
            except Exception:
                pass
            s = str(v).strip()
            return s == "" or s.lower() in ("nan", "none")

        label = leg.get("BPW Label")
        tier  = leg.get("BPW Tier")
        icon  = leg.get("BPW Icon")
        tip   = leg.get("BPW Tip")
        hr_pct = leg.get("BPW HR%")

        if any(_is_blank(x) for x in (label, tier, icon)):
            fallback = _bpw_game_map.get(str(leg.get("Game", "")))
            if fallback:
                label  = label  if not _is_blank(label)  else fallback.get("label")
                tier   = tier   if not _is_blank(tier)   else fallback.get("tier")
                icon   = icon   if not _is_blank(icon)   else fallback.get("icon")
                tip    = tip    if not _is_blank(tip)    else fallback.get("tooltip")
                if _is_blank(hr_pct):
                    hr_pct = fallback.get("hr_pct")

        # Final defaults so something always renders.
        if _is_blank(label): label = "OK"
        if _is_blank(tier):  tier  = "ok"
        if _is_blank(icon):  icon  = "🟡"
        if _is_blank(tip):   tip   = "Park/weather signal"

        try:
            hr_int = int(float(hr_pct))
            hr_str = f"{hr_int:+d}%"
        except Exception:
            hr_str = ""

        return {
            "label": str(label),
            "tier":  str(tier),
            "icon":  str(icon),
            "tooltip": str(tip).replace('"', "'"),
            "hr_str": hr_str,
        }

    def _render_parlay_card(title: str, legs: list, badge: str) -> str:
        if not legs:
            return (
                f'<div class="aip-card aip-empty">'
                f'<div class="aip-card-title">{title}</div>'
                f'<div class="aip-card-sub">Not enough qualifying legs. '
                f'Loosen the filters above.</div>'
                f'</div>'
            )
        avg_score = sum(float(l.get("AI HR Score", 0) or 0) for l in legs) / max(1, len(legs))
        tier_cls, _ = _tier(avg_score)
        leg_html = []
        for i, leg in enumerate(legs, 1):
            t_cls, t_lbl = _tier(leg.get("AI HR Score"))
            reasons = _reasons_for(leg)
            reason_html = "".join(
                f'<li style="margin:2px 0;">{r}</li>' for r in reasons
            )
            try:
                ai_str = f"{float(leg.get('AI HR Score', 0)):.1f}"
            except Exception:
                ai_str = "—"
            stats_line = _compact_stats_line(leg)
            bpw = _bpw_for_leg(leg)
            bpw_label = bpw["label"]
            bpw_tier  = bpw["tier"]
            bpw_icon  = bpw["icon"]
            bpw_tip   = bpw["tooltip"]
            bpw_hr_str = bpw["hr_str"]
            bpw_line = (
                f'<div class="aip-bpw-line aip-bpw-{bpw_tier}" title="{bpw_tip}">'
                f'{bpw_icon} <b>{bpw_label} Ballpark Weather</b>'
                + (f' <span class="aip-bpw-pct">({bpw_hr_str} HR)</span>'
                   if bpw_hr_str else '')
                + f'</div>'
            )
            leg_html.append(
                f'<div class="aip-leg">'
                f'  <div class="aip-leg-head">'
                f'    <div class="aip-leg-num">Leg {i}</div>'
                f'    <div class="aip-leg-name">{leg.get("Hitter","")}'
                f'      <span class="aip-meta">· {leg.get("Team","")} · '
                f'Bat {format_batter_stance(leg.get("Bat",""))} · Spot {leg.get("Spot","")}</span>'
                f'    </div>'
                f'    <div class="aip-leg-score">'
                f'      <span class="aip-score">{ai_str}</span>'
                f'      <span class="hrs-pill {t_cls}">{t_lbl}</span>'
                f'    </div>'
                f'  </div>'
                f'  <div class="aip-ctx">{leg.get("Game","")} '
                f'<span style="color:#94a3b8;">·</span> vs '
                f'<b>{leg.get("Opp SP","")}</b></div>'
                f'  {bpw_line}'
                f'  <div class="aip-stats">{stats_line}</div>'
                f'  <ul class="aip-reasons">{reason_html}</ul>'
                f'</div>'
            )

        return (
            f'<div class="aip-card">'
            f'  <div class="aip-card-head">'
            f'    <div>'
            f'      <div class="aip-card-title">{title}</div>'
            f'      <div class="aip-card-sub">{len(legs)} legs · '
            f'avg AI HR Score <b>{avg_score:.1f}</b></div>'
            f'    </div>'
            f'    <span class="hrs-pill {tier_cls} aip-badge">{badge}</span>'
            f'  </div>'
            + "".join(leg_html) +
            f'</div>'
        )

    css = (
        "<style>"
        ".aip-card { background:linear-gradient(180deg,#111827 0%,#0b1220 100%); "
        "  border-radius:14px; padding:14px 14px 8px 14px; "
        "  box-shadow:0 2px 16px rgba(0,0,0,.45); margin: 8px 0 16px 0; "
        "  border-left:5px solid #7c3aed; border:1px solid rgba(124,58,237,.25); "
        "  border-left:5px solid #7c3aed; }"
        ".aip-card.aip-empty { border-left-color:#334155; padding:14px; }"
        ".aip-card-head { display:flex; align-items:center; justify-content:space-between; "
        "  gap:10px; margin-bottom:6px; flex-wrap:wrap; }"
        ".aip-card-title { font-weight:900; font-size:1.08rem; color:#e2e8f0; "
        "  letter-spacing:.01em; }"
        ".aip-card-sub { color:#94a3b8; font-size:.82rem; margin-top:2px; }"
        ".aip-badge { font-size:.74rem; }"
        ".aip-leg { padding:10px 0; border-top:1px dashed rgba(255,255,255,.08); }"
        ".aip-leg:first-of-type { border-top:none; }"
        ".aip-leg-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }"
        ".aip-leg-num { font-size:.72rem; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; color:#fcd34d; background:#3b1f6b; "
        "  padding:3px 8px; border-radius:6px; }"
        ".aip-leg-name { font-weight:900; color:#ffffff; flex:1 1 200px; "
        "  font-size:1rem; text-shadow:0 1px 3px rgba(0,0,0,.5); }"
        ".aip-meta { color:#7dd3fc; font-weight:600; font-size:.82rem; }"
        ".aip-leg-score { display:flex; align-items:center; gap:6px; }"
        ".aip-score { font-weight:900; font-size:1.05rem; color:#00c896; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-ctx { color:#94a3b8; font-size:.84rem; margin: 4px 0 4px 0; }"
        ".aip-bpw-line { display:block; margin: 4px 0 6px 0; padding:6px 10px; "
        "  border-radius:8px; font-size:.86rem; font-weight:700; line-height:1.35; "
        "  border:1px solid transparent; }"
        ".aip-bpw-line b { font-weight:800; }"
        ".aip-bpw-pct { font-weight:700; margin-left:4px; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-bpw-good { background:rgba(34,197,94,.15); color:#4ade80; border-color:rgba(34,197,94,.3); }"
        ".aip-bpw-ok   { background:rgba(250,204,21,.12); color:#fde68a; border-color:rgba(250,204,21,.3); }"
        ".aip-bpw-bad  { background:rgba(239,68,68,.12); color:#fca5a5; border-color:rgba(239,68,68,.3); }"
        ".aip-stats { color:#e2e8f0; font-size:.84rem; margin: 0 0 4px 0; "
        "  background:rgba(255,255,255,.05); border-radius:6px; padding:5px 8px; "
        "  font-variant-numeric: tabular-nums; border:1px solid rgba(255,255,255,.07); }"
        ".aip-reasons { margin: 4px 0 2px 18px; padding:0; color:#cbd5e1; "
        "  font-size:.88rem; }"
        ".aip-reasons li { margin: 1px 0; line-height:1.35; }"
        ".aip-disclaimer { color:#64748b; font-size:.78rem; margin: 6px 2px 12px 2px; "
        "  font-style:italic; }"
        "@media (max-width:520px) { .aip-leg-name { font-size:.94rem; } "
        "  .aip-card-title { font-size:1rem; } .aip-reasons { font-size:.84rem; } "
        "  .aip-stats { font-size:.80rem; } }"
        "</style>"
    )
    st.markdown(css, unsafe_allow_html=True)

    badge_2 = f"{_risk} · 2-leg · #{_seed}"
    badge_3 = f"{_risk} · 3-leg · #{_seed}"
    st.markdown(_render_parlay_card("🎯 Recommended 2-Leg HR Parlay", legs_2, badge_2),
                unsafe_allow_html=True)
    st.markdown(_render_parlay_card("🚀 Recommended 3-Leg HR Parlay", legs_3, badge_3),
                unsafe_allow_html=True)

    if _show_alts:
        def _sig(legs_):
            return tuple(sorted(str(l.get("Hitter", "")) for l in (legs_ or [])))
        seen_2 = {_sig(legs_2)}
        seen_3 = {_sig(legs_3)}
        extra_seeds = [331, 433, 547, 659, 773, 887]
        alt_2_pool = [alt_2_a, alt_2_b] + [
            _pick_legs(pool, 2, _avoid_same_game, _avoid_same_team, seed=_seed + s)
            for s in extra_seeds
        ]
        alt_3_pool = [alt_3_a, alt_3_b] + [
            _pick_legs(pool, 3, _avoid_same_game, _avoid_same_team, seed=_seed * 1000 + s)
            for s in extra_seeds
        ]
        chosen_2, chosen_3 = [], []
        for cand in alt_2_pool:
            sig = _sig(cand)
            if sig in seen_2 or not cand: continue
            seen_2.add(sig); chosen_2.append(cand)
            if len(chosen_2) >= 2: break
        for cand in alt_3_pool:
            sig = _sig(cand)
            if sig in seen_3 or not cand: continue
            seen_3.add(sig); chosen_3.append(cand)
            if len(chosen_3) >= 2: break
        if chosen_2 or chosen_3:
            st.markdown(
                '<div class="section-title" style="font-size:1.08rem;margin-top:6px;">'
                '🔁 Alternate Tickets</div>',
                unsafe_allow_html=True,
            )
        for i, cand in enumerate(chosen_2, 1):
            st.markdown(
                _render_parlay_card(
                    f"🎯 Alt 2-Leg Parlay #{i}", cand, f"{_risk} · 2-leg · alt {i}"
                ),
                unsafe_allow_html=True,
            )
        for i, cand in enumerate(chosen_3, 1):
            st.markdown(
                _render_parlay_card(
                    f"🚀 Alt 3-Leg Parlay #{i}", cand, f"{_risk} · 3-leg · alt {i}"
                ),
                unsafe_allow_html=True,
            )

    # Source freshness row — odds chip removed; AI tab is feed-independent.
    st.markdown(
        render_source_chips([
            "savant:batters", "savant:pitchers",
            "statsapi:schedule", "statsapi:boxscore",
            "rotogrinders:weather", "openmeteo:weather",
        ]),
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="aip-disclaimer">'
        '⚠️ <b>Disclaimer:</b> Recommendations are model-driven analytics built '
        'from public Statcast / StatsAPI / weather data — <b>not guaranteed outcomes</b>. '
        'Verify lineups with your sportsbook before placing any bet. Bet responsibly.'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.expander("How the AI HR Score works"):
        st.markdown(
            "**AI HR Score (0-100)** — augments HR Sleeper Score with explicit "
            "Ceiling and pitch-zone fit weighting:\n"
            "- **55%** HR Sleeper Score (Barrel%, HardHit%, ISO, FB%, Pull%, "
            "Matchup, Ceiling, kHR, sleeper bonus)\n"
            "- **18% Ceiling** — park × weather × raw power × opposing SP\n"
            "- **10% Zone Fit** — hitter hot zones (pull/FB/LD profile + platoon) "
            "vs pitcher tendencies\n"
            "- **7% Pitch overlap** — hitter's top crushed pitches actually appear "
            "in the opposing SP's arsenal\n"
            "- **5% Bat Speed** (Savant bat-tracking)\n"
            "- **5% Composite power** (xSLG + SweetSpot%)\n\n"
            "**Pool:** every eligible lineup hitter on upcoming/live games — "
            "not just the top-N stars. **Sleeper Hunt** profile boosts under-owned "
            "bats (low season HR + lower lineup spot) so sleepers can win a slot.\n\n"
            "**Selection:** weighted sampling by AI HR Score (no pure top-rank "
            "sort), with Avoid-same-game/team constraints. **Generate New Parlays** "
            "rerolls within the same eligible pool."
        )
    st.stop()


# ============== HR Round Robin view (MRBETS850) ==============
# Stable, deterministic top-5 HR parlay generator for the slate. Picks the
# 5 best overall HR plays using every available metric — not just the biggest
# names. Same eligibility rules as AI HR Parlay (no completed games). Output
# is presented as a polished round-robin ticket with player cards and the
# standard round-robin combo summary.
if _view == "👑 HR Round Robin":
    # ----- Headline + logo -------------------------------------------------
    st.markdown(
        "<style>"
        ".rr-hero { display:flex; align-items:center; gap:18px; "
        "  background: linear-gradient(110deg, #14062e 0%, #3b1f6b 55%, #6b21a8 100%); "
        "  border: 2px solid #facc15; border-radius:18px; "
        "  padding:14px 18px; margin: 6px 0 14px 0; "
        "  box-shadow: 0 0 0 3px rgba(250,204,21,.18), 0 8px 22px rgba(5,20,12,.35); }"
        ".rr-hero img { width:84px; height:84px; object-fit:cover; "
        "  border-radius:14px; border:2px solid #facc15; "
        "  box-shadow: 0 4px 10px rgba(0,0,0,.35); flex: 0 0 84px; }"
        ".rr-hero .rr-title { font-weight:900; color:#facc15; "
        "  letter-spacing:.04em; font-size:1.45rem; line-height:1.05; "
        "  text-transform:uppercase; }"
        ".rr-hero .rr-sub   { color:#fde68a; font-weight:600; font-size:.92rem; "
        "  margin-top:4px; opacity:.95; }"
        "@media (max-width:520px) { "
        "  .rr-hero { padding:12px; gap:12px; } "
        "  .rr-hero img { width:64px; height:64px; flex-basis:64px; } "
        "  .rr-hero .rr-title { font-size:1.15rem; } "
        "  .rr-hero .rr-sub   { font-size:.84rem; } }"
        # Player cards
        ".rr-card { background:linear-gradient(180deg,#111827 0%,#0b1220 100%); "
        "  border-radius:14px; padding:12px 14px; "
        "  box-shadow:0 2px 16px rgba(0,0,0,.45); margin: 8px 0; "
        "  border:1px solid rgba(250,204,21,.2); border-left:6px solid #facc15; }"
        ".rr-card .rr-rank { display:inline-block; min-width:28px; "
        "  text-align:center; font-weight:900; color:#facc15; "
        "  background:#3b1f6b; border-radius:6px; padding:3px 8px; "
        "  font-size:.78rem; letter-spacing:.06em; }"
        ".rr-card .rr-name { font-weight:900; color:#ffffff; font-size:1.04rem; "
        "  margin-left:8px; text-shadow:0 1px 3px rgba(0,0,0,.5); }"
        ".rr-card .rr-meta { color:#94a3b8; font-size:.84rem; margin-top:2px; }"
        ".rr-card .rr-score { float:right; font-weight:900; color:#00c896; "
        "  font-size:1.06rem; font-variant-numeric: tabular-nums; }"
        ".rr-card .rr-stats { color:#e2e8f0; font-size:.84rem; "
        "  background:rgba(255,255,255,.05); border-radius:6px; padding:6px 9px; margin-top:6px; "
        "  font-variant-numeric: tabular-nums; border:1px solid rgba(255,255,255,.07); }"
        ".rr-card .rr-why { margin: 6px 0 2px 18px; padding:0; color:#cbd5e1; "
        "  font-size:.86rem; }"
        ".rr-card .rr-why li { margin: 1px 0; line-height:1.35; }"
        # Combos panel
        ".rr-combos { background:#3b1f6b; color:#fde68a; border-radius:14px; "
        "  padding:14px 16px; margin: 12px 0 8px 0; "
        "  border:2px solid #facc15; "
        "  box-shadow: 0 0 0 3px rgba(250,204,21,.15), 0 6px 18px rgba(5,20,12,.30); }"
        ".rr-combos h4 { color:#facc15; margin: 0 0 8px 0; "
        "  letter-spacing:.04em; font-size:1.02rem; font-weight:900; "
        "  text-transform:uppercase; }"
        ".rr-combos table { width:100%; border-collapse:collapse; "
        "  font-variant-numeric: tabular-nums; }"
        ".rr-combos td { padding:6px 4px; border-bottom:1px dashed rgba(250,204,21,.25); "
        "  font-size:.92rem; }"
        ".rr-combos td:last-child { text-align:right; font-weight:800; color:#fff; }"
        ".rr-combos tr:last-child td { border-bottom:none; }"
        ".rr-disclaimer { color:#64748b; font-size:.78rem; "
        "  margin: 8px 2px 12px 2px; font-style:italic; }"
        "</style>",
        unsafe_allow_html=True,
    )
    _logo_img = (
        f'<img src="{LOGO_URI}" alt="MRBETS850" />'
        if LOGO_URI else ''
    )
    st.markdown(
        f'<div class="rr-hero">'
        f'  {_logo_img}'
        f'  <div>'
        f'    <div class="rr-title">MRBETS850 Homerun Round Robin</div>'
        f'    <div class="rr-sub">Top 5 HR plays of the slate · '
        f'data-locked daily ticket · powered by every available metric</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if batters_df.empty:
        st.warning("Batter CSV (`Data:savant_batters.csv.csv`) hasn’t loaded yet.")
        st.stop()

    # ----- Eligibility filter (same logic as AI HR Parlay) -----------------
    # Round Robin is a batter-pick generator, so we exclude any game that
    # has already started — lineups are locked and in-game state is
    # unreliable for picking HR plays.
    _RR_COMPLETED = (
        "final", "completed", "game over", "postponed", "cancelled",
        "canceled", "suspended", "forfeit", "if necessary",
    )
    _RR_STARTED = (
        "in progress", "live", "manager challenge", "review",
        "delayed",
    )
    _RR_PRE = (
        "scheduled", "pre-game", "pregame", "pre game",
        "warmup", "warm-up", "warm up",
    )

    def _rr_eligible(row) -> bool:
        status = str(row.get("status") or "").strip().lower()
        if any(t in status for t in _RR_COMPLETED): return False
        if any(t in status for t in _RR_STARTED):   return False
        gt = row.get("game_time_utc")
        try:    start_utc = pd.to_datetime(gt, utc=True)
        except Exception: start_utc = pd.NaT
        _now = pd.Timestamp.now('UTC')
        now_utc = _now if _now.tzinfo is not None else _now.tz_localize("UTC")
        if any(t in status for t in _RR_PRE):
            if pd.isna(start_utc): return True
            return start_utc > now_utc
        if pd.isna(start_utc): return False
        return start_utc > now_utc

    if schedule_df is None or schedule_df.empty:
        st.warning("No games on the slate. Pick a different date or check back later.")
        st.stop()

    rr_elig_mask  = schedule_df.apply(_rr_eligible, axis=1)
    rr_schedule   = schedule_df[rr_elig_mask].reset_index(drop=True)
    _rr_total = int(len(schedule_df))
    _rr_excl  = _rr_total - int(len(rr_schedule))
    if rr_schedule.empty:
        st.warning(
            "All games on this slate have already started or finished. "
            "Round Robin can't lock a ticket from started games."
        )
        st.stop()

    st.markdown(
        f'<div style="margin:0 0 10px 0; padding:8px 12px; '
        f'border-left:3px solid #facc15; background:rgba(250,204,21,0.1); '
        f'border-radius:6px; color:#fde68a; font-size:0.88rem;">'
        f'🔒 Locked ticket for <b>{selected_date}</b> · '
        f'using <b>{len(rr_schedule)}</b> pre-game matchup'
        f'{"s" if len(rr_schedule) != 1 else ""}'
        f'{f" · started/completed excluded ({_rr_excl} hidden)" if _rr_excl > 0 else ""}.'
        f'</div>',
        unsafe_allow_html=True,
    )

    try:
        _slate_iso_rr = pd.to_datetime(selected_date).strftime("%Y-%m-%d")
    except Exception:
        _slate_iso_rr = ""
    with st.spinner("Scoring HR candidates across every available metric…"):
        rr_pool_df = build_hr_sleepers_table(rr_schedule, batters_df, pitchers_df,
                                              slate_date_iso=_slate_iso_rr)

    if rr_pool_df is None or rr_pool_df.empty:
        st.info("No lineups posted yet. Check back closer to first pitch.")
        st.stop()

    # Merge in extra batter columns we'll use for the composite score.
    rr_pool = rr_pool_df.copy()
    if not batters_df.empty:
        keep = [c for c in
            ["name_key","BatSpeed","SweetSpot%","LA","xwOBA","K%","BB%"]
            if c in batters_df.columns]
        if "name_key" in batters_df.columns and len(keep) > 1:
            extra = batters_df[keep].drop_duplicates("name_key")
            rr_pool["__nk"] = rr_pool["Hitter"].astype(str).map(clean_name)
            rr_pool = rr_pool.merge(extra, left_on="__nk", right_on="name_key",
                                    how="left", suffixes=("", "_b"))
            rr_pool = rr_pool.drop(columns=[c for c in ["__nk","name_key"]
                                            if c in rr_pool.columns])

    # ----- Composite HR Round Robin Score ---------------------------------
    # Uses every available HR signal in the pool. Each metric is normalized
    # to 0..100 then weighted. Missing values get a neutral 50 so a hitter
    # isn't punished for a blank column they don't have data for.
    def _norm(v, lo, hi, default=50.0):
        try:
            f = float(v)
            if pd.isna(f): return default
            return max(0.0, min(100.0, (f - lo) / (hi - lo) * 100.0))
        except Exception:
            return default

    def _rr_composite_score(row) -> float:
        # Base sleeper / AI components (already 0..100 in the codebase)
        sleeper = float(row.get("Sleeper Score") or 0.0)
        ceiling = float(row.get("Ceiling") or 0.0)
        match   = float(row.get("Matchup") or 100.0)  # 100 = neutral
        # Power / quality of contact metrics
        n_barrel = _norm(row.get("Barrel%"),    5.0,  18.0)
        n_hh     = _norm(row.get("HardHit%"),  30.0,  55.0)
        n_iso    = _norm(row.get("ISO"),        0.130, 0.300)
        n_xslg   = _norm(row.get("xSLG"),       0.350, 0.580)
        n_xwoba  = _norm(row.get("xwOBA"),      0.300, 0.430)
        n_fb     = _norm(row.get("FB%"),       25.0,  48.0)
        n_pull   = _norm(row.get("Pull%"),     30.0,  50.0)
        n_ss     = _norm(row.get("SweetSpot%"),28.0,  42.0)
        n_la     = _norm(row.get("LA"),        10.0,  20.0)
        n_bs     = _norm(row.get("BatSpeed"),  68.0,  76.0)
        n_khr    = _norm(row.get("kHR"),        0.85,  1.30)
        n_match  = _norm(match,                85.0, 125.0)
        # OPS = OBP + SLG. Universal "is the bat producing?" scalar. Read via
        # get_ops_value so rows missing the precomputed OPS still get a value
        # from the OBP / SLG components. Falls back to the .500 default when
        # nothing is available so a blank line doesn't punish the hitter.
        n_ops    = _norm(get_ops_value(row), 0.620, 0.950)
        # Lineup spot — heart of order best, late spots discounted
        try:
            sp = int(row.get("Spot") or 9)
        except Exception:
            sp = 9
        spot_bonus = {1:80, 2:88, 3:100, 4:100, 5:92, 6:78, 7:60, 8:45, 9:35}.get(sp, 35)
        # Season HR: real raw power signal but capped so it doesn't dominate
        try:
            hr_szn = float(row.get("HR (Season)") or 0)
        except Exception:
            hr_szn = 0.0
        n_hr_szn = max(0.0, min(100.0, (hr_szn / 25.0) * 100.0))
        # Composite — broad coverage, no single metric dominates. OPS is a
        # 4% weight: meaningful enough to help break ties between hitters
        # with similar power profiles but very different overall production,
        # small enough that it never overrides the dominant HR-specific
        # signals (Barrel%, ISO, FB%, Ceiling).
        composite = (
            0.21 * sleeper      # base HR Sleeper Score (already broad)
          + 0.15 * ceiling      # park × weather × SP
          + 0.08 * n_barrel
          + 0.06 * n_hh
          + 0.05 * n_iso
          + 0.05 * n_xslg
          + 0.04 * n_xwoba
          + 0.04 * n_fb
          + 0.03 * n_pull
          + 0.03 * n_ss
          + 0.03 * n_la
          + 0.05 * n_bs
          + 0.03 * n_khr
          + 0.04 * n_ops
          + 0.05 * n_match
          + 0.05 * spot_bonus
          + 0.03 * n_hr_szn
        )
        return round(max(0.0, min(100.0, composite)), 2)

    rr_pool["RR Score"] = rr_pool.apply(_rr_composite_score, axis=1)

    # Stable tie-break: by RR Score desc, then Hitter name asc.
    rr_pool = rr_pool.sort_values(
        ["RR Score", "Hitter"],
        ascending=[False, True],
    ).reset_index(drop=True)

    # ----- Reroll controls --------------------------------------------------
    # Lineups are "fully locked" only when every hitter in the eligible pool
    # is officially Confirmed. Until then, allow clients to reroll a fresh
    # slate of 5 from the qualifying candidate pool.
    rr_lineup_states = (
        rr_pool["LineupStatus"].astype(str).tolist()
        if "LineupStatus" in rr_pool.columns else []
    )
    rr_all_confirmed = bool(rr_lineup_states) and all(
        s == "Confirmed" for s in rr_lineup_states
    )

    if "rr_reroll_seed" not in st.session_state:
        st.session_state["rr_reroll_seed"] = 0  # 0 = deterministic baseline

    rr_ctrl_l, rr_ctrl_r = st.columns([3, 1])
    with rr_ctrl_l:
        if rr_all_confirmed:
            st.markdown(
                '<div style="margin:0 0 8px 0; padding:8px 12px; '
                'border-left:3px solid #16a34a; background:#ecfdf5; '
                'border-radius:6px; color:#065f46; font-size:0.88rem;">'
                '✅ <b>All lineups confirmed</b> — ticket is locked. '
                'Reroll disabled.</div>',
                unsafe_allow_html=True,
            )
        else:
            n_conf = sum(1 for s in rr_lineup_states if s == "Confirmed")
            n_total = len(rr_lineup_states)
            st.markdown(
                f'<div style="margin:0 0 8px 0; padding:8px 12px; '
                f'border-left:3px solid #2563eb; background:#eff6ff; '
                f'border-radius:6px; color:#1e3a8a; font-size:0.88rem;">'
                f'🔁 <b>Lineups still updating</b> — {n_conf}/{n_total} '
                f'confirmed. Reroll to see different player options until '
                f'lineups lock.</div>',
                unsafe_allow_html=True,
            )
    with rr_ctrl_r:
        if st.button(
            "🎲 Reroll",
            key="rr_reroll_btn",
            disabled=rr_all_confirmed,
            help=("Generates a fresh top-5 from the qualifying HR candidate "
                  "pool. Disabled once all lineups are confirmed."),
            width='stretch',
        ):
            # Bump seed each click so st.session_state drives a new slate.
            st.session_state["rr_reroll_seed"] = int(
                st.session_state.get("rr_reroll_seed", 0)
            ) + 1

    rr_seed = int(st.session_state.get("rr_reroll_seed", 0))
    if rr_all_confirmed:
        rr_seed = 0  # Lock the ticket once lineups are confirmed.

    # ----- Top-5 selection (deterministic baseline OR reroll) --------------
    # Diversity guard: don't allow more than 2 from the same game/team in
    # the top 5 (otherwise stacked games crowd out true variety).
    def _pick_top5(pool_df, seed: int):
        if seed <= 0:
            # Deterministic: greedy down the sorted list.
            picks = []
            used_g, used_t = {}, {}
            for _, r in pool_df.iterrows():
                g = str(r.get("Game", "") or "")
                t = str(r.get("Team", "") or "")
                if used_g.get(g, 0) >= 2: continue
                if used_t.get(t, 0) >= 2: continue
                picks.append(r)
                used_g[g] = used_g.get(g, 0) + 1
                used_t[t] = used_t.get(t, 0) + 1
                if len(picks) == 5: break
            return picks

        # Reroll: weighted random sample from the top candidates so each
        # click produces a meaningfully different slate while keeping
        # candidate quality high. We weight by RR Score so stronger plays
        # still surface more often.
        import random
        rng = random.Random(seed)
        # Pool size scales with available candidates: up to 15, min 6.
        pool_size = max(6, min(15, len(pool_df)))
        candidates = pool_df.head(pool_size).reset_index(drop=True)
        # Weights: RR Score with a small floor so even the lowest-ranked
        # candidate in the top-N has a non-zero shot.
        try:
            weights = [max(1.0, float(s)) for s in candidates["RR Score"].tolist()]
        except Exception:
            weights = [1.0] * len(candidates)

        picks = []
        used_g, used_t = {}, {}
        remaining_idx = list(range(len(candidates)))
        # Weighted sampling without replacement, respecting diversity guard.
        attempts = 0
        while remaining_idx and len(picks) < 5 and attempts < 200:
            attempts += 1
            sub_w = [weights[i] for i in remaining_idx]
            chosen = rng.choices(remaining_idx, weights=sub_w, k=1)[0]
            remaining_idx.remove(chosen)
            r = candidates.iloc[chosen]
            g = str(r.get("Game", "") or "")
            t = str(r.get("Team", "") or "")
            if used_g.get(g, 0) >= 2: continue
            if used_t.get(t, 0) >= 2: continue
            picks.append(r)
            used_g[g] = used_g.get(g, 0) + 1
            used_t[t] = used_t.get(t, 0) + 1
        return picks

    top5 = _pick_top5(rr_pool, rr_seed)
    # Backfill if diversity guard left us short (small slates)
    if len(top5) < 5:
        taken = {row["Hitter"] for row in top5}
        for _, r in rr_pool.iterrows():
            if r["Hitter"] in taken: continue
            top5.append(r)
            if len(top5) == 5: break

    if len(top5) < 5:
        st.info(
            f"Only {len(top5)} qualifying hitters available for this slate. "
            "Round Robin needs at least 5 — check back when more lineups post."
        )
        st.stop()

    if rr_seed > 0:
        st.caption(f"🎲 Reroll #{rr_seed} · fresh slate from the qualifying pool")

    # ----- Why-top-5 reason builder ---------------------------------------
    def _rr_why(row) -> list:
        why = []
        def _f(v):
            try:
                if v is None: return None
                f = float(v)
                if pd.isna(f): return None
                return f
            except Exception:
                return None
        ceil   = _f(row.get("Ceiling"))
        barrel = _f(row.get("Barrel%"))
        hh     = _f(row.get("HardHit%"))
        iso    = _f(row.get("ISO"))
        xslg   = _f(row.get("xSLG"))
        ops    = _f(row.get("OPS"))
        xwoba  = _f(row.get("xwOBA"))
        fb     = _f(row.get("FB%"))
        pull   = _f(row.get("Pull%"))
        bs     = _f(row.get("BatSpeed"))
        ss     = _f(row.get("SweetSpot%"))
        khr    = _f(row.get("kHR"))
        match  = _f(row.get("Matchup"))
        if ceil is not None and ceil >= 70:
            why.append(f"🏟️ Ceiling <b>{ceil:.0f}</b> — park/weather/SP combo lights up")
        # OPS is a top-line bat-quality factor (on-base + slugging in one).
        # Surfaced near the top of the "why" list because a strong OPS is
        # the single most informative scalar for "is this hitter producing?"
        if ops is not None and ops >= 0.850:
            why.append(f"🔥 OPS <b>{ops:.3f}</b> — premium bat")
        elif ops is not None and ops >= 0.750:
            why.append(f"🔥 OPS <b>{ops:.3f}</b>")
        if barrel is not None and barrel >= 8.0:
            why.append(f"💥 Barrel% <b>{barrel:.1f}</b>")
        if hh is not None and hh >= 40.0:
            why.append(f"💪 HardHit% <b>{hh:.1f}</b>")
        if iso is not None and iso >= 0.200:
            why.append(f"⚡ ISO <b>{iso:.3f}</b>")
        if xslg is not None and xslg >= 0.480:
            why.append(f"📈 xSLG <b>{xslg:.3f}</b>")
        if xwoba is not None and xwoba >= 0.360:
            why.append(f"🎯 xwOBA <b>{xwoba:.3f}</b>")
        if fb is not None and fb >= 38.0:
            why.append(f"🚀 FB% <b>{fb:.1f}</b> — gets it airborne")
        if pull is not None and pull >= 42.0:
            why.append(f"↗️ Pull% <b>{pull:.1f}</b>")
        if bs is not None and bs >= 73.0:
            why.append(f"🏏 Bat Speed <b>{bs:.1f}</b> mph")
        if ss is not None and ss >= 35.0:
            why.append(f"🍯 SweetSpot% <b>{ss:.1f}</b>")
        if khr is not None and khr >= 1.10:
            why.append(f"📊 kHR <b>{khr:.2f}</b>")
        if match is not None and match >= 110:
            why.append(f"🆚 Matchup <b>{match:.0f}</b> vs {row.get('Opp SP','SP')}")
        try:
            sp = int(row.get("Spot"))
            if 3 <= sp <= 5:
                why.append(f"📋 <b>{sp}-hole</b> — heart of the order")
        except Exception:
            pass
        try:
            hrn = int(row.get("HR (Season)"))
            if hrn <= 6 and (barrel or 0) >= 7.0:
                why.append(f"😴 Only <b>{hrn}</b> HR on year — under-owned sleeper upside")
        except Exception:
            pass
        if not why:
            why.append("🔢 Composite HR score above the slate's qualifying bar")
        return why[:4]

    # ----- Render the 5 player cards --------------------------------------
    for i, row in enumerate(top5, 1):
        try:
            score_str = f"{float(row.get('RR Score', 0)):.1f}"
        except Exception:
            score_str = "—"
        why = _rr_why(row)
        why_html = "".join(f'<li>{w}</li>' for w in why)
        # Compact stats line
        def _fd(v, fmt):
            try:
                f = float(v)
                if pd.isna(f): return None
                return fmt.format(f)
            except Exception:
                return None
        bits = []
        c = _fd(row.get("Ceiling"), "{:.0f}");        bits += [f"Ceiling <b>{c}</b>"] if c else []
        op = _fd(row.get("OPS"), "{:.3f}");            bits += [f"OPS <b>{op}</b>"] if op else []
        b = _fd(row.get("Barrel%"), "{:.1f}%");        bits += [f"Barrel <b>{b}</b>"] if b else []
        h = _fd(row.get("HardHit%"), "{:.1f}%");       bits += [f"HH <b>{h}</b>"] if h else []
        iv = _fd(row.get("ISO"), "{:.3f}");            bits += [f"ISO <b>{iv}</b>"] if iv else []
        xs = _fd(row.get("xSLG"), "{:.3f}");           bits += [f"xSLG <b>{xs}</b>"] if xs else []
        try:
            sp = int(row.get("Spot"))
            bits.append(f"<b>{sp}-hole</b>")
        except Exception:
            pass
        stats_line = " · ".join(bits) if bits else "—"
        st.markdown(
            f'<div class="rr-card">'
            f'  <div>'
            f'    <span class="rr-rank">#{i}</span>'
            f'    <span class="rr-name">{row.get("Hitter","")}</span>'
            f'    <span class="rr-score">{score_str}</span>'
            f'  </div>'
            f'  <div class="rr-meta">{row.get("Team","")} · '
            f'Bat {format_batter_stance(row.get("Bat",""))} · '
            f'{row.get("Game","")} <span style="color:#94a3b8;">·</span> '
            f'vs <b>{row.get("Opp SP","")}</b></div>'
            f'  <div class="rr-stats">{stats_line}</div>'
            f'  <ul class="rr-why">{why_html}</ul>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ----- Round Robin combo summary --------------------------------------
    # Standard round-robin combo counts on 5 legs: by-2 = C(5,2)=10,
    # by-3 = C(5,3)=10, by-4 = C(5,4)=5, by-5 = 1; total = 26 tickets.
    st.markdown(
        '<div class="rr-combos">'
        '  <h4>👑 Round Robin Combos · 5 Legs</h4>'
        '  <table>'
        '    <tr><td>By-2 (pairs)</td><td>10 tickets</td></tr>'
        '    <tr><td>By-3 (triples)</td><td>10 tickets</td></tr>'
        '    <tr><td>By-4 (quads)</td><td>5 tickets</td></tr>'
        '    <tr><td>By-5 (full parlay)</td><td>1 ticket</td></tr>'
        '    <tr><td><b>Total round-robin tickets</b></td><td><b>26</b></td></tr>'
        '  </table>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ----- Source freshness + disclaimer ----------------------------------
    st.markdown(
        render_source_chips([
            "savant:batters", "savant:pitchers",
            "statsapi:schedule", "statsapi:boxscore",
            "rotogrinders:weather", "openmeteo:weather",
        ]),
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="rr-disclaimer">'
        '⚠️ <b>Disclaimer:</b> The MRBETS850 Homerun Round Robin is a '
        'model-driven analytics ticket built from public Statcast / StatsAPI / '
        'weather data — <b>not a guaranteed outcome</b>. Always verify lineups '
        'and scratches with your sportsbook before placing any bet. '
        'No outcomes are promised. Bet responsibly.'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.expander("How the Round Robin score works"):
        st.markdown(
            "**RR Score (0-100)** is a composite that blends every HR signal "
            "we have on every eligible hitter — not name recognition:\n"
            "- **22%** HR Sleeper Score (Barrel%, HH%, ISO, FB%, Pull%, Matchup, kHR, sleeper bonus)\n"
            "- **16%** Ceiling (park × weather × opposing SP)\n"
            "- **8%** Barrel%, **6%** HardHit%, **5%** ISO, **5%** xSLG\n"
            "- **4%** xwOBA, **4%** FB%, **3%** Pull%, **3%** SweetSpot%, **3%** Launch Angle\n"
            "- **5%** Bat Speed, **3%** kHR, **5%** Matchup vs SP\n"
            "- **5%** Lineup spot (heart of order weighted), **3%** Season HR (capped)\n\n"
            "The baseline top-5 is **deterministic** — sorted by RR Score with "
            "a stable name tie-break and a diversity guard (max 2 hitters per "
            "team / per game). While lineups are still being posted you can "
            "**🎲 Reroll** to pull a fresh top-5 from the qualifying candidate "
            "pool — weighted by RR Score so quality stays high while giving "
            "clients alternative options. Once **every eligible hitter's "
            "lineup is Confirmed**, reroll disables and the ticket locks."
        )
    st.stop()


# ============== AI Pitcher Strikeouts Generator view ==============
# Builds a slate-wide ranked board and 1-/2-/3-leg pitcher-K parlay tickets
# using only data already loaded in the app (Savant pitcher leaderboard +
# StatsAPI season totals + boxscore lineups + per-batter K%). Modeled after
# the 🤖 AI HR Parlay view: weighted-sample selection, transparent reasons,
# reroll, and risk profiles. No external odds API is required — this tab is
# feed-independent and falls back gracefully when columns are missing.
if _view == "🎯 AI K Generator":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🎯 AI Pitcher Strikeouts Generator</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Slate-wide AI generator for <b>pitcher strikeout</b> props. The '
        '<b>AI K Score</b> blends starter strikeout skill (Savant K%, Whiff%, '
        'SwStr%, Strikeout Score) with <b>workload runway</b> (IP/Start, BF), '
        'opponent <b>lineup whiff profile</b> (lineup-weighted batter K%) and '
        '<b>environment</b> (park × weather K modifier). Each pick shows the '
        'data-driven reasons it landed in the ticket — and a suggested '
        'recorded-K line based on the pitcher\'s K-rate × projected batters '
        'faced when no book line is loaded.'
        '</div>',
        unsafe_allow_html=True,
    )

    if pitcher_stats_df is None or pitcher_stats_df.empty:
        st.warning(
            "Pitcher CSV (`Data:savant_pitcher_stats.csv`) hasn't loaded yet — "
            "the K generator joins by `player_id`."
        )
        st.stop()
    if schedule_df is None or schedule_df.empty:
        st.warning("No games on the slate. Pick a different date or check back later.")
        st.stop()

    # ---- Eligibility filter (mirrors AI HR Parlay) ----------------------
    _K_COMPLETED = (
        "final", "completed", "game over", "postponed", "cancelled",
        "canceled", "suspended", "forfeit", "if necessary",
    )
    _K_LIVE = (
        "in progress", "live", "manager challenge", "review",
        "delayed", "warmup", "warm-up", "warm up",
    )
    _K_PRE = ("scheduled", "pre-game", "pregame", "pre game")

    def _k_game_eligible(row) -> bool:
        status = str(row.get("status") or "").strip().lower()
        if any(t in status for t in _K_COMPLETED): return False
        if any(t in status for t in _K_LIVE):       return True
        gt = row.get("game_time_utc")
        try:    start_utc = pd.to_datetime(gt, utc=True)
        except Exception: start_utc = pd.NaT
        _now = pd.Timestamp.now('UTC')
        now_utc = _now if _now.tzinfo is not None else _now.tz_localize("UTC")
        if any(t in status for t in _K_PRE):
            if pd.isna(start_utc): return True
            return start_utc >= now_utc
        if pd.isna(start_utc): return True
        return start_utc >= now_utc

    _k_elig_mask  = schedule_df.apply(_k_game_eligible, axis=1)
    k_schedule_df = schedule_df[_k_elig_mask].reset_index(drop=True)
    _k_total = int(len(schedule_df))
    _k_elig  = int(len(k_schedule_df))
    _k_excl  = _k_total - _k_elig

    if k_schedule_df.empty:
        st.warning(
            f"All {_k_total} games on this slate have already finished or are no "
            f"longer available. No upcoming/live games to build K parlays from."
        )
        st.stop()

    st.markdown(
        f'<div style="margin:0 0 10px 0; padding:8px 12px; '
        f'border-left:3px solid #7c3aed; background:#f5f3ff; '
        f'border-radius:6px; color:#065f46; font-size:0.88rem;">'
        f'🕒 Using <b>{_k_elig}</b> upcoming/live game'
        f'{"s" if _k_elig != 1 else ""}; '
        f'completed games excluded'
        f'{f" ({_k_excl} hidden)" if _k_excl > 0 else ""}.'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- Build the slate pitcher board (re-uses Slate Pitchers builder) -
    with st.spinner("Scoring starting pitchers across the slate…"):
        sp_df = build_slate_pitcher_table(k_schedule_df, pitcher_stats_df)

    if sp_df is None or sp_df.empty:
        st.info(
            "No probable starters posted yet for the upcoming/live games. "
            "Check back closer to first pitch."
        )
        st.stop()

    # Drop TBD rows defensively (Slate Pitchers builder already skips, but
    # be safe).
    sp_df = sp_df[sp_df["Pitcher"].astype(str).str.upper() != "TBD"].copy()
    if sp_df.empty:
        st.info("All probable starters are still TBD. Check back closer to first pitch.")
        st.stop()

    # ---- Helpers: opponent lineup K% (mean) using existing lineup logic --
    @st.cache_data(ttl=600, show_spinner=False)
    def _opp_lineup_k_pct(_game_pk: int, side: str) -> dict:
        """For a probable pitcher on `side`, return opponent lineup K% summary.
        Returns {"mean_k": float|None, "n": int, "status": str, "spots": list}.
        Uses build_game_context which already merges confirmed/projected lineups
        and falls back gracefully when no lineup is posted."""
        try:
            gr = schedule_df[schedule_df["game_pk"] == _game_pk]
            if gr.empty:
                return {"mean_k": None, "n": 0, "status": "Unknown", "spots": []}
            g = gr.iloc[0]
            ctx_local = build_game_context(g)
        except Exception:
            return {"mean_k": None, "n": 0, "status": "Unknown", "spots": []}
        if side == "away":
            opp_lineup = ctx_local.get("home_lineup")
            opp_status = ctx_local.get("home_status", "Not Posted")
        else:
            opp_lineup = ctx_local.get("away_lineup")
            opp_status = ctx_local.get("away_status", "Not Posted")
        if opp_lineup is None or len(opp_lineup) == 0:
            return {"mean_k": None, "n": 0, "status": opp_status, "spots": []}
        # Look up each batter's K% from batters_df by player_id when present;
        # fall back to clean_name() match. K% in batters_df is already a
        # 0..100 number after standardize_columns.
        ks = []
        spots = []
        for _, brow in opp_lineup.iterrows():
            pid = brow.get("player_id")
            kp = None
            try:
                if pid is not None and not pd.isna(pid) and "player_id" in batters_df.columns:
                    m = batters_df[batters_df["player_id"].astype("Int64") == int(pid)]
                    if not m.empty and "K%" in m.columns:
                        v = m.iloc[0].get("K%")
                        if v is not None and not pd.isna(v):
                            kp = float(v)
            except Exception:
                kp = None
            if kp is None:
                # Fallback: name lookup (less reliable but covers projected).
                try:
                    nm = brow.get("name") or brow.get("Name") or ""
                    nk = clean_name(str(nm))
                    if nk and "name_key" in batters_df.columns and "K%" in batters_df.columns:
                        m2 = batters_df[batters_df["name_key"] == nk]
                        if not m2.empty:
                            v = m2.iloc[0].get("K%")
                            if v is not None and not pd.isna(v):
                                kp = float(v)
                except Exception:
                    pass
            if kp is not None:
                ks.append(kp)
            try:
                sp_n = int(brow.get("lineup_spot")) if brow.get("lineup_spot") is not None else None
            except Exception:
                sp_n = None
            spots.append(sp_n)
        if not ks:
            return {"mean_k": None, "n": 0, "status": opp_status, "spots": spots}
        # Top-of-order weighting: spots 1-5 contribute slightly more (~1.2x)
        # because they get more PAs against the SP. Falls back to flat mean
        # if we couldn't read lineup spots.
        try:
            weights = []
            for sp_n in spots:
                if sp_n is None: weights.append(1.0)
                elif 1 <= sp_n <= 5: weights.append(1.20)
                elif 6 <= sp_n <= 7: weights.append(1.00)
                else: weights.append(0.85)
            if len(weights) == len(ks):
                num = sum(w * k for w, k in zip(weights, ks))
                den = sum(weights) or 1.0
                mean_k = num / den
            else:
                mean_k = sum(ks) / len(ks)
        except Exception:
            mean_k = sum(ks) / len(ks)
        return {"mean_k": float(mean_k), "n": int(len(ks)), "status": opp_status, "spots": spots}

    # ---- Helper: park × weather K modifier --------------------------------
    @st.cache_data(ttl=600, show_spinner=False)
    def _env_k_modifier(_game_pk: int) -> dict:
        """Returns {"k_mod": float|None, "label": str} where k_mod is the
        compute_weather_impact() K%-modifier (e.g. -3 means strikeouts ~3%
        below MLB avg). None if weather/park context is unavailable."""
        try:
            gr = schedule_df[schedule_df["game_pk"] == _game_pk]
            if gr.empty: return {"k_mod": None, "label": ""}
            g = gr.iloc[0]
            wx = get_combined_weather(g)
            imp = compute_weather_impact(wx, g.get("park_factor"), g.get("home_abbr"))
            kp = imp.get("k_pct")
            if kp is None or pd.isna(kp): return {"k_mod": None, "label": ""}
            return {"k_mod": float(kp),
                    "label": f"{int(kp):+d}% K env" if kp != 0 else "Neutral K env"}
        except Exception:
            return {"k_mod": None, "label": ""}

    # ---- Map each slate-pitcher row back to its game_pk + side ----------
    pmap = []  # [(idx, game_pk, side)]
    for sp_idx, sp_row in sp_df.iterrows():
        team_abbr = str(sp_row.get("Team", "") or "")
        opp_abbr  = str(sp_row.get("Opp", "")  or "")
        gpk, side_match = None, None
        for _, g in k_schedule_df.iterrows():
            if str(g.get("away_abbr", "")) == team_abbr and str(g.get("home_abbr", "")) == opp_abbr:
                gpk = g.get("game_pk"); side_match = "away"; break
            if str(g.get("home_abbr", "")) == team_abbr and str(g.get("away_abbr", "")) == opp_abbr:
                gpk = g.get("game_pk"); side_match = "home"; break
        pmap.append((sp_idx, gpk, side_match))

    # ---- Enrich pitcher rows with opponent-K + environment + composite --
    with st.spinner("Layering opponent lineup K% + environment on every starter…"):
        opp_means, opp_ns, opp_statuses, env_mods, env_labels = [], [], [], [], []
        for sp_idx, gpk, side_match in pmap:
            if gpk is None or side_match is None:
                opp_means.append(None); opp_ns.append(0)
                opp_statuses.append("Unknown"); env_mods.append(None); env_labels.append("")
                continue
            try:
                opp = _opp_lineup_k_pct(int(gpk), side_match)
            except Exception:
                opp = {"mean_k": None, "n": 0, "status": "Unknown", "spots": []}
            try:
                env = _env_k_modifier(int(gpk))
            except Exception:
                env = {"k_mod": None, "label": ""}
            opp_means.append(opp.get("mean_k"))
            opp_ns.append(int(opp.get("n") or 0))
            opp_statuses.append(opp.get("status") or "Unknown")
            env_mods.append(env.get("k_mod"))
            env_labels.append(env.get("label") or "")

        sp_df = sp_df.reset_index(drop=True)
        sp_df["Opp Lineup K%"] = opp_means
        sp_df["Opp Lineup N"]   = opp_ns
        sp_df["Opp Lineup Status"] = opp_statuses
        sp_df["Env K Mod"]      = env_mods
        sp_df["Env K Label"]    = env_labels

    # ---- AI K Score: composite over all available signals ---------------
    def _k_norm(v, lo, hi, default=50.0, reverse=False):
        try:
            f = float(v)
            if pd.isna(f): return default
            x = max(lo, min(hi, f))
            pct = (x - lo) / (hi - lo) * 100.0
            return 100.0 - pct if reverse else pct
        except Exception:
            return default

    def _ai_k_score(row) -> float:
        # Pitcher K skill (Savant + StatsAPI)
        n_kpct  = _k_norm(row.get("K%"),     18.0, 33.0)
        n_whiff = _k_norm(row.get("Whiff%"), 18.0, 33.0)
        n_swstr = _k_norm(row.get("SwStr%"),  9.0, 16.0)
        # Strikeout Score is already 0..100 in the codebase
        try:
            ss_v = float(row.get("Strikeout Score"))
            if pd.isna(ss_v): ss_v = 50.0
        except Exception:
            ss_v = 50.0
        # Workload runway: more IP/Start ⇒ more chances at Ks
        ip = row.get("IP"); pa = row.get("PA")
        try:
            ip_f = float(ip) if ip is not None and not pd.isna(ip) else None
            pa_f = float(pa) if pa is not None and not pd.isna(pa) else None
            if ip_f and pa_f and pa_f > 0:
                # crude IP/PA ratio normalized vs typical starter (~5.5 IP/start),
                # but we mostly care about absolute IP volume for sample stability.
                pass
        except Exception:
            ip_f, pa_f = None, None
        n_ip = _k_norm(ip_f, 20.0, 80.0)  # ~1 month -> ~half season
        # Opponent lineup whiff profile (higher K% = better target)
        n_opp = _k_norm(row.get("Opp Lineup K%"), 18.0, 28.0)
        # Environment K modifier (-10..+10 typical, sometimes wider)
        env = row.get("Env K Mod")
        try:
            env_f = float(env) if env is not None and not pd.isna(env) else 0.0
        except Exception:
            env_f = 0.0
        n_env = _k_norm(env_f, -10.0, 10.0, default=50.0)
        # Penalize obvious blowup risk: high WHIP -> short outings -> fewer Ks
        whip = row.get("WHIP")
        try:
            whip_f = float(whip) if whip is not None and not pd.isna(whip) else None
        except Exception:
            whip_f = None
        n_whip_inv = _k_norm(whip_f, 1.05, 1.65, default=50.0, reverse=True)

        composite = (
            0.20 * ss_v
          + 0.16 * n_kpct
          + 0.14 * n_whiff
          + 0.10 * n_swstr
          + 0.10 * n_opp
          + 0.10 * n_ip
          + 0.10 * n_env
          + 0.10 * n_whip_inv
        )
        return round(max(0.0, min(100.0, composite)), 1)

    sp_df["AI K Score"] = sp_df.apply(_ai_k_score, axis=1)

    # ---- Projected Ks: K% × estimated batters faced ----------------------
    # Light, transparent fallback projection when no book line is loaded.
    # If IP is available and BF is known, use BF/start. Otherwise default to
    # 22 BF (typical 5-6 IP outing).
    def _proj_ks(row):
        try:
            kp = float(row.get("K%"))
            if pd.isna(kp): return None
        except Exception:
            return None
        # Assume ~22 BF as a baseline starter outing; adjust slightly by the
        # opponent lineup's K-tendency (higher opp K% ⇒ ~+0.5 BF added depth).
        bf_est = 22.0
        try:
            opp_k = float(row.get("Opp Lineup K%"))
            if not pd.isna(opp_k):
                bf_est += (opp_k - 22.0) * 0.05  # +0.5 BF per +10% Opp K
        except Exception:
            pass
        try:
            env = float(row.get("Env K Mod"))
            if not pd.isna(env):
                # +10% env -> ~+0.5 BF (better K env, slightly deeper Ks)
                bf_est *= (1.0 + env / 200.0)
        except Exception:
            pass
        return round((kp / 100.0) * bf_est, 1)

    sp_df["Proj Ks"] = sp_df.apply(_proj_ks, axis=1)

    # ---- Suggested K-prop line: round Proj Ks to nearest 0.5 -------------
    def _suggested_line(v):
        try:
            f = float(v)
            if pd.isna(f): return None
            # Round to nearest 0.5 then nudge to "Over" by -0.5 (typical book
            # lines sit near projection; suggesting Over the line just under
            # projection is the intuitive prop angle).
            base = round(f * 2.0) / 2.0
            return max(2.5, base - 0.5)
        except Exception:
            return None

    sp_df["Suggested Line"] = sp_df["Proj Ks"].apply(_suggested_line)

    sp_df = sp_df.sort_values("AI K Score", ascending=False).reset_index(drop=True)

    # ---- Controls (mirrors HR Parlay) -----------------------------------
    c_cols = st.columns([1.6, 1.0, 1.0, 1.0])
    with c_cols[0]:
        _k_risk = st.radio(
            "Risk profile",
            ["Safer", "Balanced", "Sleeper Hunt", "Aggressive"],
            index=1,
            horizontal=True,
            key="ai_k_risk",
            help=(
                "Safer: established K artists, strict IP/whiff thresholds. "
                "Balanced: default mix of aces + quality matchup picks. "
                "Sleeper Hunt: boosts under-the-radar starters with "
                "elite Whiff%/Opp K%. Aggressive: opens the pool wider."
            ),
        )
    with c_cols[1]:
        _k_avoid_same_game = st.checkbox(
            "Avoid same game", value=True, key="ai_k_avoid_game",
            help="Prevent two K legs from the same game (correlation risk).",
        )
    with c_cols[2]:
        _k_min_ip = st.slider(
            "Min IP (season)", min_value=0, max_value=80, value=15, step=5,
            key="ai_k_min_ip",
            help="Hide starters with very small workload samples.",
        )
    with c_cols[3]:
        _k_show_alts = st.checkbox(
            "Show alternate tickets", value=True, key="ai_k_show_alts",
            help="Also surface alternate 2-leg and 3-leg tickets.",
        )

    # Risk-profile thresholds
    if _k_risk == "Safer":
        min_score, min_kpct, min_whiff = 62.0, 22.0, 24.0
    elif _k_risk == "Sleeper Hunt":
        min_score, min_kpct, min_whiff = 50.0, 19.0, 21.0
    elif _k_risk == "Aggressive":
        min_score, min_kpct, min_whiff = 44.0, 17.0, 19.0
    else:  # Balanced
        min_score, min_kpct, min_whiff = 55.0, 20.0, 22.0

    pool_k = sp_df.copy()
    # Score gate
    pool_k = pool_k[pool_k["AI K Score"].fillna(0) >= float(min_score)]
    # K% gate (allow missing → keep, sleeper hunt only)
    if _k_risk in ("Safer", "Balanced"):
        pool_k = pool_k[pool_k["K%"].fillna(0) >= float(min_kpct)]
        pool_k = pool_k[pool_k["Whiff%"].fillna(0) >= float(min_whiff)]
    else:
        # Sleeper / Aggressive: keep rows with missing K% (often new call-ups)
        m = (pool_k["K%"].fillna(min_kpct) >= float(min_kpct))
        pool_k = pool_k[m]
    # Workload gate
    if "IP" in pool_k.columns:
        pool_k = pool_k[pool_k["IP"].fillna(0) >= float(_k_min_ip)]
    pool_k = pool_k.reset_index(drop=True)

    if pool_k.empty or len(pool_k) < 1:
        st.info(
            f"Not enough qualifying starters for the **{_k_risk}** profile — "
            f"only {len(pool_k)} pitcher(s) cleared the thresholds. "
            f"Try **Sleeper Hunt** or **Aggressive**, or lower **Min IP**."
        )
        st.stop()

    # ---- Reroll controls -------------------------------------------------
    if "ai_k_seed" not in st.session_state:
        st.session_state["ai_k_seed"] = 1
    if "ai_k_generated_at" not in st.session_state:
        st.session_state["ai_k_generated_at"] = datetime.now(
            ZoneInfo("America/New_York")
        ).strftime("%Y-%m-%d %I:%M:%S %p ET")

    r_cols = st.columns([1.0, 2.0])
    with r_cols[0]:
        if st.button("🎲 Generate New K Tickets", key="ai_k_reroll",
                     width='stretch',
                     help="Reroll: rebuilds the K board's 1-/2-/3-leg tickets via "
                          "weighted sampling on the same eligible pool."):
            st.session_state["ai_k_seed"] = int(
                st.session_state.get("ai_k_seed", 1)
            ) + 1
            st.session_state["ai_k_generated_at"] = datetime.now(
                ZoneInfo("America/New_York")
            ).strftime("%Y-%m-%d %I:%M:%S %p ET")
    with r_cols[1]:
        st.markdown(
            f'<div style="padding-top:6px; color:#475569; font-size:0.86rem;">'
            f'🎟️ <b>Ticket #{int(st.session_state["ai_k_seed"])}</b> · '
            f'{len(pool_k)} eligible starter'
            f'{"s" if len(pool_k) != 1 else ""} · '
            f'Generated {st.session_state["ai_k_generated_at"]}'
            f'</div>',
            unsafe_allow_html=True,
        )

    _k_seed = int(st.session_state["ai_k_seed"])

    # Sleeper boost: under-radar starters with elite Whiff% + favorable Opp K%
    if _k_risk == "Sleeper Hunt":
        k_sleeper_bias = 0.45
    elif _k_risk == "Aggressive":
        k_sleeper_bias = 0.20
    elif _k_risk == "Balanced":
        k_sleeper_bias = 0.10
    else:
        k_sleeper_bias = 0.0

    def _k_weighted_sample(_pool, n: int, avoid_same_game: bool, seed: int):
        if _pool is None or _pool.empty:
            return []
        rng = np.random.default_rng(int(seed))
        scores = _pool["AI K Score"].fillna(0).to_numpy(dtype=float)
        weights = np.clip(scores, 1.0, None) ** 1.4
        if k_sleeper_bias > 0:
            ip_a   = _pool["IP"].fillna(50.0).to_numpy(dtype=float) if "IP" in _pool.columns else np.full_like(scores, 50.0)
            whiff  = _pool["Whiff%"].fillna(20.0).to_numpy(dtype=float) if "Whiff%" in _pool.columns else np.full_like(scores, 20.0)
            opp_k  = _pool["Opp Lineup K%"].fillna(22.0).to_numpy(dtype=float)
            sleeper_factor = (
                np.clip((50.0 - ip_a)  / 50.0, 0.0, 1.0) * 0.40 +  # smaller-sample SP
                np.clip((whiff - 22.0) / 10.0, 0.0, 1.0) * 0.30 +  # elite whiff
                np.clip((opp_k - 22.0) / 8.0,  0.0, 1.0) * 0.30    # whiffy opponent
            )
            weights = weights * (1.0 + k_sleeper_bias * sleeper_factor)
        if not np.isfinite(weights).any() or weights.sum() <= 0:
            weights = np.ones_like(scores)

        rows = list(_pool.to_dict("records"))
        idxs = list(range(len(rows)))
        legs, used_games = [], set()
        available = idxs.copy()
        avail_w = weights.copy()
        while available and len(legs) < n:
            w = avail_w[available]
            if w.sum() <= 0:
                pick_local = int(rng.integers(0, len(available)))
            else:
                p = w / w.sum()
                pick_local = int(rng.choice(len(available), p=p))
            pick = available.pop(pick_local)
            row = rows[pick]
            g = str(row.get("Game", "") or "")
            if avoid_same_game and g and g in used_games:
                continue
            legs.append(row)
            if g: used_games.add(g)

        if len(legs) < n:
            taken = {l.get("Pitcher") for l in legs}
            for row in rows:
                if len(legs) >= n: break
                if row.get("Pitcher") in taken: continue
                legs.append(row)
        return legs[:n]

    # ---- Per-pick reasons -----------------------------------------------
    def _k_reasons_for(row) -> list:
        reasons = []
        def _f(v):
            try:
                if v is None: return None
                f = float(v)
                if pd.isna(f): return None
                return f
            except Exception:
                return None

        kpct  = _f(row.get("K%"))
        whiff = _f(row.get("Whiff%"))
        swstr = _f(row.get("SwStr%"))
        ss    = _f(row.get("Strikeout Score"))
        ip    = _f(row.get("IP"))
        whip  = _f(row.get("WHIP"))
        opp_k = _f(row.get("Opp Lineup K%"))
        opp_n = row.get("Opp Lineup N")
        env_m = _f(row.get("Env K Mod"))
        env_l = str(row.get("Env K Label") or "")
        opp_status = str(row.get("Opp Lineup Status") or "")

        # K skill
        if kpct is not None:
            if kpct >= 28.0:
                reasons.append(f"💨 K% <b>{kpct:.1f}</b> — elite strikeout rate")
            elif kpct >= 24.0:
                reasons.append(f"💨 K% <b>{kpct:.1f}</b>")
        if whiff is not None:
            if whiff >= 30.0:
                reasons.append(f"🌀 Whiff% <b>{whiff:.1f}</b> — swing-and-miss artist")
            elif whiff >= 25.0:
                reasons.append(f"🌀 Whiff% <b>{whiff:.1f}</b>")
        if swstr is not None and swstr >= 13.0:
            reasons.append(f"⚡ SwStr% <b>{swstr:.1f}</b> — generates Ks in zone")
        if ss is not None and ss >= 65.0:
            reasons.append(f"📊 Strikeout Score <b>{ss:.0f}</b>")

        # Workload runway
        if ip is not None and ip >= 60.0:
            reasons.append(f"🛢️ IP <b>{ip:.1f}</b> — stable starter sample")
        elif ip is not None and ip >= 30.0:
            reasons.append(f"🛢️ IP <b>{ip:.1f}</b>")
        if whip is not None and whip <= 1.15:
            reasons.append(f"🎯 WHIP <b>{whip:.2f}</b> — pitches deep into outings")

        # Opponent lineup
        if opp_k is not None:
            n_text = ""
            try:
                if opp_n is not None and int(opp_n) > 0:
                    n_text = f" ({int(opp_n)} bats)"
            except Exception:
                pass
            opp_team = str(row.get("Opp", "") or "")
            stat_chip = f" · {opp_status}" if opp_status and opp_status not in ("Unknown",) else ""
            if opp_k >= 25.0:
                reasons.append(
                    f"🍯 vs <b>{opp_team}</b> lineup K% <b>{opp_k:.1f}</b>{n_text}{stat_chip} — strikeout-prone matchup"
                )
            elif opp_k >= 22.5:
                reasons.append(
                    f"🍯 vs <b>{opp_team}</b> lineup K% <b>{opp_k:.1f}</b>{n_text}{stat_chip}"
                )
            elif opp_k <= 19.0:
                reasons.append(
                    f"⚠️ vs <b>{opp_team}</b> lineup K% <b>{opp_k:.1f}</b>{n_text} — contact-oriented opponent"
                )

        # Environment
        if env_m is not None and abs(env_m) >= 3.0:
            if env_m >= 3.0:
                reasons.append(f"🌬️ {env_l} — park/weather lifts Ks")
            elif env_m <= -3.0:
                reasons.append(f"🌬️ {env_l} — env tamps down Ks")

        if not reasons:
            reasons.append("🔢 Composite AI K Score above slate threshold")
        return reasons[:6]

    # ---- Sample tickets --------------------------------------------------
    legs_1 = _k_weighted_sample(pool_k, 1, _k_avoid_same_game, seed=_k_seed)
    legs_2 = _k_weighted_sample(pool_k, 2, _k_avoid_same_game, seed=_k_seed * 31 + 7)
    legs_3 = _k_weighted_sample(pool_k, 3, _k_avoid_same_game, seed=_k_seed * 1009 + 13)
    alt_2_a = _k_weighted_sample(pool_k, 2, _k_avoid_same_game, seed=_k_seed + 211)
    alt_2_b = _k_weighted_sample(pool_k, 2, _k_avoid_same_game, seed=_k_seed + 331)
    alt_3_a = _k_weighted_sample(pool_k, 3, _k_avoid_same_game, seed=_k_seed * 1009 + 313)
    alt_3_b = _k_weighted_sample(pool_k, 3, _k_avoid_same_game, seed=_k_seed * 1009 + 521)

    def _k_tier(score):
        try: s = float(score)
        except Exception: return ("ok", "Average")
        if s >= 75: return ("elite",  "Elite")
        if s >= 65: return ("strong", "Strong")
        if s >= 55: return ("ok",     "Average")
        return ("soft", "Soft")

    def _k_fmt_or_dash(v, fmt):
        try:
            if v is None: return "—"
            f = float(v)
            if pd.isna(f): return "—"
            return fmt.format(f)
        except Exception:
            return "—"

    def _k_compact_stats(leg) -> str:
        parts = []
        kp  = _k_fmt_or_dash(leg.get("K%"),     "{:.1f}%")
        if kp != "—": parts.append(f"K% <b>{kp}</b>")
        wh  = _k_fmt_or_dash(leg.get("Whiff%"), "{:.1f}%")
        if wh != "—": parts.append(f"Whiff <b>{wh}</b>")
        ip  = _k_fmt_or_dash(leg.get("IP"),     "{:.1f}")
        if ip != "—": parts.append(f"IP <b>{ip}</b>")
        op  = _k_fmt_or_dash(leg.get("Opp Lineup K%"), "{:.1f}%")
        if op != "—": parts.append(f"Opp K <b>{op}</b>")
        env = leg.get("Env K Label") or ""
        if env: parts.append(env)
        return " · ".join(parts) if parts else "—"

    def _render_k_card(title: str, legs: list, badge: str) -> str:
        if not legs:
            return (
                f'<div class="kgen-card kgen-empty">'
                f'<div class="kgen-card-title">{title}</div>'
                f'<div class="kgen-card-sub">Not enough qualifying starters. '
                f'Loosen the filters above.</div>'
                f'</div>'
            )
        avg_score = sum(float(l.get("AI K Score", 0) or 0) for l in legs) / max(1, len(legs))
        tier_cls, _ = _k_tier(avg_score)
        leg_html = []
        for i, leg in enumerate(legs, 1):
            t_cls, t_lbl = _k_tier(leg.get("AI K Score"))
            reasons = _k_reasons_for(leg)
            reason_html = "".join(
                f'<li style="margin:2px 0;">{r}</li>' for r in reasons
            )
            try:
                ai_str = f"{float(leg.get('AI K Score', 0)):.1f}"
            except Exception:
                ai_str = "—"
            stats_line = _k_compact_stats(leg)
            try:
                proj = float(leg.get("Proj Ks"))
                proj_str = f"{proj:.1f}"
            except Exception:
                proj_str = "—"
            try:
                line = float(leg.get("Suggested Line"))
                line_str = f"{line:.1f}"
            except Exception:
                line_str = "—"
            throws_chip = format_pitcher_hand(leg.get("Throws"), "")
            throws_chip = f" · {throws_chip}" if throws_chip else ""
            leg_html.append(
                f'<div class="kgen-leg">'
                f'  <div class="kgen-leg-head">'
                f'    <div class="kgen-leg-num">Leg {i}</div>'
                f'    <div class="kgen-leg-name">{leg.get("Pitcher","")}'
                f'      <span class="kgen-meta">· {leg.get("Team","")}{throws_chip} '
                f'· {leg.get("Loc","")} {leg.get("Opp","")}</span>'
                f'    </div>'
                f'    <div class="kgen-leg-score">'
                f'      <span class="kgen-score">{ai_str}</span>'
                f'      <span class="hrs-pill {t_cls}">{t_lbl}</span>'
                f'    </div>'
                f'  </div>'
                f'  <div class="kgen-ctx">'
                f'    {leg.get("Game","")} '
                f'<span style="color:#94a3b8;">·</span> '
                f'<b>Proj Ks {proj_str}</b> · suggested <b>Over {line_str}</b>'
                f'  </div>'
                f'  <div class="kgen-stats">{stats_line}</div>'
                f'  <ul class="kgen-reasons">{reason_html}</ul>'
                f'</div>'
            )
        return (
            f'<div class="kgen-card">'
            f'  <div class="kgen-card-head">'
            f'    <div>'
            f'      <div class="kgen-card-title">{title}</div>'
            f'      <div class="kgen-card-sub">{len(legs)} leg'
            f'{"s" if len(legs) != 1 else ""} · '
            f'avg AI K Score <b>{avg_score:.1f}</b></div>'
            f'    </div>'
            f'    <span class="hrs-pill {tier_cls} kgen-badge">{badge}</span>'
            f'  </div>'
            + "".join(leg_html) +
            f'</div>'
        )

    css = (
        "<style>"
        ".kgen-card { background:linear-gradient(180deg,#0d1b2e 0%,#091220 100%); "
        "  border-radius:14px; padding:14px 14px 8px 14px; "
        "  box-shadow:0 2px 16px rgba(0,0,0,.45); margin: 8px 0 16px 0; "
        "  border:1px solid rgba(29,78,216,.3); border-left:5px solid #3b82f6; }"
        ".kgen-card.kgen-empty { border-left-color:#334155; padding:14px; }"
        ".kgen-card-head { display:flex; align-items:center; justify-content:space-between; "
        "  gap:10px; margin-bottom:6px; flex-wrap:wrap; }"
        ".kgen-card-title { font-weight:900; font-size:1.08rem; color:#e2e8f0; "
        "  letter-spacing:.01em; }"
        ".kgen-card-sub { color:#94a3b8; font-size:.82rem; margin-top:2px; }"
        ".kgen-badge { font-size:.74rem; }"
        ".kgen-leg { padding:10px 0; border-top:1px dashed rgba(255,255,255,.08); }"
        ".kgen-leg:first-of-type { border-top:none; }"
        ".kgen-leg-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }"
        ".kgen-leg-num { font-size:.72rem; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; color:#fde68a; background:#1e3a8a; "
        "  padding:3px 8px; border-radius:6px; }"
        ".kgen-leg-name { font-weight:900; color:#ffffff; flex:1 1 200px; "
        "  font-size:1rem; text-shadow:0 1px 3px rgba(0,0,0,.5); }"
        ".kgen-meta { color:#7dd3fc; font-weight:600; font-size:.82rem; }"
        ".kgen-leg-score { display:flex; align-items:center; gap:6px; }"
        ".kgen-score { font-weight:900; font-size:1.05rem; color:#60a5fa; "
        "  font-variant-numeric: tabular-nums; }"
        ".kgen-ctx { color:#7dd3fc; font-size:.86rem; margin: 4px 0 4px 0; "
        "  font-variant-numeric: tabular-nums; }"
        ".kgen-stats { color:#e2e8f0; font-size:.84rem; margin: 0 0 4px 0; "
        "  background:rgba(255,255,255,.05); border-radius:6px; padding:5px 8px; "
        "  font-variant-numeric: tabular-nums; border:1px solid rgba(255,255,255,.07); }"
        ".kgen-reasons { margin: 4px 0 2px 18px; padding:0; color:#cbd5e1; "
        "  font-size:.88rem; }"
        ".kgen-reasons li { margin: 1px 0; line-height:1.35; }"
        ".kgen-disclaimer { color:#64748b; font-size:.78rem; margin: 6px 2px 12px 2px; "
        "  font-style:italic; }"
        ".kgen-board { background:#0f172a; color:#cbd5e1; border-radius:14px; "
        "  padding:12px 14px; margin: 8px 0 14px 0; }"
        ".kgen-board h4 { color:#fde68a; margin: 0 0 6px 0; "
        "  letter-spacing:.04em; font-size:.95rem; font-weight:900; "
        "  text-transform:uppercase; }"
        ".kgen-board table { width:100%; border-collapse:collapse; "
        "  font-variant-numeric: tabular-nums; font-size:.86rem; }"
        ".kgen-board th { text-align:left; color:#94a3b8; font-weight:700; "
        "  padding:4px 6px; border-bottom:1px solid #1e293b; "
        "  font-size:.75rem; text-transform:uppercase; letter-spacing:.04em; }"
        ".kgen-board td { padding:5px 6px; border-bottom:1px dashed #1e293b; "
        "  color:#e2e8f0; }"
        ".kgen-board td.num { text-align:right; }"
        ".kgen-board tr:last-child td { border-bottom:none; }"
        "@media (max-width:520px) { .kgen-leg-name { font-size:.92rem; } "
        "  .kgen-card-title { font-size:1rem; } .kgen-reasons { font-size:.84rem; } "
        "  .kgen-stats { font-size:.80rem; } "
        "  .kgen-board table { font-size:.80rem; } "
        "  .kgen-board th, .kgen-board td { padding:3px 4px; } }"
        "</style>"
    )
    st.markdown(css, unsafe_allow_html=True)

    # ---- Top-of-board: ranked starters table ----------------------------
    top_n = min(10, len(pool_k))
    board_rows = []
    for _, r in pool_k.head(top_n).iterrows():
        try:
            ai = f"{float(r.get('AI K Score')):.1f}"
        except Exception:
            ai = "—"
        try:
            kp = f"{float(r.get('K%')):.1f}"
        except Exception:
            kp = "—"
        try:
            wh = f"{float(r.get('Whiff%')):.1f}"
        except Exception:
            wh = "—"
        try:
            opk = f"{float(r.get('Opp Lineup K%')):.1f}"
        except Exception:
            opk = "—"
        try:
            pk = f"{float(r.get('Proj Ks')):.1f}"
        except Exception:
            pk = "—"
        try:
            sl = f"{float(r.get('Suggested Line')):.1f}"
        except Exception:
            sl = "—"
        board_rows.append(
            f"<tr><td><b>{r.get('Pitcher','')}</b> "
            f"<span style='color:#94a3b8;'>{r.get('Team','')} {r.get('Loc','')} "
            f"{r.get('Opp','')}</span></td>"
            f"<td class='num'>{ai}</td>"
            f"<td class='num'>{kp}</td>"
            f"<td class='num'>{wh}</td>"
            f"<td class='num'>{opk}</td>"
            f"<td class='num'>{pk}</td>"
            f"<td class='num'>O {sl}</td></tr>"
        )
    st.markdown(
        "<div class='kgen-board'>"
        f"<h4>🏆 Top {top_n} Strikeout Targets — slate board</h4>"
        "<table><thead><tr>"
        "<th>Pitcher</th><th class='num'>AI&nbsp;K</th>"
        "<th class='num'>K%</th><th class='num'>Whiff</th>"
        "<th class='num'>Opp&nbsp;K%</th>"
        "<th class='num'>Proj&nbsp;Ks</th>"
        "<th class='num'>Sugg&nbsp;Line</th>"
        "</tr></thead><tbody>"
        + "".join(board_rows) +
        "</tbody></table>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ---- Recommended tickets --------------------------------------------
    st.markdown(
        _render_k_card(
            "🎯 Top Single — Best K Pick",
            legs_1, f"{_k_risk} · single · #{_k_seed}",
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        _render_k_card(
            "🚀 Recommended 2-Leg K Parlay",
            legs_2, f"{_k_risk} · 2-leg · #{_k_seed}",
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        _render_k_card(
            "🔥 Recommended 3-Leg K Parlay",
            legs_3, f"{_k_risk} · 3-leg · #{_k_seed}",
        ),
        unsafe_allow_html=True,
    )

    if _k_show_alts:
        def _sig_k(legs_):
            return tuple(sorted(str(l.get("Pitcher", "")) for l in (legs_ or [])))
        seen_2 = {_sig_k(legs_2)}
        seen_3 = {_sig_k(legs_3)}
        extra_seeds = [331, 433, 547, 659, 773, 887]
        alt_2_pool = [alt_2_a, alt_2_b] + [
            _k_weighted_sample(pool_k, 2, _k_avoid_same_game, seed=_k_seed + s)
            for s in extra_seeds
        ]
        alt_3_pool = [alt_3_a, alt_3_b] + [
            _k_weighted_sample(pool_k, 3, _k_avoid_same_game, seed=_k_seed * 1009 + s)
            for s in extra_seeds
        ]
        chosen_2, chosen_3 = [], []
        for cand in alt_2_pool:
            sig = _sig_k(cand)
            if sig in seen_2 or not cand: continue
            seen_2.add(sig); chosen_2.append(cand)
            if len(chosen_2) >= 2: break
        for cand in alt_3_pool:
            sig = _sig_k(cand)
            if sig in seen_3 or not cand: continue
            seen_3.add(sig); chosen_3.append(cand)
            if len(chosen_3) >= 2: break
        if chosen_2 or chosen_3:
            st.markdown(
                '<div class="section-title" style="font-size:1.08rem;margin-top:6px;">'
                '🔁 Alternate K Tickets</div>',
                unsafe_allow_html=True,
            )
        for i, cand in enumerate(chosen_2, 1):
            st.markdown(
                _render_k_card(
                    f"🚀 Alt 2-Leg K Parlay #{i}", cand, f"{_k_risk} · 2-leg · alt {i}"
                ),
                unsafe_allow_html=True,
            )
        for i, cand in enumerate(chosen_3, 1):
            st.markdown(
                _render_k_card(
                    f"🔥 Alt 3-Leg K Parlay #{i}", cand, f"{_k_risk} · 3-leg · alt {i}"
                ),
                unsafe_allow_html=True,
            )

    # Source freshness row.
    try:
        st.markdown(
            render_source_chips([
                "savant:pitchers", "savant:batters",
                "statsapi:schedule", "statsapi:boxscore",
                "rotogrinders:weather", "openmeteo:weather",
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    st.markdown(
        '<div class="kgen-disclaimer">'
        '⚠️ <b>Disclaimer:</b> Recommendations are model-driven analytics built '
        'from public Statcast / StatsAPI / weather data — <b>not guaranteed outcomes</b>. '
        'Suggested lines are projections, not book lines; verify with your '
        'sportsbook before placing any bet. Bet responsibly.'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.expander("How the AI K Score works"):
        st.markdown(
            "**AI K Score (0-100)** — composite over every available "
            "strikeout-relevant signal:\n"
            "- **20%** Strikeout Score (existing K%/Whiff% composite)\n"
            "- **16%** K% (Savant; StatsAPI fallback)\n"
            "- **14%** Whiff%\n"
            "- **10%** SwStr% (Swing% × Whiff%)\n"
            "- **10% Opp Lineup K%** — lineup-weighted batter K% for the "
            "opposing team (top-of-order spots get a 1.2× weight)\n"
            "- **10%** Workload runway (IP volume normalized)\n"
            "- **10%** Park × weather K modifier\n"
            "- **10%** Inverted WHIP (deeper outings ⇒ more K opportunities)\n\n"
            "**Pool:** every probable starter on upcoming/live games. "
            "**Sleeper Hunt** boosts under-the-radar arms with elite Whiff% "
            "and a strikeout-prone matchup so they can win a slot.\n\n"
            "**Selection:** weighted sampling by AI K Score, with "
            "Avoid-same-game constraint. **Generate New K Tickets** rerolls "
            "within the same eligible pool.\n\n"
            "**Proj Ks:** K% × estimated batters faced (~22 BF baseline, "
            "tweaked by Opp Lineup K% and environment). **Suggested Line** "
            "is Proj Ks rounded to nearest 0.5 then nudged down 0.5 — it is "
            "a model projection, not a book line."
        )
    st.stop()


# ============== Ballpark Weather rankings view ==============
# Slate-wide hitter-friendliness leaderboard. Reuses the existing
# RotoGrinders → Open-Meteo weather pipeline (`get_combined_weather`) and
# `compute_weather_impact` so the score is consistent with what shows up on
# each game card. No new scraping is added.
if _view == "🌬️ Ballpark Weather":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🌬️ Ballpark Weather — Hitter-Friendliness Rankings</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Every game on tonight's slate, ranked from most hitter-friendly to "
        "least. Score blends park HR factor, temperature, wind out/in to CF, "
        "humidity, and rain risk via the same model that powers each game's "
        "weather card."
    )

    # ---- Compass helper for the wind dial (degrees → 8-point compass) ----
    def _bw_compass(deg):
        if deg is None:
            return "—"
        try:
            d = float(deg) % 360.0
        except Exception:
            return "—"
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[int((d + 22.5) // 45) % 8]

    # ---- Build one row per scheduled game ----
    bw_rows = []
    with st.spinner("Loading ballpark weather for every game…"):
        for _, gr in schedule_df.iterrows():
            try:
                wx = get_combined_weather(gr) or {}
            except Exception:
                wx = {}
            try:
                imp = compute_weather_impact(
                    wx, gr.get("park_factor", 100), gr.get("home_abbr", "")
                ) or {}
            except Exception:
                imp = {}

            domed = gr.get("home_abbr", "") in DOMED_PARKS

            # ---- Hitter-friendly score (higher = better for bats) ----
            # Anchored at compute_weather_impact's HR%/Runs% deltas (already
            # account for park, temp, wind-out, humidity) and lightly
            # penalized for rain risk. Domes / unknown weather collapse to a
            # neutral 50 so they sort to the middle rather than the top or
            # bottom.
            hr_pct = imp.get("hr_pct")
            runs_pct = imp.get("runs_pct")
            rain_pct = wx.get("rain_pct") or 0
            if hr_pct is None and runs_pct is None:
                score = 50.0
            else:
                base = 0.6 * float(hr_pct or 0) + 0.4 * float(runs_pct or 0)
                rain_drag = max(0.0, float(rain_pct) - 20.0) * 0.20
                score = 50.0 + base * 1.6 - rain_drag
                if domed:
                    # Roof closed → no outdoor weather edge; pull toward neutral.
                    score = 50.0 + (score - 50.0) * 0.25
                score = max(0.0, min(100.0, score))

            # ---- Reason chips: short why-it-ranks-here strip ----
            chips = []
            pf = gr.get("park_factor", 100)
            try:
                pf_i = int(round(float(pf)))
            except Exception:
                pf_i = 100
            if pf_i >= 105:
                chips.append(("Hitter park", "good", f"PF {pf_i}"))
            elif pf_i <= 95:
                chips.append(("Pitcher park", "bad", f"PF {pf_i}"))
            else:
                chips.append(("Neutral park", "neu", f"PF {pf_i}"))

            temp = wx.get("temp_f")
            if temp is not None and not domed:
                t_i = int(round(float(temp)))
                if t_i >= 80:
                    chips.append(("Hot", "good", f"{t_i}°F"))
                elif t_i <= 55:
                    chips.append(("Cold", "bad", f"{t_i}°F"))

            wind_lbl = imp.get("wind_label") or ""
            wind_mph = wx.get("wind_mph")
            if domed:
                chips.append(("Roof / Dome", "neu", "indoor"))
            elif wind_lbl in ("out", "out to CF") and wind_mph:
                chips.append(("Wind helping", "good",
                              f"{int(round(float(wind_mph)))} mph {wind_lbl}"))
            elif wind_lbl in ("in", "in from CF") and wind_mph:
                chips.append(("Wind suppressing", "bad",
                              f"{int(round(float(wind_mph)))} mph {wind_lbl}"))

            if rain_pct and float(rain_pct) >= 40:
                chips.append(("Rain risk", "bad", f"{int(round(float(rain_pct)))}%"))

            bw_rows.append({
                "matchup": gr.get("short_label") or
                           f"{gr.get('away_abbr','')} @ {gr.get('home_abbr','')}",
                "time": gr.get("time_short", ""),
                "venue": gr.get("venue", "Unknown"),
                "home_abbr": gr.get("home_abbr", ""),
                "domed": domed,
                "sky": imp.get("sky", "—"),
                "sky_icon": imp.get("sky_icon", ""),
                "temp": temp,
                "rain_pct": rain_pct,
                "wind_mph": wind_mph,
                "wind_dir_deg": wx.get("wind_dir_deg"),
                "wind_compass": _bw_compass(wx.get("wind_dir_deg")),
                "wind_label": wind_lbl or ("calm" if not domed else "roof closed"),
                "park_factor": pf_i,
                "hr_pct": hr_pct,
                "runs_pct": runs_pct,
                "score": round(score, 1),
                "chips": chips,
                "source_label": wx.get("source_label") or "—",
            })

    if not bw_rows:
        st.info("No games on the schedule yet. Check back closer to first pitch.")
        st.stop()

    bw_rows.sort(key=lambda r: r["score"], reverse=True)

    # ---- Dark, compact card/table styling (matches reference screenshot) ----
    st.markdown(
        "<style>"
        ".bw-wrap { background: linear-gradient(180deg, #0b1220 0%, #0f172a 100%); "
        "  border-radius: 14px; padding: 10px 8px; "
        "  border: 1px solid #1e293b; "
        "  box-shadow: 0 6px 20px rgba(2,6,23,.35); }"
        ".bw-row { display: grid; "
        "  grid-template-columns: 44px 1.4fr 0.9fr 1.4fr 0.7fr 0.7fr 0.9fr 0.8fr; "
        "  gap: 8px; align-items: center; "
        "  padding: 10px 12px; border-radius: 10px; "
        "  margin: 6px 0; "
        "  background: #111827; border: 1px solid #1f2937; "
        "  color: #e5e7eb; font-size: 0.92rem; }"
        ".bw-row.head { background: transparent; border: none; "
        "  color: #94a3b8; font-size: .72rem; letter-spacing: .08em; "
        "  text-transform: uppercase; padding: 4px 12px; margin: 0; }"
        ".bw-rank { display: inline-flex; align-items: center; justify-content: center; "
        "  width: 32px; height: 32px; border-radius: 50%; "
        "  background: #1e293b; color: #facc15; font-weight: 900; "
        "  border: 2px solid #facc15; }"
        ".bw-rank.r1 { background: #facc15; color: #0f172a; }"
        ".bw-rank.r2 { background: #cbd5e1; color: #0f172a; border-color: #cbd5e1; }"
        ".bw-rank.r3 { background: #b45309; color: #fde68a; border-color: #f59e0b; }"
        ".bw-matchup { font-weight: 800; color: #f8fafc; }"
        ".bw-meta { color: #94a3b8; font-size: .78rem; line-height: 1.25; }"
        ".bw-time { font-weight: 700; color: #e2e8f0; }"
        ".bw-venue { color: #cbd5e1; font-size: .8rem; }"
        ".bw-temp { font-weight: 800; color: #fbbf24; font-size: 1.05rem; }"
        ".bw-precip { color: #38bdf8; font-weight: 700; }"
        ".bw-wind { display: flex; align-items: center; gap: 6px; }"
        ".bw-arrow { display: inline-block; width: 22px; height: 22px; "
        "  border-radius: 50%; background: #0f172a; "
        "  border: 1px solid #334155; position: relative; "
        "  font-size: .72rem; line-height: 22px; text-align: center; "
        "  color: #fbbf24; font-weight: 800; }"
        ".bw-windspd { font-weight: 800; color: #e2e8f0; }"
        ".bw-windlbl { color: #94a3b8; font-size: .72rem; }"
        ".bw-score { display: inline-flex; align-items: center; "
        "  justify-content: center; min-width: 54px; "
        "  padding: 4px 10px; border-radius: 999px; "
        "  font-weight: 900; font-size: 1.0rem; "
        "  background: #1e293b; color: #facc15; border: 1px solid #334155; }"
        ".bw-score.good { background: linear-gradient(135deg, #14532d, #166534); "
        "  color: #bbf7d0; border-color: #22c55e; }"
        ".bw-score.bad  { background: linear-gradient(135deg, #4c1d24, #7f1d1d); "
        "  color: #fecaca; border-color: #f87171; }"
        ".bw-score.neu  { background: linear-gradient(135deg, #1e293b, #334155); "
        "  color: #fde68a; border-color: #facc15; }"
        ".bw-chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }"
        ".bw-chip { display: inline-flex; align-items: center; gap: 4px; "
        "  padding: 2px 7px; border-radius: 999px; font-size: .68rem; "
        "  font-weight: 700; letter-spacing: .02em; "
        "  background: #0f172a; border: 1px solid #334155; color: #cbd5e1; }"
        ".bw-chip.good { background: rgba(34,197,94,.12); "
        "  border-color: rgba(34,197,94,.55); color: #86efac; }"
        ".bw-chip.bad  { background: rgba(248,113,113,.10); "
        "  border-color: rgba(248,113,113,.55); color: #fecaca; }"
        ".bw-chip.neu  { background: rgba(250,204,21,.10); "
        "  border-color: rgba(250,204,21,.45); color: #fde68a; }"
        ".bw-chip-val { color: inherit; opacity: .85; font-weight: 600; }"
        "@media (max-width: 720px) { "
        "  .bw-row { grid-template-columns: 38px 1.3fr 0.7fr 0.7fr 0.85fr; "
        "    row-gap: 4px; } "
        "  .bw-row .bw-col-venue, .bw-row .bw-col-precip, .bw-row .bw-col-impact "
        "  { display: none; } "
        "  .bw-row.head .bw-col-venue, .bw-row.head .bw-col-precip, "
        "  .bw-row.head .bw-col-impact { display: none; } "
        "}"
        # Below 640px we drop the table grid entirely and render every game
        # as a self-contained dark card with stacked rows, matching the
        # Slate-Pitchers card visual language.
        "@media (max-width: 640px) { "
        "  .bw-row.head { display: none; } "
        "  .bw-row { display: block; padding: 12px 14px; margin: 8px 0; "
        "    border-radius: 14px; background: linear-gradient(180deg,#15102b 0%,#0b0820 100%); "
        "    border: 1px solid #2a1e4a; box-shadow: 0 4px 12px rgba(0,0,0,.30); } "
        "  .bw-row > div { display: block; padding: 2px 0; } "
        "  .bw-rank { width: 28px; height: 28px; "
        "    border-color: #fcd34d; color: #fcd34d; background: #3b1f6b; } "
        "  .bw-matchup { font-size: 1.05rem; color: #f8fafc; } "
        "  .bw-temp { display: inline-block; margin-right: 12px; } "
        "  .bw-wind { display: inline-flex; margin-right: 12px; } "
        "  .bw-precip { display: inline-block; } "
        # Show the columns the 720px breakpoint hid — they're useful again now
        # that each game is a full-width card.
        "  .bw-row .bw-col-venue, .bw-row .bw-col-precip, .bw-row .bw-col-impact "
        "  { display: inline-block !important; margin-right: 12px; } "
        "}"
        "</style>",
        unsafe_allow_html=True,
    )

    # ---- Header row ----
    header_html = (
        '<div class="bw-row head">'
        '<div>#</div>'
        '<div>Matchup</div>'
        '<div>Time</div>'
        '<div class="bw-col-venue">Ballpark</div>'
        '<div>Temp</div>'
        '<div class="bw-col-precip">Precip</div>'
        '<div>Wind</div>'
        '<div class="bw-col-impact">Hitter Score</div>'
        '</div>'
    )

    cards_html = []
    for i, r in enumerate(bw_rows, 1):
        rank_cls = "r1" if i == 1 else ("r2" if i == 2 else ("r3" if i == 3 else ""))
        # Score color tier
        sc = r["score"]
        if sc >= 62: sc_cls = "good"
        elif sc <= 38: sc_cls = "bad"
        else: sc_cls = "neu"

        temp_html = (f"{int(round(float(r['temp'])))}°F"
                     if r["temp"] is not None else
                     ("Dome" if r["domed"] else "—"))
        rain_v = r["rain_pct"]
        rain_html = (f"{int(round(float(rain_v)))}%"
                     if rain_v is not None and not r["domed"] else
                     ("—" if not r["domed"] else "—"))
        wind_v = r["wind_mph"]
        if r["domed"]:
            wind_html = (
                '<span class="bw-arrow">·</span>'
                '<span class="bw-windspd">—</span>'
                '<span class="bw-windlbl">roof</span>'
            )
        elif wind_v is None:
            wind_html = (
                '<span class="bw-arrow">·</span>'
                '<span class="bw-windspd">—</span>'
                '<span class="bw-windlbl">unknown</span>'
            )
        else:
            wind_html = (
                f'<span class="bw-arrow" title="{r["wind_compass"]}">{r["wind_compass"]}</span>'
                f'<span class="bw-windspd">{int(round(float(wind_v)))} mph</span>'
                f'<span class="bw-windlbl">{r["wind_label"]}</span>'
            )

        chips_html = ""
        if r["chips"]:
            chip_items = []
            for label, kind, val in r["chips"]:
                chip_items.append(
                    f'<span class="bw-chip {kind}">{label}'
                    f'<span class="bw-chip-val">· {val}</span></span>'
                )
            chips_html = '<div class="bw-chips">' + "".join(chip_items) + '</div>'

        # HR / Runs deltas in the meta line (transparent)
        hr_pct = r["hr_pct"]; runs_pct = r["runs_pct"]
        impact_bits = []
        if hr_pct is not None:
            impact_bits.append(f"HR {hr_pct:+d}%")
        if runs_pct is not None:
            impact_bits.append(f"Runs {runs_pct:+d}%")
        impact_str = " · ".join(impact_bits) if impact_bits else "neutral"

        cards_html.append(
            '<div class="bw-row">'
            f'<div><span class="bw-rank {rank_cls}">{i}</span></div>'
            f'<div><div class="bw-matchup">{r["matchup"]}</div>'
            f'<div class="bw-meta">{r["sky_icon"]} {r["sky"]} · {impact_str}</div>'
            f'{chips_html}</div>'
            f'<div class="bw-time">{r["time"]}</div>'
            f'<div class="bw-col-venue bw-venue">{r["venue"]}</div>'
            f'<div class="bw-temp">{temp_html}</div>'
            f'<div class="bw-col-precip bw-precip">{rain_html}</div>'
            f'<div class="bw-wind">{wind_html}</div>'
            f'<div class="bw-col-impact"><span class="bw-score {sc_cls}">{sc:.1f}</span></div>'
            '</div>'
        )

    st.markdown(
        '<div class="bw-wrap">' + header_html + "".join(cards_html) + '</div>',
        unsafe_allow_html=True,
    )

    with st.expander("How the hitter-friendly score is built"):
        st.markdown(
            "**Hitter Score (0-100)** — anchored at 50 (neutral) and reuses "
            "the existing `compute_weather_impact` model:\n\n"
            "- **Park HR factor** — hitter parks (PF > 100) lift the score; "
            "pitcher parks (PF < 100) drop it.\n"
            "- **Temperature** — every °F over 70 ≈ +0.4% HR; cold air drags "
            "carry.\n"
            "- **Wind out / in to CF** — out-to-CF wind helps (~+1% HR per "
            "mph of out-component); in-from-CF wind kills.\n"
            "- **Humidity** — heavier (humid) air shortens flights slightly.\n"
            "- **Rain risk** — high precip% adds a drag (delay / suppressed "
            "carry).\n"
            "- **Domes** — roof-closed games collapse to ~50 (neutral) — "
            "no outdoor edge either way.\n\n"
            "Higher = more hitter-friendly. Data source: RotoGrinders MLB "
            "weather (preferred) → Open-Meteo fallback. Park factors from the "
            "app's built-in table."
        )

    # Source freshness chips so users know how stale/live the weather is.
    try:
        st.markdown(
            render_source_chips([
                "rotogrinders:weather", "openmeteo:weather", "statsapi:schedule",
            ]),
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    st.stop()


# ============== AI 1+ Hits Parlay view ==============
# Builds 2-, 3-, and 4-leg "1+ hits" parlays from the slate's full eligible
# lineup pool. Score is HITS-specific: contact quality (xBA, AVG, K%-inverse,
# LD%, SweetSpot%, Whiff%-inverse), opportunity (lineup spot PA weight),
# matchup (opposing SP AVG/xBA-allowed, K%, HardHit%-allowed, platoon edge),
# environment (park + weather hit-friendliness), and a small recent-power
# tilt (xwOBA / xSLG) so productive contact bats float up. Avoids HR-specific
# weighting (Barrel%, FB%, Pull%) so we don't bias toward boom/bust profiles
# that go 0-for-4 with a homer.
if _view == "🥎 AI 1+ Hits Parlay":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🥎 AI 1+ Hits Parlay Generator</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Two-, three-, and four-leg <b>1+ hits</b> parlays built from the '
        'slate model. The <b>AI Hits Score</b> blends <b>contact quality</b> '
        '(xBA, AVG, K%-inverse, LD%, SweetSpot%, Whiff%-inverse), '
        '<b>opportunity</b> (lineup-spot PA weight), <b>matchup</b> '
        '(opposing SP AVG/xBA allowed, K%, HardHit% allowed, platoon edge), '
        'and <b>environment</b> (park + weather hit-friendliness). '
        'No HR bias — every leg is a contact-first 1+ hit play.'
        '</div>',
        unsafe_allow_html=True,
    )

    if batters_df is None or batters_df.empty:
        st.warning("Batter CSV (`Data:savant_batters.csv.csv`) hasn’t loaded yet.")
        st.stop()

    # ---- Eligibility filter (mirrors AI HR Parlay) ----------------------
    # Hits parlays are batter picks: once a game has started, lineups are
    # locked and in-game state (deep innings) is unreliable for prop
    # recommendations. Only accept pre-game matchups.
    _COMPLETED_TOKENS_H = (
        "final", "completed", "game over", "postponed", "cancelled",
        "canceled", "suspended", "forfeit", "if necessary",
    )
    _STARTED_TOKENS_H = (
        "in progress", "live", "manager challenge", "review",
        "delayed",
    )
    _PREGAME_TOKENS_H = (
        "scheduled", "pre-game", "pregame", "pre game",
        "warmup", "warm-up", "warm up",
    )

    def _hits_game_eligible(row) -> bool:
        status = str(row.get("status") or "").strip().lower()
        if any(tok in status for tok in _COMPLETED_TOKENS_H):
            return False
        if any(tok in status for tok in _STARTED_TOKENS_H):
            return False
        gt = row.get("game_time_utc")
        try:
            start_utc = pd.to_datetime(gt, utc=True)
        except Exception:
            start_utc = pd.NaT
        _now = pd.Timestamp.now('UTC')
        now_utc = _now if _now.tzinfo is not None else _now.tz_localize("UTC")
        if any(tok in status for tok in _PREGAME_TOKENS_H):
            if pd.isna(start_utc):
                return True
            return start_utc > now_utc
        if pd.isna(start_utc):
            return False
        return start_utc > now_utc

    if schedule_df is None or schedule_df.empty:
        st.warning("No games on the slate. Pick a different date or check back later.")
        st.stop()

    _h_mask = schedule_df.apply(_hits_game_eligible, axis=1)
    eligible_h_df = schedule_df[_h_mask].reset_index(drop=True)
    _h_total = int(len(schedule_df))
    _h_elig  = int(len(eligible_h_df))
    _h_excl  = _h_total - _h_elig

    if eligible_h_df.empty:
        st.warning(
            f"All {_h_total} games on this slate have already started or finished. "
            f"No pre-game matchups remain to build a hits parlay from."
        )
        st.stop()

    st.markdown(
        f'<div style="margin:0 0 10px 0; padding:8px 12px; '
        f'border-left:3px solid #7c3aed; background:#f5f3ff; '
        f'border-radius:6px; color:#065f46; font-size:0.88rem;">'
        f'🕒 Using <b>{_h_elig}</b> pre-game matchup'
        f'{"s" if _h_elig != 1 else ""}; '
        f'started &amp; completed games excluded'
        f'{f" ({_h_excl} hidden)" if _h_excl > 0 else ""}.'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- Per-game ballpark+weather indicator map keyed by short_label.
    # Mirrors the AI HR Parlay flow so each leg can render the BPW pill.
    _h_bpw_map = {}
    for _, _g in eligible_h_df.iterrows():
        try:
            _gc = build_game_context(_g)
            _wx = _gc.get("weather", {}) if isinstance(_gc, dict) else {}
        except Exception:
            _wx = {}
        try:
            _h_bpw_map[str(_g.get("short_label", ""))] = park_weather_indicator(
                _wx, _g.get("park_factor"), _g.get("home_abbr", "")
            )
        except Exception:
            _h_bpw_map[str(_g.get("short_label", ""))] = {
                "label": "OK", "tier": "ok", "hr_pct": 0,
                "icon": "🟡", "tooltip": "Park/weather signal unavailable",
            }

    # ---- Controls ------------------------------------------------------
    hc_cols = st.columns([1.6, 1.0, 1.0, 1.0])
    with hc_cols[0]:
        _h_risk = st.radio(
            "Risk profile",
            ["Safer", "Balanced", "Sleeper Hunt", "Aggressive"],
            index=1,
            horizontal=True,
            key="ai_hits_risk",
            help=(
                "Safer: top-of-order contact bats, strict thresholds. "
                "Balanced: default mix of contact stars and quality plays. "
                "Sleeper Hunt: boosts under-radar contact bats with strong "
                "AI Hits Score. Aggressive: opens spots 1-9, deeper variance."
            ),
        )
    with hc_cols[1]:
        _h_avoid_game = st.checkbox(
            "Avoid same game", value=True, key="ai_hits_avoid_game",
            help="Prevent two legs from the same game (correlation risk).",
        )
    with hc_cols[2]:
        _h_avoid_team = st.checkbox(
            "Avoid same team", value=True, key="ai_hits_avoid_team",
            help="Prevent two legs from the same team.",
        )
    with hc_cols[3]:
        _h_max_spot = st.slider(
            "Max lineup spot", min_value=4, max_value=9, value=8, step=1,
            key="ai_hits_max_spot",
            help="Hide bats batting deeper than this in the order.",
        )

    # ---- Hits-specific scoring helper ----------------------------------
    # Returns AI Hits Score (0..100) plus the structured signal dict used
    # by the reasons builder. Robust to missing fields — every input falls
    # back to a neutral mid-range value rather than crashing.
    def _norm_h(val, lo, hi, default=50.0):
        try:
            f = float(val)
            if pd.isna(f):
                return float(default)
        except Exception:
            return float(default)
        if hi == lo:
            return float(default)
        return float(max(0.0, min(100.0, (f - lo) / (hi - lo) * 100.0)))

    def _opt_float(v):
        try:
            f = float(v)
            if pd.isna(f):
                return None
            return f
        except Exception:
            return None

    def _platoon_edge_h(bat_side: str, opp_hand: str) -> float:
        """Return 0..1 platoon-edge multiplier component (0.5 neutral)."""
        b = (str(bat_side or "").upper()[:1])
        h = (str(opp_hand or "").upper()[:1])
        if not b or not h:
            return 0.5
        if b == "S":
            return 0.62  # switch hitters always grab a small edge
        if (b == "L" and h == "R") or (b == "R" and h == "L"):
            return 0.85
        if (b == "L" and h == "L") or (b == "R" and h == "R"):
            return 0.30
        return 0.5

    def _ai_hits_score(b_row, p_row, lineup_spot, bat_side, opp_hand,
                       weather: dict, park_factor):
        """Compute AI Hits Score (0..100) and signals for one batter vs SP.

        Weights:
          35% Contact   (xBA 12 · AVG 9 · K%-inverse 7 · LD% 4 · SweetSpot% 3)
          15% Plate-skill (Whiff%-inverse 8 · Bat speed 4 · BB% 3)
          20% Opportunity (PA weight by lineup spot)
          20% Matchup    (SP AVG/xBA allowed 8 · SP K% inverse 6 · SP HardHit% 4
                          · Platoon edge 2)
          10% Environment (park HR/run factor + weather hit boost)
        """
        # --- Contact quality (35) ---
        xba    = _opt_float(b_row.get("xBA")     if b_row is not None else None)
        avg    = _opt_float(b_row.get("AVG")     if b_row is not None else None)
        k_pct  = _opt_float(b_row.get("K%")      if b_row is not None else None)
        ld     = _opt_float(b_row.get("LD%")     if b_row is not None else None)
        ss_pct = _opt_float(b_row.get("SweetSpot%") if b_row is not None else None)
        whiff  = _opt_float(b_row.get("Whiff%")  if b_row is not None else None)
        bb_pct = _opt_float(b_row.get("BB%")     if b_row is not None else None)
        bs     = _opt_float(b_row.get("BatSpeed") if b_row is not None else None)
        xwoba  = _opt_float(b_row.get("xwOBA")   if b_row is not None else None)
        xslg   = _opt_float(b_row.get("xSLG")    if b_row is not None else None)
        # OPS surfaces the same on-base-plus-slugging bat quality used in
        # heat maps / target tables. Robust to source omissions: fall back to
        # OBP+SLG when the precomputed OPS is missing.
        ops    = _opt_float(b_row.get("OPS")     if b_row is not None else None)
        if ops is None and b_row is not None:
            _obp_v = _opt_float(b_row.get("OBP"))
            _slg_v = _opt_float(b_row.get("SLG"))
            if _obp_v is not None and _slg_v is not None:
                ops = _obp_v + _slg_v

        n_xba   = _norm_h(xba,    0.220, 0.310)
        n_avg   = _norm_h(avg,    0.210, 0.320)
        n_kinv  = 100.0 - _norm_h(k_pct, 12.0, 32.0)  # lower K% better
        n_ld    = _norm_h(ld,     17.0, 28.0)
        n_ss    = _norm_h(ss_pct, 28.0, 40.0)
        # OPS index — universal bat-quality scalar. Anchored .620 → .950 so
        # the band matches the heat-map / target-table scoring.
        n_ops   = _norm_h(ops,    0.620, 0.950)
        # Contact part keeps its 35% total but reallocates 3 pts to OPS so a
        # strong overall bat doesn't get punished when xBA / AVG haven't yet
        # caught up to the underlying production. xBA → 12 (unchanged),
        # AVG 9 → 8, K%-inv 7 → 6, LD% 4 → 3, SS% 3 (unchanged), OPS 3.
        contact = (0.12*n_xba + 0.08*n_avg + 0.06*n_kinv + 0.03*n_ld
                   + 0.03*n_ss + 0.03*n_ops)

        # --- Plate skill (15) ---
        n_whiffinv = 100.0 - _norm_h(whiff, 16.0, 34.0)
        n_bs       = _norm_h(bs,    67.0, 76.0)
        n_bb       = _norm_h(bb_pct, 5.0, 14.0)
        plate = 0.08*n_whiffinv + 0.04*n_bs + 0.03*n_bb

        # --- Opportunity (20) — PA weight by lineup spot ---
        spot_w = _spot_pa_weight(lineup_spot)  # 0..100
        opp_part = 0.20 * spot_w

        # --- Matchup vs opposing SP (20) ---
        # SP AVG / xBA allowed -> higher = easier hits.
        p_avg = _opt_float(p_row.get("AVG") if p_row is not None else None)
        p_xba = _opt_float(p_row.get("xBA") if p_row is not None else None)
        p_k   = _opt_float(p_row.get("K%")  if p_row is not None else None)
        p_hh  = _opt_float(p_row.get("HardHit%") if p_row is not None else None)
        n_pavg = _norm_h(p_avg, 0.210, 0.290)   # higher AVG-against = better for hitter
        n_pxba = _norm_h(p_xba, 0.220, 0.290)
        # Low K% pitcher = more contact = more hit chances. Invert.
        n_pkinv = 100.0 - _norm_h(p_k, 16.0, 30.0)
        n_phh   = _norm_h(p_hh, 32.0, 44.0)     # lots of hard contact allowed = good
        sp_avg_part = 0.5*n_pavg + 0.5*n_pxba
        plat = _platoon_edge_h(bat_side, opp_hand) * 100.0
        matchup = 0.08*sp_avg_part + 0.06*n_pkinv + 0.04*n_phh + 0.02*plat

        # --- Environment (10) ---
        # Use compute_weather_impact for runs/HR % deltas and park factor.
        env_score = 50.0
        try:
            imp = compute_weather_impact(weather or {}, park_factor, "") or {}
            r_pct = imp.get("runs_pct")
            h_pct = imp.get("hr_pct")
            base = 0.0
            if r_pct is not None: base += 0.7 * float(r_pct)
            if h_pct is not None: base += 0.3 * float(h_pct)
            env_score = max(0.0, min(100.0, 50.0 + base * 1.2))
        except Exception:
            pass
        env = 0.10 * env_score

        score = contact + plate + opp_part + matchup + env
        score = round(max(0.0, min(100.0, score)), 1)

        signals = {
            "xBA": xba, "AVG": avg, "K%": k_pct, "LD%": ld,
            "SweetSpot%": ss_pct, "Whiff%": whiff, "BB%": bb_pct,
            "BatSpeed": bs, "xwOBA": xwoba, "xSLG": xslg, "OPS": ops,
            "p_AVG_against": p_avg, "p_xBA_against": p_xba,
            "p_K%": p_k, "p_HardHit%": p_hh,
            "platoon": plat / 100.0,
            "spot_w": spot_w, "env": env_score,
            "contact_part": contact, "matchup_part": matchup,
        }
        return score, signals

    # ---- Build candidate pool (one row per batter on every eligible game)
    def _build_hits_pool():
        rows = []
        for _, g in eligible_h_df.iterrows():
            g = apply_live_pitcher_overlay(g)
            try:
                cc = build_game_context(g)
            except Exception:
                continue
            wx = cc.get("weather", {}) if isinstance(cc, dict) else {}
            for side, lineup_df, opp_pitcher, opp_pid in (
                ("away", cc.get("away_lineup"), g.get("home_probable"),
                 g.get("home_probable_id")),
                ("home", cc.get("home_lineup"), g.get("away_probable"),
                 g.get("away_probable_id")),
            ):
                if lineup_df is None or lineup_df.empty:
                    continue
                p_row = find_pitcher_row(pitchers_df, opp_pitcher,
                                          pitcher_id=opp_pid)
                opp_p_hand = (cc.get("home_pitch_hand") if side == "away"
                              else cc.get("away_pitch_hand")) or ""
                lineup_status = (cc.get("away_status") if side == "away"
                                 else cc.get("home_status")) or "Not Posted"
                bpw = _h_bpw_map.get(str(g.get("short_label", "")), {
                    "label":"OK","tier":"ok","hr_pct":0,
                    "icon":"🟡","tooltip":"Park/weather signal"
                })
                for _, r in lineup_df.iterrows():
                    b = find_player_row(batters_df, r["name_key"], r["team"],
                                         player_id=r.get("player_id"))
                    bat_side = r.get("bat_side") or ""
                    spot = r.get("lineup_spot")
                    score, sig = _ai_hits_score(
                        b, p_row, spot, bat_side, opp_p_hand,
                        wx, g.get("park_factor", 100),
                    )
                    rows.append({
                        "_player_id": r.get("player_id"),
                        "Hitter":   r.get("player_name", ""),
                        "Team":     norm_team(r.get("team", "")),
                        "Bat":      bat_side,
                        "Spot":     int(spot) if pd.notna(spot) else 99,
                        "LineupStatus": lineup_status,
                        "Game":     g.get("short_label", ""),
                        "Opp SP":   opp_pitcher or "TBD",
                        "OppHand":  opp_p_hand,
                        "AI Hits Score": score,
                        # surfaced metrics for reasons + compact line
                        "AVG":      sig.get("AVG"),
                        "xBA":      sig.get("xBA"),
                        "K%":       sig.get("K%"),
                        "LD%":      sig.get("LD%"),
                        "SweetSpot%": sig.get("SweetSpot%"),
                        "Whiff%":   sig.get("Whiff%"),
                        "BB%":      sig.get("BB%"),
                        "BatSpeed": sig.get("BatSpeed"),
                        "xwOBA":    sig.get("xwOBA"),
                        "xSLG":     sig.get("xSLG"),
                        "OPS":      sig.get("OPS"),
                        "p_AVG_against": sig.get("p_AVG_against"),
                        "p_xBA_against": sig.get("p_xBA_against"),
                        "p_K%":     sig.get("p_K%"),
                        "p_HardHit%": sig.get("p_HardHit%"),
                        "platoon":  sig.get("platoon"),
                        "env":      sig.get("env"),
                        "BPW Label":  bpw.get("label"),
                        "BPW Tier":   bpw.get("tier"),
                        "BPW HR%":    bpw.get("hr_pct"),
                        "BPW Icon":   bpw.get("icon"),
                        "BPW Tip":    bpw.get("tooltip"),
                    })
        return pd.DataFrame(rows)

    with st.spinner("Scoring 1+ hits candidates across the slate…"):
        h_pool = _build_hits_pool()

    if h_pool is None or h_pool.empty:
        st.info("No lineups posted yet for the upcoming/live games. Check back closer to first pitch.")
        st.stop()

    # ---- Risk-profile thresholds + filter ------------------------------
    if _h_risk == "Safer":
        h_min_score, h_min_xba, h_max_k = 62.0, 0.255, 26.0
        h_max_spot = min(_h_max_spot, 5)
    elif _h_risk == "Sleeper Hunt":
        h_min_score, h_min_xba, h_max_k = 50.0, 0.235, 30.0
        h_max_spot = _h_max_spot
    elif _h_risk == "Aggressive":
        h_min_score, h_min_xba, h_max_k = 45.0, 0.225, 34.0
        h_max_spot = _h_max_spot
    else:  # Balanced
        h_min_score, h_min_xba, h_max_k = 55.0, 0.245, 28.0
        h_max_spot = _h_max_spot

    # Soft thresholds: if a metric is missing we DON'T filter on it (None
    # passes), so coverage is robust when CSVs are partial.
    def _passes_min(v, lo):
        if v is None: return True
        try: return float(v) >= float(lo)
        except Exception: return True

    def _passes_max(v, hi):
        if v is None: return True
        try: return float(v) <= float(hi)
        except Exception: return True

    h_pool = h_pool[h_pool["AI Hits Score"].fillna(0) >= float(h_min_score)]
    h_pool = h_pool[h_pool["xBA"].apply(lambda v: _passes_min(v, h_min_xba))]
    h_pool = h_pool[h_pool["K%"].apply(lambda v: _passes_max(v, h_max_k))]
    h_pool = h_pool[h_pool["Spot"].fillna(99).astype(int) <= int(h_max_spot)]
    h_pool = h_pool.sort_values("AI Hits Score", ascending=False).reset_index(drop=True)

    if h_pool.empty or len(h_pool) < 2:
        st.info(
            f"Not enough qualifying bats for the **{_h_risk}** profile — "
            f"only {len(h_pool)} hitter(s) cleared the thresholds. "
            f"Try **Sleeper Hunt** or **Aggressive**, or raise **Max lineup spot**."
        )
        st.stop()

    # ---- Reroll controls -----------------------------------------------
    if "ai_hits_seed" not in st.session_state:
        st.session_state["ai_hits_seed"] = 1
    if "ai_hits_generated_at" not in st.session_state:
        st.session_state["ai_hits_generated_at"] = datetime.now(
            ZoneInfo("America/New_York")
        ).strftime("%Y-%m-%d %I:%M:%S %p ET")

    hr_cols = st.columns([1.0, 1.0, 2.0])
    with hr_cols[0]:
        if st.button("🎲 Generate New Hits Parlays", key="ai_hits_reroll",
                     width='stretch',
                     help="Reroll: rebuilds 2-leg, 3-leg, and 4-leg tickets via "
                          "weighted sampling on the same eligible pool."):
            st.session_state["ai_hits_seed"] = int(
                st.session_state.get("ai_hits_seed", 1)
            ) + 1
            st.session_state["ai_hits_generated_at"] = datetime.now(
                ZoneInfo("America/New_York")
            ).strftime("%Y-%m-%d %I:%M:%S %p ET")
    with hr_cols[1]:
        _h_show_alts = st.checkbox(
            "Show alternate tickets", value=True, key="ai_hits_show_alts",
            help="Also surface alternate 2-leg and 3-leg tickets from the same pool.",
        )
    with hr_cols[2]:
        st.markdown(
            f'<div style="padding-top:6px; color:#475569; font-size:0.86rem;">'
            f'🎟️ <b>Ticket #{int(st.session_state["ai_hits_seed"])}</b> · '
            f'{len(h_pool)} eligible hitters · '
            f'Generated {st.session_state["ai_hits_generated_at"]}'
            f'</div>',
            unsafe_allow_html=True,
        )

    _h_seed = int(st.session_state["ai_hits_seed"])

    # Sleeper-hunt boost on contact bats with low playing-time recognition:
    # weight rises for bats with strong xBA but lower lineup spot (under-owned
    # contact plays). Aggressive gets a smaller version; Safer none.
    if _h_risk == "Sleeper Hunt":
        h_sleeper_bias = 0.55
    elif _h_risk == "Aggressive":
        h_sleeper_bias = 0.25
    elif _h_risk == "Balanced":
        h_sleeper_bias = 0.10
    else:
        h_sleeper_bias = 0.0

    def _weighted_sample_hits(_pool, n: int,
                              avoid_same_game: bool, avoid_same_team: bool,
                              seed: int):
        if _pool is None or _pool.empty:
            return []
        rng = np.random.default_rng(int(seed))
        scores = _pool["AI Hits Score"].fillna(0).to_numpy(dtype=float)
        weights = np.clip(scores, 1.0, None) ** 1.4
        if h_sleeper_bias > 0:
            xba_arr = _pool["xBA"].fillna(0.245).to_numpy(dtype=float)
            spot    = _pool["Spot"].fillna(5).to_numpy(dtype=float)
            sleeper_factor = (
                np.clip((xba_arr - 0.245) / 0.060, 0.0, 1.0) * 0.5 +
                np.clip((spot - 2.0) /  7.0,        0.0, 1.0) * 0.5
            )
            weights = weights * (1.0 + h_sleeper_bias * sleeper_factor)
        if not np.isfinite(weights).any() or weights.sum() <= 0:
            weights = np.ones_like(scores)

        rows = list(_pool.to_dict("records"))
        idxs = list(range(len(rows)))
        legs, used_games, used_teams = [], set(), set()
        available = idxs.copy()
        avail_w = weights.copy()
        while available and len(legs) < n:
            w = avail_w[available]
            if w.sum() <= 0:
                pick_local = int(rng.integers(0, len(available)))
            else:
                p = w / w.sum()
                pick_local = int(rng.choice(len(available), p=p))
            pick = available.pop(pick_local)
            row = rows[pick]
            g = str(row.get("Game", "") or "")
            t = str(row.get("Team", "") or "")
            if avoid_same_game and g and g in used_games:
                continue
            if avoid_same_team and t and t in used_teams:
                continue
            legs.append(row)
            if g: used_games.add(g)
            if t: used_teams.add(t)

        if len(legs) < n:
            taken_names = {l.get("Hitter") for l in legs}
            for row in rows:
                if len(legs) >= n: break
                if row.get("Hitter") in taken_names: continue
                legs.append(row)
        return legs[:n]

    def _h_pick_legs(_pool, n, avoid_same_game, avoid_same_team, seed=0):
        return _weighted_sample_hits(_pool, n, avoid_same_game,
                                     avoid_same_team, seed)

    # ---- Hits-specific reasons ----------------------------------------
    def _h_reasons_for(row) -> list:
        reasons = []
        def _f(v):
            try:
                if v is None: return None
                f = float(v)
                if pd.isna(f): return None
                return f
            except Exception:
                return None

        xba   = _f(row.get("xBA"))
        avg   = _f(row.get("AVG"))
        kpct  = _f(row.get("K%"))
        ld    = _f(row.get("LD%"))
        ss    = _f(row.get("SweetSpot%"))
        whiff = _f(row.get("Whiff%"))
        bs    = _f(row.get("BatSpeed"))
        xwoba = _f(row.get("xwOBA"))
        ops   = _f(row.get("OPS"))
        p_avg = _f(row.get("p_AVG_against"))
        p_xba = _f(row.get("p_xBA_against"))
        p_k   = _f(row.get("p_K%"))
        p_hh  = _f(row.get("p_HardHit%"))
        plat  = _f(row.get("platoon"))
        env   = _f(row.get("env"))
        spot  = row.get("Spot")
        bat   = (row.get("Bat") or "").upper()[:1]
        opp_h = (row.get("OppHand") or "").upper()[:1]

        # 1. Contact quality (priority — this prop is hits)
        # OPS leads when MVP-tier; it's the most universal "bat is producing"
        # signal so it deserves top billing even on a hits-prop card.
        if ops is not None and ops >= 0.900:
            reasons.append(f"🔥 OPS <b>{ops:.3f}</b> — elite bat")
        elif ops is not None and ops >= 0.800:
            reasons.append(f"🔥 OPS <b>{ops:.3f}</b>")
        if xba is not None and xba >= 0.290 and len(reasons) < 5:
            reasons.append(f"🎯 xBA <b>{xba:.3f}</b> — elite contact profile")
        elif xba is not None and xba >= 0.270 and len(reasons) < 5:
            reasons.append(f"🎯 xBA <b>{xba:.3f}</b>")
        if avg is not None and avg >= 0.300 and len(reasons) < 5:
            reasons.append(f"📈 Hitting <b>{avg:.3f}</b> on the year")
        if kpct is not None and kpct <= 16.0 and len(reasons) < 5:
            reasons.append(f"🧠 K% <b>{kpct:.1f}</b> — rarely strikes out")
        elif kpct is not None and kpct <= 20.0 and len(reasons) < 5:
            reasons.append(f"🧠 K% <b>{kpct:.1f}</b>")

        # 2. Lineup spot — opportunity is huge for hits
        try:
            sp = int(spot)
            if sp <= 2 and len(reasons) < 5:
                reasons.append(f"📋 <b>{sp}-hole</b> — extra PA upside")
            elif 3 <= sp <= 5 and len(reasons) < 5:
                reasons.append(f"📋 <b>{sp}-hole</b> — heart of the order")
        except Exception:
            pass

        # 3. Matchup vs SP
        opp_sp = row.get("Opp SP", "SP") or "SP"
        if p_avg is not None and p_avg >= 0.270 and len(reasons) < 5:
            reasons.append(f"🆚 {opp_sp} allows <b>{p_avg:.3f}</b> AVG")
        elif p_xba is not None and p_xba >= 0.265 and len(reasons) < 5:
            reasons.append(f"🆚 {opp_sp} allows <b>{p_xba:.3f}</b> xBA")
        if p_k is not None and p_k <= 19.0 and len(reasons) < 5:
            reasons.append(f"🪶 {opp_sp} K% <b>{p_k:.1f}</b> — contact-prone")
        if p_hh is not None and p_hh >= 40.0 and len(reasons) < 5:
            reasons.append(f"💥 SP HardHit% allowed <b>{p_hh:.1f}</b>")

        # 4. Platoon edge
        if plat is not None and plat >= 0.80 and bat and opp_h and len(reasons) < 5:
            reasons.append(f"⚔️ Platoon edge — {bat}HB vs {opp_h}HP")
        elif bat == "S" and len(reasons) < 5:
            reasons.append(f"🔁 Switch hitter — neutralizes platoon")

        # 5. Plate skill / line-drive context
        if whiff is not None and whiff <= 18.0 and len(reasons) < 5:
            reasons.append(f"👀 Whiff% <b>{whiff:.1f}</b> — barrels stuff up")
        if ld is not None and ld >= 24.0 and len(reasons) < 5:
            reasons.append(f"📐 LD% <b>{ld:.1f}</b> — squares up consistently")
        if ss is not None and ss >= 36.0 and len(reasons) < 5:
            reasons.append(f"🎯 SweetSpot% <b>{ss:.1f}</b>")
        if bs is not None and bs >= 73.0 and len(reasons) < 5:
            reasons.append(f"🏏 Bat Speed <b>{bs:.1f}</b> mph")

        # 6. Environment — only mention when notably positive
        if env is not None and env >= 60.0 and len(reasons) < 5:
            reasons.append(f"🏟️ Park + weather lean <b>hitter-friendly</b>")
        if xwoba is not None and xwoba >= 0.360 and len(reasons) < 5:
            reasons.append(f"🔥 xwOBA <b>{xwoba:.3f}</b> — productive contact")

        if not reasons:
            reasons.append("🔢 Composite AI Hits Score above slate threshold")
        return reasons[:5]

    def _h_tier(score):
        try: s = float(score)
        except Exception: return ("ok", "Average")
        if s >= 75: return ("elite",  "Elite")
        if s >= 65: return ("strong", "Strong")
        if s >= 55: return ("ok",     "Average")
        return ("soft", "Soft")

    def _h_fmt_or_dash(v, fmt):
        try:
            if v is None: return "—"
            f = float(v)
            if pd.isna(f): return "—"
            return fmt.format(f)
        except Exception:
            return "—"

    def _h_compact_stats_line(leg) -> str:
        parts = []
        # OPS first — universal bat-quality signal carried for every leg via
        # the signals dict. Read via get_ops_value so OBP+SLG-only rows still
        # surface a value rather than collapse to an em-dash.
        op = fmt_ops(get_ops_value(leg))
        if op != "—":
            parts.append(f"OPS <b>{op}</b>")
        x = leg.get("xBA")
        if _h_fmt_or_dash(x, "{:.3f}") != "—":
            parts.append(f"xBA <b>{_h_fmt_or_dash(x, '{:.3f}')}</b>")
        a = leg.get("AVG")
        if _h_fmt_or_dash(a, "{:.3f}") != "—":
            parts.append(f"AVG <b>{_h_fmt_or_dash(a, '{:.3f}')}</b>")
        k = leg.get("K%")
        if _h_fmt_or_dash(k, "{:.1f}%") != "—":
            parts.append(f"K <b>{_h_fmt_or_dash(k, '{:.1f}%')}</b>")
        ld = leg.get("LD%")
        if _h_fmt_or_dash(ld, "{:.1f}%") != "—":
            parts.append(f"LD <b>{_h_fmt_or_dash(ld, '{:.1f}%')}</b>")
        try:
            sp = int(leg.get("Spot"))
            if sp <= 9:
                parts.append(f"<b>{sp}-hole</b>")
        except Exception:
            pass
        return " · ".join(parts) if parts else "—"

    def _h_bpw_for_leg(leg) -> dict:
        """Resolve a Good/OK/Bad ballpark+weather indicator for one leg.
        Mirrors AI HR Parlay's _bpw_for_leg with the same fallback chain."""
        def _is_blank(v):
            try:
                if v is None: return True
                if isinstance(v, float) and pd.isna(v): return True
                if pd.isna(v): return True
            except Exception:
                pass
            s = str(v).strip()
            return s == "" or s.lower() in ("nan", "none")

        label  = leg.get("BPW Label")
        tier   = leg.get("BPW Tier")
        icon   = leg.get("BPW Icon")
        tip    = leg.get("BPW Tip")
        hr_pct = leg.get("BPW HR%")
        if any(_is_blank(x) for x in (label, tier, icon)):
            fallback = _h_bpw_map.get(str(leg.get("Game", "")))
            if fallback:
                label  = label  if not _is_blank(label)  else fallback.get("label")
                tier   = tier   if not _is_blank(tier)   else fallback.get("tier")
                icon   = icon   if not _is_blank(icon)   else fallback.get("icon")
                tip    = tip    if not _is_blank(tip)    else fallback.get("tooltip")
                if _is_blank(hr_pct):
                    hr_pct = fallback.get("hr_pct")
        if _is_blank(label): label = "OK"
        if _is_blank(tier):  tier  = "ok"
        if _is_blank(icon):  icon  = "🟡"
        if _is_blank(tip):   tip   = "Park/weather signal"
        try:
            hr_int = int(float(hr_pct))
            hr_str = f"{hr_int:+d}%"
        except Exception:
            hr_str = ""
        return {
            "label": str(label), "tier": str(tier), "icon": str(icon),
            "tooltip": str(tip).replace('"', "'"), "hr_str": hr_str,
        }

    def _h_render_card(title: str, legs: list, badge: str) -> str:
        if not legs:
            return (
                f'<div class="aip-card aip-empty">'
                f'<div class="aip-card-title">{title}</div>'
                f'<div class="aip-card-sub">Not enough qualifying legs. '
                f'Loosen the filters above.</div>'
                f'</div>'
            )
        avg_score = sum(float(l.get("AI Hits Score", 0) or 0) for l in legs) / max(1, len(legs))
        tier_cls, _ = _h_tier(avg_score)
        leg_html = []
        for i, leg in enumerate(legs, 1):
            t_cls, t_lbl = _h_tier(leg.get("AI Hits Score"))
            reasons = _h_reasons_for(leg)
            reason_html = "".join(
                f'<li style="margin:2px 0;">{r}</li>' for r in reasons
            )
            try:
                ai_str = f"{float(leg.get('AI Hits Score', 0)):.1f}"
            except Exception:
                ai_str = "—"
            stats_line = _h_compact_stats_line(leg)
            bpw = _h_bpw_for_leg(leg)
            bpw_line = (
                f'<div class="aip-bpw-line aip-bpw-{bpw["tier"]}" '
                f'title="{bpw["tooltip"]}">'
                f'{bpw["icon"]} <b>{bpw["label"]} Ballpark Weather</b>'
                + (f' <span class="aip-bpw-pct">({bpw["hr_str"]} HR)</span>'
                   if bpw["hr_str"] else '')
                + f'</div>'
            )
            leg_html.append(
                f'<div class="aip-leg">'
                f'  <div class="aip-leg-head">'
                f'    <div class="aip-leg-num">Leg {i}</div>'
                f'    <div class="aip-leg-name">{leg.get("Hitter","")}'
                f'      <span class="aip-meta">· {leg.get("Team","")} · '
                f'Bat {format_batter_stance(leg.get("Bat",""))} · Spot {leg.get("Spot","")}</span>'
                f'    </div>'
                f'    <div class="aip-leg-score">'
                f'      <span class="aip-score">{ai_str}</span>'
                f'      <span class="hrs-pill {t_cls}">{t_lbl}</span>'
                f'    </div>'
                f'  </div>'
                f'  <div class="aip-ctx">{leg.get("Game","")} '
                f'<span style="color:#94a3b8;">·</span> vs '
                f'<b>{leg.get("Opp SP","")}</b></div>'
                f'  {bpw_line}'
                f'  <div class="aip-stats">{stats_line}</div>'
                f'  <ul class="aip-reasons">{reason_html}</ul>'
                f'</div>'
            )

        return (
            f'<div class="aip-card">'
            f'  <div class="aip-card-head">'
            f'    <div>'
            f'      <div class="aip-card-title">{title}</div>'
            f'      <div class="aip-card-sub">{len(legs)} legs · '
            f'avg AI Hits Score <b>{avg_score:.1f}</b></div>'
            f'    </div>'
            f'    <span class="hrs-pill {tier_cls} aip-badge">{badge}</span>'
            f'  </div>'
            + "".join(leg_html) +
            f'</div>'
        )

    # Reuse the AI HR Parlay card CSS — same .aip-* class names.
    h_css = (
        "<style>"
        ".aip-card { background:linear-gradient(180deg,#111827 0%,#0b1220 100%); "
        "  border-radius:14px; padding:14px 14px 8px 14px; "
        "  box-shadow:0 2px 16px rgba(0,0,0,.45); margin: 8px 0 16px 0; "
        "  border:1px solid rgba(124,58,237,.25); border-left:5px solid #7c3aed; }"
        ".aip-card.aip-empty { border-left-color:#334155; padding:14px; }"
        ".aip-card-head { display:flex; align-items:center; justify-content:space-between; "
        "  gap:10px; margin-bottom:6px; flex-wrap:wrap; }"
        ".aip-card-title { font-weight:900; font-size:1.08rem; color:#e2e8f0; "
        "  letter-spacing:.01em; }"
        ".aip-card-sub { color:#94a3b8; font-size:.82rem; margin-top:2px; }"
        ".aip-badge { font-size:.74rem; }"
        ".aip-leg { padding:10px 0; border-top:1px dashed rgba(255,255,255,.08); }"
        ".aip-leg:first-of-type { border-top:none; }"
        ".aip-leg-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }"
        ".aip-leg-num { font-size:.72rem; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; color:#fcd34d; background:#3b1f6b; "
        "  padding:3px 8px; border-radius:6px; }"
        ".aip-leg-name { font-weight:900; color:#ffffff; flex:1 1 200px; "
        "  font-size:1rem; text-shadow:0 1px 3px rgba(0,0,0,.5); }"
        ".aip-meta { color:#7dd3fc; font-weight:600; font-size:.82rem; }"
        ".aip-leg-score { display:flex; align-items:center; gap:6px; }"
        ".aip-score { font-weight:900; font-size:1.05rem; color:#00c896; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-ctx { color:#94a3b8; font-size:.84rem; margin: 4px 0 4px 0; }"
        ".aip-bpw-line { display:block; margin: 4px 0 6px 0; padding:6px 10px; "
        "  border-radius:8px; font-size:.86rem; font-weight:700; line-height:1.35; "
        "  border:1px solid transparent; }"
        ".aip-bpw-line b { font-weight:800; }"
        ".aip-bpw-pct { font-weight:700; margin-left:4px; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-bpw-good { background:rgba(34,197,94,.15); color:#4ade80; border-color:rgba(34,197,94,.3); }"
        ".aip-bpw-ok   { background:rgba(250,204,21,.12); color:#fde68a; border-color:rgba(250,204,21,.3); }"
        ".aip-bpw-bad  { background:rgba(239,68,68,.12); color:#fca5a5; border-color:rgba(239,68,68,.3); }"
        ".aip-stats { color:#e2e8f0; font-size:.84rem; margin: 0 0 4px 0; "
        "  background:rgba(255,255,255,.05); border-radius:6px; padding:5px 8px; "
        "  font-variant-numeric: tabular-nums; border:1px solid rgba(255,255,255,.07); }"
        ".aip-reasons { margin: 4px 0 2px 18px; padding:0; color:#cbd5e1; "
        "  font-size:.88rem; }"
        ".aip-reasons li { margin: 1px 0; line-height:1.35; }"
        ".aip-disclaimer { color:#64748b; font-size:.78rem; margin: 6px 2px 12px 2px; "
        "  font-style:italic; }"
        "@media (max-width:520px) { .aip-leg-name { font-size:.94rem; } "
        "  .aip-card-title { font-size:1rem; } .aip-reasons { font-size:.84rem; } "
        "  .aip-stats { font-size:.80rem; } }"
        "</style>"
    )
    st.markdown(h_css, unsafe_allow_html=True)

    h_legs_2 = _h_pick_legs(h_pool, 2, _h_avoid_game, _h_avoid_team, seed=_h_seed)
    h_legs_3 = _h_pick_legs(h_pool, 3, _h_avoid_game, _h_avoid_team, seed=_h_seed * 1000 + 7)
    h_legs_4 = _h_pick_legs(h_pool, 4, _h_avoid_game, _h_avoid_team, seed=_h_seed * 1000 + 19)

    h_badge_2 = f"{_h_risk} · 2-leg · #{_h_seed}"
    h_badge_3 = f"{_h_risk} · 3-leg · #{_h_seed}"
    h_badge_4 = f"{_h_risk} · 4-leg · #{_h_seed}"
    st.markdown(_h_render_card("🥎 Recommended 2-Leg 1+ Hits Parlay", h_legs_2, h_badge_2),
                unsafe_allow_html=True)
    st.markdown(_h_render_card("🚀 Recommended 3-Leg 1+ Hits Parlay", h_legs_3, h_badge_3),
                unsafe_allow_html=True)
    st.markdown(_h_render_card("🎯 Recommended 4-Leg 1+ Hits Parlay", h_legs_4, h_badge_4),
                unsafe_allow_html=True)

    if _h_show_alts:
        def _h_sig(legs_):
            return tuple(sorted(str(l.get("Hitter", "")) for l in (legs_ or [])))
        seen_2h = {_h_sig(h_legs_2)}
        seen_3h = {_h_sig(h_legs_3)}
        extra_seeds = [331, 433, 547, 659, 773, 887]
        alt2 = [
            _h_pick_legs(h_pool, 2, _h_avoid_game, _h_avoid_team, seed=_h_seed + s)
            for s in extra_seeds
        ]
        alt3 = [
            _h_pick_legs(h_pool, 3, _h_avoid_game, _h_avoid_team, seed=_h_seed * 1000 + s)
            for s in extra_seeds
        ]
        chosen_2h, chosen_3h = [], []
        for cand in alt2:
            sig = _h_sig(cand)
            if sig in seen_2h or not cand: continue
            seen_2h.add(sig); chosen_2h.append(cand)
            if len(chosen_2h) >= 2: break
        for cand in alt3:
            sig = _h_sig(cand)
            if sig in seen_3h or not cand: continue
            seen_3h.add(sig); chosen_3h.append(cand)
            if len(chosen_3h) >= 2: break
        if chosen_2h or chosen_3h:
            st.markdown(
                '<div class="section-title" style="font-size:1.08rem;margin-top:6px;">'
                '🔁 Alternate Hits Tickets</div>',
                unsafe_allow_html=True,
            )
        for i, cand in enumerate(chosen_2h, 1):
            st.markdown(
                _h_render_card(
                    f"🥎 Alt 2-Leg Hits Parlay #{i}", cand,
                    f"{_h_risk} · 2-leg · alt {i}"
                ),
                unsafe_allow_html=True,
            )
        for i, cand in enumerate(chosen_3h, 1):
            st.markdown(
                _h_render_card(
                    f"🚀 Alt 3-Leg Hits Parlay #{i}", cand,
                    f"{_h_risk} · 3-leg · alt {i}"
                ),
                unsafe_allow_html=True,
            )

    st.markdown(
        render_source_chips([
            "savant:batters", "savant:pitchers",
            "statsapi:schedule", "statsapi:boxscore",
            "rotogrinders:weather", "openmeteo:weather",
        ]),
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="aip-disclaimer">'
        '⚠️ <b>Disclaimer:</b> Recommendations are model-driven analytics built '
        'from public Statcast / StatsAPI / weather data — <b>not guaranteed outcomes</b>. '
        'Verify lineups with your sportsbook before placing any bet. Bet responsibly.'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.expander("How the AI Hits Score works"):
        st.markdown(
            "**AI Hits Score (0-100)** — built from the ground up for **1+ hits**, "
            "not HRs. Components:\n"
            "- **35% Contact quality** — xBA (12), AVG (9), K%-inverse (7), "
            "LD% (4), SweetSpot% (3)\n"
            "- **15% Plate skill** — Whiff%-inverse (8), Bat speed (4), BB% (3)\n"
            "- **20% Opportunity** — lineup-spot PA weight (1-hole highest, "
            "9-hole lowest)\n"
            "- **20% Matchup** — opposing SP AVG / xBA allowed (8), "
            "SP K%-inverse (6), SP HardHit% allowed (4), platoon edge (2)\n"
            "- **10% Environment** — park HR/runs factor + weather impact "
            "(`compute_weather_impact`)\n\n"
            "**Pool:** every eligible lineup hitter on upcoming/live games. "
            "**Sleeper Hunt** boosts contact bats with strong xBA but lower "
            "lineup spots so under-owned plays can win a slot.\n\n"
            "**Selection:** weighted sampling by AI Hits Score with avoid-same-"
            "game/team constraints. **Generate New Hits Parlays** rerolls within "
            "the same eligible pool. Missing fields fall back to neutral values "
            "rather than dropping the bat — so coverage stays high even when "
            "Whiff% / Bat-speed / xBA aren't filled in for a player."
        )
    st.stop()


# ============== Live HR Tracker view ==============
# Real-time home run feed wired to the official MLB StatsAPI
# (/api/v1/schedule + /api/v1.1/game/{pk}/feed/live). Every new HR fires a
# flaming banner, confetti burst, and a team-colored player card with
# season HR / OPS / ISO. A dev-only Demo toggle inside the page lets you
# inject synthetic events for visual QA.
if _view == "🏟️ Live HR Tracker":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🏟️ Live HR Tracker</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Real-time home run feed for today\'s MLB slate, pulled directly '
        'from the official MLB StatsAPI live feed. Every homer fires a '
        'flaming banner, confetti burst, and a team-colored player card '
        'with season HR, OPS and ISO.'
        '</div>',
        unsafe_allow_html=True,
    )
    try:
        from live_hr_tracker import render_live_hr_tracker as _render_lhrt
        _render_lhrt()
    except Exception as _lhrt_err:
        st.error(f"Live HR Tracker failed to load: {_lhrt_err}")
    st.stop()


# ===========================================================================
# 🎯 Pitcher Weak Spots — slot-based attack-zone intelligence
# ---------------------------------------------------------------------------
# Identifies where each starter is most vulnerable inside the opposing
# batting order. Scores live on the slot (1..9) so they remain valid as
# projected lineups roll over to confirmed lineups; confirmed hitters
# inherit the slot score and get a stronger highlight when their slot
# falls inside an attack zone. All data sourced from the app's existing
# schedule + pitcher CSV + lineup service caches — no extra feeds.
# ===========================================================================
if _view == "🎯 Pitcher Weak Spots":
    from services import pitcher_weak_spots as pws
    try:
        from services.lineup_service import (
            get_daily_lineups as _lineup_daily,
            lineup_to_dict_rows as _lineup_to_rows,
            LINEUP_STATUS_CONFIRMED as _LS_CONFIRMED,
            LINEUP_STATUS_EXPECTED as _LS_EXPECTED,
            LINEUP_STATUS_LIVE as _LS_LIVE,
            LINEUP_STATUS_FINAL as _LS_FINAL,
            LINEUP_STATUS_POSTPONED as _LS_POSTPONED,
            LINEUP_STATUS_NOT_POSTED as _LS_NOT_POSTED,
        )
    except Exception:
        _lineup_daily = None
        _lineup_to_rows = None
        _LS_CONFIRMED = "confirmed"
        _LS_EXPECTED = "expected"
        _LS_LIVE = "live"
        _LS_FINAL = "final"
        _LS_POSTPONED = "postponed"
        _LS_NOT_POSTED = "not_posted"
    # Statuses where we treat the lineup as locked in (not projected) and
    # apply the confirmed-style highlight. Includes live/final because once a
    # game starts, the batting order is real even if we end up rendering it
    # without a boxscore.
    _LS_LOCKED = {_LS_CONFIRMED, _LS_LIVE, _LS_FINAL}

    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🎯 Pitcher Weak Spots'
        '</div>'
        '<div style="color:#cbd5e1; font-size:.92rem; margin:-4px 0 12px 0; line-height:1.45;">'
        'Daily matchup intelligence: each starter\'s most attackable opposing batting-order slots, '
        'with projected lineups locked in by position until MLB posts the confirmed order.'
        '</div>',
        unsafe_allow_html=True,
    )

    if schedule_df is None or schedule_df.empty:
        st.info("No games scheduled for the selected date.")
        st.stop()

    # ---- Build one card per starting pitcher (away then home for each game) ----
    @st.cache_data(ttl=60, show_spinner=False)
    def _pws_lineups_for_date(date_iso: str):
        """Pull daily lineups via the shared lineup service. Short cache so we
        pick up confirmed lineups and live/final state quickly — the
        underlying ``LineupService`` already does its own status-aware TTL.
        Returns a {game_pk: GameLineups} dict (best-effort; empty on error)."""
        if _lineup_daily is None:
            return {}
        try:
            data = _lineup_daily(date_iso) or []
        except Exception:
            return {}
        return {int(getattr(g, "game_pk", 0) or 0): g for g in data}

    _slate_iso = selected_date.isoformat() if hasattr(selected_date, "isoformat") else str(selected_date)
    _lineups_by_pk = _pws_lineups_for_date(_slate_iso)

    def _lineup_for_side(game_pk, side_team_abbr, opp_team_id, opp_team_abbr):
        """Return (LineupBatter list, status). Confirmed > projected > anonymous.

        For live/final games we honor the game-state status even when the
        boxscore arrived without a batting order — the UI then renders a
        truthful "Live - lineup unavailable" / "Final - lineup unavailable"
        instead of misleading "Lineup pending".
        """
        gl = _lineups_by_pk.get(int(game_pk) if game_pk else -1)
        game_state_status = None
        if gl is not None:
            # Capture the *game's* abstract status — used so a live/final
            # game without a posted batting order still labels truthfully.
            abstract = (gl.abstract_status or "").lower()
            if gl.is_postponed:
                game_state_status = _LS_POSTPONED
            elif abstract == "live":
                game_state_status = _LS_LIVE
            elif abstract == "final":
                game_state_status = _LS_FINAL
        if gl is not None and _lineup_to_rows is not None:
            opp_side = None
            if str(gl.away.team_abbr or "").upper() == str(opp_team_abbr or "").upper():
                opp_side = gl.away
            elif str(gl.home.team_abbr or "").upper() == str(opp_team_abbr or "").upper():
                opp_side = gl.home
            if opp_side is not None and opp_side.starters:
                rows = _lineup_to_rows(opp_side)
                status = opp_side.status or game_state_status or _LS_EXPECTED
                is_proj = status not in _LS_LOCKED
                return pws.lineup_from_rows(rows, is_projected=is_proj), status
        # Fall back to the app's projected lineup (recent-starts greedy assign).
        if opp_team_id:
            try:
                proj_df = get_projected_lineup(int(opp_team_id), opp_team_abbr, _slate_iso)
                if proj_df is not None and not proj_df.empty:
                    rows = proj_df.to_dict(orient="records")
                    # If the game is already live/final, prefer the truthful
                    # game-state status over a stale "Projected" badge.
                    status = game_state_status or _LS_EXPECTED
                    is_proj = status not in _LS_LOCKED
                    return pws.lineup_from_rows(rows, is_projected=is_proj), status
            except Exception:
                pass
        # Last resort — anonymous 1..9 slots, but honor live/final state when
        # we have it so the user sees "Live - lineup unavailable" instead of
        # "Lineup pending".
        return (
            [pws.make_projected_batter(s) for s in range(1, 10)],
            game_state_status or _LS_NOT_POSTED,
        )

    @st.cache_data(ttl=90, show_spinner=False)
    def _pws_build_cards(date_iso: str):
        """Build every card for the slate. Cached short enough that live
        lineup status flips through within a couple refreshes, while filter/
        sort widget interactions still hit the cache."""
        cards: list[pws.WeakSpotCard] = []
        for _, gr in schedule_df.iterrows():
            game_pk = gr.get("game_pk")
            time_label = gr.get("time_short") or gr.get("game_time_ct", "")
            away_abbr = gr.get("away_abbr", "")
            home_abbr = gr.get("home_abbr", "")
            away_team_id = gr.get("away_id")
            home_team_id = gr.get("home_id")
            for side in ("away", "home"):
                pitcher_name = gr.get(f"{side}_probable", "TBD") or "TBD"
                if not pitcher_name or str(pitcher_name).upper() == "TBD":
                    continue
                pitcher_id = gr.get(f"{side}_probable_id")
                pitcher_team_abbr = away_abbr if side == "away" else home_abbr
                opponent_abbr = home_abbr if side == "away" else away_abbr
                opp_team_id = home_team_id if side == "away" else away_team_id
                # Find pitcher row from existing pitcher CSV (graceful None).
                p_row = find_pitcher_row(pitchers_df, pitcher_name, pitcher_id) \
                    if pitchers_df is not None and not pitchers_df.empty else None
                # Pitch hand from roster (statsapi) — best-effort, default R.
                pitcher_hand = "R"
                try:
                    box = get_boxscore(game_pk) if game_pk else {}
                    side_box = box.get("teams", {}).get(side, {})
                    roster = roster_df_from_box(side_box, pitcher_team_abbr)
                    pitcher_hand = lookup_pitch_hand(roster, pitcher_name) or "R"
                except Exception:
                    pitcher_hand = "R"
                prof = pws.build_pitcher_profile(
                    p_row,
                    pitcher_name=pitcher_name,
                    pitcher_id=int(pitcher_id) if pitcher_id else None,
                    pitcher_hand=pitcher_hand,
                    is_home=(side == "home"),
                )
                lineup, lstatus = _lineup_for_side(
                    game_pk, pitcher_team_abbr, opp_team_id, opponent_abbr,
                )
                # Enrich each batter with batter CSV stats so card-level
                # scoring isn't purely slot-based when we have identities.
                if batters_df is not None and not batters_df.empty:
                    for b in lineup:
                        if b.name and not b.name.startswith("Projected #"):
                            try:
                                b_row = find_player_row(
                                    batters_df, clean_name(b.name),
                                    pitcher_team_abbr,  # team not strictly required
                                    player_id=b.player_id,
                                )
                                if b_row is not None:
                                    pws.enrich_batter_from_row(b, b_row)
                            except Exception:
                                continue
                card = pws.assemble_card(
                    game_pk=int(game_pk) if game_pk else 0,
                    game_time_label=str(time_label or ""),
                    away_abbr=away_abbr,
                    home_abbr=home_abbr,
                    pitcher_name=pitcher_name,
                    pitcher_hand=pitcher_hand,
                    pitcher_team_abbr=pitcher_team_abbr,
                    opponent_abbr=opponent_abbr,
                    pitcher_profile=prof,
                    lineup=lineup,
                    lineup_status=lstatus,
                )
                cards.append(card)
        return cards

    with st.spinner("Scoring tonight's pitcher weak spots…"):
        _all_cards = _pws_build_cards(_slate_iso)

    if not _all_cards:
        st.info("No probable starters posted yet for this slate. Check back closer to first pitch.")
        st.stop()

    # ---- Filters + sort -----------------------------------------------------
    # Single-select segmented filter keeps mobile usable (no crowded
    # multiselect chips) and the sort selectbox is paired alongside. Filter
    # names are grouped into a tighter, clearer vocabulary so users can scan
    # the bar at a glance against the app's dark background.
    _filter_options = [
        "All",
        "Confirmed",
        "Lefty weakness",
        "Righty weakness",
        "Top-order targets",
        "Value bats",
        "High confidence",
    ]
    _sort_options = [
        "Highest overall pitcher weakness",
        "Most targetable top-4 hitters",
        "Best value stack",
        "Highest platoon edge",
        "Confirmed lineups first",
        "Earliest game time",
    ]
    st.markdown('<div class="pws-controls">', unsafe_allow_html=True)
    _filter_col, _sort_col = st.columns([1.0, 1.0])
    with _filter_col:
        _flt_choice = st.selectbox(
            "Show",
            _filter_options,
            index=0,
            key="pws_filter_single",
        )
    with _sort_col:
        _sort_choice = st.selectbox(
            "Sort by",
            _sort_options,
            index=0,
            key="pws_sort",
        )
    st.markdown('</div>', unsafe_allow_html=True)

    def _passes_filter(card: pws.WeakSpotCard, flt: str) -> bool:
        if flt == "All":
            return True
        if flt == "Confirmed":
            return card.is_lineup_confirmed
        if flt == "Lefty weakness":
            return card.has_lefty_weakness
        if flt == "Righty weakness":
            return card.has_righty_weakness
        if flt == "Top-order targets":
            return card.has_top_order_target
        if flt == "Value bats":
            value = sum(
                1 for s in card.slot_scores
                if s["slot"] >= 4 and s["zone"] in (pws.ZONE_PRIMARY, pws.ZONE_SECONDARY)
            )
            return value >= 2
        if flt == "High confidence":
            return card.confidence >= 60.0
        return True

    _filtered = [c for c in _all_cards if _passes_filter(c, _flt_choice)]

    def _sort_key(card: pws.WeakSpotCard):
        if _sort_choice == "Highest overall pitcher weakness":
            return (-card.overall_score,)
        if _sort_choice == "Most targetable top-4 hitters":
            return (-card.top4_targetable, -card.overall_score)
        if _sort_choice == "Best value stack":
            adj_value = sum(
                1 for s in card.slot_scores
                if s["slot"] >= 4 and s["zone"] in (pws.ZONE_PRIMARY, pws.ZONE_SECONDARY)
            )
            return (-adj_value, -card.overall_score)
        if _sort_choice == "Highest platoon edge":
            edges = sum(1 for s in card.slot_scores if s.get("platoon_edge"))
            return (-edges, -card.overall_score)
        if _sort_choice == "Confirmed lineups first":
            return (0 if card.is_lineup_confirmed else 1, -card.overall_score)
        if _sort_choice == "Earliest game time":
            return (card.game_time_label or "ZZ",)
        return (-card.overall_score,)

    _filtered.sort(key=_sort_key)

    # ---- Tab-scoped CSS -----------------------------------------------------
    # Color system (chosen against the app's dark #0f172a background):
    #   primary   = red/orange (#ef4444) — strong attack
    #   secondary = amber/yellow (#f59e0b) — worth a look
    #   neutral   = slate gray (#64748b) — fade
    # Confirmed lineup uses a green halo (#22c55e) layered on top of the
    # zone color so the "target this slot" cue is independent of attack
    # zone (red can still be a confirmed target without color noise).
    # Mobile (max-width 640px) collapses the 9-slot grid into a vertical
    # list so each slot's batter name fits without truncating.
    st.markdown(
        "<style>"
        ":root { --pws-primary:#ef4444; --pws-secondary:#f59e0b; "
        "  --pws-neutral:#64748b; --pws-confirmed:#22c55e; }"
        ".pws-controls { background:#1a0b3a; border:1px solid #4c1d95; "
        "  border-radius:12px; padding:10px 12px; margin:2px 0 12px; }"
        ".pws-controls [data-testid=\"stWidgetLabel\"] p, "
        ".pws-controls label, "
        ".pws-controls label * { color:#facc15 !important; "
        "  font-weight:800 !important; font-size:.85rem !important; "
        "  letter-spacing:.04em; text-transform:uppercase; }"
        ".pws-controls [data-baseweb=\"select\"] > div { "
        "  background:#1a2744 !important; border-radius:10px !important; "
        "  border:1.5px solid #facc15 !important; color:#e2e8f0 !important; "
        "  font-weight:700 !important; }"
        ".pws-controls [data-baseweb=\"select\"] svg { color:#facc15 !important; }"
        ".pws-summary { color:#fde68a; font-weight:700; font-size:.92rem; "
        "  margin:0 4px 10px; }"
        ".pws-card { background:linear-gradient(180deg,#111827 0%,#0b1220 100%); "
        "  border-radius:16px; padding:14px 16px; "
        "  margin-bottom:14px; border:1.5px solid rgba(255,255,255,.08); "
        "  box-shadow:0 2px 12px rgba(0,0,0,.4); }"
        ".pws-card.confirmed { border:2px solid var(--pws-confirmed); "
        "  box-shadow:0 0 0 2px rgba(34,197,94,.18), 0 4px 16px rgba(21,128,61,.22); }"
        ".pws-header { display:flex; align-items:flex-start; gap:12px; "
        "  flex-wrap:wrap; justify-content:space-between; margin-bottom:8px; }"
        ".pws-header .left { min-width:0; flex:1 1 auto; }"
        ".pws-header .right { display:flex; flex-direction:column; "
        "  align-items:flex-end; gap:6px; flex:0 0 auto; }"
        ".pws-matchup { font-weight:900; font-size:1.05rem; color:#ffffff; "
        "  display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; "
        "  text-shadow:0 1px 3px rgba(0,0,0,.5); }"
        ".pws-matchup .time { color:#94a3b8; font-weight:700; font-size:.82rem; }"
        ".pws-pitcher { font-size:.95rem; color:#e2e8f0; font-weight:700; "
        "  margin-top:2px; }"
        ".pws-pitcher .hand { display:inline-block; padding:1px 7px; "
        "  border-radius:6px; background:#1e1147; color:#facc15; "
        "  font-weight:800; font-size:.72rem; margin-left:6px; }"
        ".pws-pitcher .vs { color:#475569; font-weight:600; margin-left:4px; }"
        ".pws-badge { display:inline-flex; align-items:center; gap:5px; "
        "  padding:4px 10px; border-radius:999px; font-weight:800; "
        "  font-size:.74rem; letter-spacing:.04em; text-transform:uppercase; "
        "  white-space:nowrap; }"
        ".pws-badge .dot { width:7px; height:7px; border-radius:50%; "
        "  display:inline-block; }"
        ".pws-badge.confirmed { background:#dcfce7; color:#14532d; "
        "  border:1.5px solid #15803d; }"
        ".pws-badge.confirmed .dot { background:#16a34a; }"
        ".pws-badge.live { background:#fee2e2; color:#7f1d1d; "
        "  border:1.5px solid #dc2626; }"
        ".pws-badge.live .dot { background:#dc2626; animation:pwsPulse 1.4s infinite; }"
        ".pws-badge.final { background:#1e1147; color:#facc15; "
        "  border:1.5px solid #facc15; }"
        ".pws-badge.final .dot { background:#facc15; }"
        ".pws-badge.expected { background:#fef3c7; color:#78350f; "
        "  border:1.5px solid #d97706; }"
        ".pws-badge.expected .dot { background:#d97706; }"
        ".pws-badge.pending { background:#e2e8f0; color:#334155; "
        "  border:1.5px solid #94a3b8; }"
        ".pws-badge.pending .dot { background:#64748b; }"
        ".pws-badge.postponed { background:#f5f3ff; color:#5b21b6; "
        "  border:1.5px solid #7c3aed; }"
        ".pws-badge.postponed .dot { background:#7c3aed; }"
        "@keyframes pwsPulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }"
        ".pws-overall { background:#1a0b3a; color:#facc15; padding:6px 12px; "
        "  border-radius:12px; font-weight:900; font-size:.95rem; "
        "  display:inline-flex; align-items:center; gap:6px; "
        "  border:1.5px solid #facc15; }"
        ".pws-meta { color:#94a3b8; font-size:.84rem; margin-top:6px; "
        "  display:flex; align-items:center; gap:8px; flex-wrap:wrap; }"
        ".pws-meta b { color:#e2e8f0; }"
        ".pws-tagrow { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 4px; }"
        ".pws-tag { padding:3px 9px; border-radius:8px; font-size:.72rem; "
        "  font-weight:800; letter-spacing:.02em; }"
        ".pws-tag.top    { background:rgba(239,68,68,.15); color:#fca5a5; border:1px solid rgba(239,68,68,.3); }"
        ".pws-tag.mid    { background:rgba(251,146,60,.12); color:#fb923c; border:1px solid rgba(251,146,60,.3); }"
        ".pws-tag.bot    { background:rgba(124,58,237,.15); color:#c4b5fd; border:1px solid rgba(124,58,237,.3); }"
        ".pws-tag.lefty  { background:rgba(59,130,246,.12); color:#93c5fd; border:1px solid rgba(59,130,246,.3); }"
        ".pws-tag.righty { background:rgba(236,72,153,.12); color:#f9a8d4; border:1px solid rgba(236,72,153,.3); }"
        ".pws-tag.ttop   { background:rgba(250,204,21,.12); color:#fde68a; border:1px solid rgba(250,204,21,.3); }"
        ".pws-tag.power  { background:rgba(239,68,68,.15); color:#fca5a5; border:1px solid rgba(239,68,68,.3); }"
        ".pws-tag.lowk   { background:rgba(6,182,212,.12); color:#67e8f9; border:1px solid rgba(6,182,212,.3); }"
        ".pws-tag.platoon{ background:rgba(34,197,94,.12); color:#4ade80; border:1px solid rgba(34,197,94,.3); }"
        ".pws-target-list { background:rgba(245,158,11,.08); border-left:4px solid var(--pws-secondary); "
        "  padding:9px 12px; border-radius:8px; margin:8px 0 6px; "
        "  font-size:.88rem; color:#e2e8f0; line-height:1.45; "
        "  border:1px solid rgba(245,158,11,.2); border-left:4px solid var(--pws-secondary); }"
        ".pws-target-list b { color:#fde68a; }"
        ".pws-reason { background:rgba(255,255,255,.05); border-radius:8px; padding:9px 12px; "
        "  margin-top:8px; font-size:.85rem; color:#cbd5e1; line-height:1.45; "
        "  border:1px solid rgba(255,255,255,.08); }"
        ".pws-reason b { color:#e2e8f0; }"
        ".pws-confidence-bar { height:6px; border-radius:3px; background:rgba(255,255,255,.1); "
        "  margin-top:6px; overflow:hidden; }"
        ".pws-confidence-bar > span { display:block; height:100%; background:var(--pws-confirmed); }"
        # Lineup grid: 3 cols on desktop / 2 on tablet / 1 (vertical list) on phones.
        ".pws-slot-grid { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); "
        "  gap:8px; margin-top:10px; }"
        ".pws-slot { border:1.5px solid; border-radius:10px; padding:8px 10px; "
        "  display:flex; flex-direction:column; gap:2px; position:relative; "
        "  background:rgba(255,255,255,.04); }"
        ".pws-slot.projected { opacity:.92; border-style:dashed; }"
        ".pws-slot.zone-primary   { background:rgba(239,68,68,.1); border-color:var(--pws-primary); }"
        ".pws-slot.zone-secondary { background:rgba(245,158,11,.08); border-color:var(--pws-secondary); }"
        ".pws-slot.zone-neutral   { background:rgba(255,255,255,.03); border-color:var(--pws-neutral); }"
        ".pws-slot.confirmed-target { box-shadow:inset 0 0 0 2px var(--pws-confirmed); }"
        ".pws-slot .row1 { display:flex; align-items:center; justify-content:space-between; "
        "  gap:6px; }"
        ".pws-slot .spot { font-weight:900; font-size:.86rem; color:#e2e8f0; "
        "  display:inline-flex; align-items:center; gap:5px; }"
        ".pws-slot .spot .side { color:#94a3b8; font-weight:700; font-size:.72rem; "
        "  background:rgba(255,255,255,.08); border-radius:6px; padding:1px 6px; }"
        ".pws-slot .score-pill { font-weight:900; font-size:.84rem; padding:2px 8px; "
        "  border-radius:999px; }"
        ".pws-slot.zone-primary   .score-pill { background:var(--pws-primary);   color:#fff; }"
        ".pws-slot.zone-secondary .score-pill { background:var(--pws-secondary); color:#1e293b; }"
        ".pws-slot.zone-neutral   .score-pill { background:rgba(255,255,255,.1); color:#cbd5e1; }"
        ".pws-slot .name { font-weight:800; font-size:.95rem; color:#ffffff; "
        "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; "
        "  text-shadow:0 1px 2px rgba(0,0,0,.4); }"
        ".pws-slot .meta { font-size:.74rem; color:#7dd3fc; display:flex; "
        "  align-items:center; gap:4px; }"
        ".pws-slot .meta .check { color:var(--pws-confirmed); font-weight:900; }"
        # Tablet: 2 columns.
        "@media (max-width: 900px) { .pws-slot-grid { grid-template-columns:repeat(2, minmax(0,1fr)); } }"
        # Phone: vertical compact list — name and score sit on the same row.
        "@media (max-width: 640px) {"
        "  .pws-slot-grid { grid-template-columns:1fr; gap:6px; }"
        "  .pws-slot { padding:7px 10px; }"
        "  .pws-slot .name { font-size:.92rem; }"
        "  .pws-header { gap:8px; }"
        "  .pws-header .right { align-items:flex-start; flex-direction:row; "
        "    flex-wrap:wrap; }"
        "  .pws-matchup { font-size:1.0rem; }"
        "  .pws-pitcher { font-size:.9rem; }"
        "  .pws-overall { font-size:.85rem; padding:5px 10px; }"
        "  .pws-target-list, .pws-reason { font-size:.85rem; }"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )

    _tag_class = {
        pws.TAG_TOP_ORDER: "top", pws.TAG_MIDDLE_ORDER: "mid",
        pws.TAG_BOTTOM_ORDER: "bot",
        pws.TAG_LEFTY_CLUSTER: "lefty", pws.TAG_RIGHTY_CLUSTER: "righty",
        pws.TAG_SECOND_TIME_THROUGH: "ttop", pws.TAG_THIRD_TIME_THROUGH: "ttop",
        pws.TAG_POWER_RISK: "power", pws.TAG_LOW_K_ZONE: "lowk",
        pws.TAG_PLATOON_EDGE: "platoon",
    }

    # Map lineup_status → badge CSS class. Live/final/postponed each get
    # their own visual treatment so the tab honestly reflects game state.
    _badge_cls_by_status = {
        _LS_CONFIRMED: "confirmed",
        _LS_LIVE: "live",
        _LS_FINAL: "final",
        _LS_EXPECTED: "expected",
        _LS_POSTPONED: "postponed",
        _LS_NOT_POSTED: "pending",
    }
    _zone_cls = {
        pws.ZONE_PRIMARY: "zone-primary",
        pws.ZONE_SECONDARY: "zone-secondary",
        pws.ZONE_NEUTRAL: "zone-neutral",
    }

    def _render_card(card: pws.WeakSpotCard):
        badge_cls = _badge_cls_by_status.get(card.lineup_status, "pending")
        card_cls = "pws-card confirmed" if card.is_lineup_confirmed else "pws-card"
        html = [f'<div class="{card_cls}">']
        html.append('<div class="pws-header">')
        html.append(
            f'<div class="left">'
            f'<div class="pws-matchup">{card.away_abbr} @ {card.home_abbr}'
            f'<span class="time">{card.game_time_label}</span></div>'
            f'<div class="pws-pitcher">{card.pitcher_name}'
            f'<span class="hand">{format_pitcher_hand(card.pitcher_hand or "R", "RHP")}</span>'
            f'<span class="vs">vs {card.opponent_abbr} lineup</span></div></div>'
        )
        html.append(
            f'<div class="right">'
            f'<span class="pws-overall">⚡ {card.overall_score:.0f} weak-spot</span>'
            f'<span class="pws-badge {badge_cls}">'
            f'<span class="dot"></span>{card.lineup_status_label}</span>'
            f'</div>'
        )
        html.append('</div>')

        # Ranked target list — top 3 slots by score
        sorted_scores = sorted(card.slot_scores, key=lambda s: -s["score"])
        target_pieces = []
        for s in sorted_scores[:3]:
            name = card.batters[s["slot"] - 1].name if 1 <= s["slot"] <= 9 else f"#{s['slot']}"
            target_pieces.append(
                f"#{s['slot']} <b>{name}</b> "
                f"({pws.ZONE_LABEL.get(s['zone'], s['zone'])}, {s['score']:.0f})"
            )
        if target_pieces:
            html.append(
                '<div class="pws-target-list">🎯 <b>Best slots to attack:</b> '
                + " · ".join(target_pieces) + '</div>'
            )

        # Tags (collect from top 3 slot scores) — capped to 4 so the row
        # stays scannable instead of sprouting a pill garden.
        top_tags: list[str] = []
        for s in sorted_scores[:3]:
            for t in s.get("tags", []):
                if t not in top_tags:
                    top_tags.append(t)
        if top_tags:
            tag_html = []
            for t in top_tags[:4]:
                cls = _tag_class.get(t, "mid")
                tag_html.append(f'<span class="pws-tag {cls}">{t}</span>')
            html.append('<div class="pws-tagrow">' + "".join(tag_html) + '</div>')

        # "Why this spot?" — show the top slot's reasons
        if sorted_scores:
            top = sorted_scores[0]
            reasons = top.get("reasons", [])
            if reasons:
                top_name = card.batters[top["slot"] - 1].name if 1 <= top["slot"] <= 9 else ""
                html.append(
                    f'<div class="pws-reason"><b>Why slot #{top["slot"]} '
                    f'({top_name})?</b><br>• ' + "<br>• ".join(reasons) + '</div>'
                )

        # Confidence bar
        pct = max(0, min(100, int(round(card.confidence))))
        html.append(
            f'<div class="pws-meta">Confidence: <b>{card.confidence:.0f}%</b></div>'
            f'<div class="pws-confidence-bar"><span style="width:{pct}%;"></span></div>'
        )

        # 9-slot lineup: grid on desktop, vertical list on phones (handled by CSS).
        html.append('<div class="pws-slot-grid">')
        for row in pws.card_to_slot_rows(card):
            zone = row["zone"]
            cls = ["pws-slot", _zone_cls.get(zone, "zone-neutral")]
            if row["is_projected"]:
                cls.append("projected")
            # Green halo: only on a *confirmed* slot that's a primary/secondary
            # attack zone. This decouples the "lineup is real" cue from the
            # color of the zone itself, so a confirmed red target shows a red
            # background with a green inset ring rather than competing colors.
            if (not row["is_projected"]) and zone in (pws.ZONE_PRIMARY, pws.ZONE_SECONDARY):
                cls.append("confirmed-target")
            side_letter = (row["bat_side"] or "R")[:1].upper()
            status_text = "Confirmed" if not row["is_projected"] else "Projected"
            status_icon = '<span class="check">✓</span>' if not row["is_projected"] else ""
            html.append(
                f'<div class="{" ".join(cls)}">'
                f'<div class="row1">'
                f'<span class="spot">#{row["slot"]}'
                f'<span class="side">{side_letter}HB</span></span>'
                f'<span class="score-pill">{row["score"]:.0f}</span>'
                f'</div>'
                f'<div class="name">{row["name"] or "—"}</div>'
                f'<div class="meta">{status_icon}'
                f'<span>{pws.ZONE_LABEL.get(zone, zone)} · {status_text}</span></div>'
                f'</div>'
            )
        html.append('</div>')
        html.append('</div>')
        st.markdown("".join(html), unsafe_allow_html=True)

    st.markdown(
        f'<div class="pws-summary">Showing <b>{len(_filtered)}</b> of '
        f'{len(_all_cards)} pitcher-vs-lineup cards · auto-refreshes with the slate.</div>',
        unsafe_allow_html=True,
    )
    for _card in _filtered:
        _render_card(_card)

    st.stop()


# ----- Game selector: clickable HTML pill carousel driven by ?g=<idx> query param -----
labels = schedule_df["label"].tolist()
n_games = len(labels)

# Read selection from query params (set by clicking a carousel pill)
try:
    qp = st.query_params
    raw_g = qp.get("g", None)
except Exception:
    qp = None
    raw_g = st.experimental_get_query_params().get("g", [None])[0] if hasattr(st, "experimental_get_query_params") else None

try:
    selected_idx = int(raw_g) if raw_g is not None else st.session_state.get("_selected_idx", 0)
except (TypeError, ValueError):
    selected_idx = 0
selected_idx = max(0, min(selected_idx, n_games - 1))
st.session_state["_selected_idx"] = selected_idx

render_live_ticker(schedule_df)
render_game_carousel(schedule_df, selected_idx)
st.caption(f"Tap any game above to switch · currently viewing **{labels[selected_idx]}**")

game_row = schedule_df.iloc[selected_idx]
# Apply the live current-pitcher overlay so all downstream consumers
# (matchup tables, heat-map, pitcher panels, top-3 strip, generators) see
# the *current* pitcher once a game is live. Pregame rows pass through
# unchanged. ``game_row`` becomes a plain dict here — pandas accessors
# below stay compatible since dict supports ``["..."]`` and ``.get(...)``.
game_row = apply_live_pitcher_overlay(game_row)
ctx = build_game_context(game_row)
weather = ctx["weather"]
render_game_header(game_row, ctx, weather)

# ----- Build all tables once -----
away_matchup = build_matchup_table(ctx["away_lineup"], batters_df, pitchers_df, game_row["home_probable"], weather, game_row["park_factor"],
                                   arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=game_row.get("home_probable_id"),
                                   slate_date=selected_date)
home_matchup = build_matchup_table(ctx["home_lineup"], batters_df, pitchers_df, game_row["away_probable"], weather, game_row["park_factor"],
                                   arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=game_row.get("away_probable_id"),
                                   slate_date=selected_date)

# ----- Tabs -----
# Game views are now simplified to just Matchup + Injuries. The slate-wide
# leaderboards (Hot / Cold / HR Milestones / Day vs Night HR) have moved up
# into the Apps & Generators pill row above, so this section stays focused
# on the selected matchup. We still render a header banner so mobile users
# clearly see they're inside the per-game area.
#
# Render flags drive which sections execute below. `nullcontext()` is a
# no-op context manager — `with nullcontext():` still runs the body — so
# we MUST also gate each `with tab_X:` block on the corresponding flag
# (`_render_X`) to actually suppress that section. Without these flags,
# the slate-wide leaderboards leak onto the main Games page even though
# their tab containers were nulled out.
_is_games_view = (_view == "⚾ Games")
_render_matchup = _is_games_view
_render_injuries = _is_games_view
_render_hot = (_view == "🔥 Hot Batters")
_render_cold = (_view == "🧊 Cold Batters")
_render_hr_milestones = (_view == "💣 HR Milestones")
_render_day_night = (_view == "☀️🌙 Day vs Night HR")

if _is_games_view:
    st.markdown(
        "<div style='display:flex;align-items:center;gap:10px;"
        "margin:10px 0 0;padding:7px 12px;"
        "background:rgba(12,26,46,0.85);"
        "border:1px solid rgba(0,200,150,0.14);"
        "border-radius:5px 5px 0 0;"
        "border-bottom:none;'>"
        "<span style='font-weight:700;font-size:0.6rem;color:#4e6a8a;"
        "letter-spacing:0.12em;text-transform:uppercase;'>Game Analysis</span>"
        "<span class='game-views-sub' style='color:#3a5a7a;font-weight:500;font-size:0.7rem;"
        "margin-left:auto;'>Matchup &middot; Injuries</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    tab_matchup, tab_injuries = st.tabs(["📊 Matchup", "🏥 Injuries"])
    tab_hot = nullcontext()
    tab_cold = nullcontext()
    tab_hr_milestones = nullcontext()
    tab_day_night = nullcontext()
else:
    tab_matchup = nullcontext()
    tab_injuries = nullcontext()
    tab_hot = st.container() if _render_hot else nullcontext()
    tab_cold = st.container() if _render_cold else nullcontext()
    tab_hr_milestones = st.container() if _render_hr_milestones else nullcontext()
    tab_day_night = st.container() if _render_day_night else nullcontext()

# ============== Matchup tab ==============
# `nullcontext()` does NOT suppress a `with` body, so simply nulling
# out the tab containers (as the previous version did) left every
# section's Streamlit calls executing on the main Games page. We now
# guard each section with an explicit `_render_X` check.
if _render_matchup:
 with tab_matchup:
    # Make explicit that the board below recomputes against the selected
    # slate game's context, not a global/season ranking.
    st.caption(
        "Matchup scores are keyed to the selected slate game, opponent, "
        "probable pitcher, lineup context, park/weather where available. "
        "Recent batting form (L5 / L10 games and last 30 days, MLB StatsAPI) "
        "is shown under each hitter and modestly blended into the matchup score."
    )
    # ---- Per-game pitcher mini-cards (top of Matchup) ----
    try:
        if pitcher_stats_df is not None and not pitcher_stats_df.empty:
            _away_sp = build_slate_pitcher_row(game_row, "away", pitcher_stats_df)
            _home_sp = build_slate_pitcher_row(game_row, "home", pitcher_stats_df)
            if _away_sp or _home_sp:
                st.markdown(
                    render_slate_pitcher_minicards(_away_sp, _home_sp),
                    unsafe_allow_html=True,
                )
    except Exception as _mc_e:
        # Mini-cards are non-critical — don't let any data hiccup break the tab.
        pass

    # Lineup-source caption appears once at the top of the tab
    if ctx["away_status"] == "Projected" or ctx["home_status"] == "Projected":
        st.caption("⚡ Showing **projected lineups** built from each team's most-used 9 over recent games. Rows auto-update once MLB posts the confirmed lineup.")

    # ----- Live pitcher freshness chip ---------------------------------------
    # Tell the user at a glance whether each side's matchup card is keyed
    # to the *current* pitcher (live game) or the pregame probable. Updates
    # within ~60s of an actual pitching change via the live overlay.
    _live_status = (game_row.get("_live_state_status") or "").lower()
    if _live_status in ("live", "final"):
        try:
            _away_src_label = _svc_pitcher_freshness_label(game_row, "away")
            _home_src_label = _svc_pitcher_freshness_label(game_row, "home")
            _inning = game_row.get("_live_state_inning")
            _half = (game_row.get("_live_state_inning_half") or "").lower()
            _half_short = "T" if _half == "top" else "B" if _half == "bottom" else ""
            _inning_tag = f"{_half_short}{_inning}" if _inning else ""
            _now_str = datetime.now(MLB_TZ).strftime("%-I:%M %p CT")
            _chip = (
                f"🟢 **Live** {('· ' + _inning_tag) if _inning_tag else ''} · "
                f"{game_row.get('away_abbr','')} SP: {_away_src_label} · "
                f"{game_row.get('home_abbr','')} SP: {_home_src_label} · "
                f"refreshed {_now_str}"
            )
            if _live_status == "final":
                _chip = (
                    f"🔘 **Final** · "
                    f"{game_row.get('away_abbr','')} SP: {_away_src_label} · "
                    f"{game_row.get('home_abbr','')} SP: {_home_src_label}"
                )
            st.caption(_chip)
            # Compact warning row when a side's pitcher has been pulled, so
            # the change is unmistakable even if the user scrolls past the
            # pitcher cards.
            _changes = []
            if game_row.get("away_pitcher_changed"):
                _orig = _safe_str(game_row.get("away_original_probable"), "starter")
                _now = _safe_str(game_row.get("away_probable"), "reliever")
                _changes.append(f"{_safe_str(game_row.get('away_abbr'),'Away')}: {_orig} → {_now}")
            if game_row.get("home_pitcher_changed"):
                _orig = _safe_str(game_row.get("home_original_probable"), "starter")
                _now = _safe_str(game_row.get("home_probable"), "reliever")
                _changes.append(f"{_safe_str(game_row.get('home_abbr'),'Home')}: {_orig} → {_now}")
            if _changes:
                st.markdown(
                    "<div style='display:inline-block;margin:2px 0 8px;"
                    "padding:6px 12px;border-radius:999px;"
                    "background:#fef3c7;color:#7c2d12;"
                    "border:1px solid #f59e0b;"
                    "font-weight:800;font-size:.78rem;"
                    "letter-spacing:.04em;text-transform:uppercase;'>"
                    "⚠️ Pitching Change Detected"
                    "</div>"
                    "<div style='font-size:.78rem;color:#7c2d12;"
                    "margin:-2px 0 8px 4px;font-weight:600;'>"
                    + " · ".join(_changes)
                    + "</div>",
                    unsafe_allow_html=True,
                )
        except Exception:
            pass

    # ----- Pitcher Vulnerability panels (shown above lineups so hitter
    # heat-maps below can be read against each SP's exploitable profile) -----
    st.markdown('<div class="section-title" style="margin-top:14px;">Pitcher Vulnerability</div>', unsafe_allow_html=True)
    pc1, pc2 = st.columns(2)
    _away_probable = _safe_str(game_row.get("away_probable"), "TBD")
    _home_probable = _safe_str(game_row.get("home_probable"), "TBD")
    _away_abbr = _safe_str(game_row.get("away_abbr"), "Away")
    _home_abbr = _safe_str(game_row.get("home_abbr"), "Home")
    with pc1:
        try:
            away_mix = pitcher_pitch_mix(arsenal_pitcher_df, game_row.get("away_probable_id"))
        except Exception:
            away_mix = None
        try:
            _away_row = find_pitcher_row(pitchers_df, _away_probable)
        except Exception:
            _away_row = None
        render_pitcher_panel(f"Away SP — {_away_abbr}", _away_probable,
                              (ctx or {}).get("away_pitch_hand", "") if isinstance(ctx, dict) else "",
                              _away_row,
                              pitch_mix_df=away_mix,
                              pitcher_changed=bool(game_row.get("away_pitcher_changed")),
                              original_name=_safe_str(game_row.get("away_original_probable"), ""))
    with pc2:
        try:
            home_mix = pitcher_pitch_mix(arsenal_pitcher_df, game_row.get("home_probable_id"))
        except Exception:
            home_mix = None
        try:
            _home_row = find_pitcher_row(pitchers_df, _home_probable)
        except Exception:
            _home_row = None
        render_pitcher_panel(f"Home SP — {_home_abbr}", _home_probable,
                              (ctx or {}).get("home_pitch_hand", "") if isinstance(ctx, dict) else "",
                              _home_row,
                              pitch_mix_df=home_mix,
                              pitcher_changed=bool(game_row.get("home_pitcher_changed")),
                              original_name=_safe_str(game_row.get("home_original_probable"), ""))

    # away lineup — full heat-map stat board (one place for all stats)
    away_board = build_matchup_heatmap_board(
        ctx["away_lineup"], batters_df, pitchers_df,
        game_row["home_probable"], weather, game_row["park_factor"],
        arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df,
        opp_pitcher_id=game_row.get("home_probable_id"),
        slate_date=selected_date,
        home_abbr=game_row.get("home_abbr"),
        venue=game_row.get("venue"),
        side="away",
        opp_abbr=game_row.get("home_abbr"),
    )
    home_board = build_matchup_heatmap_board(
        ctx["home_lineup"], batters_df, pitchers_df,
        game_row["away_probable"], weather, game_row["park_factor"],
        arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df,
        opp_pitcher_id=game_row.get("away_probable_id"),
        slate_date=selected_date,
        home_abbr=game_row.get("home_abbr"),
        venue=game_row.get("venue"),
        side="home",
        opp_abbr=game_row.get("away_abbr"),
    )
    # Per-board widget keys so each game's away/home sort controls stay isolated
    # in Streamlit's session_state when the user switches between games.
    _board_key_base = str(game_row.get("game_pk", selected_idx))
    _freshness_text, _, _ = get_lineup_freshness(game_row["game_pk"])
    render_lineup_banner(
        game_row["away_id"], game_row["away_abbr"], game_row["home_probable"],
        ctx["away_status"], freshness_text=_freshness_text,
        opp_pitch_hand=ctx.get("home_pitch_hand", ""),
    )
    if away_board.empty:
        st.info(f"{game_row['away_abbr']} lineup not available yet — not enough recent games on file to project.")
    else:
        render_matchup_board_with_sort(
            away_board,
            key_prefix=f"away_{_board_key_base}",
            label=f"{game_row['away_abbr']} lineup",
            pitchers_df=pitchers_df,
            slate_date=selected_date,
        )
    # home lineup
    render_lineup_banner(
        game_row["home_id"], game_row["home_abbr"], game_row["away_probable"],
        ctx["home_status"], freshness_text=_freshness_text,
        opp_pitch_hand=ctx.get("away_pitch_hand", ""),
    )
    if home_board.empty:
        st.info(f"{game_row['home_abbr']} lineup not available yet — not enough recent games on file to project.")
    else:
        render_matchup_board_with_sort(
            home_board,
            key_prefix=f"home_{_board_key_base}",
            label=f"{game_row['home_abbr']} lineup",
            pitchers_df=pitchers_df,
            slate_date=selected_date,
        )
    st.caption(
        "Use the **Sort** controls above each lineup to rank by any column "
        "(High → Low or Low → High). Dark green = best, light green → yellow → "
        "orange → red = worst. Swipe horizontally to see all stats. SwStr% and "
        "GB% are reverse-scaled (lower = better for power); LA peaks around 14° "
        "(sweet-spot range)."
    )

    # ----- Top Insights Panel (replaces Top 3 white cards) -----
    combined_for_ranking = pd.concat([away_matchup, home_matchup], ignore_index=True) \
        if (not away_matchup.empty or not home_matchup.empty) else pd.DataFrame()
    if not combined_for_ranking.empty and "Matchup" in combined_for_ranking.columns:
        top3 = combined_for_ranking.sort_values("Matchup", ascending=False).head(3).reset_index(drop=True)
        st.markdown(
            '<div class="section-title" style="margin-top:16px;">'
            'TOP INTEL — This Game'
            '</div>',
            unsafe_allow_html=True,
        )

        def _fmt_v(v, fmt):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "—"
            try:
                return fmt.format(float(v))
            except Exception:
                return "—"

        rows_html = []
        for i, r in top3.iterrows():
            name = str(r.get("Hitter", ""))
            team = str(r.get("Team", ""))
            spot = r.get("Spot", "")
            bat = r.get("_bat_side", "") or r.get("Bat", "")
            opp_pitcher = (
                game_row["home_probable"]
                if team == game_row.get("away_abbr", "") else
                game_row["away_probable"]
            )
            matchup_v = r.get("Matchup", 0) or 0
            brl_v  = r.get("Brl/BIP%")
            ev_v   = r.get("EV")
            hh_v   = r.get("HH%")
            iso_v  = r.get("ISO")
            ops_v  = r.get("OPS")

            photo_url = player_headshot_url(r.get("_player_id"))
            initials = "".join([p[:1] for p in name.split()[:2]]).upper() or "?"
            if photo_url:
                # Wrap: fallback div sits behind; img overlays it if it loads.
                # No onerror JS needed — Streamlit's sanitizer strips JS attributes.
                photo_html = (
                    f'<div class="insight-photo-wrap">'
                    f'<div class="insight-photo-fallback">{initials}</div>'
                    f'<img class="insight-photo" src="{photo_url}" alt="{name}">'
                    f'</div>'
                )
            else:
                photo_html = (
                    f'<div class="insight-photo-wrap">'
                    f'<div class="insight-photo-fallback">{initials}</div>'
                    f'</div>'
                )

            # Crush chips
            crush_chips_html = ""
            crush_df = hitter_pitch_crush(arsenal_batter_df, r.get("_player_id"), top_n=2, min_pa=15)
            if not crush_df.empty:
                chips = []
                for _, cr in crush_df.iterrows():
                    pt = str(cr.get("pitch_type", "")).strip().upper()
                    pname = PITCH_NAME_MAP.get(pt, str(cr.get("pitch_name", pt)))
                    pemoji = PITCH_EMOJI.get(pt, "⚾")
                    cw = cr.get("woba")
                    woba_str = f"{float(cw):.3f}" if pd.notna(cw) else "—"
                    chips.append(
                        f'<span class="insight-crush-chip">{pemoji} {pname} &middot; wOBA {woba_str}</span>'
                    )
                crush_chips_html = (
                    f'<div class="insight-crush">{"".join(chips)}</div>'
                )

            # Score bar (0–200 scale)
            bar_pct = min(100, max(0, (float(matchup_v) / 200) * 100))
            rank_cls = f"rank-{i + 1}"
            bat_label = format_batter_stance(bat, "")

            rows_html.append(
                f'<div class="insight-row">'
                f'<div class="insight-rank {rank_cls}">#{i + 1}</div>'
                f'{photo_html}'
                f'<div class="insight-player">'
                f'<div class="insight-player-name">{name}</div>'
                f'<div class="insight-player-meta">{team}'
                f'{f" &middot; Bats {bat_label}" if bat_label else ""}'
                f' &middot; #{spot} &middot; vs {opp_pitcher}</div>'
                f'{crush_chips_html}'
                f'</div>'
                f'<div class="insight-stats">'
                f'<div class="insight-stat">'
                f'<span class="insight-stat-val">{_fmt_v(ops_v, "{:.3f}")}</span>'
                f'<span class="insight-stat-label">OPS</span>'
                f'</div>'
                f'<div class="insight-stat">'
                f'<span class="insight-stat-val">{_fmt_v(brl_v, "{:.1f}%")}</span>'
                f'<span class="insight-stat-label">Brl%</span>'
                f'</div>'
                f'<div class="insight-stat">'
                f'<span class="insight-stat-val">{_fmt_v(ev_v, "{:.1f}")}</span>'
                f'<span class="insight-stat-label">EV</span>'
                f'</div>'
                f'<div class="insight-stat">'
                f'<span class="insight-stat-val">{_fmt_v(hh_v, "{:.1f}%")}</span>'
                f'<span class="insight-stat-label">HH%</span>'
                f'</div>'
                f'<div class="insight-stat">'
                f'<span class="insight-stat-val">{_fmt_v(iso_v, "{:.3f}")}</span>'
                f'<span class="insight-stat-label">ISO</span>'
                f'</div>'
                f'<div class="insight-score-bar">'
                f'<div class="insight-score-bar-track">'
                f'<div class="insight-score-bar-fill" style="width:{bar_pct:.0f}%;"></div>'
                f'</div>'
                f'<div class="insight-score-num">{matchup_v:.1f}</div>'
                f'</div>'
                f'</div>'
                f'</div>'
            )

        st.markdown(
            f'<div class="insights-panel">'
            f'<div class="insights-panel-header">'
            f'<span class="insights-panel-title">Top Performers — This Matchup</span>'
            f'<span class="insights-panel-sub">Ranked by Matchup Score</span>'
            f'</div>'
            + "".join(rows_html) +
            f'</div>',
            unsafe_allow_html=True,
        )

# ============== Hot / Cold Batters tabs (slate-wide) ==============
@st.cache_data(ttl=600, show_spinner=False)
def _build_slate_dataframe(_schedule_df, _batters_df, _pitchers_df, cache_key, slate_date=None):
    """Score every batter in every posted lineup across the slate. Returns a single DataFrame.
    cache_key is a string used to invalidate the cache when the slate or data changes."""
    rows = []
    for _, g in _schedule_df.iterrows():
        g = apply_live_pitcher_overlay(g)
        try:
            cc = build_game_context(g)
            a = build_matchup_table(cc["away_lineup"], _batters_df, _pitchers_df, g["home_probable"], cc["weather"], g["park_factor"],
                                    arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=g.get("home_probable_id"),
                                    slate_date=slate_date)
            h = build_matchup_table(cc["home_lineup"], _batters_df, _pitchers_df, g["away_probable"], cc["weather"], g["park_factor"],
                                    arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=g.get("away_probable_id"),
                                    slate_date=slate_date)
            for _, r in a.iterrows():
                d = r.to_dict(); d["Game"] = g["short_label"]; d["OppPitcher"] = g["home_probable"]; rows.append(d)
            for _, r in h.iterrows():
                d = r.to_dict(); d["Game"] = g["short_label"]; d["OppPitcher"] = g["away_probable"]; rows.append(d)
        except Exception:
            pass
    slate = pd.DataFrame(rows)
    if slate.empty:
        return slate
    # Lift selected underscore-prefixed HR-context fields into public columns
    # so the slate-wide leaderboards (Hot/Cold/HR Milestones) can show them.
    if "_LastHRDate" in slate.columns:
        slate["Last HR Date"] = slate["_LastHRDate"]
    if "_HRLast10" in slate.columns:
        slate["HR (L10G)"] = slate["_HRLast10"]
    if "_HRSeasonFromLog" in slate.columns:
        slate["Season HR"] = slate["_HRSeasonFromLog"]
    if "_player_id" in slate.columns:
        slate["player_id"] = slate["_player_id"]
    if "_bat_side" in slate.columns:
        slate["Bat"] = slate["_bat_side"]
    return slate.drop(columns=[c for c in slate.columns if c.startswith("_")], errors="ignore")

# Cache key includes the lineup-confirmation state of every game on the
# slate so that the moment MLB posts a confirmed lineup (replacing a
# projected one), the slate dataframe is rebuilt with the real names.
# We hash the per-game (game_pk, away_status, home_status) tuple set so
# the key flips deterministically when any single lineup confirms.
def _slate_lineup_signature(_schedule_df) -> str:
    parts = []
    for _, g in _schedule_df.iterrows():
        try:
            cc = build_game_context(g)
            # Fold the live pitcher ids into the signature so a pitching
            # change immediately invalidates the slate cache and triggers
            # a rebuild against the new opposing pitcher.
            ov = apply_live_pitcher_overlay(g)
            live_a = ov.get("away_probable_id") if isinstance(ov, dict) else None
            live_h = ov.get("home_probable_id") if isinstance(ov, dict) else None
            parts.append(
                f"{g.get('game_pk','?')}:{cc.get('away_status','')}:"
                f"{cc.get('home_status','')}:p{live_a}-{live_h}"
            )
        except Exception:
            parts.append(f"{g.get('game_pk','?')}:err")
    return "|".join(parts)

_slate_lineup_sig = _slate_lineup_signature(schedule_df)
_slate_cache_key = f"{selected_date}_{len(schedule_df)}_{_slate_lineup_sig}"
_slate_df = _build_slate_dataframe(schedule_df, batters_df, pitchers_df, _slate_cache_key, slate_date=selected_date)

def _df_mobile_cards_html(df, *, name_col=None, sub_col=None, score_col=None,
                          score_label="Score", chip_cols=None, foot_cols=None,
                          rank_col="#", max_chips=8, always_show=False):
    """Generic dataframe -> mobile-card-grid renderer.

    - name_col: header of the card (player / batter / matchup)
    - sub_col:  one-line sub under the name
    - score_col: top-right big number (formatted as-is)
    - chip_cols: list of column names to render as stat chips (in order)
    - foot_cols: list of column names to render as a meta line at the bottom
    Falls back gracefully when columns aren't present.
    """
    if df is None or df.empty:
        return '<div class="mc-empty">No data to display.</div>'
    chip_cols = chip_cols or []
    foot_cols = foot_cols or []
    cards = []
    for _, r in df.iterrows():
        name = str(r.get(name_col, "")) if name_col else ""
        sub  = str(r.get(sub_col, ""))  if sub_col  else ""
        sub_bits = [sub] if sub else []
        bat_raw = r.get("Bat", None)
        if bat_raw is None:
            bat_raw = r.get("bat_side", None)
        bat_label = format_batter_stance(bat_raw, "")
        if bat_label and not any("Bats " in bit for bit in sub_bits):
            sub_bits.append(f"Bats {bat_label}")
        throws_label = format_pitcher_hand(r.get("Throws", None), "")
        if throws_label and not any("Throws " in bit for bit in sub_bits):
            sub_bits.append(f"Throws {throws_label}")
        sub = " · ".join(sub_bits)
        score = r.get(score_col) if score_col else None
        # Render score as text directly (column may already be formatted)
        score_html = ""
        if score is not None and not (isinstance(score, float) and pd.isna(score)):
            try:
                txt = f"{float(score):.1f}"
            except Exception:
                txt = str(score)
            score_html = (
                f'<div class="mc-score">{txt}'
                f'<small>{score_label}</small></div>'
            )
        rank_val = r.get(rank_col) if rank_col else None
        try:
            rank_int = int(rank_val) if rank_val is not None else None
        except Exception:
            rank_int = None
        rank_html = f'<span class="mc-rank">#{rank_int}</span>' if rank_int else ""
        # Chips
        chip_html_parts = []
        for c in chip_cols[:max_chips]:
            if c not in r.index and c not in df.columns:
                continue
            v = r.get(c)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                txt = None
            else:
                try:
                    if isinstance(v, float):
                        txt = f"{v:.3f}" if abs(v) < 5 else f"{v:.1f}"
                    else:
                        txt = str(v)
                except Exception:
                    txt = str(v)
            chip_html_parts.append(_mc_chip(c, txt, "mid"))
        chips_html = (
            '<div class="mc-grid2">' + "".join(chip_html_parts) + "</div>"
            if chip_html_parts else ""
        )
        # Foot
        foot_bits = []
        for c in foot_cols:
            if c not in r.index and c not in df.columns:
                continue
            val = r.get(c)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            foot_bits.append(f"<b>{c}</b> {val}")
        foot_html = (
            '<div class="mc-foot">' + " · ".join(foot_bits) + "</div>"
            if foot_bits else ""
        )
        cards.append(
            '<div class="mc-card">'
            '<div class="mc-head">'
            f'{rank_html}'
            f'<div class="mc-id"><div class="mc-name">{name}</div>'
            f'<div class="mc-sub">{sub}</div></div>'
            f'{score_html}'
            '</div>'
            f'{chips_html}'
            f'{foot_html}'
            '</div>'
        )
    wrapper_cls = "mc-mobile mc-always" if always_show else "mc-mobile"
    return (
        f'<div class="{wrapper_cls}"><div class="mc-grid">'
        + "".join(cards) +
        '</div></div>'
    )


def _df_with_cards(df, *, cards_only=False, **kwargs):
    """Render a player-card grid for `df`.

    By default (cards_only=False) emits an `st.dataframe` on desktop and a
    card grid on mobile (the legacy hybrid). When cards_only=True the
    desktop table is omitted and the card grid is shown on every viewport
    — used by the Apps & Generators sections (Hot/Cold Batters, HR
    Milestones, Day vs Night HR) where the wide white table was clutter.
    """
    if df is None or df.empty:
        return
    if not cards_only:
        st.markdown('<div class="mc-desktop">', unsafe_allow_html=True)
        st.dataframe(
            df,
            width='stretch',
            hide_index=True,
            height=min(720, 60 + 36 * len(df)),
        )
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown(
        MOBILE_CARDS_CSS + _df_mobile_cards_html(df, always_show=cards_only, **kwargs),
        unsafe_allow_html=True,
    )


def _render_leaderboard(df, title, top=True, n=15, sort_col="Matchup"):
    if df.empty:
        st.info("No lineups posted yet across the slate. Check back closer to first pitch.")
        return
    if sort_col not in df.columns:
        st.warning(f"Sort column '{sort_col}' missing from slate data.")
        return
    ranked = df.sort_values(sort_col, ascending=not top).head(n).reset_index(drop=True)
    ranked.insert(0, "#", range(1, len(ranked) + 1))
    show_cols = [c for c in ["#", "Hitter", "Team", "Game", "Spot", "OppPitcher", "Crushes",
                              "Matchup", "OPS", "ISO", "Brl/BIP%", "FB%", "GB%", "EV", "HH%",
                              "HR/FB%", "PullAir%", "LA", "xwOBA", "SweetSpot%",
                              "Last HR Date", "HR (L10G)", "Likely"]
                 if c in ranked.columns]
    out = ranked[show_cols]
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)

    # Modern-minimal: dark player cards only. The wide white desktop
    # dataframe was visual clutter ("ugly column graphs") so it's been
    # removed in favor of the same Slate-Pitchers-style card grid we
    # already render on phones, now also shown on desktop.
    mobile_cards = []
    for _, r in ranked.iterrows():
        match_score = r.get("Matchup")
        # Reuse the same 0-100ish "Matchup" scale for tier coloring.
        if match_score is not None and not (isinstance(match_score, float) and pd.isna(match_score)):
            try:
                m = float(match_score)
                if m >= 130:   tier = ("Elite", "elite")
                elif m >= 110: tier = ("Strong", "strong")
                elif m >= 90:  tier = ("Average", "ok")
                else:          tier = ("Soft", "soft")
            except Exception:
                tier = ("—", "ok")
        else:
            tier = ("—", "ok")
        tiers = [tier]
        likely = r.get("Likely")
        if isinstance(likely, str) and likely.strip():
            tiers.append((likely.strip(), "gold"))

        chips = [
            _mc_chip("Match", _mc_fmt(r.get("Matchup"), "{:.1f}"),
                     _mc_chip_tone(r.get("Matchup"), 80, 140)),
            # OPS sits right after Matchup — universal bat-quality signal,
            # same .650 → .900 band the heat-map / target chips use.
            _mc_chip("OPS", _mc_fmt(r.get("OPS"), "{:.3f}"),
                     _mc_chip_tone(r.get("OPS"), 0.650, 0.900)),
            _mc_chip("ISO", _mc_fmt(r.get("ISO"), "{:.3f}"),
                     _mc_chip_tone(r.get("ISO"), 0.130, 0.260)),
            _mc_chip("Barrel%", _mc_fmt(r.get("Brl/BIP%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("Brl/BIP%"), 5.0, 14.0)),
            _mc_chip("HardHit%", _mc_fmt(r.get("HH%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("HH%"), 32.0, 50.0)),
            _mc_chip("Exit Velo", _mc_fmt(r.get("EV"), "{:.1f}"),
                     _mc_chip_tone(r.get("EV"), 86.0, 94.0)),
            _mc_chip("HR/FB%", _mc_fmt(r.get("HR/FB%"), "{:.1f}%"),
                     _mc_chip_tone(r.get("HR/FB%"), 6.0, 18.0)),
            _mc_chip("xwOBA", _mc_fmt(r.get("xwOBA"), "{:.3f}"),
                     _mc_chip_tone(r.get("xwOBA"), 0.300, 0.400)),
            _mc_chip("LA", _mc_fmt(r.get("LA"), "{:.1f}°"), "mid"),
        ]
        foot_bits = []
        if r.get("Game"):
            foot_bits.append(f'<b>{r.get("Game")}</b>')
        if r.get("OppPitcher"):
            foot_bits.append(f'vs {r.get("OppPitcher")}')
        if r.get("Bat") not in (None, ""):
            foot_bits.append(f'Bats {format_batter_stance(r.get("Bat"))}')
        if r.get("Spot") not in (None, ""):
            foot_bits.append(f'Spot {r.get("Spot")}')
        if r.get("HR (L10G)") not in (None, ""):
            foot_bits.append(f'L10G HR: <b>{r.get("HR (L10G)")}</b>')
        foot = f'<div class="mc-foot">{" · ".join(foot_bits)}</div>' if foot_bits else ""
        mobile_cards.append(_mc_card(
            rank=int(r.get("#")) if r.get("#") is not None else None,
            name=r.get("Hitter", ""),
            sub=f'{r.get("Team","")} · Bats {format_batter_stance(r.get("Bat",""))}',
            score=r.get("Matchup"),
            score_label="Matchup",
            tiers=tiers,
            chips_html='<div class="mc-grid2">' + "".join(chips) + "</div>",
            foot_html=foot,
        ))
    st.markdown(
        MOBILE_CARDS_CSS +
        '<div class="mc-mobile mc-always"><div class="mc-grid">'
        + "".join(mobile_cards) +
        '</div></div>',
        unsafe_allow_html=True,
    )

    csv = out.to_csv(index=False)
    st.download_button(
        f"⬇️ Download {title} CSV",
        csv,
        file_name=f"{selected_date}_{'hot' if top else 'cold'}_top{n}.csv",
        mime="text/csv",
        width='stretch',
    )

if _render_hot:
 with tab_hot:
    st.markdown(
        '<div style="margin: 4px 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Top 15 hitters across the entire slate — ranked by Matchup score (combines opposing pitcher, '
        'hand split, park, weather, recent form). These are the most exploitable spots tonight.'
        '</div>', unsafe_allow_html=True)
    _render_leaderboard(_slate_df, "🔥 Hot Batters — Top 15", top=True, n=15, sort_col="Matchup")

if _render_cold:
 with tab_cold:
    st.markdown(
        '<div style="margin: 4px 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Bottom 15 hitters across the slate — toughest matchups. Useful for fade lists, '
        'unders, and pitcher-side bets.'
        '</div>', unsafe_allow_html=True)
    _render_leaderboard(_slate_df, "🧊 Cold Batters — Bottom 15", top=False, n=15, sort_col="Matchup")

# ============== HR Milestones tab ==============
if _render_hr_milestones:
 with tab_hr_milestones:
    st.markdown(
        '<div style="margin: 4px 0 12px 0; color:#475569; font-size:0.92rem;">'
        '💣 <b>HR Milestones</b> — season HR totals, most recent HR date, and HR over each batter\'s '
        'last 10 played games (MLB StatsAPI gameLog). Use the date picker below to look up every '
        'home run hit on a specific date.'
        '</div>', unsafe_allow_html=True)

    # --- Section 1: Slate batter HR milestones (season HR, last HR, HR L10G) ---
    st.markdown('<div class="section-title">⚾ Today\'s Slate — Batter HR Milestones</div>',
                unsafe_allow_html=True)
    if _slate_df is None or _slate_df.empty:
        st.info("No lineups posted yet across the slate. Check back closer to first pitch.")
    else:
        wanted_cols = [c for c in ["Hitter", "Team", "Game", "Spot", "Bat", "OppPitcher",
                                    "Season HR", "Last HR Date", "HR (L10G)",
                                    "OPS", "Matchup", "Likely"]
                       if c in _slate_df.columns]
        slate_hr = _slate_df[wanted_cols].copy()

        # ----- Sort + display controls (default: Season HR, max 25 players)
        # so the slate panel is never overwhelming on mobile. Cap at 50.
        _sort_options = [c for c in
                         ["Season HR", "HR (L10G)", "Matchup", "Last HR Date",
                          "Hitter", "Team"]
                         if c in slate_hr.columns]
        _ctrl1, _ctrl2, _ctrl3 = st.columns([1.3, 1.0, 1.0])
        with _ctrl1:
            _hrm_sort = st.selectbox(
                "Sort by",
                _sort_options,
                index=0 if _sort_options else 0,
                key="hrm_sort_field",
            ) if _sort_options else None
        with _ctrl2:
            _hrm_dir = st.radio(
                "Direction",
                ["Descending", "Ascending"],
                index=0,
                horizontal=True,
                key="hrm_sort_dir",
            )
        with _ctrl3:
            _hrm_limit = st.slider(
                "Max players shown",
                min_value=10,
                max_value=50,
                value=min(25, len(slate_hr)) if len(slate_hr) else 25,
                step=5,
                key="hrm_max_players",
                help=(
                    "Limit how many slate hitters are listed below. "
                    "Capped at 50 so the page stays readable on mobile."
                ),
            )

        _ascending = (_hrm_dir == "Ascending")
        if _hrm_sort:
            if _hrm_sort in ("Hitter", "Team", "Last HR Date"):
                slate_hr["_sort_key"] = slate_hr[_hrm_sort].astype(str).fillna("")
                slate_hr = slate_hr.sort_values(
                    "_sort_key", ascending=_ascending, na_position="last"
                ).drop(columns=["_sort_key"]).reset_index(drop=True)
            else:
                slate_hr["_sort_key"] = pd.to_numeric(slate_hr[_hrm_sort], errors="coerce")
                slate_hr = slate_hr.sort_values(
                    "_sort_key", ascending=_ascending, na_position="last"
                ).drop(columns=["_sort_key"]).reset_index(drop=True)
        # Apply the cap and rebuild the rank column from 1.
        slate_hr = slate_hr.head(int(_hrm_limit)).reset_index(drop=True)
        slate_hr.insert(0, "#", range(1, len(slate_hr) + 1))

        # Modern-minimal: dark player cards only (no wide white table).
        hrm_cards = []
        for _, r in slate_hr.iterrows():
            season_hr = r.get("Season HR")
            try:
                shr = float(season_hr) if season_hr not in (None, "") else None
            except Exception:
                shr = None
            if shr is None:
                tier = ("Off log", "warn")
            elif shr >= 25:
                tier = ("Power 🔥", "elite")
            elif shr >= 15:
                tier = ("Threat", "strong")
            elif shr >= 8:
                tier = ("Streaky", "ok")
            else:
                tier = ("Sleeper", "soft")
            tiers = [tier]
            likely = r.get("Likely")
            if isinstance(likely, str) and likely.strip():
                tiers.append((likely.strip(), "gold"))
            chips = [
                _mc_chip("Season HR", _mc_fmt(r.get("Season HR"), "{:.0f}"),
                         _mc_chip_tone(r.get("Season HR"), 5, 30)),
                _mc_chip("HR L10G", _mc_fmt(r.get("HR (L10G)"), "{:.0f}"),
                         _mc_chip_tone(r.get("HR (L10G)"), 0, 4)),
                # OPS chip: pure bat-quality signal alongside the HR-count
                # signals, so the milestones card doesn't read as "HR totals
                # only" — a 25-HR threat with a .680 OPS gets visibly flagged.
                _mc_chip("OPS", _mc_fmt(r.get("OPS"), "{:.3f}"),
                         _mc_chip_tone(r.get("OPS"), 0.650, 0.900)),
                _mc_chip("Match", _mc_fmt(r.get("Matchup"), "{:.1f}"),
                         _mc_chip_tone(r.get("Matchup"), 80, 140)),
                _mc_chip("Last HR", (str(r.get("Last HR Date")) if r.get("Last HR Date") not in (None, "") else None), "mid"),
            ]
            foot_bits = []
            if r.get("Game"):  foot_bits.append(f'<b>{r.get("Game")}</b>')
            if r.get("OppPitcher"): foot_bits.append(f'vs {r.get("OppPitcher")}')
            if r.get("Spot") not in (None, ""): foot_bits.append(f'Spot {r.get("Spot")}')
            if r.get("Bat") not in (None, ""):  foot_bits.append(f'Bats {format_batter_stance(r.get("Bat"))}')
            foot = f'<div class="mc-foot">{" · ".join(foot_bits)}</div>' if foot_bits else ""
            hrm_cards.append(_mc_card(
                rank=int(r.get("#")) if r.get("#") is not None else None,
                name=r.get("Hitter", ""),
                sub=f'{r.get("Team","")} · Bats {format_batter_stance(r.get("Bat",""))}',
                score=r.get("Season HR"),
                score_label="HR",
                tiers=tiers,
                chips_html='<div class="mc-grid2">' + "".join(chips) + "</div>",
                foot_html=foot,
            ))
        st.markdown(
            MOBILE_CARDS_CSS +
            '<div class="mc-mobile mc-always"><div class="mc-grid">'
            + "".join(hrm_cards) +
            '</div></div>',
            unsafe_allow_html=True,
        )

        st.download_button(
            "⬇️ Download HR Milestones CSV",
            slate_hr.to_csv(index=False),
            file_name=f"{selected_date}_hr_milestones.csv",
            mime="text/csv",
            width='stretch',
        )
        st.caption(
            "Season HR and Last HR Date are computed from each batter's MLB StatsAPI gameLog "
            "(authoritative). HR (L10G) sums HR over the batter's most recent 10 played games. "
            "Cells show '—' when the gameLog is unavailable (offseason / network hiccup)."
        )

    # --- Section 2: Date-based HR search across the league ---
    st.markdown('<div class="section-title" style="margin-top:18px;">🔎 Find Home Runs by Date</div>',
                unsafe_allow_html=True)
    st.caption(
        "Pick any date — we'll pull every home run hit across MLB that day from the official "
        "live game feed (statsapi.mlb.com). Useful for checking yesterday's slate, parlay "
        "review, or scouting recent power surges."
    )
    today_local = today_ct()
    default_hr_date = today_local - timedelta(days=1)
    # Anchor the upper bound to today (CT) so picking 'tomorrow' doesn't surface
    # an empty result page.
    hr_search_date = st.date_input(
        "Search HR by date",
        value=default_hr_date,
        min_value=date(2015, 1, 1),
        max_value=today_local,
        key="hr_milestones_date",
        help="Defaults to yesterday so completed slates show up immediately.",
    )
    try:
        hr_date_iso = hr_search_date.strftime("%Y-%m-%d")
    except Exception:
        hr_date_iso = (today_local - timedelta(days=1)).strftime("%Y-%m-%d")

    with st.spinner(f"Pulling home runs hit on {hr_date_iso}…"):
        try:
            hr_on_date = get_home_runs_on_date(hr_date_iso)
        except Exception as _hr_e:
            hr_on_date = pd.DataFrame()
            st.warning(f"Couldn't pull HRs for {hr_date_iso}: {_hr_e}")

    if hr_on_date is None or hr_on_date.empty:
        st.info(
            f"No home runs found for {hr_date_iso}. Possible reasons: no completed MLB games on "
            "that date, the live feed has not posted yet, or a transient network issue. Try a "
            "different date or refresh the page."
        )
    else:
        # Friendlier display: team chip "AWY @ HOM", inning, batter, distance, pitcher.
        display = hr_on_date.copy()
        display["Game"] = display["away_abbr"].fillna("") + " @ " + display["home_abbr"].fillna("")
        display["Inning"] = display.apply(
            lambda r: f'{"T" if str(r.get("half","")).lower().startswith("top") else "B"}{int(r["inning"])}'
                       if pd.notna(r.get("inning")) else "—",
            axis=1,
        )
        display["Distance (ft)"] = display["hr_distance"].apply(
            lambda v: f"{int(v)}" if pd.notna(v) else "—"
        )
        display["Exit Velo (mph)"] = display["launch_speed"].apply(
            lambda v: f"{float(v):.1f}" if pd.notna(v) else "—"
        )
        display = display.rename(columns={
            "batter": "Batter",
            "batter_team_abbr": "Team",
            "pitcher": "Off Pitcher",
            "description": "Play",
        })
        display["Bat"] = display.apply(
            lambda r: format_batter_stance(
                lookup_batter_stance(
                    player_id=r.get("batter_id"),
                    name=r.get("Batter"),
                    team=r.get("Team"),
                ),
                "",
            ),
            axis=1,
        )
        cols_show = ["Game", "Inning", "Batter", "Team", "Off Pitcher",
                     "Bat", "Distance (ft)", "Exit Velo (mph)", "Play"]
        cols_show = [c for c in cols_show if c in display.columns]
        out_hr = display[cols_show].reset_index(drop=True)
        out_hr.insert(0, "#", range(1, len(out_hr) + 1))

        # Optional in-tab name filter so a client can quickly narrow to a player.
        name_q = st.text_input(
            "Filter by batter name (optional)",
            value="",
            key="hr_milestones_name_filter",
            placeholder="e.g. Judge, Ohtani, Soto",
        ).strip()
        if name_q:
            try:
                mask = out_hr["Batter"].str.contains(name_q, case=False, na=False)
                out_hr = out_hr[mask].reset_index(drop=True)
                if not out_hr.empty:
                    out_hr["#"] = range(1, len(out_hr) + 1)
            except Exception:
                pass

        if out_hr.empty:
            st.info(f"No HRs match '{name_q}' on {hr_date_iso}.")
        else:
            # ----- Sort + max-50 control so a 30+ HR day doesn't dump every
            # play into one scroll on mobile. Distance default keeps the
            # bombs at the top.
            _hr_sort_opts = [c for c in
                             ["Distance (ft)", "Exit Velo (mph)", "Batter",
                              "Team", "Inning", "Game"]
                             if c in out_hr.columns]
            _hrs1, _hrs2, _hrs3 = st.columns([1.3, 1.0, 1.0])
            with _hrs1:
                _hr_sort = st.selectbox(
                    "Sort HRs by",
                    _hr_sort_opts,
                    index=0,
                    key="hr_date_sort_field",
                ) if _hr_sort_opts else None
            with _hrs2:
                _hr_dir = st.radio(
                    "Direction",
                    ["Descending", "Ascending"],
                    index=0,
                    horizontal=True,
                    key="hr_date_sort_dir",
                )
            with _hrs3:
                _hr_limit = st.slider(
                    "Max HRs shown",
                    min_value=10,
                    max_value=50,
                    value=min(25, len(out_hr)) if len(out_hr) else 25,
                    step=5,
                    key="hr_date_max",
                    help=(
                        "Cap the number of home runs rendered below. "
                        "Capped at 50 to keep the mobile view readable."
                    ),
                )
            _hr_asc = (_hr_dir == "Ascending")
            if _hr_sort:
                if _hr_sort in ("Distance (ft)", "Exit Velo (mph)"):
                    out_hr["_sort_key"] = pd.to_numeric(
                        out_hr[_hr_sort].replace("—", pd.NA), errors="coerce"
                    )
                else:
                    out_hr["_sort_key"] = out_hr[_hr_sort].astype(str).fillna("")
                out_hr = out_hr.sort_values(
                    "_sort_key", ascending=_hr_asc, na_position="last"
                ).drop(columns=["_sort_key"]).reset_index(drop=True)
            out_hr = out_hr.head(int(_hr_limit)).reset_index(drop=True)
            out_hr["#"] = range(1, len(out_hr) + 1)
            st.markdown(
                f'<div style="margin: 6px 0 10px 0; font-weight:800; color:#0f172a;">'
                f'⚾ {len(out_hr)} home run{"s" if len(out_hr) != 1 else ""} on {hr_date_iso}'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Modern-minimal: dark player cards only (no wide white table).
            hrd_cards = []
            for _, hrr in out_hr.iterrows():
                # Distance/EV come in as already-formatted strings ("123" / "—")
                dist_v = hrr.get("Distance (ft)")
                ev_v = hrr.get("Exit Velo (mph)")
                try:
                    dist_num = float(dist_v) if dist_v not in (None, "", "—") else None
                except Exception:
                    dist_num = None
                try:
                    ev_num = float(ev_v) if ev_v not in (None, "", "—") else None
                except Exception:
                    ev_num = None
                tier = ("BOMB 💣", "elite") if (dist_num is not None and dist_num >= 430) else (
                       ("No-Doubt", "strong") if (dist_num is not None and dist_num >= 400) else
                       ("Solo Shot", "ok")
                )
                chips = [
                    _mc_chip("Distance", f"{int(dist_num)} ft" if dist_num is not None else None,
                             _mc_chip_tone(dist_num, 380, 450) if dist_num is not None else "mid"),
                    _mc_chip("Exit Velo", f"{ev_num:.1f}" if ev_num is not None else None,
                             _mc_chip_tone(ev_num, 95, 112) if ev_num is not None else "mid"),
                    _mc_chip("Inning", str(hrr.get("Inning") or "—"), "mid"),
                    _mc_chip("Pitcher", str(hrr.get("Off Pitcher") or "—"), "mid"),
                ]
                play = str(hrr.get("Play") or "").strip()
                foot = (
                    f'<div class="mc-foot"><b>{hrr.get("Game","")}</b>'
                    + (f' · {play}' if play else '')
                    + '</div>'
                )
                bat_label = format_batter_stance(hrr.get("Bat", ""), "")
                sub_bits = [str(hrr.get("Team", "") or "")]
                if bat_label:
                    sub_bits.append(f"Bats {bat_label}")
                hrd_cards.append(_mc_card(
                    rank=int(hrr.get("#")) if hrr.get("#") is not None else None,
                    name=hrr.get("Batter", ""),
                    sub=" · ".join(bit for bit in sub_bits if bit),
                    score=None,
                    score_label="",
                    tiers=[tier],
                    chips_html='<div class="mc-grid2">' + "".join(chips) + "</div>",
                    foot_html=foot,
                ))
            st.markdown(
                MOBILE_CARDS_CSS +
                '<div class="mc-mobile mc-always"><div class="mc-grid">'
                + "".join(hrd_cards) +
                '</div></div>',
                unsafe_allow_html=True,
            )
            st.download_button(
                f"⬇️ Download HRs for {hr_date_iso} CSV",
                out_hr.to_csv(index=False),
                file_name=f"hrs_{hr_date_iso}.csv",
                mime="text/csv",
                width='stretch',
            )
    st.caption(
        "Source: MLB StatsAPI live game feed (allPlays · eventType=home_run). "
        "Distance / exit velocity come from the in-play hitData payload when "
        "Statcast publishes it — older games or non-tracked parks may show '—'."
    )

# ============== Day vs Night HR tab ==============
if _render_day_night:
 with tab_day_night:
    st.markdown(
        '<div style="margin: 4px 0 12px 0; color:#475569; font-size:0.92rem;">'
        '☀️🌙 <b>Day vs Night HR splits</b> — season-long Day Games vs Night '
        'Games leaderboards for hitters, pulled from the official MLB StatsAPI '
        '(<code>statSplits · sitCodes=d,n</code>). Built for prop research and '
        'slate-targeting: when the slate skews heavy day-game or heavy night, '
        'pivot to the hitters with a real edge in that lighting.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ----- Filters row -----
    _today_ct = today_ct()
    # Use the same season convention as scripts/refresh_savant.py: before
    # mid-March, the current MLB season hasn't started so default to the
    # prior year (which has a full split sample).
    _default_season = (
        _today_ct.year - 1
        if (_today_ct.month < 3 or (_today_ct.month == 3 and _today_ct.day < 15))
        else _today_ct.year
    )
    _season_choices = list(range(_today_ct.year, 2014, -1))
    if _default_season not in _season_choices:
        _season_choices.insert(0, _default_season)

    f1, f2, f3, f4 = st.columns([1.1, 1.3, 1.3, 1.3])
    with f1:
        dn_season = st.selectbox(
            "Season",
            _season_choices,
            index=_season_choices.index(_default_season),
            key="dn_season",
        )
    with f2:
        dn_view = st.radio(
            "View",
            ["Today's Slate Targets", "Day Games", "Night Games",
             "Side-by-Side", "Biggest Splits"],
            index=0,
            horizontal=False,
            key="dn_view",
            help=(
                "Today's Slate Targets ranks only hitters on teams playing the "
                "selected slate date, scored by each game's actual day/night "
                "bucket. The other views are season-wide leaderboards."
            ),
        )
    with f3:
        dn_min_pa = st.slider(
            "Min PA (in that split)",
            min_value=10, max_value=300, value=40, step=5,
            key="dn_min_pa",
            help=(
                "PA denominator in the chosen split. Day-game PA samples are "
                "smaller than night, so 40 PA is a reasonable floor for early "
                "season; bump to 100+ for stable rate-stat reads."
            ),
        )
    with f4:
        dn_top_n = st.slider(
            "Show Top N",
            min_value=10, max_value=200, value=40, step=5,
            key="dn_top_n",
        )

    # ----- Pull splits -----
    with st.spinner(f"Pulling Day/Night HR splits for {int(dn_season)}…"):
        try:
            dn_df = get_day_night_hr_splits(int(dn_season))
        except Exception as _dn_e:
            dn_df = pd.DataFrame()
            st.warning(f"Couldn't pull Day/Night splits: {_dn_e}")

    if dn_df is None or dn_df.empty:
        st.info(
            "No Day/Night splits available right now — could be a transient MLB "
            "StatsAPI hiccup, or the season hasn't started. Try refreshing the "
            "page or selecting a prior season."
        )
    else:
        # Optional team + player search filters
        _teams = sorted([t for t in dn_df["team_abbr"].dropna().unique() if t])
        g1, g2 = st.columns([1.2, 2])
        with g1:
            dn_team = st.selectbox(
                "Team filter",
                ["All teams"] + _teams,
                index=0,
                key="dn_team",
            )
        with g2:
            dn_name_q = st.text_input(
                "Filter by player name (optional)",
                value="",
                key="dn_name_filter",
                placeholder="e.g. Judge, Ohtani, Soto",
            ).strip()

        view_df = dn_df.copy()
        if dn_team != "All teams":
            view_df = view_df[view_df["team_abbr"] == dn_team]
        if dn_name_q:
            try:
                view_df = view_df[view_df["player"].str.contains(
                    dn_name_q, case=False, na=False
                )]
            except Exception:
                pass
        if "player_id" in view_df.columns:
            try:
                view_df["bat_side"] = view_df["player_id"].apply(
                    lambda pid: lookup_batter_stance(player_id=pid)
                )
            except Exception:
                view_df["bat_side"] = ""

        # Helpers used by all sub-views below.
        def _fmt_rate(v, digits=3):
            try:
                return f"{float(v):.{digits}f}".lstrip("0") if v is not None and pd.notna(v) else "—"
            except Exception:
                return "—"

        def _fmt_pct(v):
            try:
                return f"{float(v)*100:.1f}%" if v is not None and pd.notna(v) else "—"
            except Exception:
                return "—"

        def _confidence(pa: int) -> str:
            """Quick sample-size label so users don't over-weight tiny denominators."""
            if pa is None or pa < 25: return "Tiny"
            if pa < 60: return "Small"
            if pa < 120: return "Medium"
            if pa < 250: return "Solid"
            return "Stable"

        # ---- Day Games leaderboard ----
        def _render_day(df_in: pd.DataFrame) -> pd.DataFrame:
            out = df_in[df_in["day_pa"] >= dn_min_pa].copy()
            out = out.sort_values(
                ["day_hr", "day_hr_rate"], ascending=[False, False], na_position="last"
            ).head(int(dn_top_n))
            disp = pd.DataFrame({
                "Player": out["player"],
                "Team": out["team_abbr"],
                "Bat": out["bat_side"].apply(format_batter_stance) if "bat_side" in out.columns else "—",
                "Day HR": out["day_hr"].astype(int),
                "Day PA": out["day_pa"].astype(int),
                "Day AB": out["day_ab"].astype(int),
                "HR/PA": out["day_hr_rate"].apply(_fmt_pct),
                "AVG": out["day_avg"].apply(_fmt_rate),
                "OPS": out["day_ops"].apply(_fmt_rate),
                "Confidence": out["day_pa"].apply(_confidence),
            }).reset_index(drop=True)
            disp.insert(0, "#", range(1, len(disp) + 1))
            return disp

        # ---- Night Games leaderboard ----
        def _render_night(df_in: pd.DataFrame) -> pd.DataFrame:
            out = df_in[df_in["night_pa"] >= dn_min_pa].copy()
            out = out.sort_values(
                ["night_hr", "night_hr_rate"], ascending=[False, False], na_position="last"
            ).head(int(dn_top_n))
            disp = pd.DataFrame({
                "Player": out["player"],
                "Team": out["team_abbr"],
                "Bat": out["bat_side"].apply(format_batter_stance) if "bat_side" in out.columns else "—",
                "Night HR": out["night_hr"].astype(int),
                "Night PA": out["night_pa"].astype(int),
                "Night AB": out["night_ab"].astype(int),
                "HR/PA": out["night_hr_rate"].apply(_fmt_pct),
                "AVG": out["night_avg"].apply(_fmt_rate),
                "OPS": out["night_ops"].apply(_fmt_rate),
                "Confidence": out["night_pa"].apply(_confidence),
            }).reset_index(drop=True)
            disp.insert(0, "#", range(1, len(disp) + 1))
            return disp

        # ---- Biggest split-edge leaderboard ----
        def _render_edges(df_in: pd.DataFrame, day_bias: bool) -> pd.DataFrame:
            # Require both denominators to clear min_pa so the edge is meaningful.
            out = df_in[
                (df_in["day_pa"] >= dn_min_pa) & (df_in["night_pa"] >= dn_min_pa)
            ].copy()
            if day_bias:
                out = out.sort_values("split_edge_hr_rate", ascending=False, na_position="last")
            else:
                out = out.sort_values("split_edge_hr_rate", ascending=True, na_position="last")
            out = out.head(int(dn_top_n))
            disp = pd.DataFrame({
                "Player": out["player"],
                "Team": out["team_abbr"],
                "Bat": out["bat_side"].apply(format_batter_stance) if "bat_side" in out.columns else "—",
                "Day HR": out["day_hr"].astype(int),
                "Day PA": out["day_pa"].astype(int),
                "Day HR/PA": out["day_hr_rate"].apply(_fmt_pct),
                "Night HR": out["night_hr"].astype(int),
                "Night PA": out["night_pa"].astype(int),
                "Night HR/PA": out["night_hr_rate"].apply(_fmt_pct),
                "Edge (Day − Night)": out["split_edge_hr_rate"].apply(
                    lambda v: f"{(v or 0)*100:+.2f} pp" if pd.notna(v) else "—"
                ),
            }).reset_index(drop=True)
            disp.insert(0, "#", range(1, len(disp) + 1))
            return disp

        # ===== Render the selected view =====
        st.markdown(
            f'<div class="section-title">🏆 {dn_view} · {int(dn_season)}</div>',
            unsafe_allow_html=True,
        )

        if dn_view == "Day Games":
            table = _render_day(view_df)
            if table.empty:
                st.info(f"No hitters cleared the {dn_min_pa}-PA day-game floor with the current filters.")
            else:
                _df_with_cards(
                    table,
                    cards_only=True,
                    name_col="Player", sub_col="Team",
                    score_col="Day HR", score_label="Day HR",
                    chip_cols=["Day HR/PA", "Day PA", "OPS", "AVG", "HR", "PA"],
                    foot_cols=["Team", "Confidence"],
                )
                st.download_button(
                    "⬇️ Download Day HR leaderboard (CSV)",
                    table.to_csv(index=False),
                    file_name=f"day_hr_{int(dn_season)}_min{int(dn_min_pa)}pa.csv",
                    mime="text/csv",
                    width='stretch',
                )

        elif dn_view == "Night Games":
            table = _render_night(view_df)
            if table.empty:
                st.info(f"No hitters cleared the {dn_min_pa}-PA night-game floor with the current filters.")
            else:
                _df_with_cards(
                    table,
                    cards_only=True,
                    name_col="Player", sub_col="Team",
                    score_col="Night HR", score_label="Night HR",
                    chip_cols=["Night HR/PA", "Night PA", "OPS", "AVG", "HR", "PA"],
                    foot_cols=["Team", "Confidence"],
                )
                st.download_button(
                    "⬇️ Download Night HR leaderboard (CSV)",
                    table.to_csv(index=False),
                    file_name=f"night_hr_{int(dn_season)}_min{int(dn_min_pa)}pa.csv",
                    mime="text/csv",
                    width='stretch',
                )

        elif dn_view == "Side-by-Side":
            # On phones, stack vertically (handled by global CSS); on desktop,
            # the two st.columns(2) calls remain side-by-side.
            c_left, c_right = st.columns(2)
            with c_left:
                st.markdown(
                    '<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">☀️ Day Games</div>',
                    unsafe_allow_html=True,
                )
                d_tbl = _render_day(view_df)
                if d_tbl.empty:
                    st.info("No day-game qualifiers.")
                else:
                    _df_with_cards(
                        d_tbl,
                        cards_only=True,
                        name_col="Player", sub_col="Team",
                        score_col="Day HR", score_label="Day HR",
                        chip_cols=["Day HR/PA", "Day PA", "OPS", "AVG"],
                        foot_cols=["Team"],
                    )
            with c_right:
                st.markdown(
                    '<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">🌙 Night Games</div>',
                    unsafe_allow_html=True,
                )
                n_tbl = _render_night(view_df)
                if n_tbl.empty:
                    st.info("No night-game qualifiers.")
                else:
                    _df_with_cards(
                        n_tbl,
                        cards_only=True,
                        name_col="Player", sub_col="Team",
                        score_col="Night HR", score_label="Night HR",
                        chip_cols=["Night HR/PA", "Night PA", "OPS", "AVG"],
                        foot_cols=["Team"],
                    )

        elif dn_view == "Today's Slate Targets":
            # ----- Today's slate: pull the schedule for the selected slate
            # date (defaults to today). Each game's MLB-official dayNight
            # bucket decides whether a team's hitters are scored against
            # their day or night HR/PA split. -----
            try:
                _slate_dn_df = get_schedule(selected_date)
            except Exception as _slate_err:
                _slate_dn_df = pd.DataFrame()
                st.warning(
                    f"Couldn't pull the slate schedule for {selected_date}: {_slate_err}"
                )

            if _slate_dn_df is None or _slate_dn_df.empty:
                st.info(
                    f"No games on the MLB slate for **{selected_date}** "
                    "(off day, all-star break, or offseason). Pick a different "
                    "date with the 📅 Slate date control above, or switch to "
                    "one of the season-wide views."
                )
            else:
                # Build a team_abbr -> {day_night, opp_abbr, game_time_ct,
                # short_label} lookup so each hitter knows which bucket and
                # which opponent to display. A team appears at most once per
                # slate day (doubleheaders share the same dayNight bucket in
                # practice; if MLB ever splits one across buckets, the second
                # game overwrites — we surface that via a games-played count).
                team_to_game: dict = {}
                day_game_count = 0
                night_game_count = 0
                tbd_game_count = 0
                # Side -> (own probable key, opp probable key, opp probable id key)
                _side_pitcher_keys = {
                    "away_abbr": ("home_probable", "home_probable_id"),
                    "home_abbr": ("away_probable", "away_probable_id"),
                }
                for _, _g in _slate_dn_df.iterrows():
                    _dn = (_g.get("day_night") or "").lower()
                    if _dn == "day":
                        day_game_count += 1
                    elif _dn == "night":
                        night_game_count += 1
                    else:
                        tbd_game_count += 1
                    for _side, _opp in (("away_abbr", "home_abbr"),
                                        ("home_abbr", "away_abbr")):
                        _tm = _g.get(_side, "")
                        if not _tm:
                            continue
                        _opp_p_name_key, _opp_p_id_key = _side_pitcher_keys[_side]
                        team_to_game[_tm] = {
                            "day_night": _dn,
                            "opp_abbr": _g.get(_opp, ""),
                            "game_time_ct": _g.get("time_short", ""),
                            "short_label": _g.get("short_label", ""),
                            "status": _g.get("status", ""),
                            "opp_sp_name": _g.get(_opp_p_name_key, "") or "",
                            "opp_sp_id": _safe_pid(_g.get(_opp_p_id_key)),
                        }

                # Batch-fetch handedness for every distinct probable starter on
                # the slate in a single MLB StatsAPI /people call. Avoids one
                # request per displayed hitter and caches the result for an
                # hour. Missing/TBD pitchers fall through as "". IDs sourced
                # from a DataFrame may be NaN/blank/str — _safe_pid normalizes.
                _opp_pid_set = set()
                for g in team_to_game.values():
                    _pid_val = _safe_pid(g.get("opp_sp_id"))
                    if _pid_val:
                        _opp_pid_set.add(_pid_val)
                try:
                    _opp_hand_map = _fetch_pitcher_throws_batch(
                        tuple(sorted(_opp_pid_set))
                    )
                except Exception:
                    _opp_hand_map = {}
                for _tm, _info in team_to_game.items():
                    _pid = _safe_pid(_info.get("opp_sp_id"))
                    _info["opp_sp_hand"] = _opp_hand_map.get(_pid, "") if _pid else ""

                # Slate-overview caption — instantly tells the handicapper
                # whether to lean day or night before reading the table.
                _slate_summary_bits = []
                if day_game_count:
                    _slate_summary_bits.append(f"☀️ {day_game_count} day")
                if night_game_count:
                    _slate_summary_bits.append(f"🌙 {night_game_count} night")
                if tbd_game_count:
                    _slate_summary_bits.append(f"⏳ {tbd_game_count} TBD")
                # Count probable starters posted / TBD across the slate so
                # the user can see at a glance how much of the matchup grid
                # is grounded in real probables.
                _sp_posted = sum(
                    1 for _info in team_to_game.values()
                    if _info.get("opp_sp_name") and _info["opp_sp_name"] != "TBD"
                )
                _sp_total = len(team_to_game)
                _sp_tbd = _sp_total - _sp_posted
                st.markdown(
                    f"<div style='color:#475569;font-size:0.9rem;margin:-2px 0 8px 0;'>"
                    f"<b>Slate {selected_date}</b> · {len(_slate_dn_df)} games · "
                    f"{' · '.join(_slate_summary_bits) if _slate_summary_bits else 'no day/night data yet'} · "
                    f"🎯 {_sp_posted}/{_sp_total} probable SPs posted"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if _sp_tbd and _sp_posted < _sp_total:
                    st.info(
                        f"ℹ️ {_sp_tbd} of {_sp_total} matchups still show a "
                        f"TBD opposing starter. Opp SP Hand will fill in as "
                        f"MLB posts probables — usually 18–36 hours before "
                        f"first pitch."
                    )

                # Optional slate-only day/night filter
                _slate_dn_filter = st.radio(
                    "Bucket",
                    ["All slate games", "Day games only", "Night games only"],
                    index=0,
                    horizontal=True,
                    key="dn_slate_bucket",
                )

                # Reduce splits dataframe to hitters whose team is on the slate.
                _slate_teams = set(team_to_game.keys())
                slate_view = view_df[view_df["team_abbr"].isin(_slate_teams)].copy()

                if slate_view.empty:
                    st.info(
                        "None of the hitters in the season splits sample play "
                        "on this slate (try clearing the team/player filters, "
                        "or pick a date with games)."
                    )
                else:
                    # Annotate each hitter with their game's day/night bucket
                    # and bookkeeping columns.
                    slate_view["bucket"] = slate_view["team_abbr"].map(
                        lambda t: team_to_game.get(t, {}).get("day_night", "")
                    )
                    slate_view["opp_abbr"] = slate_view["team_abbr"].map(
                        lambda t: team_to_game.get(t, {}).get("opp_abbr", "")
                    )
                    slate_view["game_time"] = slate_view["team_abbr"].map(
                        lambda t: team_to_game.get(t, {}).get("game_time_ct", "")
                    )
                    slate_view["game"] = slate_view["team_abbr"].map(
                        lambda t: team_to_game.get(t, {}).get("short_label", "")
                    )
                    slate_view["opp_sp_name"] = slate_view["team_abbr"].map(
                        lambda t: team_to_game.get(t, {}).get("opp_sp_name", "")
                    )
                    slate_view["opp_sp_hand"] = slate_view["team_abbr"].map(
                        lambda t: team_to_game.get(t, {}).get("opp_sp_hand", "")
                    )

                    if _slate_dn_filter == "Day games only":
                        slate_view = slate_view[slate_view["bucket"] == "day"]
                    elif _slate_dn_filter == "Night games only":
                        slate_view = slate_view[slate_view["bucket"] == "night"]

                    # Score each hitter against the bucket of THEIR game:
                    #   bucket_hr / bucket_pa / bucket_hr_rate / bucket_ops
                    # Hitters in TBD-bucket games drop to "—" but still appear
                    # so the user can see the slate is incomplete.
                    def _pick(row, col_prefix):
                        b = row["bucket"]
                        if b == "day":
                            return row.get(f"day_{col_prefix}")
                        if b == "night":
                            return row.get(f"night_{col_prefix}")
                        return None

                    slate_view["bucket_hr"] = slate_view.apply(
                        lambda r: _pick(r, "hr"), axis=1
                    )
                    slate_view["bucket_pa"] = slate_view.apply(
                        lambda r: _pick(r, "pa"), axis=1
                    )
                    slate_view["bucket_hr_rate"] = slate_view.apply(
                        lambda r: _pick(r, "hr_rate"), axis=1
                    )
                    slate_view["bucket_ops"] = slate_view.apply(
                        lambda r: _pick(r, "ops"), axis=1
                    )

                    # Apply Min PA floor against the bucket-specific denominator
                    # only when the bucket is known (TBD bucket rows skip the
                    # floor so they don't disappear silently).
                    qualified = slate_view[
                        (slate_view["bucket"].isin(("day", "night")))
                        & (slate_view["bucket_pa"].fillna(0) >= dn_min_pa)
                    ].copy()

                    if qualified.empty:
                        st.info(
                            f"No slate hitters cleared the {dn_min_pa}-PA "
                            f"bucket floor. Lower the Min PA slider to widen "
                            f"the pool (early-season day-game samples are tiny)."
                        )
                    else:
                        # Simple, transparent target score. HR/PA is the
                        # backbone; OPS gives a small contact-quality nudge so
                        # a 2-HR / 200-PA grinder doesn't outrank a 7-HR /
                        # 200-PA slugger. Confidence weight scales the OPS
                        # bonus by sample size so tiny-PA outliers don't fly
                        # to the top.
                        def _score(row):
                            hr_rate = row.get("bucket_hr_rate") or 0.0
                            ops = row.get("bucket_ops") or 0.0
                            pa = row.get("bucket_pa") or 0
                            conf_w = min(1.0, float(pa) / 200.0)
                            return (hr_rate * 100.0) + (ops * 4.0 * conf_w)

                        qualified["target_score"] = qualified.apply(_score, axis=1)
                        qualified = qualified.sort_values(
                            ["target_score", "bucket_hr"],
                            ascending=[False, False],
                            na_position="last",
                        ).head(int(dn_top_n))

                        def _fmt_opp_sp(name: str) -> str:
                            n = (name or "").strip()
                            if not n or n.upper() == "TBD":
                                return "TBD"
                            return n

                        def _fmt_matchup(hand: str) -> str:
                            h = (hand or "").upper()
                            if h == "R":
                                return "vs RHP"
                            if h == "L":
                                return "vs LHP"
                            return "TBD"

                        disp = pd.DataFrame({
                            "Player": qualified["player"],
                            "Team": qualified["team_abbr"],
                            "Bat": qualified["bat_side"].apply(format_batter_stance) if "bat_side" in qualified.columns else "—",
                            "Opp": qualified["opp_abbr"].apply(
                                lambda v: f"vs {v}" if v else "—"
                            ),
                            "Opp SP": qualified["opp_sp_name"].apply(_fmt_opp_sp),
                            "Opp SP Hand": qualified["opp_sp_hand"].apply(
                                lambda h: format_pitcher_hand(h)
                            ),
                            "Matchup": qualified["opp_sp_hand"].apply(_fmt_matchup),
                            "Game Time (CT)": qualified["game_time"].fillna("—"),
                            "Bucket": qualified["bucket"].map(
                                {"day": "☀️ Day", "night": "🌙 Night"}
                            ).fillna("—"),
                            "HR": qualified["bucket_hr"].fillna(0).astype(int),
                            "PA": qualified["bucket_pa"].fillna(0).astype(int),
                            "HR/PA": qualified["bucket_hr_rate"].apply(_fmt_pct),
                            "OPS": qualified["bucket_ops"].apply(_fmt_rate),
                            "Confidence": qualified["bucket_pa"].apply(_confidence),
                            "Target Score": qualified["target_score"].apply(
                                lambda v: f"{float(v):.2f}" if pd.notna(v) else "—"
                            ),
                        }).reset_index(drop=True)
                        disp.insert(0, "#", range(1, len(disp) + 1))

                        # ----- Quick-look summary metrics -----
                        # Surface the top overall target plus the best day-game
                        # and best night-game target so the handicapper sees
                        # the headline plays without scrolling the table.
                        _top_overall = qualified.iloc[0] if len(qualified) else None
                        _day_qual = qualified[qualified["bucket"] == "day"]
                        _night_qual = qualified[qualified["bucket"] == "night"]
                        _top_day = _day_qual.iloc[0] if len(_day_qual) else None
                        _top_night = _night_qual.iloc[0] if len(_night_qual) else None

                        def _fmt_top(row) -> str:
                            if row is None:
                                return "—"
                            try:
                                return (
                                    f"{row['player']} ({row['team_abbr']}) · "
                                    f"{float(row['target_score']):.2f}"
                                )
                            except Exception:
                                return "—"

                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric(
                            "Qualified hitters",
                            f"{len(qualified)}",
                            help=(
                                f"Hitters from {len(_slate_teams)} slate teams "
                                f"clearing the {dn_min_pa}-PA bucket floor."
                            ),
                        )
                        m2.metric(
                            "🏆 Top target",
                            _fmt_top(_top_overall),
                            help="Highest Target Score across the filtered slate.",
                        )
                        m3.metric(
                            "☀️ Top day target",
                            _fmt_top(_top_day),
                            help="Best target playing a day game today.",
                        )
                        m4.metric(
                            "🌙 Top night target",
                            _fmt_top(_top_night),
                            help="Best target playing a night game today.",
                        )

                        st.markdown(
                            '<div style="font-weight:800;color:#0f172a;margin:4px 0 6px 0;">'
                            '🎯 Slate-locked HR targets — ranked by each hitter\'s split '
                            'against tonight\'s lighting</div>',
                            unsafe_allow_html=True,
                        )
                        _df_with_cards(
                            disp,
                            cards_only=True,
                            name_col="Player", sub_col="Team",
                            score_col="Target Score", score_label="Target",
                            chip_cols=["HR/PA", "PA", "HR", "OPS", "Bucket", "Confidence"],
                            foot_cols=["Game", "Opp SP", "Opp SP Hand", "Matchup"],
                        )
                        st.download_button(
                            "⬇️ Download Slate HR Targets (CSV)",
                            disp.to_csv(index=False),
                            file_name=(
                                f"slate_day_night_hr_targets_"
                                f"{selected_date}.csv"
                            ),
                            mime="text/csv",
                            width='stretch',
                        )

                        st.caption(
                            "**Target Score** = `HR/PA × 100 + OPS × 4 × min(1, PA/200)`. "
                            "HR/PA is the backbone (the prop-friendly rate); "
                            "OPS adds a small contact-quality nudge weighted by "
                            "PA so tiny-sample sluggers don't outrank stable bats. "
                            "**Opp SP / Opp SP Hand / Matchup** come from the "
                            "MLB schedule's probable pitchers + the StatsAPI "
                            "/people endpoint for throwing hand (batch-fetched "
                            "and cached for an hour). Bucket auto-locks to each "
                            "game's MLB-official day/night classification. "
                            "Hitters on TBD-bucket games are hidden until MLB "
                            "posts the start time; TBD pitchers show as `TBD`."
                        )

        else:  # "Biggest Splits"
            st.caption(
                "Edge = HR/PA in day games **minus** HR/PA in night games. "
                "Positive = day-bias hitter (target on early/afternoon slates); "
                "negative = night-bias hitter (target on prime-time slates). "
                "Both PA denominators must clear the Min PA floor."
            )
            t_day = _render_edges(view_df, day_bias=True)
            t_night = _render_edges(view_df, day_bias=False)
            c_left, c_right = st.columns(2)
            with c_left:
                st.markdown(
                    '<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">☀️ Day-bias hitters (Day HR/PA &gt; Night HR/PA)</div>',
                    unsafe_allow_html=True,
                )
                if t_day.empty:
                    st.info("No qualifiers with both PA denominators above the floor.")
                else:
                    _df_with_cards(
                        t_day,
                        cards_only=True,
                        name_col="Player", sub_col="Team",
                        score_col="Edge (Day − Night)", score_label="Edge",
                        chip_cols=["Day HR", "Day PA", "Day HR/PA",
                                   "Night HR", "Night PA", "Night HR/PA"],
                        foot_cols=["Team"],
                    )
            with c_right:
                st.markdown(
                    '<div style="font-weight:800;color:#0f172a;margin-bottom:6px;">🌙 Night-bias hitters (Night HR/PA &gt; Day HR/PA)</div>',
                    unsafe_allow_html=True,
                )
                if t_night.empty:
                    st.info("No qualifiers with both PA denominators above the floor.")
                else:
                    _df_with_cards(
                        t_night,
                        cards_only=True,
                        name_col="Player", sub_col="Team",
                        score_col="Edge (Day − Night)", score_label="Edge",
                        chip_cols=["Night HR", "Night PA", "Night HR/PA",
                                   "Day HR", "Day PA", "Day HR/PA"],
                        foot_cols=["Team"],
                    )

        # Footer caption — methodology so the user trusts the numbers.
        st.caption(
            "**Methodology:** all hitters with ≥1 PA appear in the source pull "
            "(playerPool=All). HR/PA uses plate appearances as the denominator "
            "— the most prop-friendly rate. *Confidence* maps day-side PA: "
            "Tiny &lt;25 · Small &lt;60 · Medium &lt;120 · Solid &lt;250 · Stable ≥250. "
            "Day-game samples are always smaller than night, so trust HR rate "
            "more once a hitter clears ~100 day PA. "
            "Source: MLB StatsAPI <code>/stats?stats=statSplits&sitCodes=d,n</code>. "
            "Cached for 6 hours."
        )

# ============== Injuries tab ==============
if _render_injuries:
 with tab_injuries:
    st.markdown(
        '<div style="margin: 4px 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Injured List + day-to-day players for both teams. Pulled live from MLB StatsAPI '
        '(updated hourly). Status legend: '
        '<span style="background:#7f1d1d;color:#fecaca;border-radius:999px;padding:1px 8px;font-size:0.75rem;font-weight:800;">60-Day IL</span> '
        '<span style="background:#991b1b;color:#fecaca;border-radius:999px;padding:1px 8px;font-size:0.75rem;font-weight:800;">15-Day IL</span> '
        '<span style="background:#b45309;color:#fde68a;border-radius:999px;padding:1px 8px;font-size:0.75rem;font-weight:800;">10-Day IL</span> '
        '<span style="background:#1e3a8a;color:#bfdbfe;border-radius:999px;padding:1px 8px;font-size:0.75rem;font-weight:800;">DTD</span>'
        '</div>', unsafe_allow_html=True)
    try:
        away_team_id = game_row.get("away_id")
        home_team_id = game_row.get("home_id")
        away_label = game_row.get("away_team", "Away")
        home_label = game_row.get("home_team", "Home")
        away_abbr = game_row.get("away_abbr", "")
        home_abbr = game_row.get("home_abbr", "")
        col_a, col_h = st.columns(2)
        with col_a:
            with st.spinner(f"Loading {away_abbr} injuries…"):
                away_inj = get_team_injuries(away_team_id, away_label)
            st.markdown(
                render_team_injury_panel(away_label, away_abbr, away_inj),
                unsafe_allow_html=True,
            )
        with col_h:
            with st.spinner(f"Loading {home_abbr} injuries…"):
                home_inj = get_team_injuries(home_team_id, home_label)
            st.markdown(
                render_team_injury_panel(home_label, home_abbr, home_inj),
                unsafe_allow_html=True,
            )
        st.caption(
            "Source: MLB StatsAPI roster + transactions. Detail text comes from the most recent IL transaction "
            "and may be terse. Refresh the app to re-pull (cached 1 hour)."
        )
    except Exception as _inj_e:
        st.warning(f"Couldn't load injuries: {_inj_e}")

# ----- Data status -----
def _render_data_status_table() -> str:
    """Render the full per-source freshness table as styled HTML."""
    if not DATA_SOURCES:
        return '<div style="color:#64748b;">No data-source telemetry recorded yet.</div>'

    css = (
        "<style>"
        ".ds-tbl { width:100%; border-collapse: separate; border-spacing:0; "
        "  font-size:.92rem; background:#0b1220; border-radius:12px; overflow:hidden; "
        "  box-shadow: 0 2px 10px rgba(0,0,0,.4); margin: 6px 0 4px 0; "
        "  border:1px solid rgba(255,255,255,.07); }"
        ".ds-tbl th { background:#3b1f6b; color:#fcd34d; text-align:left; "
        "  font-weight:800; padding:9px 10px; letter-spacing:.03em; font-size:.78rem; "
        "  text-transform:uppercase; }"
        ".ds-tbl td { padding:8px 10px; border-bottom:1px solid rgba(255,255,255,.06); "
        "  color:#e2e8f0; vertical-align: top; }"
        ".ds-tbl tr:nth-child(even) td { background:rgba(255,255,255,.03); }"
        ".ds-pill { display:inline-block; padding:3px 9px; border-radius:999px; "
        "  font-weight:800; font-size:.74rem; letter-spacing:.04em; "
        "  white-space:nowrap; }"
        ".ds-detail { color:#94a3b8; font-size:.82rem; line-height:1.35; }"
        "</style>"
    )
    rows_html = []
    # Stable order: live first, then fallback, stale, error, unconfigured.
    order = {"live": 0, "fallback": 1, "stale": 2, "error": 3, "unconfigured": 4}
    items = sorted(
        DATA_SOURCES.items(),
        key=lambda kv: (order.get(kv[1].get("status", ""), 9), kv[1].get("label", kv[0]))
    )
    for key, info in items:
        bg, fg, txt = status_pill(info.get("status", ""))
        pill = (f'<span class="ds-pill" style="background:{bg};color:{fg};">{txt}</span>')
        age = source_age_minutes(key)
        age_str = fmt_age(age) if age is not None else "—"
        rows_html.append(
            "<tr>"
            f'<td><b>{info.get("label", key)}</b></td>'
            f'<td>{pill}</td>'
            f'<td class="ds-detail">{age_str}</td>'
            f'<td class="ds-detail">{info.get("detail", "") or "—"}</td>'
            "</tr>"
        )
    return css + (
        '<table class="ds-tbl">'
        '<thead><tr><th>Source</th><th>Status</th><th>Last fetched</th>'
        '<th>Detail</th></tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table>'
    )


render_install_help_expander(expanded=False)

with st.expander("📊 Data status & sources", expanded=False):
    c1, c2, c3 = st.columns(3)
    with c1: st.metric("Batters loaded", len(batters_df))
    with c2: st.metric("Pitchers loaded", len(pitchers_df))
    with c3:
        live_n = sum(1 for v in DATA_SOURCES.values() if v.get("status") == "live")
        st.metric("Live sources", f"{live_n}/{len(DATA_SOURCES)}")
    st.markdown(_render_data_status_table(), unsafe_allow_html=True)
    st.caption(
        "Status legend: **LIVE** = fresh fetch this session · **FALLBACK** = using "
        "prior-season or projected data because the live feed is not yet usable · "
        "**STALE** = data older than its refresh budget · **ERROR** = fetch failed · "
        "**OFF** = optional source (e.g. odds API) not configured. Use the 🔄 Refresh "
        "data button up top to force a re-pull."
    )
    st.markdown("**Source URLs:**")
    for label, url in CSV_URLS.items():
        st.markdown(f"- **{label}**: [{CSV_FILES[label]}]({url})")

st.markdown(
    '<div class="footer">⚾ <b>THE MLB EDGE</b> · '
    'Powered by Baseball Savant + MLB StatsAPI + Open-Meteo · '
    'Tiers: Elite 🟢 (≥130) · Strong 🟢 (110-129) · OK 🟡 (95-109) · Avoid 🔴 (&lt;95) · '
    'Heatmaps: Green = Strong · Yellow = OK · Red = Avoid · For research purposes only.</div>',
    unsafe_allow_html=True,
)
