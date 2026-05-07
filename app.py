# app version: 2026-04-29-rbi2-redeploy
import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
import unicodedata
import urllib.parse
import io
import os
import base64
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Streamlit Cloud servers run in UTC, so date.today() flips at 7pm CT (8pm CDT)
# and breaks the slate-date picker for late-evening users. Always anchor
# "today" to America/Chicago so the date matches when MLB games are played.
MLB_TZ = ZoneInfo("America/Chicago")

def today_ct() -> date:
    """Return today's date in Central Time (matches the MLB slate day)."""
    return datetime.now(MLB_TZ).date()

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
LOGO_FILENAME = "mrbets850_logo.jpg"

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
    page_title="MrBets850 — MLB Edge",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ===========================================================================
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
            f'background:#f1f5f9;border-radius:8px;padding:2px 8px;'
            f'font-size:.74rem;color:#0f172a;">'
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
# Global styles  (the look you wanted: horizontal carousel, heatmap tables)
# ===========================================================================
st.markdown("""
<style>
/* ---- base ---- */
/* Wide PC layout: bounded max-width (~1600px) centered, with neutral
   background outside the content area so no green tint bleeds through. */
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
    background: #f1f5f9 !important;
    background-color: #f1f5f9 !important;
    background-image: none !important;
}
/* Belt-and-suspenders: a body::before strip that paints any uncovered
   pixel behind the iframe content. Sits at z-index:-1 so it never
   overlaps actual UI. */
body::before {
    content: "";
    position: fixed; inset: 0;
    background: #f1f5f9;
    z-index: -1;
    pointer-events: none;
}
@media (min-width: 1200px) {
    .block-container { padding-left: 2rem; padding-right: 2rem; }
}
@media (min-width: 1600px) {
    .block-container { padding-left: 2.5rem; padding-right: 2.5rem; }
}
@media (max-width: 640px) {
    .block-container { padding-left: 0.6rem; padding-right: 0.6rem; }
}
/* Let wide tables scroll horizontally instead of being clipped */
[data-testid="stDataFrame"], [data-testid="stTable"] { width: 100% !important; }
[data-testid="stDataFrame"] > div { overflow-x: auto !important; }
.stDataFrame, .stTable { width: 100% !important; }
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, Arial, sans-serif;
    color: #0f172a;
    font-size: 16px;
}
/* ---- desktop readability: bump font sizes on wider screens ---- */
@media (min-width: 1100px) {
    html, body, [class*="css"] { font-size: 17px; }
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] li { font-size: 1rem; line-height: 1.55; }
    [data-testid="stWidgetLabel"], label { font-size: 0.98rem !important; }
    .stTabs [data-baseweb="tab"] { font-size: 1rem !important; padding: 11px 18px !important; }
    [data-testid="stRadio"] label { font-size: 1rem !important; }
    [data-testid="stDataFrame"] { font-size: 0.98rem; }
    [data-testid="stDataFrame"] td,
    [data-testid="stDataFrame"] th { font-size: 0.95rem !important; }
    [data-testid="stTable"] td, [data-testid="stTable"] th { font-size: 0.95rem !important; }
    .hrs-table td, .hrs-table th,
    .tg-table td,  .tg-table th,
    .sp-table td,  .sp-table th { font-size: 0.95rem !important; }
}
@media (min-width: 1500px) {
    html, body, [class*="css"] { font-size: 18px; }
    .stTabs [data-baseweb="tab"] { font-size: 1.05rem !important; padding: 12px 20px !important; }
    [data-testid="stDataFrame"] td,
    [data-testid="stDataFrame"] th { font-size: 1rem !important; }
    .hrs-table td, .hrs-table th,
    .tg-table td,  .tg-table th,
    .sp-table td,  .sp-table th { font-size: 1rem !important; }
}

/* hide empty markdown wrappers */
.element-container:has(> .stMarkdown:only-child > [data-testid="stMarkdownContainer"]:empty) { display: none; }

/* ---- brand bar ---- */
.brand-bar {
    display: flex; align-items: center; justify-content: space-between; gap: 18px;
    padding: 14px 22px; border-radius: 18px;
    background: linear-gradient(110deg, #04130b 0%, #0d2a18 55%, #133a23 100%);
    box-shadow: 0 12px 28px rgba(5,20,12,0.32);
    border: 1px solid rgba(250,204,21,0.35);
    margin-bottom: 14px; color: #fff;
}
.brand-bar .brand-left { display:flex; align-items:center; gap: 14px; min-width: 0; }
.brand-bar .brand-logo {
    width: 64px; height: 64px; flex: 0 0 64px;
    border-radius: 14px; background: #0a1f12;
    border: 1px solid rgba(250,204,21,0.45);
    box-shadow: 0 4px 14px rgba(0,0,0,0.35);
    object-fit: contain; padding: 4px;
}
.brand-name { font-size: 1.55rem; font-weight: 900; letter-spacing: 0.04em; line-height: 1.05;
    color: #facc15; text-shadow: 0 1px 0 rgba(0,0,0,0.35); }
.brand-tag  { color: #fde68a; font-size: 0.78rem; letter-spacing: 0.18em; text-transform: uppercase; font-weight: 700; }
.brand-meta { text-align: right; color: #fde68a; font-size: 0.92rem; font-weight: 700; }
.brand-meta .big { font-size: 1.1rem; color: #fff; font-weight: 800; }
@media (max-width: 600px) {
    .brand-bar .brand-logo { width: 48px; height: 48px; flex-basis: 48px; }
    .brand-name { font-size: 1.2rem; }
}

/* ---- horizontal game carousel ---- */
.carousel-wrap {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 18px;
    padding: 10px 6px;
    margin-bottom: 14px;
    box-shadow: 0 4px 12px rgba(15,23,42,0.05);
    overflow: hidden;
}
.carousel-strip {
    display: flex;
    gap: 10px;
    overflow-x: auto;
    scroll-behavior: smooth;
    padding: 6px 12px 10px;
    scrollbar-width: thin;
}
.carousel-strip::-webkit-scrollbar { height: 8px; }
.carousel-strip::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 8px; }
.carousel-strip::-webkit-scrollbar-track { background: transparent; }

.game-pill {
    flex: 0 0 auto;
    min-width: 188px;
    background: #f8fafc;
    border: 2px solid #e2e8f0;
    border-radius: 16px;
    padding: 10px 14px;
    text-align: center;
    cursor: pointer;
    transition: all 0.15s ease;
    display: block;
    text-decoration: none !important;
    color: inherit !important;
    user-select: none;
}
.game-pill:hover { border-color: #94a3b8; background: #eef2f7; transform: translateY(-1px); }
.game-pill.active {
    border-color: #1d4ed8;
    background: linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
    box-shadow: 0 4px 14px rgba(29,78,216,0.22);
}
.game-pill .logos {
    display: flex; align-items: center; justify-content: center; gap: 6px;
    margin-bottom: 4px;
}
.game-pill .logos img { width: 38px; height: 38px; object-fit: contain; }
.game-pill .at { color: #64748b; font-weight: 800; font-size: 1.05rem; }
.game-pill .matchup-text {
    display: block;
    color: #0f172a; font-weight: 800; font-size: 0.85rem; letter-spacing: 0.02em;
}
.game-pill .time {
    display: block;
    color: #64748b; font-size: 0.76rem; font-weight: 700; margin-top: 2px;
    letter-spacing: 0.02em;
}

/* ---- section card ---- */
.section-card {
    background: #fff; border: 1px solid #e2e8f0; border-radius: 18px;
    padding: 16px 18px; margin-bottom: 12px;
    box-shadow: 0 4px 12px rgba(15,23,42,0.04);
}
.section-card.dark {
    background: linear-gradient(180deg, #0b1437 0%, #0a2350 100%);
    border: 1px solid #1e3a8a; color:#fff;
}
.section-title, .section-title-lg {
    /* Vibrant green that pops on BOTH light and dark backgrounds.
       #16a34a = tailwind green-600, readable on white; the text-shadow
       gives it a soft halo so it stays crisp on dark too. */
    color: #16a34a; font-size: 1.1rem; font-weight: 900;
    margin: 0 0 10px; letter-spacing: -0.01em;
    display: flex; align-items: center; gap: 10px;
    text-shadow: 0 1px 0 rgba(255,255,255,0.35), 0 0 1px rgba(0,0,0,0.15);
}
.section-title img, .section-title-lg img { width: 28px; height: 28px; }

/* ---- big game header card (above the tabs) ---- */
.game-header {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 12px;
}
.game-header .matchup-display {
    display: flex; align-items: center; gap: 12px;
}
.game-header .matchup-display img { width: 56px; height: 56px; object-fit: contain; }
.game-header .matchup-display .vs {
    color: #94b8ff; font-weight: 700; font-size: 1.4rem; padding: 0 6px;
}
.game-header .team-abbr { color:#fff; font-size: 1.8rem; font-weight: 900; letter-spacing: 0.02em; }
.game-header .meta { color: #c7dafe; font-size: 0.92rem; font-weight: 600; margin-top: 4px; }
.game-header .probables {
    text-align: right; color: #fff; font-size: 0.95rem; font-weight: 800;
}
.game-header .probables .label {
    color: #c7dafe; font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 800;
}
.game-header .probables .hand { color: #94b8ff; }

.kpi-row { display:flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
.kpi {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 12px; padding: 7px 11px; color: #fff; font-size: 0.88rem; font-weight: 700;
}
.kpi .k {
    color: #c7dafe; font-size: 0.66rem; letter-spacing: 0.1em; text-transform: uppercase;
    display:block; margin-bottom: 2px; font-weight: 700;
}

/* ---- tier pills ---- */
.tier {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-weight: 800; font-size: 0.74rem; letter-spacing: 0.04em; text-transform: uppercase;
    border: 1px solid transparent;
}
.tier-elite   { background:#dcfce7; color:#14532d; border-color:#86efac; }
.tier-strong  { background:#d1fae5; color:#065f46; border-color:#6ee7b7; }
.tier-ok      { background:#fef3c7; color:#78350f; border-color:#fcd34d; }
.tier-avoid   { background:#fee2e2; color:#7f1d1d; border-color:#fca5a5; }
.tier-neutral { background:#e2e8f0; color:#334155; border-color:#cbd5e1; }

/* ---- lineup table headers row (with team logo) ---- */
.lineup-banner {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 14px;
    background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
    border: 1px solid #e2e8f0;
    border-radius: 14px 14px 0 0;
    margin-top: 10px;
}
.lineup-banner img { width: 36px; height: 36px; }
.lineup-banner .lineup-title { font-weight: 900; font-size: 1.05rem; color:#0f172a; }
.lineup-banner .vs-pitcher { color: #475569; font-size: 0.92rem; font-weight: 700; margin-left: 6px; }
.lineup-banner .badge { margin-left: auto; }

/* ---- streamlit tabs styling (Matchup / Rolling / Zones / Exports) ----
   Mobile users were missing this strip because it blended into the page.
   Now: dark green band, gold border, white pills, big tap targets, and
   a fade on the right edge that hints there are more tabs to scroll. */
.stTabs { position: relative; }
.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    background: linear-gradient(180deg, #0b1f15 0%, #0f3a2e 100%);
    padding: 8px;
    border-radius: 14px;
    border: 2px solid #facc15;
    box-shadow: 0 4px 14px rgba(5, 20, 12, .25), inset 0 1px 0 rgba(250,204,21,.25);
    overflow-x: auto;
    scrollbar-width: thin;
}
/* Right-edge fade so users see there's more to swipe to.
   IMPORTANT: scope this to the tab-list only. Previously it was on
   .stTabs::after with top:0/bottom:0, which made the green fade extend
   down the FULL HEIGHT of the tab container — i.e. a vertical green
   strip running alongside every tab's content panel. That looked like
   "the whole right side of the page is tinted green". The fix: anchor
   the fade inside the [data-baseweb="tab-list"] element so it only
   covers the horizontal tab strip itself. */
.stTabs [data-baseweb="tab-list"] { position: relative; }
.stTabs [data-baseweb="tab-list"]::after {
    content: "";
    position: absolute; top: 0; right: 0; bottom: 0; width: 36px;
    pointer-events: none;
    background: linear-gradient(90deg, rgba(15,58,46,0) 0%, rgba(15,58,46,.85) 100%);
    border-radius: 0 14px 14px 0;
}
.stTabs [data-baseweb="tab"] {
    background: rgba(255,255,255,.10);
    border-radius: 10px;
    padding: 10px 16px;
    min-height: 44px;
    font-weight: 800;
    color: #fef3c7 !important;
    border: 1px solid rgba(250,204,21,.30);
    transition: all .18s ease;
    white-space: nowrap;
}
.stTabs [data-baseweb="tab"]:hover {
    background: rgba(250,204,21,.18);
    border-color: #facc15;
}
.stTabs [aria-selected="true"] {
    background: #ffffff !important;
    color: #0f3a2e !important;
    border-color: #facc15 !important;
    box-shadow: 0 0 0 2px rgba(250,204,21,.40), 0 4px 10px rgba(5,20,12,.30) !important;
    transform: translateY(-1px);
}
.stTabs [data-baseweb="tab-highlight"] { background: #facc15 !important; height: 3px !important; }

/* ---- input polish ---- */
.stButton > button {
    border-radius: 12px; font-weight: 800;
    border: 1px solid #1d4ed8; background: #1d4ed8; color: #fff; padding: 8px 16px;
}
.stButton > button:hover { background: #1e40af; border-color: #1e40af; color:#fff; }

/* ---- dataframe polish - ensures heatmap shows clearly ---- */
[data-testid="stDataFrame"] {
    border-radius: 14px; overflow: hidden;
    border: 1px solid #e2e8f0;
}

/* ---- footer ---- */
.footer {
    margin-top: 18px; padding: 12px 16px; border-radius: 12px;
    background: #f1f5f9; color: #475569; font-size: 0.82rem; text-align: center; font-weight: 600;
}

/* =========================================================================
   DARK MODE OVERRIDES
   ----------------------------------------------------------------------
   Many of the custom HTML cards in this app hard-code light backgrounds
   with dark text. When the user has Streamlit set to dark mode (system
   pref or sidebar toggle), the page background goes dark but those cards
   keep light bg/dark text — which is fine. The problem cases are:
     1. Plain text rendered by Streamlit (markdown, captions, st.info,
        labels) that inherits the dark-mode color — our hard-coded dark
        slate colors become invisible.
     2. Card text that says color: #0f172a with NO background override.
     3. Tables (.hrs-table, .tg-table, .sp-table) with light bg.
   Strategy: scope every override to Streamlit's [data-theme="dark"]
   attribute so they only fire when the app itself is rendered in dark.
   We intentionally do NOT use @media (prefers-color-scheme: dark) because
   it matches the device OS — even when Streamlit Cloud is rendering in
   light, which washes out light-mode text on phones set to dark.
   ======================================================================= */

/* Streamlit sets data-theme="dark" on the root when the user picks dark
   in app settings. All dark-mode overrides live under this selector so
   they never leak into light mode. */
[data-theme="dark"] [data-testid="stMarkdownContainer"] p,
[data-theme="dark"] [data-testid="stMarkdownContainer"] li,
[data-theme="dark"] [data-testid="stMarkdownContainer"] span,
[data-theme="dark"] [data-testid="stCaptionContainer"],
[data-theme="dark"] [data-testid="stWidgetLabel"],
[data-theme="dark"] label {
    color: #e2e8f0 !important;
}
[data-theme="dark"] .section-title, [data-theme="dark"] .section-title-lg {
    /* Brighter green on true dark theme so the title glows */
    color: #4ade80 !important;
    text-shadow: 0 0 8px rgba(74, 222, 128, 0.35) !important;
}
[data-theme="dark"] [style*="color:#475569"],
[data-theme="dark"] [style*="color: #475569"],
[data-theme="dark"] [style*="color:#64748b"],
[data-theme="dark"] [style*="color: #64748b"] {
    color: #cbd5e1 !important;
}
[data-theme="dark"] .top-tab-row {
    /* Keep the gold strip in dark mode too — matches the logo */
    background: linear-gradient(180deg, #fde68a 0%, #f59e0b 55%, #b45309 100%) !important;
    border-color: #92400e !important;
}
[data-theme="dark"] .top-tab-row .top-tab-pill {
    background: #1e293b !important;
    color: #f1f5f9 !important;
    border-color: #475569 !important;
}
[data-theme="dark"] .top-tab-row .top-tab-pill.active {
    background: linear-gradient(110deg, #04130b 0%, #0f3a2e 60%, #1d5a3f 100%) !important;
    color: #facc15 !important;
    border-color: #facc15 !important;
}
[data-theme="dark"] .hrs-table td, [data-theme="dark"] .tg-table td {
    color: #0f172a !important;
}
[data-theme="dark"] .footer {
    background: #1e293b !important;
    color: #cbd5e1 !important;
}
[data-theme="dark"] .top3-stat .lab,
[data-theme="dark"] .spc-meta,
[data-theme="dark"] .spc-bigscore .lab,
[data-theme="dark"] .spc-stat .lab {
    color: #cbd5e1 !important;
}
[data-theme="dark"] .top3-stat .val { color: #f8fafc !important; }
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
    return df

@st.cache_data(ttl=1800)
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
            rows.append({
                "game_pk": game.get("gamePk"),
                "label": f'{away_info["abbr"]} @ {home_info["abbr"]} · {game_time_ct.strftime("%-I:%M %p")}',
                "short_label": f'{away_info["abbr"]} @ {home_info["abbr"]}',
                "time_short": game_time_ct.strftime("%-I:%M %p"),
                "game_time_ct": game_time_ct.strftime("%a %b %-d · %-I:%M %p CT"),
                "game_time_utc": game_time_utc,
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

@st.cache_data(ttl=1800)
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
            f"<div style='background:#0f3a2e;border:2px solid #facc15;border-radius:14px;"
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
        f"<div style='background:#0f3a2e;border:2px solid #facc15;border-radius:14px;"
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

def find_pitcher_row(df, pitcher_name):
    if df.empty or not pitcher_name or pitcher_name == "TBD": return None
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

def build_matchup_table(lineup_df, batters_df, pitchers_df, opp_pitcher_name, weather, park_factor,
                       arsenal_b=None, arsenal_p=None, opp_pitcher_id=None):
    """The main heatmap-ready dataframe — columns mirror your reference site."""
    cols = ["Spot", "Hitter", "Team", "Matchup", "Test Score",
            "Ceiling", "Zone Fit", "Crushes", "HR Form", "kHR", "HR", "ISO", "Barrel%", "HardHit%"]
    if lineup_df.empty:
        return pd.DataFrame(columns=cols)
    p_row = find_pitcher_row(pitchers_df, opp_pitcher_name)
    opp_pitches = _build_pitcher_arsenal_set(arsenal_p, opp_pitcher_id)
    rows = []
    for _, r in lineup_df.iterrows():
        b_row = find_player_row(batters_df, r["name_key"], r["team"])
        opp_hand = r.get("opposing_pitch_hand", "")
        m   = matchup_score(b_row, p_row, r["lineup_spot"], weather, park_factor, r["bat_side"], opp_hand)
        ts  = test_score(b_row, p_row)
        cl  = ceiling_score(b_row, weather, park_factor)
        zf  = zone_fit(b_row, p_row, r["bat_side"], opp_hand)
        hrf, arrow = hr_form_pct(b_row)
        khr = k_adj_hr(b_row, p_row, cl)
        pid = r.get("player_id") if "player_id" in r.index else None
        crushes = _crushes_cell(arsenal_b, pid, opp_pitches) if arsenal_b is not None else "—"
        rows.append({
            "Spot": int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
            "Hitter": r["player_name"],
            "Team": norm_team(r["team"]),
            "Matchup": m,
            "Test Score": ts,
            "Ceiling": cl,
            "Zone Fit": zf,
            "Crushes": crushes,
            "HR Form": f"{int(hrf)}% {arrow}",
            "_HR Form Num": hrf,
            "kHR": khr,
            "HR": safe_float(b_row.get("HR") if b_row is not None else None, 0),
            "ISO": safe_float(b_row.get("ISO") if b_row is not None else None, 0.170),
            "Barrel%": safe_float(b_row.get("Barrel%") if b_row is not None else None, 8.0),
            "HardHit%": safe_float(b_row.get("HardHit%") if b_row is not None else None, 38.0),
            # carried-along (hidden) so the Top 3 cards can show MLB headshots / Bat side
            "_player_id": pid,
            "_bat_side": r["bat_side"] or "",
        })
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
    if df.empty: return df
    # Hide internal columns from display
    show_cols = [c for c in df.columns if c not in ("_HR Form Num", "_player_id", "_bat_side")]
    styler = df[show_cols].style.format({
        "Matchup": "{:.3f}", "Test Score": "{:.3f}", "Ceiling": "{:.3f}",
        "Zone Fit": "{:.3f}", "kHR": "{:.3f}",
        "ISO": "{:.3f}", "Barrel%": "{:.1f}", "HardHit%": "{:.1f}", "HR": "{:.0f}",
    })
    base_text = [
        {"selector": "th", "props": [("color", "#0f172a"), ("font-size", "12px"),
                                     ("font-weight", "800"), ("background-color", "#f1f5f9"),
                                     ("text-transform", "uppercase"), ("letter-spacing", "0.04em"),
                                     ("padding", "8px 10px")]},
        {"selector": "td", "props": [("color", "#0f172a"), ("font-size", "13px"),
                                     ("font-weight", "700"), ("padding", "6px 10px")]},
    ]
    styler = styler.set_table_styles(base_text)
    if "Matchup"    in df.columns: styler = styler.map(heat_matchup,  subset=["Matchup"])
    if "Test Score" in df.columns: styler = styler.map(heat_score,    subset=["Test Score"])
    if "Ceiling"    in df.columns: styler = styler.map(heat_score,    subset=["Ceiling"])
    if "Zone Fit"   in df.columns: styler = styler.map(heat_zone_fit, subset=["Zone Fit"])
    if "kHR"        in df.columns: styler = styler.map(heat_score,    subset=["kHR"])
    if "ISO"        in df.columns: styler = styler.map(heat_iso,      subset=["ISO"])
    if "Barrel%"    in df.columns: styler = styler.map(heat_pct,      subset=["Barrel%"])
    if "HardHit%"   in df.columns: styler = styler.map(heat_hardhit,  subset=["HardHit%"])
    return styler

def style_rolling_table(df):
    if df.empty: return df
    styler = df.style.format(precision=1)
    base_text = [
        {"selector": "th", "props": [("color", "#0f172a"), ("font-size", "12px"),
                                     ("font-weight", "800"), ("background-color", "#f1f5f9"),
                                     ("text-transform", "uppercase"), ("letter-spacing", "0.04em")]},
        {"selector": "td", "props": [("color", "#0f172a"), ("font-size", "13px"), ("font-weight", "700")]},
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
    "Test Score":   (35.0,    85.0,   False, "{:.0f}"),
    "Ceiling":      (35.0,    85.0,   False, "{:.0f}"),
    "Zone Fit":     (0.030,   0.090,  False, "{:.3f}"),
    "HR Form":      (25.0,    75.0,   False, "{:.0f}%"),
    "kHR":          (25.0,    75.0,   False, "{:.1f}"),
    "Pitches":      (200.0,   1800.0, False, "{:.0f}"),
    "BIP":          (40.0,    320.0,  False, "{:.0f}"),
    "ISO":          (0.130,   0.250,  False, "{:.3f}"),
    "xwOBA":        (0.290,   0.380,  False, "{:.3f}"),
    "xwOBAcon":     (0.330,   0.470,  False, "{:.3f}"),
    "SwStr%":       (8.0,     16.0,   True,  "{:.1f}%"),
    "PulledBrl%":   (3.0,     14.0,   False, "{:.1f}%"),
    "Brl/BIP%":     (4.0,     14.0,   False, "{:.1f}%"),
    "SweetSpot%":   (28.0,    40.0,   False, "{:.1f}%"),
    "FB%":          (28.0,    45.0,   False, "{:.1f}%"),
    "GB%":          (35.0,    50.0,   True,  "{:.1f}%"),
    "HH%":          (32.0,    48.0,   False, "{:.1f}%"),
    "LA":           (None,    None,   False, "{:.1f}°"),  # optimal-range, custom
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

def _likely_label(matchup, ceiling):
    """Synthesize a 'Likely' outcome chip from Matchup + Ceiling tiers."""
    try:
        m = float(matchup); c = float(ceiling)
    except Exception:
        return "—"
    if m >= 145 and c >= 75: return "🔥 HR"
    if m >= 130 and c >= 65: return "💪 XBH"
    if m >= 115:             return "✅ Hit"
    if m >= 100:             return "➖ Avg"
    return "❌ Tough"

def build_matchup_heatmap_board(lineup_df, batters_df, pitchers_df, opp_pitcher_name,
                                weather, park_factor,
                                arsenal_b=None, arsenal_p=None, opp_pitcher_id=None):
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
    cols = ["Spot", "Hitter", "Team", "Crushes", "Matchup", "Test Score", "Ceiling",
            "Zone Fit", "HR Form", "kHR", "Pitches", "BIP", "ISO", "xwOBA", "xwOBAcon",
            "SwStr%", "PulledBrl%", "Brl/BIP%", "SweetSpot%", "FB%", "GB%", "HH%",
            "LA", "Likely"]
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
        "LA":         _league_avg("LA",          12.0),
    }
    p_row = find_pitcher_row(pitchers_df, opp_pitcher_name)
    opp_pitches = _build_pitcher_arsenal_set(arsenal_p, opp_pitcher_id)
    rows = []
    for _, r in lineup_df.iterrows():
        b_row = find_player_row(
            batters_df, r["name_key"], r["team"],
            player_id=r.get("player_id") if "player_id" in r.index else None,
        )
        pid = r.get("player_id") if "player_id" in r.index else None
        crushes = _crushes_cell(arsenal_b, pid, opp_pitches) if arsenal_b is not None else "—"
        opp_hand = r.get("opposing_pitch_hand", "")
        m   = matchup_score(b_row, p_row, r["lineup_spot"], weather, park_factor, r["bat_side"], opp_hand)
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
        xslg    = _g("xSLG", _LG["xSLG"]) or _LG["xSLG"]
        xobp_v  = _g("xOBP", _LG["xOBP"]) or _LG["xOBP"]
        # xwOBAcon proxy = xSLG/1.7 + xOBP/3 (scales typical Statcast values
        # into the .330–.470 band shown on Savant's "xwOBA on contact" view).
        xwobacon = round(xslg * 0.55 + xobp_v * 0.45, 3) if xslg and xobp_v else None

        whiff   = _g("Whiff%", _LG["Whiff%"])
        swing   = _g("Swing%", _LG["Swing%"])
        # SwStr% = Whiff% × Swing% / 100 (rate of swinging strikes per pitch).
        if whiff is not None and swing is not None:
            swstr = round(whiff * swing / 100.0, 1)
        elif whiff is not None:
            # Whiff% alone is per-swing; approximate by multiplying league-avg swing rate.
            swstr = round(whiff * 0.47, 1)
        else:
            swstr = round(_LG["Whiff%"] * _LG["Swing%"] / 100.0, 1)

        barrel  = _g("Barrel%", _LG["Barrel%"])
        pull    = _g("Pull%", _LG["Pull%"])
        # PulledBrl% proxy = Barrel% × (Pull% / 100) — Savant doesn't expose
        # the exact pulled-barrel rate in the public CSV, but pulled barrels
        # are the most damaging contact type and this is a strong correlate.
        pulledbrl = round(barrel * pull / 100.0, 1) if (barrel is not None and pull is not None) else None
        brl_bip = barrel  # Savant's barrel_batted_rate IS Brl/BIP%

        sweet   = _g("SweetSpot%", _LG["SweetSpot%"])
        fb      = _g("FB%", _LG["FB%"])
        gb      = _g("GB%", _LG["GB%"])
        hh      = _g("HardHit%", _LG["HardHit%"])
        la      = _g("LA", _LG["LA"])

        rows.append({
            "Spot": int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
            "Hitter": r["player_name"],
            "Team": norm_team(r["team"]),
            "Crushes": crushes,
            "Matchup": m,
            "Test Score": ts,
            "Ceiling": cl,
            "Zone Fit": zf,
            "HR Form": hrf,
            "_HR Trend": hr_trend,
            "kHR": khr,
            "Pitches": round(pitches, 0) if pitches is not None else None,
            "BIP": round(bip, 0) if bip is not None else None,
            "ISO": round(iso, 3) if iso is not None else None,
            "xwOBA": round(xwoba, 3) if xwoba is not None else None,
            "xwOBAcon": xwobacon,
            "SwStr%": swstr,
            "PulledBrl%": pulledbrl,
            "Brl/BIP%": round(brl_bip, 1) if brl_bip is not None else None,
            "SweetSpot%": round(sweet, 1) if sweet is not None else None,
            "FB%": round(fb, 1) if fb is not None else None,
            "GB%": round(gb, 1) if gb is not None else None,
            "HH%": round(hh, 1) if hh is not None else None,
            "LA": round(la, 1) if la is not None else None,
            "Likely": _likely_label(m, cl),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Spot").reset_index(drop=True)
    return df

def render_matchup_heatmap_html(df):
    """Render the wide heat-map board as an HTML table that scrolls horizontally
    on mobile + desktop. The first two columns (Spot, Hitter) are sticky so
    the player stays visible while you swipe through the stat columns."""
    if df is None or df.empty:
        return '<div class="mhm-empty">Lineup not posted yet.</div>'

    # Numeric columns in display order (everything except identifiers/Likely).
    numeric_cols = [c for c in df.columns if c in HEATMAP_THRESHOLDS]
    # Spot collapses into row label; _HR Trend is carried alongside HR Form
    # for arrow rendering and is not its own column.
    display_cols = [c for c in df.columns if c not in ("Spot", "_HR Trend")]

    css = """
<style>
.mhm-wrap { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch;
  border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff;
  box-shadow: 0 2px 10px rgba(15,23,42,0.06); margin: 6px 0 14px 0; }
.mhm-wrap::-webkit-scrollbar { height: 10px; }
.mhm-wrap::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 6px; }
.mhm-wrap::-webkit-scrollbar-track { background: #f1f5f9; border-radius: 6px; }
.mhm-table { border-collapse: separate; border-spacing: 0; width: max-content; min-width: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.mhm-table th { position: sticky; top: 0; z-index: 3; background: #0f172a; color: #f8fafc;
  font-size: 0.7rem; font-weight: 800; text-transform: uppercase; letter-spacing: .04em;
  padding: 8px 10px; text-align: center; white-space: nowrap; border-bottom: 2px solid #1e293b; }
.mhm-table td { padding: 7px 10px; text-align: center; font-size: 0.82rem;
  font-weight: 700; color: #0f172a; white-space: nowrap; border-bottom: 1px solid #e2e8f0; }
.mhm-table tr:last-child td { border-bottom: none; }
.mhm-col-hitter { position: sticky; left: 0; z-index: 2; background: #ffffff;
  text-align: left !important; min-width: 170px; box-shadow: 2px 0 4px rgba(15,23,42,0.06); }
.mhm-table th.mhm-col-hitter { z-index: 4; background: #0f172a; }
.mhm-table tr:nth-child(even) td.mhm-col-hitter { background: #f8fafc; }
.mhm-hitter-name { font-weight: 800; color: #0f172a; }
.mhm-hitter-meta { font-size: 0.68rem; color: #64748b; font-weight: 700; margin-top: 1px; }
.mhm-hitter-crushes { font-size: 0.68rem; color: #be185d; font-weight: 800; margin-top: 2px;
  white-space: normal; line-height: 1.15; max-width: 220px; }
.mhm-likely { padding: 2px 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 800;
  background: #f1f5f9; color: #0f172a; display: inline-block; }
/* Missing/insufficient-sample cells: muted neutral background instead of bare
   white, so the heat map reads as one continuous board. */
.mhm-na { background-color: #cbd5e1; color: #475569; font-weight: 800; }
.mhm-empty { padding: 14px 16px; color: #64748b; font-weight: 700; font-style: italic; }
.mhm-trend { display: inline-block; margin-left: 4px; font-size: 0.78rem;
  font-weight: 900; line-height: 1; vertical-align: baseline; }
.mhm-trend-up   { color: #15803d; }
.mhm-trend-down { color: #b91c1c; }
.mhm-trend-flat { color: #475569; }
</style>
"""

    # Build header row
    header_cells = []
    for c in display_cols:
        cls = "mhm-col-hitter" if c == "Hitter" else ""
        header_cells.append(f'<th class="{cls}">{c}</th>')

    # Build body rows
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
                cells.append(
                    f'<td class="mhm-col-hitter">'
                    f'<div class="mhm-hitter-name">{spot}. {v}</div>'
                    f'<div class="mhm-hitter-meta">{team}</div>'
                    f'{crushes_html}'
                    f'</td>'
                )
                continue
            if c == "Team" or c == "Crushes":
                # Already rendered alongside Hitter — skip dedicated cell.
                continue
            if c == "Likely":
                if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "—":
                    cells.append('<td class="mhm-na">—</td>')
                else:
                    cells.append(f'<td><span class="mhm-likely">{v}</span></td>')
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

# Columns the user can sort the heat-map board by, in display order.
# "Lineup Spot" maps to the default Spot ordering (1-9) — exposed as the first
# option so the existing default behavior is preserved.
MATCHUP_SORTABLE_COLUMNS = [
    "Lineup Spot", "Hitter",
    "Matchup", "Test Score", "Ceiling", "Zone Fit", "HR Form", "kHR",
    "Pitches", "BIP", "ISO", "xwOBA", "xwOBAcon", "SwStr%", "PulledBrl%",
    "Brl/BIP%", "SweetSpot%", "FB%", "GB%", "HH%", "LA", "Likely",
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

def render_matchup_board_with_sort(board_df, key_prefix, label):
    """Render sort controls (column + direction) above a matchup heat-map
    board, then render the sorted board as colored HTML. `key_prefix` must be
    unique per board on the page so Streamlit widget state stays isolated
    (e.g. "away_NYY_BOS" vs "home_NYY_BOS").
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
    st.markdown(render_matchup_heatmap_html(sorted_df), unsafe_allow_html=True)

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
            "IP":   ip,
            "ERA":  _f(s.get("era")),
            "WHIP": _f(s.get("whip")),
            "BAA":  _f(s.get("avg")),
            "BF":   bf,
        }
        # Compute K% / BB% from raw counts since StatsAPI doesn't ship them
        # directly. Falls back to per-9 rates when batters-faced isn't published.
        try:
            so = int(s.get("strikeOuts") or 0)
            bb = int(s.get("baseOnBalls") or 0)
            if bf and bf > 0:
                out["K%"]  = round(100.0 * so / bf, 1)
                out["BB%"] = round(100.0 * bb / bf, 1)
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

    ip   = api_stats.get("IP")
    era  = api_stats.get("ERA")
    whip = api_stats.get("WHIP")
    bf   = api_stats.get("BF")

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
    "BB%":             (5.0,  11.0,   True),
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
        ".sp-wrap { overflow-x:auto; border-radius:14px; border:1px solid #e2e8f0; "
        "  background:#fff; box-shadow: 0 2px 8px rgba(15,23,42,.04); margin: 6px 0 14px 0; }"
        ".sp-table { border-collapse: separate; border-spacing:0; width:100%; "
        "  font-size: 0.86rem; color:#0f172a; font-family: inherit; }"
        ".sp-table thead th { background:#f1f5f9; color:#334155; font-weight:800; "
        "  text-align:center; padding: 10px 8px; border-bottom:1px solid #e2e8f0; "
        "  position: sticky; top: 0; z-index: 1; white-space: nowrap; }"
        ".sp-table thead th.sp-sort { color:#0f172a; }"
        ".sp-table tbody td { padding: 8px 8px; border-bottom:1px solid #f1f5f9; "
        "  text-align:center; white-space: nowrap; }"
        ".sp-table tbody tr:hover td { background:#f8fafc; }"
        ".sp-team-cell { display:flex; align-items:center; gap:8px; justify-content:flex-start; "
        "  text-align:left; padding-left:6px; }"
        ".sp-team-cell img { width:24px; height:24px; object-fit:contain; }"
        ".sp-loc { color:#64748b; font-weight:800; width: 28px; }"
        ".sp-pitcher { text-align:left; font-weight:700; }"
        ".sp-pitcher-link { color:#0f172a; text-decoration:none; border-bottom: 1px dashed #94a3b8; }"
        ".sp-pitcher-link:hover { color:#0a4ea2; border-bottom-color:#0a4ea2; }"
        ".sp-pitcher-link:hover::after { color:#0a4ea2; }"
        ".sp-num { font-variant-numeric: tabular-nums; font-weight:700; }"
        ".sp-na { color:#94a3b8; }"
        ".sp-empty { padding:14px 18px; color:#64748b; background:#f8fafc; border-radius:14px; "
        "  border:1px dashed #cbd5e1; }"
        ".sp-src { display:inline-block; padding: 2px 8px; border-radius: 999px; "
        "  font-size: .68rem; font-weight: 800; letter-spacing: .04em; "
        "  text-transform: uppercase; line-height: 1.4; }"
        ".sp-src-savant   { background:#dcfce7; color:#166534; border:1px solid #86efac; }"
        ".sp-src-savant-sm{ background:#fef3c7; color:#854d0e; border:1px solid #fde68a; }"
        ".sp-src-savant-xs{ background:#ffedd5; color:#9a3412; border:1px solid #fed7aa; }"
        ".sp-src-statsapi { background:#dbeafe; color:#1e40af; border:1px solid #bfdbfe; }"
        ".sp-src-none     { background:#f1f5f9; color:#64748b; border:1px solid #cbd5e1; }"
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
                cells.append(f"<td>{v if v not in (None, '') else '<span class=\"sp-na\">—</span>'}</td>")
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

def render_slate_pitcher_minicards(away_row: dict, home_row: dict):
    """Compact two-up cards summarizing both starters in the current game.
    Each card pulls Pitch Score, Strikeout Score and the key Statcast headline
    metrics (xwOBA-against, K%, Whiff%, Barrel%, HH%) from the Slate Pitchers
    builder. Used at the top of the per-game Matchup tab."""
    css = (
        "<style>"
        ".spc-row { display:flex; gap:12px; flex-wrap:wrap; margin: 4px 0 14px 0; }"
        ".spc-card { flex:1 1 0; min-width: 280px; background:#fff; border:2px solid #e2e8f0; "
        "  border-radius:14px; padding: 12px 14px; box-shadow: 0 2px 8px rgba(15,23,42,.05); "
        "  position: relative; }"
        ".spc-tier-elite  { border-color:#16a34a; background: linear-gradient(180deg,#f0fdf4 0%,#fff 60%); }"
        ".spc-tier-strong { border-color:#84cc16; }"
        ".spc-tier-ok     { border-color:#facc15; }"
        ".spc-tier-soft   { border-color:#f97316; }"
        ".spc-tier-poor   { border-color:#ef4444; background: linear-gradient(180deg,#fef2f2 0%,#fff 60%); }"
        ".spc-tag { position:absolute; top:-10px; left:14px; background:#0f172a; color:#fff; "
        "  font-size:.68rem; font-weight:800; padding:3px 9px; border-radius:999px; letter-spacing:.06em; }"
        ".spc-head { display:flex; align-items:center; gap:10px; margin-top:2px; }"
        ".spc-logo { width: 36px; height: 36px; object-fit: contain; flex: 0 0 36px; }"
        ".spc-name { font-size: 1.02rem; font-weight: 900; color:#0f172a; line-height:1.1; }"
        ".spc-name a { color: inherit; text-decoration: none; border-bottom: 1px dashed #94a3b8; }"
        ".spc-name a:hover { color:#0a4ea2; border-bottom-color:#0a4ea2; }"
        ".spc-meta { color:#64748b; font-size:.78rem; font-weight:700; letter-spacing:.02em; }"
        ".spc-scores { display:flex; gap:14px; align-items:flex-end; margin-top:8px; }"
        ".spc-bigscore { display:flex; flex-direction:column; }"
        ".spc-bigscore .lab { color:#64748b; font-size:.62rem; font-weight:800; "
        "  text-transform:uppercase; letter-spacing:.08em; }"
        ".spc-bigscore .val { font-size: 1.55rem; font-weight: 900; color:#0f172a; line-height:1; }"
        ".spc-pill { display:inline-block; padding: 3px 10px; border-radius: 999px; "
        "  font-weight: 800; font-size: .78rem; }"
        ".spc-pill.elite  { background:#dcfce7; color:#065f46; }"
        ".spc-pill.strong { background:#ecfccb; color:#365314; }"
        ".spc-pill.ok     { background:#fef9c3; color:#713f12; }"
        ".spc-pill.soft   { background:#ffedd5; color:#9a3412; }"
        ".spc-pill.poor   { background:#fee2e2; color:#991b1b; }"
        ".spc-stats { display:grid; grid-template-columns: repeat(5, 1fr); gap:8px; margin-top:12px; }"
        ".spc-stat .lab { color:#64748b; font-size:.62rem; font-weight:800; "
        "  text-transform:uppercase; letter-spacing:.06em; }"
        ".spc-stat .val { color:#0f172a; font-size: .98rem; font-weight: 800; }"
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
        throws = row.get("Throws") or "?"
        team = row.get("Team", "")
        return (
            f'<div class="spc-card spc-tier-{tier_cls}">'
            f'<div class="spc-tag">{label}</div>'
            f'<div class="spc-head">{logo_html}'
            f'<div><div class="spc-name">{name_html} '
            f'<span style="color:#64748b; font-weight:700; font-size:.82rem;">({throws})</span></div>'
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

def build_hr_sleepers_table(_schedule_df, _batters_df, _pitchers_df):
    """Score every posted-lineup batter for HR sleeper potential and return a
    sorted DataFrame ready for rendering."""
    rows = []
    for _, g in _schedule_df.iterrows():
        try:
            cc = build_game_context(g)
        except Exception:
            continue
        bpw = park_weather_indicator(
            cc.get("weather", {}), g.get("park_factor"), g.get("home_abbr", "")
        )
        for side, lineup_df, opp_pitcher in (
            ("away", cc["away_lineup"], g["home_probable"]),
            ("home", cc["home_lineup"], g["away_probable"]),
        ):
            if lineup_df is None or lineup_df.empty:
                continue
            p_row = find_pitcher_row(_pitchers_df, opp_pitcher)
            for _, r in lineup_df.iterrows():
                b = find_player_row(_batters_df, r["name_key"], r["team"])
                if b is None:
                    continue
                opp_hand = r.get("opposing_pitch_hand", "")
                # Reuse the existing scoring stack so sleeper rankings stay
                # consistent with the per-game model.
                m_score = matchup_score(b, p_row, r["lineup_spot"], cc["weather"],
                                         g["park_factor"], r["bat_side"], opp_hand)
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

                # Normalize each input to 0-100
                n_barrel = _norm(barrel, 4.0, 18.0)
                n_hh     = _norm(hh,     30.0, 55.0)
                n_iso    = _norm(iso,    0.100, 0.280)
                n_fb     = _norm(fb,     20.0, 45.0)
                n_pull   = _norm(pull,   30.0, 50.0)
                n_match  = _norm(m_score, 80.0, 140.0)
                n_ceil   = _norm(c_score, 80.0, 140.0)
                n_khr    = _norm(khr,     0.4,  1.6)
                bonus    = _sleeper_bonus(hr_total, r.get("lineup_spot", 9))

                power_part   = (0.22 * n_barrel + 0.14 * n_hh + 0.10 * n_iso
                                + 0.07 * n_fb + 0.07 * n_pull)
                matchup_part = 0.10 * n_match + 0.08 * n_ceil + 0.07 * n_khr
                bonus_part   = 0.15 * bonus
                sleeper      = round(power_part + matchup_part + bonus_part, 1)

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
                    "Matchup":  m_score,
                    "Ceiling":  c_score,
                    "kHR":      khr,
                    "PA":       pa,
                    "BPW Label":  bpw["label"],
                    "BPW Tier":   bpw["tier"],
                    "BPW HR%":    bpw["hr_pct"],
                    "BPW Icon":   bpw["icon"],
                    "BPW Tip":    bpw["tooltip"],
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("Sleeper Score", ascending=False).reset_index(drop=True)
    return df

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
        "  font-size:.92rem; background:#fff; border-radius:12px; overflow:hidden; "
        "  box-shadow: 0 2px 10px rgba(15,23,42,.06); }"
        ".hrs-table th { background:#0f3a2e; color:#fcd34d; text-align:left; "
        "  font-weight:800; padding:9px 10px; letter-spacing:.03em; font-size:.78rem; "
        "  text-transform:uppercase; }"
        ".hrs-table td { padding:8px 10px; border-bottom:1px solid #f1f5f9; "
        "  color:#0f172a; }"
        ".hrs-table tr:nth-child(even) td { background:#fafafa; }"
        ".hrs-pill { display:inline-block; padding:3px 9px; border-radius:999px; "
        "  font-weight:800; font-size:.74rem; letter-spacing:.04em; }"
        ".hrs-pill.elite  { background:#dcfce7; color:#065f46; }"
        ".hrs-pill.strong { background:#ecfccb; color:#365314; }"
        ".hrs-pill.ok     { background:#fef9c3; color:#713f12; }"
        ".hrs-pill.soft   { background:#ffedd5; color:#9a3412; }"
        ".hrs-score { font-weight:900; font-size:1.05rem; color:#0f172a; }"
        ".hrs-name { font-weight:800; color:#0f172a; }"
        ".hrs-meta { color:#64748b; font-size:.78rem; }"
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

    rows_html = []
    for i, r in df.iterrows():
        tier_cls, tier_label = _tier(r.get("Sleeper Score"))
        rows_html.append(
            "<tr>"
            f'<td class="hrs-num">{i+1}</td>'
            f'<td><div class="hrs-name">{r.get("Hitter","")}</div>'
            f'<div class="hrs-meta">{r.get("Team","")} · Bat {r.get("Bat","")} · Spot {r.get("Spot","")}</div></td>'
            f'<td class="hrs-meta">{r.get("Game","")}<br/><span style="color:#475569;">vs {r.get("Opp SP","")}</span></td>'
            f'<td><span class="hrs-score">{r.get("Sleeper Score",0):.1f}</span> '
            f'<span class="hrs-pill {tier_cls}" style="margin-left:6px;">{tier_label}</span></td>'
            f'<td class="hrs-num">{int(r.get("HR (Season)", 0))}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("Barrel%"))}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("HardHit%"))}</td>'
            f'<td class="hrs-num">{_fmt_num(r.get("ISO"), 3)}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("FB%"))}</td>'
            f'<td class="hrs-num">{_fmt_pct(r.get("Pull%"))}</td>'
            f'<td class="hrs-num">{_fmt_num(r.get("Matchup"), 1)}</td>'
            f'<td class="hrs-num">{_fmt_num(r.get("kHR"), 2)}</td>'
            "</tr>"
        )

    return css + (
        '<div class="hrs-wrap"><table class="hrs-table">'
        '<thead><tr>'
        '<th>#</th><th>Hitter</th><th>Game</th><th>Sleeper</th>'
        '<th>HR</th><th>Barrel%</th><th>HH%</th><th>ISO</th><th>FB%</th>'
        '<th>Pull%</th><th>Matchup</th><th>kHR</th>'
        '</tr></thead><tbody>'
        + "".join(rows_html) +
        '</tbody></table></div>'
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


def build_targets_table(_schedule_df, _batters_df, _pitchers_df, mode="tb"):
    """Build a target-prop sleeper table for either:
       mode="tb"  -> Over 1.5 Total Bases
       mode="hrr" -> Over 1.5 Hits+Runs+RBI
    Returns a sorted DataFrame ready for rendering."""
    rows = []
    for _, g in _schedule_df.iterrows():
        try:
            cc = build_game_context(g)
        except Exception:
            continue
        for side, lineup_df, opp_pitcher in (
            ("away", cc["away_lineup"], g["home_probable"]),
            ("home", cc["home_lineup"], g["away_probable"]),
        ):
            if lineup_df is None or lineup_df.empty:
                continue
            p_row = find_pitcher_row(_pitchers_df, opp_pitcher)
            for _, r in lineup_df.iterrows():
                b = find_player_row(_batters_df, r["name_key"], r["team"])
                if b is None:
                    continue
                opp_hand = r.get("opposing_pitch_hand", "")
                m_score = matchup_score(b, p_row, r["lineup_spot"], cc["weather"],
                                         g["park_factor"], r["bat_side"], opp_hand)
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

                if mode == "tb":
                    contact_part = 0.12*n_xba + 0.10*n_avg + 0.08*n_kinv + 0.05*n_ld
                    power_part   = 0.12*n_xslg + 0.10*n_iso + 0.08*n_barrel
                    spot_part    = 0.15 * _spot_pa_weight(r.get("lineup_spot", 9))
                    matchup_part = 0.12*n_match + 0.08*n_ceil
                    score = contact_part + power_part + spot_part + matchup_part
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
                    score = power_part + spot_part + matchup_part + contact_part
                else:  # hrr
                    onbase_part  = 0.12*n_xba + 0.13*n_xobp + 0.10*n_avg
                    power_part   = 0.10*n_xslg + 0.06*n_iso + 0.04*n_barrel
                    spot_part    = 0.25 * _spot_hrr_weight(r.get("lineup_spot", 9))
                    matchup_part = 0.12*n_match + 0.08*n_ceil
                    score = onbase_part + power_part + spot_part + matchup_part

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
        "  font-size:.92rem; background:#fff; border-radius:12px; overflow:hidden; "
        "  box-shadow: 0 2px 10px rgba(15,23,42,.06); }"
        ".tg-table th { background:#0f3a2e; color:#fcd34d; text-align:left; "
        "  font-weight:800; padding:9px 10px; letter-spacing:.03em; font-size:.78rem; "
        "  text-transform:uppercase; }"
        ".tg-table td { padding:8px 10px; border-bottom:1px solid #f1f5f9; "
        "  color:#0f172a; }"
        ".tg-table tr:nth-child(even) td { background:#fafafa; }"
        ".tg-pill { display:inline-block; padding:3px 9px; border-radius:999px; "
        "  font-weight:800; font-size:.74rem; letter-spacing:.04em; }"
        ".tg-pill.elite  { background:#dcfce7; color:#065f46; }"
        ".tg-pill.strong { background:#ecfccb; color:#365314; }"
        ".tg-pill.ok     { background:#fef9c3; color:#713f12; }"
        ".tg-pill.soft   { background:#ffedd5; color:#9a3412; }"
        ".tg-score { font-weight:900; font-size:1.05rem; color:#0f172a; }"
        ".tg-name { font-weight:800; color:#0f172a; }"
        ".tg-meta { color:#64748b; font-size:.78rem; }"
        ".tg-num { font-variant-numeric: tabular-nums; }"
        # Lineup-status mini-pills shown inline next to player name.
        ".tg-lp { display:inline-block; padding:1px 7px; border-radius:999px; "
        "  font-weight:800; font-size:.66rem; letter-spacing:.04em; "
        "  margin-left:6px; vertical-align: middle; }"
        ".tg-lp.confirmed { background:#dcfce7; color:#065f46; }"
        ".tg-lp.projected { background:#dbeafe; color:#1e3a8a; }"
        ".tg-lp.notposted { background:#fee2e2; color:#7f1d1d; }"
        # Park-factor pill colored by neutral / hitter-friendly / pitcher-friendly.
        ".tg-park { display:inline-block; padding:1px 7px; border-radius:6px; "
        "  font-weight:700; font-size:.7rem; }"
        ".tg-park.hot   { background:#fee2e2; color:#7f1d1d; }"
        ".tg-park.neut  { background:#f1f5f9; color:#334155; }"
        ".tg-park.cold  { background:#dbeafe; color:#1e3a8a; }"
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
    if mode == "tb":
        # TB: AVG, xBA, xSLG, ISO, Barrel%, K%, LD%, Matchup
        headers = ["#", "Hitter", "Game", "Context", "TB Score", "AVG", "xBA", "xSLG", "ISO", "Barrel%", "K%", "Match"]
    elif mode == "rbi2":
        # 2+ RBI: AVG, xSLG, ISO, Barrel%, HardHit%, K%, Matchup
        headers = ["#", "Hitter", "Game", "Context", "RBI Score", "AVG", "xSLG", "ISO", "Barrel%", "HardHit%", "K%", "Match"]
    else:
        # HRR: AVG, xBA, xOBP, xSLG, ISO, Barrel%, K%, Matchup
        headers = ["#", "Hitter", "Game", "Context", "HRR Score", "AVG", "xBA", "xOBP", "xSLG", "ISO", "K%", "Match"]

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
            f'margin-left:4px;">({opp_hand}HP)</span>' if opp_hand else ""
        )
        platoon = _platoon_marker(r.get("Bat", ""), opp_hand)
        park_chip = _park_chip(r.get("Park", ""), r.get("ParkFactor", 100))
        weather_str = r.get("Weather", "—") or "—"
        common = (
            "<tr>"
            f'<td class="tg-num">{i+1}</td>'
            f'<td><div class="tg-name">{r.get("Hitter","")}{platoon}{lineup_pill}</div>'
            f'<div class="tg-meta">{r.get("Team","")} · Bat {r.get("Bat","")} · Spot {r.get("Spot","")}</div></td>'
            f'<td class="tg-meta">{r.get("Game","")}<br/>'
            f'<span style="color:#475569;">vs {r.get("Opp SP","")}{opp_hand_chip}</span></td>'
            f'<td class="tg-meta">{park_chip}<br/>'
            f'<span style="color:#475569;">{weather_str}</span></td>'
            f'<td><span class="tg-score">{r.get("Score",0):.1f}</span> '
            f'<span class="tg-pill {tier_cls}" style="margin-left:6px;">{tier_label}</span></td>'
        )
        if mode == "tb":
            metrics_html = (
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
    return css + (
        f'<div class="tg-wrap"><table class="tg-table">'
        f'<thead><tr>{head_html}</tr></thead><tbody>'
        + "".join(rows_html) +
        '</tbody></table></div>'
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
        f'<img class="brand-logo" src="{LOGO_URI}" alt="MrBets850" />'
        if LOGO_URI else '<span class="brand-logo" style="display:flex;align-items:center;'
                         'justify-content:center;font-size:1.6rem;">👑</span>'
    )
    # Time-stamp the brand bar with the slate's freshness in Central Time.
    # Also surface a quick health summary: live / total sources.
    try:
        now_ct = datetime.now(MLB_TZ).strftime("%a %b %-d · %-I:%M %p CT")
    except Exception:
        now_ct = ""
    live_n = sum(1 for v in DATA_SOURCES.values() if v.get("status") == "live")
    total_n = len(DATA_SOURCES)
    if total_n == 0:
        health_html = "Live data · Auto-refresh 30 min"
    else:
        health_html = f"{live_n}/{total_n} live sources · As of {now_ct}" if now_ct else f"{live_n}/{total_n} live sources"
    st.markdown(f"""
    <div class="brand-bar">
        <div class="brand-left">
            {logo_html}
            <div>
                <div class="brand-tag">👑 MrBets850 · MLB Edge</div>
                <div class="brand-name">MLB Matchup Board</div>
            </div>
        </div>
        <div class="brand-meta">
            <div class="big">{slate_count} {'game' if slate_count == 1 else 'games'} on slate</div>
            <div>{health_html}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_game_carousel(schedule_df, selected_idx):
    """Horizontal scrolling logo carousel. Pills are anchor links that set the
    `?g=<idx>` query parameter, which the app reads to drive game selection."""
    if schedule_df.empty:
        return
    pills = []
    for i, g in schedule_df.iterrows():
        active = "active" if i == selected_idx else ""
        away_logo = logo_url(g["away_id"]) if g["away_id"] else ""
        home_logo = logo_url(g["home_id"]) if g["home_id"] else ""
        pills.append(
            f'<a class="game-pill {active}" href="?g={i}" target="_self">'
            f'<span class="logos">'
            f'<img src="{away_logo}" alt="{g["away_abbr"]}" />'
            f'<span class="at">@</span>'
            f'<img src="{home_logo}" alt="{g["home_abbr"]}" />'
            f'</span>'
            f'<span class="matchup-text">{g["away_abbr"]} @ {g["home_abbr"]}</span>'
            f'<span class="time">{g["time_short"]}</span>'
            f'</a>'
        )
    st.markdown(
        '<div class="carousel-wrap"><div class="carousel-strip">' + "".join(pills) + '</div></div>',
        unsafe_allow_html=True,
    )

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
    line_str = (f"line: <span style='color:#0f172a; font-weight:900;'>{line:.1f}</span>"
                if line is not None else
                "<span style='color:#94a3b8;'>line: not set · add ODDS_API_KEY in Streamlit secrets</span>")
    avg_str = (f"hist. avg: <span style='color:#0f172a; font-weight:900;'>{avg:.1f}</span>"
               if avg is not None else "")
    # If we have a line, draw the Under/Push/Over distribution bar
    if ou.get("under") is not None:
        u, p, o = ou["under"], ou["push"] or 0, ou["over"]
        bar_html = (
            f'<div style="display:flex; height:36px; border-radius:999px; overflow:hidden; '
            f'border:1px solid #e2e8f0; margin-top:6px;">'
            f'<div style="flex:{u}; background:linear-gradient(180deg,#fde2e2 0%,#fecaca 100%); '
            f'display:flex; align-items:center; justify-content:center; '
            f'color:#7c2d12; font-weight:900; font-size:0.85rem;">'
            f'<span style="margin-right:6px; opacity:.7; font-size:0.7rem;">UNDER</span>{u}%</div>'
        )
        if p > 0:
            bar_html += (
                f'<div style="flex:{p}; background:#f1f5f9; '
                f'display:flex; align-items:center; justify-content:center; '
                f'color:#475569; font-weight:800; font-size:0.72rem;">'
                f'<span style="opacity:.7; font-size:0.6rem; margin-right:3px;">PUSH</span>{p}%</div>'
            )
        bar_html += (
            f'<div style="flex:{o}; background:linear-gradient(180deg,#dcfce7 0%,#bbf7d0 100%); '
            f'display:flex; align-items:center; justify-content:center; '
            f'color:#14532d; font-weight:900; font-size:0.85rem;">'
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
        '<div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; '
        'padding:0; margin-top:14px; overflow:hidden; '
        'box-shadow:0 2px 8px rgba(15,23,42,.06);">'
        # ---- Top chip strip: temp, dew, wind, rain, sky, sample ----
        '<div style="background:linear-gradient(180deg,#0f3a2e 0%,#0b1f15 100%); '
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
        'margin-top:8px; padding-top:6px; border-top:1px dashed #e2e8f0;">'
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
    st.markdown(f"""
    <div class="section-card dark">
        <div class="game-header">
            <div>
                <div class="matchup-display">
                    <img src="{away_logo}" alt="{game_row['away_abbr']}" />
                    <div class="team-abbr">{game_row['away_abbr']}</div>
                    <span class="vs">@</span>
                    <div class="team-abbr">{game_row['home_abbr']}</div>
                    <img src="{home_logo}" alt="{game_row['home_abbr']}" />
                </div>
                <div class="meta">{game_row['game_time_ct']} · {game_row['venue']} · {game_row['status']}</div>
            </div>
            <div class="probables">
                <div class="label">Probables</div>
                <div>{game_row['away_probable']} <span class="hand">({ctx['away_pitch_hand'] or '?'})</span></div>
                <div>vs {game_row['home_probable']} <span class="hand">({ctx['home_pitch_hand'] or '?'})</span></div>
            </div>
        </div>
        {weather_card_html}
        <div class="kpi-row" style="margin-top:10px;">
            <div class="kpi"><span class="tier {away_pill}">{game_row['away_abbr']}: {ctx['away_status']}</span></div>
            <div class="kpi"><span class="tier {home_pill}">{game_row['home_abbr']}: {ctx['home_status']}</span></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_lineup_banner(team_id, team_abbr, opp_pitcher, status):
    if status == "Confirmed": pill_cls = "tier-strong"
    elif status == "Projected": pill_cls = "tier-ok"
    else: pill_cls = "tier-avoid"
    logo = logo_url(team_id) if team_id else ""
    st.markdown(f"""
    <div class="lineup-banner">
        <img src="{logo}" alt="{team_abbr}" />
        <div class="lineup-title">{team_abbr} Lineup</div>
        <div class="vs-pitcher">vs {opp_pitcher}</div>
        <div class="badge"><span class="tier {pill_cls}">{status}</span></div>
    </div>
    """, unsafe_allow_html=True)

def render_pitch_mix_block(mix_df: pd.DataFrame, surface_bg: str = "#ffffff") -> str:
    """Build the 'Pitch Mix' mini-table HTML for a pitcher.
    Columns: pitch (emoji+name), Use%, wOBA allowed, Whiff%, RV/100.
    Color-codes wOBA allowed: green = strong (low), red = vulnerable (high)."""
    if mix_df is None or mix_df.empty:
        return (
            '<div style="margin-top:10px; font-size:0.78rem; color:#64748b; '
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
            if w <= 0.300: woba_col = "#16a34a"
            elif w <= 0.340: woba_col = "#65a30d"
            elif w <= 0.380: woba_col = "#d97706"
            else: woba_col = "#dc2626"
            woba_str = f"{w:.3f}"
        except Exception:
            woba_col, woba_str = "#475569", "—"
        use_str = f"{float(use):.0f}%" if pd.notna(use) else "—"
        whiff_str = f"{float(whiff):.0f}%" if pd.notna(whiff) else "—"
        try: rv_str = f"{float(rv100):+.1f}"
        except Exception: rv_str = "—"
        rows_html.append(
            f'<tr>'
            f'<td style="padding:5px 6px; font-weight:800; color:#0f172a; white-space:nowrap;">{emoji} {name}</td>'
            f'<td style="padding:5px 6px; text-align:right; font-weight:800; color:#0f172a;">{use_str}</td>'
            f'<td style="padding:5px 6px; text-align:right; font-weight:900; color:{woba_col};">{woba_str}</td>'
            f'<td style="padding:5px 6px; text-align:right; font-weight:700; color:#475569;">{whiff_str}</td>'
            f'<td style="padding:5px 6px; text-align:right; font-weight:700; color:#475569;">{rv_str}</td>'
            f'</tr>'
        )
    return (
        '<div style="margin-top:12px; background:' + surface_bg + '; border:1px solid #e2e8f0; '
        'border-radius:10px; padding:6px 4px;">'
        '<div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; '
        'letter-spacing:.08em; font-weight:800; padding:2px 8px 4px;">Pitch Mix — what they throw &amp; results allowed</div>'
        '<table style="width:100%; border-collapse:collapse; font-size:0.82rem;">'
        '<thead><tr style="color:#64748b; font-size:0.66rem; font-weight:800; text-transform:uppercase;">'
        '<th style="text-align:left; padding:3px 6px;">Pitch</th>'
        '<th style="text-align:right; padding:3px 6px;">Use</th>'
        '<th style="text-align:right; padding:3px 6px;">wOBA</th>'
        '<th style="text-align:right; padding:3px 6px;">Whiff</th>'
        '<th style="text-align:right; padding:3px 6px;" title="Run value per 100 pitches — negative is better for the pitcher">RV/100</th>'
        '</tr></thead>'
        '<tbody>' + "".join(rows_html) + '</tbody></table>'
        '<div style="font-size:0.7rem; color:#64748b; padding:4px 8px 2px;">'
        'Green wOBA = pitch is dominant · Red = batters punish it.</div>'
        '</div>'
    )

def render_pitcher_panel(label, pitcher_name, pitch_hand, p_row, pitch_mix_df=None):
    score, key, verdict = pitcher_vulnerability(p_row)
    if p_row is None:
        k = bb = era_w = barrel = hardhit = "—"
    else:
        k = f"{safe_float(p_row.get('K%')):.1f}%"
        bb = f"{safe_float(p_row.get('BB%')):.1f}%"
        era_w = f"{safe_float(p_row.get('xwOBA')):.3f}"
        barrel = f"{safe_float(p_row.get('Barrel%')):.1f}%"
        hardhit = f"{safe_float(p_row.get('HardHit%')):.1f}%"
    color_bg = {"elite": "#fef2f2", "strong": "#fffbeb", "ok": "#f8fafc", "avoid": "#f0fdf4"}[key]
    border = {"elite": "#ef4444", "strong": "#f59e0b", "ok": "#94a3b8", "avoid": "#16a34a"}[key]
    mix_html = render_pitch_mix_block(pitch_mix_df, surface_bg="#ffffff") if pitch_mix_df is not None else ""
    st.markdown(f"""
    <div style="background:{color_bg}; border:1px solid #e2e8f0; border-left:6px solid {border};
                border-radius:14px; padding:12px 14px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <div>
                <div style="font-size:0.7rem; color:#64748b; text-transform:uppercase; letter-spacing:0.1em; font-weight:800;">{label}</div>
                <div style="font-size:1.05rem; font-weight:900; color:#0f172a;">{pitcher_name or 'TBD'}
                    <span style="color:#64748b; font-weight:700; font-size:0.85rem;">({pitch_hand or '?'})</span></div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:1.5rem; font-weight:900; color:#0f172a; line-height:1;">{score}</div>
                <span class="tier tier-{('elite' if key=='elite' else 'strong' if key=='strong' else 'ok' if key=='ok' else 'avoid')}">{verdict}</span>
            </div>
        </div>
        <div style="display:grid; grid-template-columns: repeat(5, 1fr); gap: 6px 10px; margin-top:10px;">
          <div><div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; font-weight:700;">K%</div><div style="font-weight:800;">{k}</div></div>
          <div><div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; font-weight:700;">BB%</div><div style="font-weight:800;">{bb}</div></div>
          <div><div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; font-weight:700;">xwOBA</div><div style="font-weight:800;">{era_w}</div></div>
          <div><div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; font-weight:700;">Barrel%</div><div style="font-weight:800;">{barrel}</div></div>
          <div><div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; font-weight:700;">HardHit%</div><div style="font-weight:800;">{hardhit}</div></div>
        </div>
        {mix_html}
    </div>
    """, unsafe_allow_html=True)

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
    '<div><div class="brand-tag">👑 MrBets850 · MLB Edge</div>'
    '<div class="brand-name">MLB Matchup Board</div></div></div>'
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
# Apply any pending "Today" reset BEFORE the date_input widget is instantiated.
# Streamlit forbids writing to st.session_state[<widget_key>] after the widget
# has been created, so we use a one-shot flag and rerun pattern.
if st.session_state.pop("_reset_to_today", False):
    st.session_state["slate_date_picker"] = today_ct()
    st.session_state["_selected_idx"] = 0

top_cols = st.columns([2.2, 1, 1])
with top_cols[0]:
    selected_date = st.date_input("📅 Slate date", value=today_ct(), key="slate_date_picker")
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
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()

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
    try:
        _hero_df = build_targets_table(schedule_df, batters_df, pitchers_df, mode="rbi2")
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
        ".rbi-hero { margin: 4px 0 16px 0; padding: 14px 14px 10px; "
        "  background: linear-gradient(135deg, #7f1d1d 0%, #b91c1c 50%, #dc2626 100%); "
        "  border-radius: 16px; border: 2px solid #fbbf24; "
        "  box-shadow: 0 4px 16px rgba(127,29,29,.35); }"
        ".rbi-hero-title { color:#fde68a; font-weight:900; font-size:1.15rem; "
        "  letter-spacing:.02em; margin: 0 0 4px 0; "
        "  text-shadow: 0 1px 2px rgba(0,0,0,.4); }"
        ".rbi-hero-sub { color:#fee2e2; font-size:.82rem; margin: 0 0 10px 0; }"
        ".rbi-hero-rail { display:flex; gap:10px; overflow-x:auto; "
        "  padding: 4px 2px 8px; scroll-snap-type: x mandatory; "
        "  -webkit-overflow-scrolling: touch; }"
        ".rbi-hero-rail::-webkit-scrollbar { height:6px; }"
        ".rbi-hero-rail::-webkit-scrollbar-thumb { background:#fbbf24; border-radius:3px; }"
        ".rbi-card { flex: 0 0 auto; min-width: 180px; max-width: 200px; "
        "  background:#fff; border-radius:12px; padding:10px 12px; "
        "  scroll-snap-align: start; "
        "  box-shadow: 0 2px 6px rgba(0,0,0,.15); }"
        ".rbi-card-rank { display:inline-block; background:#0f3a2e; color:#facc15; "
        "  font-weight:900; font-size:.72rem; padding:2px 8px; border-radius:999px; "
        "  letter-spacing:.05em; }"
        ".rbi-card-score { float:right; font-weight:900; font-size:1.05rem; "
        "  color:#dc2626; }"
        ".rbi-card-name { font-weight:800; color:#0f172a; font-size:.96rem; "
        "  margin-top:6px; line-height:1.15; }"
        ".rbi-card-meta { color:#64748b; font-size:.74rem; margin-top:2px; }"
        ".rbi-card-game { color:#475569; font-size:.74rem; margin-top:6px; "
        "  border-top:1px solid #f1f5f9; padding-top:5px; }"
        ".rbi-card-stats { display:flex; gap:8px; margin-top:6px; "
        "  font-variant-numeric: tabular-nums; font-size:.72rem; color:#0f172a; }"
        ".rbi-card-stats span b { color:#dc2626; }"
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
            f'<div class="rbi-card-meta">{r.get("Team","")} · Bat {r.get("Bat","")} · Spot {r.get("Spot","")}</div>'
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
        'RBI matchup — swipe to see all 15. Open the <b>🔥 2+ RBI</b> tab for filters &amp; full table.</div>'
        '<div class="rbi-hero-rail">' + "".join(cards_html) + '</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

_render_rbi_hero_strip()

# ===========================================================================
# Top-level tab switcher: "⚾ Games" vs "🥎 Slate Pitchers"
# Implemented as a styled radio so we can toggle large sections of the page
# without re-indenting the entire game flow.
# ===========================================================================
_TOP_VIEW_OPTIONS = [
    "⚾ Games",
    "🥎 Slate Pitchers",
    "💎 HR Sleepers",
    "📊 Total Bases 1.5+",
    "🎯 HRR 1.5+",
    "🔥 2+ RBI",
    "🤖 AI HR Parlay",
    "👑 HR Round Robin",
    "🎯 AI K Generator",
    "🌬️ Ballpark Weather",
    "🥎 AI 1+ Hits Parlay",
]

st.markdown(
    "<style>"
    # ---- Top-level view tabs: bold, mobile-friendly pill carousel ----
    # Pure HTML anchor pills — no Streamlit radio internals to fight.
    ".top-tab-row { margin: 8px 0 14px 0; padding: 10px; "
    "  background: linear-gradient(180deg, #fde68a 0%, #f59e0b 55%, #b45309 100%); "
    "  border-radius: 16px; border: 2px solid #92400e; "
    "  box-shadow: 0 2px 8px rgba(120,53,15,.25), inset 0 1px 0 rgba(255,255,255,.45); }"
    ".top-tab-strip { display:flex; gap:10px; flex-wrap:nowrap; "
    "  justify-content:flex-start; overflow-x:auto; overflow-y:hidden; "
    "  -webkit-overflow-scrolling:touch; scroll-snap-type:x proximity; "
    "  scrollbar-width:thin; scrollbar-color:#92400e transparent; "
    "  padding-bottom:4px; }"
    ".top-tab-strip::-webkit-scrollbar { height:6px; }"
    ".top-tab-strip::-webkit-scrollbar-thumb { background:#92400e; border-radius:3px; }"
    ".top-tab-strip::-webkit-scrollbar-track { background:transparent; }"
    ".top-tab-pill { scroll-snap-align:start; flex:0 0 auto; white-space:nowrap; "
    "  background:#ffffff; padding:10px 18px; min-height:44px; "
    "  border-radius:999px; border:2px solid #cbd5e1; cursor:pointer; "
    "  font-weight:800; font-size:0.98rem; color:#0f172a; "
    "  text-decoration:none; line-height:1.2; letter-spacing:.01em; "
    "  transition:all .18s ease; box-shadow:0 1px 3px rgba(15,23,42,.06); "
    "  display:inline-flex; align-items:center; }"
    ".top-tab-pill:hover { border-color:#0f3a2e; transform:translateY(-1px); "
    "  box-shadow:0 4px 10px rgba(15,58,46,.12); color:#0f172a; "
    "  text-decoration:none; }"
    ".top-tab-pill.active { "
    "  background:linear-gradient(110deg, #04130b 0%, #0f3a2e 60%, #1d5a3f 100%); "
    "  color:#facc15; border-color:#facc15; "
    "  box-shadow:0 0 0 3px rgba(250,204,21,.25), 0 6px 16px rgba(5,20,12,.35); "
    "  transform:translateY(-1px); text-decoration:none; }"
    ".top-tab-pill.active:hover { color:#facc15; }"
    "@media (max-width: 640px) { "
    "  .top-tab-row { padding:12px; } "
    "  .top-tab-strip { gap:8px; } "
    "  .top-tab-pill { padding:12px 14px; min-height:48px; font-size:1.0rem; } "
    "}"
    ".sp-legend { color:#64748b; font-size:.78rem; margin: 4px 0 12px 0; }"
    ".sp-legend code { background:#f1f5f9; padding: 1px 6px; border-radius:6px; "
    "  font-family: inherit; font-weight:700; color:#334155; }"
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
            st.query_params.clear()
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

_pills_html = []
for _i, _opt in enumerate(_TOP_VIEW_OPTIONS):
    _active = " active" if _opt == _view else ""
    _href = _build_top_tab_href(_i)
    _pills_html.append(
        f'<a class="top-tab-pill{_active}" href="{_href}" target="_self">{_opt}</a>'
    )
st.markdown(
    '<div class="top-tab-row"><div class="top-tab-strip">'
    + "".join(_pills_html)
    + '</div></div>',
    unsafe_allow_html=True,
)

if _view == "🥎 Slate Pitchers":
    st.markdown('<div class="section-title" style="font-size:1.4rem;margin-top:8px;">🥎 Slate Pitchers</div>', unsafe_allow_html=True)
    if pitcher_stats_df is None or pitcher_stats_df.empty:
        st.warning(
            "Pitcher stats CSV (`Data:savant_pitcher_stats.csv`) hasn’t loaded yet. "
            "Make sure it’s pushed to the data repo — the Slate Pitchers tab joins by `player_id`."
        )
        st.stop()
    with st.spinner("Building slate pitcher board…"):
        sp_df = build_slate_pitcher_table(schedule_df, pitcher_stats_df)
    if sp_df.empty:
        st.info("No probable starters posted yet for this slate. Check back closer to first pitch.")
    else:
        # ---- Filter row: Hide TBD / Min PA / Hide unmatched ----
        f_cols = st.columns([1, 1, 1.2, 2.6])
        with f_cols[0]:
            _hide_tbd = st.checkbox("Hide TBD", value=True, key="sp_hide_tbd",
                                    help="Hide rows whose probable starter is still TBD.")
        with f_cols[1]:
            _hide_unmatched = st.checkbox("Hide unmatched", value=False, key="sp_hide_unmatched",
                                          help="Hide rows with no Savant CSV row (blank metrics).")
        with f_cols[2]:
            _min_pa = st.number_input("Min PA", min_value=0, value=0, step=10,
                                      key="sp_min_pa",
                                      help="Filter to pitchers with at least this many PA in the CSV.")

        sp_df_filtered = sp_df.copy()
        if _hide_tbd:
            sp_df_filtered = sp_df_filtered[sp_df_filtered["Pitcher"].astype(str).str.upper() != "TBD"]
        if _hide_unmatched:
            sp_df_filtered = sp_df_filtered[
                ~(sp_df_filtered["xwOBA"].isna() & sp_df_filtered["K%"].isna())
            ]
        if _min_pa and _min_pa > 0 and "PA" in sp_df_filtered.columns:
            sp_df_filtered = sp_df_filtered[
                sp_df_filtered["PA"].fillna(-1).astype(float) >= float(_min_pa)
            ]

        # Coverage summary — one chip per data tier so the user can see at a
        # glance how much of the slate is grounded in real Statcast data.
        if "Source" in sp_df.columns:
            counts = sp_df["Source"].fillna("No sample").value_counts().to_dict()
            total = int(sum(counts.values()))
            def _ct(tag): return int(counts.get(tag, 0))
            n_savant = _ct("Savant") + _ct("Savant·sm") + _ct("Savant·xs")
            n_api    = _ct("StatsAPI")
            n_none   = _ct("No sample")
            st.markdown(
                f'<div style="margin: 4px 0 10px 0; font-size:.85rem; color:#334155;">'
                f'<b>{total}</b> probable starters · '
                f'<span style="color:#166534;">●</span> {n_savant} Savant 2026 · '
                f'<span style="color:#1e40af;">●</span> {n_api} StatsAPI fallback · '
                f'<span style="color:#64748b;">●</span> {n_none} no sample'
                f'</div>',
                unsafe_allow_html=True,
            )
        # Highlight if any pitcher couldn't be matched to ANY 2026 source.
        unmatched = sp_df[sp_df["Source"] == "No sample"] if "Source" in sp_df.columns else pd.DataFrame()
        if not unmatched.empty and not _hide_unmatched:
            names = ", ".join(unmatched["Pitcher"].astype(str).tolist())
            st.caption(
                f"ℹ️ No 2026 sample yet for: **{names}**. Likely a season debut "
                "or recent call-up — metrics will populate after their first appearance."
            )
        if sp_df_filtered.empty:
            st.info("No pitchers match the current filters. Try lowering Min PA or unchecking Hide TBD.")
        st.markdown(render_slate_pitcher_html(sp_df_filtered, schedule_df), unsafe_allow_html=True)
        st.markdown(
            '<div class="sp-legend">'
            'Sorted by <code>↓ Pitch Score</code> (35% xwOBA-against · 25% K-BB% · '
            '20% Whiff% · 20% Barrel%-against). Pitch Score is only published when '
            '≥60% of those inputs are real (no placeholder fills). Green = stronger '
            'pitcher, red = weaker. <code>SwStr%</code> = Swing% × Whiff%. '
            'Source chip shows where each row’s data came from: '
            '<b>Savant</b> 2026 (Statcast), <b>Savant·sm</b>/<b>·xs</b> (small sample), '
            '<b>StatsAPI</b> fallback (season totals only — fills K%, BB%, ERA, WHIP, IP), '
            'or <b>No sample</b> (— shown across the row).'
            '</div>',
            unsafe_allow_html=True,
        )
        # CSV download
        csv_bytes = sp_df_filtered.drop(columns=[c for c in sp_df_filtered.columns if c.startswith("_")], errors="ignore").to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download Slate Pitchers (CSV)",
            data=csv_bytes,
            file_name=f"slate_pitchers_{selected_date}.csv",
            mime="text/csv",
            width='content',
        )
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

    with st.spinner("Scoring sleeper candidates across the slate…"):
        hrs_df = build_hr_sleepers_table(schedule_df, batters_df, pitchers_df)

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
            'Matchup 25% (Matchup 10 · Ceiling 8 · kHR 7) · Sleeper bonus 15% (low HR total + lower spot).'
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

    with st.spinner("Scoring TB targets across the slate…"):
        tb_df = build_targets_table(schedule_df, batters_df, pitchers_df, mode="tb")

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
            '★ marks a platoon edge (LHB vs RHP / RHB vs LHP).'
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

    with st.spinner("Scoring HRR targets across the slate…"):
        hrr_df = build_targets_table(schedule_df, batters_df, pitchers_df, mode="hrr")

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
            '★ = platoon edge.'
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

# ============== 2+ RBI view ==============
if _view == "🔥 2+ RBI":
    st.markdown(
        '<div class="section-title" style="font-size:1.4rem;margin-top:8px;">'
        '🔥 2+ RBI Plays — Top Targets</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Best plays for clearing 2+ RBI tonight. RBI Score (0-100) blends '
        '<b>Power</b> 40% (xSLG, ISO, Barrel%, HardHit%) · <b>Lineup spot</b> 30% '
        '(spots 3-5 dominate — they bat with runners on most often) · '
        '<b>Tonight’s matchup</b> 20% (opp SP, park, weather, ceiling) · '
        '<b>Contact</b> 10% (xBA — must put it in play).'
        '</div>',
        unsafe_allow_html=True,
    )
    if batters_df.empty:
        st.warning("Batter CSV hasn’t loaded yet.")
        st.stop()

    with st.spinner("Scoring 2+ RBI targets across the slate…"):
        rbi_df = build_targets_table(schedule_df, batters_df, pitchers_df, mode="rbi2")

    if rbi_df.empty:
        st.info("No lineups posted yet across the slate. Check back closer to first pitch.")
        st.stop()

    # Filters
    f_cols = st.columns([1.0, 1.0, 1.0, 1.0, 1.6])
    with f_cols[0]:
        _max_spot = st.number_input("Max lineup spot", min_value=1, max_value=9, value=6, step=1,
                                    key="rbi_max_spot",
                                    help="Bottom-of-order bats rarely come up with runners on.")
    with f_cols[1]:
        _min_iso = st.number_input("Min ISO", min_value=0.000, max_value=0.400, value=0.150, step=0.005,
                                   format="%.3f", key="rbi_min_iso",
                                   help="Floor for power — RBIs usually need an XBH or HR.")
    with f_cols[2]:
        _min_barrel = st.number_input("Min Barrel%", min_value=0.0, max_value=20.0, value=6.0, step=0.5,
                                      key="rbi_min_barrel",
                                      help="Power profile floor. League average is ~7%.")
    with f_cols[3]:
        _min_pa = st.number_input("Min PA", min_value=0, max_value=700, value=80, step=10,
                                  key="rbi_min_pa")
    with f_cols[4]:
        _topn = st.slider("Show top N", min_value=10, max_value=30, value=15, step=5, key="rbi_topn")

    fdf = rbi_df.copy()
    fdf = fdf[fdf["Spot"] <= int(_max_spot)]
    if _min_iso and _min_iso > 0:
        fdf = fdf[fdf["ISO"].fillna(-1) >= float(_min_iso)]
    if _min_barrel and _min_barrel > 0:
        fdf = fdf[fdf["Barrel%"].fillna(-1) >= float(_min_barrel)]
    if _min_pa and _min_pa > 0:
        fdf = fdf[fdf["PA"].fillna(0).astype(float) >= float(_min_pa)]
    fdf = fdf.head(int(_topn)).reset_index(drop=True)

    if fdf.empty:
        st.info("No targets match the current filters. Try lowering Min ISO or Min Barrel%.")
    else:
        st.markdown(render_targets_html(fdf, mode="rbi2"), unsafe_allow_html=True)
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
            'Sort: RBI Score ↓. '
            'Pills: <b>CONF</b>/<b>PROJ</b>/<b>TBD</b> for lineup status. '
            '★ = platoon edge.'
            '</div>',
            unsafe_allow_html=True,
        )
        dl_cols = [c for c in fdf.columns if not c.startswith("_")]
        csv_bytes = fdf[dl_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download 2+ RBI Targets (CSV)",
            data=csv_bytes,
            file_name=f"rbi_targets_{selected_date}.csv",
            mime="text/csv",
            width='content',
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
        f'border-left:3px solid #0f3a2e; background:#ecfdf5; '
        f'border-radius:6px; color:#065f46; font-size:0.88rem;">'
        f'🕒 Using <b>{_n_elig}</b> pre-game matchup'
        f'{"s" if _n_elig != 1 else ""}; '
        f'started &amp; completed games excluded'
        f'{f" ({_n_excl} hidden)" if _n_excl > 0 else ""}.'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.spinner("Scoring HR candidates across the slate…"):
        ai_pool_df = build_hr_sleepers_table(eligible_schedule_df, batters_df, pitchers_df)

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

        # 3. Power — Barrel%, HardHit%, ISO, xSLG
        cands = []
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
        """One-line compact stats: Ceiling · Barrel · HH · ISO · Zone fit · Spot."""
        parts = []
        ceil = leg.get("Ceiling")
        try:
            cf = float(ceil)
            if not pd.isna(cf):
                parts.append(f"Ceiling <b>{cf:.0f}</b>")
        except Exception:
            pass
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
                f'Bat {leg.get("Bat","") or "—"} · Spot {leg.get("Spot","")}</span>'
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
        ".aip-card { background:#fff; border-radius:14px; padding:14px 14px 8px 14px; "
        "  box-shadow:0 2px 12px rgba(15,23,42,.07); margin: 8px 0 16px 0; "
        "  border-left:5px solid #0f3a2e; }"
        ".aip-card.aip-empty { border-left-color:#cbd5e1; padding:14px; }"
        ".aip-card-head { display:flex; align-items:center; justify-content:space-between; "
        "  gap:10px; margin-bottom:6px; flex-wrap:wrap; }"
        ".aip-card-title { font-weight:900; font-size:1.08rem; color:#0f3a2e; "
        "  letter-spacing:.01em; }"
        ".aip-card-sub { color:#64748b; font-size:.82rem; margin-top:2px; }"
        ".aip-badge { font-size:.74rem; }"
        ".aip-leg { padding:10px 0; border-top:1px dashed #e2e8f0; }"
        ".aip-leg:first-of-type { border-top:none; }"
        ".aip-leg-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }"
        ".aip-leg-num { font-size:.72rem; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; color:#fcd34d; background:#0f3a2e; "
        "  padding:3px 8px; border-radius:6px; }"
        ".aip-leg-name { font-weight:800; color:#0f172a; flex:1 1 200px; "
        "  font-size:.98rem; }"
        ".aip-meta { color:#64748b; font-weight:500; font-size:.82rem; }"
        ".aip-leg-score { display:flex; align-items:center; gap:6px; }"
        ".aip-score { font-weight:900; font-size:1.05rem; color:#0f172a; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-ctx { color:#475569; font-size:.84rem; margin: 4px 0 4px 0; }"
        ".aip-bpw-line { display:block; margin: 4px 0 6px 0; padding:6px 10px; "
        "  border-radius:8px; font-size:.86rem; font-weight:700; line-height:1.35; "
        "  border:1px solid transparent; }"
        ".aip-bpw-line b { font-weight:800; }"
        ".aip-bpw-pct { font-weight:700; margin-left:4px; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-bpw-good { background:#dcfce7; color:#065f46; border-color:#86efac; }"
        ".aip-bpw-ok   { background:#fef9c3; color:#713f12; border-color:#fde68a; }"
        ".aip-bpw-bad  { background:#fee2e2; color:#7f1d1d; border-color:#fecaca; }"
        ".aip-stats { color:#0f172a; font-size:.84rem; margin: 0 0 4px 0; "
        "  background:#f8fafc; border-radius:6px; padding:5px 8px; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-reasons { margin: 4px 0 2px 18px; padding:0; color:#0f172a; "
        "  font-size:.88rem; }"
        ".aip-reasons li { margin: 1px 0; line-height:1.35; }"
        ".aip-disclaimer { color:#64748b; font-size:.78rem; margin: 6px 2px 12px 2px; "
        "  font-style:italic; }"
        "@media (max-width:520px) { .aip-leg-name { font-size:.92rem; } "
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
        "  background: linear-gradient(110deg, #04130b 0%, #0f3a2e 55%, #1d5a3f 100%); "
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
        ".rr-card { background:#fff; border-radius:14px; padding:12px 14px; "
        "  box-shadow:0 2px 12px rgba(15,23,42,.08); margin: 8px 0; "
        "  border-left:6px solid #facc15; }"
        ".rr-card .rr-rank { display:inline-block; min-width:28px; "
        "  text-align:center; font-weight:900; color:#facc15; "
        "  background:#0f3a2e; border-radius:6px; padding:3px 8px; "
        "  font-size:.78rem; letter-spacing:.06em; }"
        ".rr-card .rr-name { font-weight:900; color:#0f172a; font-size:1.04rem; "
        "  margin-left:8px; }"
        ".rr-card .rr-meta { color:#475569; font-size:.84rem; margin-top:2px; }"
        ".rr-card .rr-score { float:right; font-weight:900; color:#0f3a2e; "
        "  font-size:1.06rem; font-variant-numeric: tabular-nums; }"
        ".rr-card .rr-stats { color:#0f172a; font-size:.84rem; "
        "  background:#f8fafc; border-radius:6px; padding:6px 9px; margin-top:6px; "
        "  font-variant-numeric: tabular-nums; }"
        ".rr-card .rr-why { margin: 6px 0 2px 18px; padding:0; color:#0f172a; "
        "  font-size:.86rem; }"
        ".rr-card .rr-why li { margin: 1px 0; line-height:1.35; }"
        # Combos panel
        ".rr-combos { background:#0f3a2e; color:#fde68a; border-radius:14px; "
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
        f'border-left:3px solid #facc15; background:#fffbeb; '
        f'border-radius:6px; color:#713f12; font-size:0.88rem;">'
        f'🔒 Locked ticket for <b>{selected_date}</b> · '
        f'using <b>{len(rr_schedule)}</b> pre-game matchup'
        f'{"s" if len(rr_schedule) != 1 else ""}'
        f'{f" · started/completed excluded ({_rr_excl} hidden)" if _rr_excl > 0 else ""}.'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.spinner("Scoring HR candidates across every available metric…"):
        rr_pool_df = build_hr_sleepers_table(rr_schedule, batters_df, pitchers_df)

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
        # Composite — broad coverage, no single metric dominates
        composite = (
            0.22 * sleeper      # base HR Sleeper Score (already broad)
          + 0.16 * ceiling      # park × weather × SP
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
        xwoba  = _f(row.get("xwOBA"))
        fb     = _f(row.get("FB%"))
        pull   = _f(row.get("Pull%"))
        bs     = _f(row.get("BatSpeed"))
        ss     = _f(row.get("SweetSpot%"))
        khr    = _f(row.get("kHR"))
        match  = _f(row.get("Matchup"))
        if ceil is not None and ceil >= 70:
            why.append(f"🏟️ Ceiling <b>{ceil:.0f}</b> — park/weather/SP combo lights up")
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
            f'Bat {row.get("Bat","") or "—"} · '
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
        f'border-left:3px solid #0f3a2e; background:#ecfdf5; '
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
            throws = leg.get("Throws") or ""
            throws_chip = f" · {throws}HP" if throws else ""
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
        ".kgen-card { background:#fff; border-radius:14px; padding:14px 14px 8px 14px; "
        "  box-shadow:0 2px 12px rgba(15,23,42,.07); margin: 8px 0 16px 0; "
        "  border-left:5px solid #1d4ed8; }"
        ".kgen-card.kgen-empty { border-left-color:#cbd5e1; padding:14px; }"
        ".kgen-card-head { display:flex; align-items:center; justify-content:space-between; "
        "  gap:10px; margin-bottom:6px; flex-wrap:wrap; }"
        ".kgen-card-title { font-weight:900; font-size:1.08rem; color:#1e3a8a; "
        "  letter-spacing:.01em; }"
        ".kgen-card-sub { color:#64748b; font-size:.82rem; margin-top:2px; }"
        ".kgen-badge { font-size:.74rem; }"
        ".kgen-leg { padding:10px 0; border-top:1px dashed #e2e8f0; }"
        ".kgen-leg:first-of-type { border-top:none; }"
        ".kgen-leg-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }"
        ".kgen-leg-num { font-size:.72rem; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; color:#fde68a; background:#1e3a8a; "
        "  padding:3px 8px; border-radius:6px; }"
        ".kgen-leg-name { font-weight:800; color:#0f172a; flex:1 1 200px; "
        "  font-size:.98rem; }"
        ".kgen-meta { color:#64748b; font-weight:500; font-size:.82rem; }"
        ".kgen-leg-score { display:flex; align-items:center; gap:6px; }"
        ".kgen-score { font-weight:900; font-size:1.05rem; color:#0f172a; "
        "  font-variant-numeric: tabular-nums; }"
        ".kgen-ctx { color:#1e3a8a; font-size:.86rem; margin: 4px 0 4px 0; "
        "  font-variant-numeric: tabular-nums; }"
        ".kgen-stats { color:#0f172a; font-size:.84rem; margin: 0 0 4px 0; "
        "  background:#f8fafc; border-radius:6px; padding:5px 8px; "
        "  font-variant-numeric: tabular-nums; }"
        ".kgen-reasons { margin: 4px 0 2px 18px; padding:0; color:#0f172a; "
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
        f'border-left:3px solid #0f3a2e; background:#ecfdf5; '
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

        n_xba   = _norm_h(xba,    0.220, 0.310)
        n_avg   = _norm_h(avg,    0.210, 0.320)
        n_kinv  = 100.0 - _norm_h(k_pct, 12.0, 32.0)  # lower K% better
        n_ld    = _norm_h(ld,     17.0, 28.0)
        n_ss    = _norm_h(ss_pct, 28.0, 40.0)
        contact = 0.12*n_xba + 0.09*n_avg + 0.07*n_kinv + 0.04*n_ld + 0.03*n_ss

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
            "BatSpeed": bs, "xwOBA": xwoba, "xSLG": xslg,
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
            try:
                cc = build_game_context(g)
            except Exception:
                continue
            wx = cc.get("weather", {}) if isinstance(cc, dict) else {}
            for side, lineup_df, opp_pitcher in (
                ("away", cc.get("away_lineup"), g.get("home_probable")),
                ("home", cc.get("home_lineup"), g.get("away_probable")),
            ):
                if lineup_df is None or lineup_df.empty:
                    continue
                p_row = find_pitcher_row(pitchers_df, opp_pitcher)
                opp_p_hand = (cc.get("home_pitch_hand") if side == "away"
                              else cc.get("away_pitch_hand")) or ""
                lineup_status = (cc.get("away_status") if side == "away"
                                 else cc.get("home_status")) or "Not Posted"
                bpw = _h_bpw_map.get(str(g.get("short_label", "")), {
                    "label":"OK","tier":"ok","hr_pct":0,
                    "icon":"🟡","tooltip":"Park/weather signal"
                })
                for _, r in lineup_df.iterrows():
                    b = find_player_row(batters_df, r["name_key"], r["team"])
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
        if xba is not None and xba >= 0.290:
            reasons.append(f"🎯 xBA <b>{xba:.3f}</b> — elite contact profile")
        elif xba is not None and xba >= 0.270:
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
                f'Bat {leg.get("Bat","") or "—"} · Spot {leg.get("Spot","")}</span>'
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
        ".aip-card { background:#fff; border-radius:14px; padding:14px 14px 8px 14px; "
        "  box-shadow:0 2px 12px rgba(15,23,42,.07); margin: 8px 0 16px 0; "
        "  border-left:5px solid #0f3a2e; }"
        ".aip-card.aip-empty { border-left-color:#cbd5e1; padding:14px; }"
        ".aip-card-head { display:flex; align-items:center; justify-content:space-between; "
        "  gap:10px; margin-bottom:6px; flex-wrap:wrap; }"
        ".aip-card-title { font-weight:900; font-size:1.08rem; color:#0f3a2e; "
        "  letter-spacing:.01em; }"
        ".aip-card-sub { color:#64748b; font-size:.82rem; margin-top:2px; }"
        ".aip-badge { font-size:.74rem; }"
        ".aip-leg { padding:10px 0; border-top:1px dashed #e2e8f0; }"
        ".aip-leg:first-of-type { border-top:none; }"
        ".aip-leg-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }"
        ".aip-leg-num { font-size:.72rem; font-weight:800; letter-spacing:.06em; "
        "  text-transform:uppercase; color:#fcd34d; background:#0f3a2e; "
        "  padding:3px 8px; border-radius:6px; }"
        ".aip-leg-name { font-weight:800; color:#0f172a; flex:1 1 200px; "
        "  font-size:.98rem; }"
        ".aip-meta { color:#64748b; font-weight:500; font-size:.82rem; }"
        ".aip-leg-score { display:flex; align-items:center; gap:6px; }"
        ".aip-score { font-weight:900; font-size:1.05rem; color:#0f172a; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-ctx { color:#475569; font-size:.84rem; margin: 4px 0 4px 0; }"
        ".aip-bpw-line { display:block; margin: 4px 0 6px 0; padding:6px 10px; "
        "  border-radius:8px; font-size:.86rem; font-weight:700; line-height:1.35; "
        "  border:1px solid transparent; }"
        ".aip-bpw-line b { font-weight:800; }"
        ".aip-bpw-pct { font-weight:700; margin-left:4px; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-bpw-good { background:#dcfce7; color:#065f46; border-color:#86efac; }"
        ".aip-bpw-ok   { background:#fef9c3; color:#713f12; border-color:#fde68a; }"
        ".aip-bpw-bad  { background:#fee2e2; color:#7f1d1d; border-color:#fecaca; }"
        ".aip-stats { color:#0f172a; font-size:.84rem; margin: 0 0 4px 0; "
        "  background:#f8fafc; border-radius:6px; padding:5px 8px; "
        "  font-variant-numeric: tabular-nums; }"
        ".aip-reasons { margin: 4px 0 2px 18px; padding:0; color:#0f172a; "
        "  font-size:.88rem; }"
        ".aip-reasons li { margin: 1px 0; line-height:1.35; }"
        ".aip-disclaimer { color:#64748b; font-size:.78rem; margin: 6px 2px 12px 2px; "
        "  font-style:italic; }"
        "@media (max-width:520px) { .aip-leg-name { font-size:.92rem; } "
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

render_game_carousel(schedule_df, selected_idx)
st.caption(f"Tap any game above to switch · currently viewing **{labels[selected_idx]}**")

game_row = schedule_df.iloc[selected_idx]
ctx = build_game_context(game_row)
weather = ctx["weather"]
render_game_header(game_row, ctx, weather)

# ----- Build all tables once -----
away_matchup = build_matchup_table(ctx["away_lineup"], batters_df, pitchers_df, game_row["home_probable"], weather, game_row["park_factor"],
                                   arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=game_row.get("home_probable_id"))
home_matchup = build_matchup_table(ctx["home_lineup"], batters_df, pitchers_df, game_row["away_probable"], weather, game_row["park_factor"],
                                   arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=game_row.get("away_probable_id"))

# ----- Tabs -----
# Tiny hint above the strip so mobile users know to swipe for more views
st.markdown(
    "<div style='display:flex;align-items:center;gap:8px;margin:6px 2px 4px;"
    "font-weight:800;font-size:0.95rem;color:#0f3a2e;'>"
    "<span style='display:inline-flex;align-items:center;justify-content:center;"
    "width:22px;height:22px;border-radius:50%;background:#facc15;color:#0f3a2e;"
    "font-weight:900;'>☰</span>"
    "Game views — tap a tab below (swipe → for more)"
    "</div>",
    unsafe_allow_html=True,
)
tab_matchup, tab_rolling, tab_hot, tab_cold, tab_injuries = st.tabs(
    ["📊 Matchup", "📈 Rolling", "🔥 Hot Batters", "🧊 Cold Batters", "🏥 Injuries"]
)

# ============== Matchup tab ==============
with tab_matchup:
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

    # away lineup — full heat-map stat board (one place for all stats)
    away_board = build_matchup_heatmap_board(
        ctx["away_lineup"], batters_df, pitchers_df,
        game_row["home_probable"], weather, game_row["park_factor"],
        arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df,
        opp_pitcher_id=game_row.get("home_probable_id"),
    )
    home_board = build_matchup_heatmap_board(
        ctx["home_lineup"], batters_df, pitchers_df,
        game_row["away_probable"], weather, game_row["park_factor"],
        arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df,
        opp_pitcher_id=game_row.get("away_probable_id"),
    )
    # Per-board widget keys so each game's away/home sort controls stay isolated
    # in Streamlit's session_state when the user switches between games.
    _board_key_base = str(game_row.get("game_pk", selected_idx))
    render_lineup_banner(game_row["away_id"], game_row["away_abbr"], game_row["home_probable"], ctx["away_status"])
    if away_board.empty:
        st.info(f"{game_row['away_abbr']} lineup not available yet — not enough recent games on file to project.")
    else:
        render_matchup_board_with_sort(
            away_board,
            key_prefix=f"away_{_board_key_base}",
            label=f"{game_row['away_abbr']} lineup",
        )
    # home lineup
    render_lineup_banner(game_row["home_id"], game_row["home_abbr"], game_row["away_probable"], ctx["home_status"])
    if home_board.empty:
        st.info(f"{game_row['home_abbr']} lineup not available yet — not enough recent games on file to project.")
    else:
        render_matchup_board_with_sort(
            home_board,
            key_prefix=f"home_{_board_key_base}",
            label=f"{game_row['home_abbr']} lineup",
        )
    st.caption(
        "Use the **Sort** controls above each lineup to rank by any column "
        "(High → Low or Low → High). Dark green = best, light green → yellow → "
        "orange → red = worst. Swipe horizontally to see all stats. SwStr% and "
        "GB% are reverse-scaled (lower = better for power); LA peaks around 14° "
        "(sweet-spot range)."
    )

    # ----- Top 3 Hitters for this game (above Pitcher Vulnerability) -----
    combined_for_ranking = pd.concat([away_matchup, home_matchup], ignore_index=True) \
        if (not away_matchup.empty or not home_matchup.empty) else pd.DataFrame()
    if not combined_for_ranking.empty and "Matchup" in combined_for_ranking.columns:
        top3 = combined_for_ranking.sort_values("Matchup", ascending=False).head(3).reset_index(drop=True)
        st.markdown('<div class="section-title" style="margin-top:18px;">🔥 Top 3 Hitters — This Game</div>', unsafe_allow_html=True)
        st.markdown(
            '<style>'
            '.top3-row { display:flex; gap:12px; flex-wrap:wrap; margin: 6px 0 14px 0; }'
            '.top3-card { flex: 1 1 0; min-width: 240px; background: #ffffff; border: 2px solid #e2e8f0; '
            '  border-radius: 14px; padding: 14px 16px 14px 16px; box-shadow: 0 2px 8px rgba(15,23,42,0.05); position: relative; }'
            '.top3-card.rank1 { border-color:#16a34a; background: linear-gradient(180deg, #f0fdf4 0%, #ffffff 60%); }'
            '.top3-card.rank2 { border-color:#22c55e; }'
            '.top3-card.rank3 { border-color:#84cc16; }'
            '.top3-rank { position:absolute; top:-10px; left:14px; background:#0f172a; color:#fff; '
            '  font-size: 0.72rem; font-weight: 800; padding: 3px 9px; border-radius: 999px; letter-spacing:.05em; }'
            '.top3-head { display:flex; align-items:center; gap: 12px; margin-top: 4px; }'
            '.top3-photo { width: 64px; height: 64px; flex: 0 0 64px; border-radius: 50%; '
            '  object-fit: cover; background: #e2e8f0; border: 2px solid #cbd5e1; }'
            '.top3-card.rank1 .top3-photo { border-color: #16a34a; }'
            '.top3-card.rank2 .top3-photo { border-color: #22c55e; }'
            '.top3-card.rank3 .top3-photo { border-color: #84cc16; }'
            '.top3-photo-fallback { width:64px; height:64px; flex:0 0 64px; border-radius:50%; '
            '  background:#0f172a; color:#facc15; display:flex; align-items:center; justify-content:center; '
            '  font-weight:900; font-size:1.1rem; border:2px solid #cbd5e1; }'
            '.top3-head-text { display:flex; flex-direction:column; min-width:0; }'
            '.top3-name { font-size: 1.02rem; font-weight: 800; color:#0f172a; margin-top: 2px; '
            '  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }'
            '.top3-meta { color:#64748b; font-size:.78rem; font-weight:700; margin-bottom: 0; letter-spacing:.02em; }'
            '.top3-stats { display:flex; gap:14px; flex-wrap:wrap; }'
            '.top3-stat { display:flex; flex-direction:column; }'
            '.top3-stat .lab { color:#64748b; font-size:.66rem; font-weight:800; text-transform:uppercase; letter-spacing:.06em; }'
            '.top3-stat .val { color:#0f172a; font-size:1.05rem; font-weight:800; }'
            '.top3-score { background:#dcfce7; color:#065f46; padding: 2px 10px; border-radius: 999px; '
            '  font-weight: 800; font-size: .82rem; display:inline-block; margin-top:8px; }'
            '</style>',
            unsafe_allow_html=True,
        )
        cards = []
        for i, r in top3.iterrows():
            rank_cls = f"rank{i+1}"
            name = str(r.get("Hitter", ""))
            team = str(r.get("Team", ""))
            spot = r.get("Spot", "")
            bat = r.get("_bat_side", "") or r.get("Bat", "")
            opp_pitcher = game_row["home_probable"] if team == game_row["away_abbr"] else game_row["away_probable"]
            matchup_v = r.get("Matchup", 0)
            ceiling_v = r.get("Ceiling", 0)
            zonefit_v = r.get("Zone Fit", 0)
            khr_v     = r.get("kHR", 0)
            hrform    = r.get("HR Form", "")
            # Player headshot from MLB CDN; fall back to initials disc when no id
            photo_url = player_headshot_url(r.get("_player_id"))
            if photo_url:
                photo_html = (
                    f'<img class="top3-photo" src="{photo_url}" alt="{name}" '
                    f'onerror="this.outerHTML=&#39;<div class=\'top3-photo-fallback\'>'
                    f'{(name[:1] or "?").upper()}</div>&#39;" />'
                )
            else:
                initials = "".join([p[:1] for p in name.split()[:2]]).upper() or "?"
                photo_html = f'<div class="top3-photo-fallback">{initials}</div>'
            # "Crushes" line — the 1-2 pitch types this hitter punishes most.
            crush_html = ""
            crush_df = hitter_pitch_crush(arsenal_batter_df, r.get("_player_id"), top_n=2, min_pa=15)
            if not crush_df.empty:
                chips = []
                for _, cr in crush_df.iterrows():
                    pt = str(cr.get("pitch_type", "")).strip().upper()
                    pname = PITCH_NAME_MAP.get(pt, str(cr.get("pitch_name", pt)))
                    pemoji = PITCH_EMOJI.get(pt, "⚾")
                    cw = cr.get("woba")
                    cslg = cr.get("slg")
                    woba_str = f"{float(cw):.3f}" if pd.notna(cw) else "—"
                    slg_str = f"{float(cslg):.3f}" if pd.notna(cslg) else "—"
                    chips.append(
                        f'<span style="display:inline-flex; align-items:center; gap:5px; '
                        f'background:#fef3c7; color:#78350f; border:1px solid #fbbf24; '
                        f'border-radius:999px; padding:3px 9px; font-weight:800; font-size:0.78rem; '
                        f'margin-right:6px; margin-top:4px;">'
                        f'{pemoji} {pname} '
                        f'<span style="color:#92400e; font-weight:700;">'
                        f'· wOBA {woba_str} · SLG {slg_str}</span></span>'
                    )
                crush_html = (
                    '<div style="margin-top:10px;">'
                    '<div style="font-size:0.66rem; color:#64748b; text-transform:uppercase; '
                    'letter-spacing:.06em; font-weight:800; margin-bottom:2px;">Pitches They Crush</div>'
                    + "".join(chips) + '</div>'
                )
            else:
                crush_html = (
                    '<div style="margin-top:10px; font-size:0.72rem; color:#94a3b8; '
                    'font-weight:700;">Pitches They Crush — not enough sample yet.</div>'
                )
            cards.append(
                f'<div class="top3-card {rank_cls}">'
                f'<div class="top3-rank">#{i+1}</div>'
                f'<div class="top3-head">'
                f'{photo_html}'
                f'<div class="top3-head-text">'
                f'<div class="top3-name">{name}</div>'
                f'<div class="top3-meta">{team} · Spot {spot} · Bats {bat} · vs {opp_pitcher}</div>'
                f'<div class="top3-score">Matchup {matchup_v:.1f}</div>'
                f'</div>'
                f'</div>'
                f'<div class="top3-stats" style="margin-top:12px;">'
                f'<div class="top3-stat"><span class="lab">Ceiling</span><span class="val">{ceiling_v:.1f}</span></div>'
                f'<div class="top3-stat"><span class="lab">Zone Fit</span><span class="val">{zonefit_v:.3f}</span></div>'
                f'<div class="top3-stat"><span class="lab">kHR</span><span class="val">{khr_v:.1f}</span></div>'
                f'<div class="top3-stat"><span class="lab">HR Form</span><span class="val">{hrform}</span></div>'
                f'</div>'
                f'{crush_html}'
                f'</div>'
            )
        st.markdown('<div class="top3-row">' + "".join(cards) + '</div>', unsafe_allow_html=True)

    # Pitcher panels under matchup tab
    st.markdown('<div class="section-title" style="margin-top:14px;">🎯 Pitcher Vulnerability</div>', unsafe_allow_html=True)
    pc1, pc2 = st.columns(2)
    with pc1:
        away_mix = pitcher_pitch_mix(arsenal_pitcher_df, game_row.get("away_probable_id"))
        render_pitcher_panel(f"Away SP — {game_row['away_abbr']}", game_row["away_probable"],
                              ctx["away_pitch_hand"], find_pitcher_row(pitchers_df, game_row["away_probable"]),
                              pitch_mix_df=away_mix)
    with pc2:
        home_mix = pitcher_pitch_mix(arsenal_pitcher_df, game_row.get("home_probable_id"))
        render_pitcher_panel(f"Home SP — {game_row['home_abbr']}", game_row["home_probable"],
                              ctx["home_pitch_hand"], find_pitcher_row(pitchers_df, game_row["home_probable"]),
                              pitch_mix_df=home_mix)

# ============== Rolling tab ==============
with tab_rolling:
    win = st.radio("Window", ["7", "15", "30"], index=1, horizontal=True, format_func=lambda x: f"Last {x} days", key="roll_win")
    away_roll = build_rolling_table(ctx["away_lineup"], batters_df, win)
    home_roll = build_rolling_table(ctx["home_lineup"], batters_df, win)
    render_lineup_banner(game_row["away_id"], game_row["away_abbr"], game_row["home_probable"], ctx["away_status"])
    if away_roll.empty: st.info(f"{game_row['away_abbr']} lineup not posted yet.")
    else: st.dataframe(style_rolling_table(away_roll), width='stretch', hide_index=True)
    render_lineup_banner(game_row["home_id"], game_row["home_abbr"], game_row["away_probable"], ctx["home_status"])
    if home_roll.empty: st.info(f"{game_row['home_abbr']} lineup not posted yet.")
    else: st.dataframe(style_rolling_table(home_roll), width='stretch', hide_index=True)
    st.caption("Rolling form is derived from full-season Baseball Savant aggregates with the selected window weighting recency emphasis.")

# ============== Hot / Cold Batters tabs (slate-wide) ==============
@st.cache_data(ttl=600, show_spinner=False)
def _build_slate_dataframe(_schedule_df, _batters_df, _pitchers_df, cache_key):
    """Score every batter in every posted lineup across the slate. Returns a single DataFrame.
    cache_key is a string used to invalidate the cache when the slate or data changes."""
    rows = []
    for _, g in _schedule_df.iterrows():
        try:
            cc = build_game_context(g)
            a = build_matchup_table(cc["away_lineup"], _batters_df, _pitchers_df, g["home_probable"], cc["weather"], g["park_factor"],
                                    arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=g.get("home_probable_id"))
            h = build_matchup_table(cc["home_lineup"], _batters_df, _pitchers_df, g["away_probable"], cc["weather"], g["park_factor"],
                                    arsenal_b=arsenal_batter_df, arsenal_p=arsenal_pitcher_df, opp_pitcher_id=g.get("away_probable_id"))
            for _, r in a.iterrows():
                d = r.to_dict(); d["Game"] = g["short_label"]; d["OppPitcher"] = g["home_probable"]; rows.append(d)
            for _, r in h.iterrows():
                d = r.to_dict(); d["Game"] = g["short_label"]; d["OppPitcher"] = g["away_probable"]; rows.append(d)
        except Exception:
            pass
    slate = pd.DataFrame(rows)
    if slate.empty:
        return slate
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
            parts.append(f"{g.get('game_pk','?')}:{cc.get('away_status','')}:{cc.get('home_status','')}")
        except Exception:
            parts.append(f"{g.get('game_pk','?')}:err")
    return "|".join(parts)

_slate_lineup_sig = _slate_lineup_signature(schedule_df)
_slate_cache_key = f"{selected_date}_{len(schedule_df)}_{_slate_lineup_sig}"
_slate_df = _build_slate_dataframe(schedule_df, batters_df, pitchers_df, _slate_cache_key)

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
                              "Matchup", "Test Score", "Ceiling", "Zone Fit", "HR Form", "kHR",
                              "ISO", "Barrel%", "HardHit%"] if c in ranked.columns]
    out = ranked[show_cols]
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
    st.dataframe(
        style_matchup_table(out),
        width='stretch',
        hide_index=True,
        height=min(640, 60 + 38 * len(out)),
    )
    csv = out.to_csv(index=False)
    st.download_button(
        f"⬇️ Download {title} CSV",
        csv,
        file_name=f"{selected_date}_{'hot' if top else 'cold'}_top{n}.csv",
        mime="text/csv",
        width='stretch',
    )

with tab_hot:
    st.markdown(
        '<div style="margin: 4px 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Top 15 hitters across the entire slate — ranked by Matchup score (combines opposing pitcher, '
        'hand split, park, weather, recent form). These are the most exploitable spots tonight.'
        '</div>', unsafe_allow_html=True)
    _render_leaderboard(_slate_df, "🔥 Hot Batters — Top 15", top=True, n=15, sort_col="Matchup")

with tab_cold:
    st.markdown(
        '<div style="margin: 4px 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Bottom 15 hitters across the slate — toughest matchups. Useful for fade lists, '
        'unders, and pitcher-side bets.'
        '</div>', unsafe_allow_html=True)
    _render_leaderboard(_slate_df, "🧊 Cold Batters — Bottom 15", top=False, n=15, sort_col="Matchup")

# ============== Injuries tab ==============
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
        "  font-size:.92rem; background:#fff; border-radius:12px; overflow:hidden; "
        "  box-shadow: 0 2px 10px rgba(15,23,42,.06); margin: 6px 0 4px 0; }"
        ".ds-tbl th { background:#0f3a2e; color:#fcd34d; text-align:left; "
        "  font-weight:800; padding:9px 10px; letter-spacing:.03em; font-size:.78rem; "
        "  text-transform:uppercase; }"
        ".ds-tbl td { padding:8px 10px; border-bottom:1px solid #f1f5f9; "
        "  color:#0f172a; vertical-align: top; }"
        ".ds-tbl tr:nth-child(even) td { background:#fafafa; }"
        ".ds-pill { display:inline-block; padding:3px 9px; border-radius:999px; "
        "  font-weight:800; font-size:.74rem; letter-spacing:.04em; "
        "  white-space:nowrap; }"
        ".ds-detail { color:#475569; font-size:.82rem; line-height:1.35; }"
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
    '<div class="footer">⚾ <b>MrBets850 MLB Edge</b> · '
    'Powered by Baseball Savant + MLB StatsAPI + Open-Meteo · '
    'Tiers: Elite 🟢 (≥130) · Strong 🟢 (110-129) · OK 🟡 (95-109) · Avoid 🔴 (&lt;95) · '
    'Heatmaps: Green = Strong · Yellow = OK · Red = Avoid · For research purposes only.</div>',
    unsafe_allow_html=True,
)
