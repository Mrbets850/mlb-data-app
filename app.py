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
    "pitchers":        "Data:savant_pitchers.csv.csv",
    # Pitcher results (xwOBA, Whiff%, Barrel%-against, HH%, FB%, K%, BB%, etc.)
    # used by the Slate Pitchers tab. Joined to the slate by player_id.
    "pitcher_stats":   "Data:savant_pitcher_stats.csv",
}

def raw_github_url(path: str) -> str:
    encoded = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{encoded}"

CSV_URLS = {label: raw_github_url(name) for label, name in CSV_FILES.items()}

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
[data-theme="dark"] .top-tab-row [role="radiogroup"] > label {
    background: #1e293b !important;
    color: #f1f5f9 !important;
    border-color: #475569 !important;
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
def load_remote_csv(url):
    """Fetch a Savant CSV via raw GitHub.

    Returns (df, last_modified_utc). last_modified_utc is the GitHub
    object's Last-Modified header parsed to a UTC timestamp, or None
    if not available. We HEAD the URL first (cheap) so we know exactly
    how stale the underlying file in the repo is — independent of our
    in-process cache TTL."""
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
        "batters":       "Baseball Savant · Batter Statcast leaderboard",
        "pitchers":      "Baseball Savant · Pitcher Statcast leaderboard",
        "pitcher_stats": "Baseball Savant · Pitcher results leaderboard",
    }
    # Savant CSVs are refreshed nightly via GitHub Actions, so a 36-hr
    # cushion accounts for in-day Savant publishing latency without
    # flagging a healthy file as stale.
    for label, url in CSV_URLS.items():
        df, last_mod = load_remote_csv(url)
        out[label] = df
        key = f"savant:{label}"
        if df is None or df.empty:
            record_source(
                key,
                label=SOURCE_LABEL.get(label, f"Savant · {label}"),
                url=url, status="error",
                detail="Empty CSV — Savant may have rate-limited the nightly refresh.",
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
        "avg_swing_speed": "SwingSpeed",
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
# Sportsbook O/U totals via the-odds-api.com (free 500/mo tier)
# Looks for st.secrets["ODDS_API_KEY"]; if missing, returns empty dict.
# Cached 30 min so we don't burn requests.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def get_odds_totals_map() -> dict:
    """Return {(away_abbr, home_abbr): line_float} for today's MLB games.
    Returns {} if API key not configured or call fails."""
    SRC = "oddsapi:totals"
    LABEL = "the-odds-api · MLB totals (O/U lines)"
    try:
        key = st.secrets["ODDS_API_KEY"]
    except Exception:
        record_source(SRC, label=LABEL, status="unconfigured",
                      detail="No ODDS_API_KEY secret. Park-history O/U falls back to no book line.",
                      max_age_min=60)
        return {}
    if not key:
        record_source(SRC, label=LABEL, status="unconfigured",
                      detail="ODDS_API_KEY is empty.",
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

def build_game_context(game_row):
    weather = get_weather(game_row["lat"], game_row["lon"], game_row["game_time_utc"])
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

def find_player_row(df, name_key, team):
    if df.empty: return None
    exact = df[(df["name_key"] == name_key) & (df["team_key"] == norm_team(team))]
    if not exact.empty: return exact.iloc[0]
    exact2 = df[df["name_key"] == name_key]
    if not exact2.empty: return exact2.iloc[0]
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
def build_matchup_table(lineup_df, batters_df, pitchers_df, opp_pitcher_name, weather, park_factor):
    """The main heatmap-ready dataframe — columns mirror your reference site."""
    cols = ["Spot", "Hitter", "Team", "Bat", "Matchup", "Test Score",
            "Ceiling", "Zone Fit", "HR Form", "kHR", "HR", "ISO", "Barrel%", "HardHit%"]
    if lineup_df.empty:
        return pd.DataFrame(columns=cols)
    p_row = find_pitcher_row(pitchers_df, opp_pitcher_name)
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
        rows.append({
            "Spot": int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
            "Hitter": r["player_name"],
            "Team": norm_team(r["team"]),
            "Bat": r["bat_side"] or "",
            "Matchup": m,
            "Test Score": ts,
            "Ceiling": cl,
            "Zone Fit": zf,
            "HR Form": f"{int(hrf)}% {arrow}",
            "_HR Form Num": hrf,
            "kHR": khr,
            "HR": safe_float(b_row.get("HR") if b_row is not None else None, 0),
            "ISO": safe_float(b_row.get("ISO") if b_row is not None else None, 0.170),
            "Barrel%": safe_float(b_row.get("Barrel%") if b_row is not None else None, 8.0),
            "HardHit%": safe_float(b_row.get("HardHit%") if b_row is not None else None, 38.0),
            # carried-along (hidden) so the Top 3 cards can show MLB headshots
            "_player_id": r.get("player_id") if "player_id" in r.index else None,
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

def build_pitcher_zones_table(pitcher_name, pitchers_df):
    """Pitcher arsenal table — uses the FF/SL/CH/CU/SI/FC pitch-mix columns."""
    p = find_pitcher_row(pitchers_df, pitcher_name)
    if p is None: return pd.DataFrame(columns=["Pitch", "Velo", "Spin", "H Break", "V Break"])
    pitches = [
        ("4-Seam",  "ff"), ("Sinker",  "si"),
        ("Cutter",  "fc"), ("Slider",  "sl"),
        ("Curve",   "cu"), ("Change",  "ch"),
    ]
    rows = []
    for label, prefix in pitches:
        n = safe_float(p.get(f"n_{prefix}_formatted"), 0)
        if n <= 0: continue
        rows.append({
            "Pitch": label,
            "Usage%": round(n, 1),
            "Velo": round(safe_float(p.get(f"{prefix}_avg_speed")), 1),
            "Spin": int(safe_float(p.get(f"{prefix}_avg_spin"))),
            "H Break": round(safe_float(p.get(f"{prefix}_avg_break_x")), 1),
            "V Break": round(safe_float(p.get(f"{prefix}_avg_break_z")), 1),
        })
    return pd.DataFrame(rows)

def build_hitter_zones_table(lineup_df, batters_df):
    """Hitter batted-ball / swing profile — shows where each hitter does damage."""
    cols = ["Spot", "Hitter", "Team", "Pull%", "Oppo%", "FB%", "LD%", "GB%", "SwingSpeed", "Whiff%"]
    if lineup_df.empty: return pd.DataFrame(columns=cols)
    rows = []
    for _, r in lineup_df.iterrows():
        b = find_player_row(batters_df, r["name_key"], r["team"])
        rows.append({
            "Spot": int(r["lineup_spot"]) if pd.notna(r["lineup_spot"]) else 99,
            "Hitter": r["player_name"],
            "Team": norm_team(r["team"]),
            "Pull%":   round(safe_float(b.get("Pull%") if b is not None else None, 38), 1),
            "Oppo%":   round(safe_float(b.get("Oppo%") if b is not None else None, 25), 1),
            "FB%":     round(safe_float(b.get("FB%") if b is not None else None, 35), 1),
            "LD%":     round(safe_float(b.get("LD%") if b is not None else None, 22), 1),
            "GB%":     round(safe_float(b.get("GB%") if b is not None else None, 43), 1),
            "SwingSpeed": round(safe_float(b.get("SwingSpeed") if b is not None else None, 70), 1),
            "Whiff%":  round(safe_float(b.get("Whiff%") if b is not None else None, 25), 1),
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
    # Hide internal numeric column from display
    show_cols = [c for c in df.columns if c not in ("_HR Form Num", "_player_id")]
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

def style_zones_table(df, kind="hitter"):
    if df.empty: return df
    styler = df.style.format(precision=1)
    base_text = [
        {"selector": "th", "props": [("color", "#0f172a"), ("font-size", "12px"),
                                     ("font-weight", "800"), ("background-color", "#f1f5f9"),
                                     ("text-transform", "uppercase"), ("letter-spacing", "0.04em")]},
        {"selector": "td", "props": [("color", "#0f172a"), ("font-size", "13px"), ("font-weight", "700")]},
    ]
    styler = styler.set_table_styles(base_text)
    if kind == "hitter":
        for c, lo, hi, rev in [("Pull%", 30, 50, False), ("Oppo%", 18, 32, False),
                                ("FB%", 28, 45, False), ("LD%", 18, 28, False),
                                ("GB%", 35, 50, True),  ("SwingSpeed", 65, 78, False),
                                ("Whiff%", 18, 35, True)]:
            if c in df.columns:
                styler = styler.map(lambda v, lo=lo, hi=hi, rev=rev: heat_color(v, lo, hi, reverse=rev), subset=[c])
    else:
        if "Velo" in df.columns: styler = styler.map(lambda v: heat_color(v, 82, 100), subset=["Velo"])
        if "Spin" in df.columns: styler = styler.map(lambda v: heat_color(v, 1800, 2700), subset=["Spin"])
    return styler

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
    """Build one display row for the Slate Pitchers table by joining the
    schedule's probable-pitcher id to the Savant pitcher_stats CSV."""
    pid = game_row.get(f"{side}_probable_id")
    pname = game_row.get(f"{side}_probable") or ""
    if not pid or pname in ("", "TBD"):
        return None
    stat = _slate_pitcher_lookup(pitcher_stats_df, pid)
    throws = _fetch_pitcher_throws(int(pid))

    # standardize_columns has already mapped raw Savant columns to canonical
    # names where possible: K%, BB%, xwOBA, wOBA, Whiff%, Swing%, Barrel%,
    # HardHit%, FB%, GB%, SweetSpot%. Things it leaves alone (CSV-only):
    # f_strike_percent, in_zone_percent, meatball_percent, avg_best_speed,
    # avg_hyper_speed.
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
    fstrike = _g("f_strike_percent")
    in_zone = _g("in_zone_percent")
    meatball = _g("meatball_percent")
    barrel = _g("Barrel%")
    hardhit = _g("HardHit%")
    sweet  = _g("SweetSpot%")
    fb_pct = _g("FB%")
    gb_pct = _g("GB%")
    ev_best   = _g("avg_best_speed")
    ev_hyper  = _g("avg_hyper_speed")

    # SwStr% (true): swing-rate × whiff-on-swing.
    sw_str = (swing / 100.0) * (whiff / 100.0) * 100.0 if (swing is not None and whiff is not None) else None
    # CSW% proxy: called-strike + whiff. The CSV doesn't expose called-strike
    # rate directly; use first-pitch-strike rate * (1 - whiff fraction) as a
    # rough called-strike proxy, then add SwStr.
    if (fstrike is not None) and (whiff is not None) and (swing is not None):
        called_proxy = fstrike * (1.0 - swing / 100.0)
        csw_proxy = called_proxy + (sw_str or 0.0)
    else:
        csw_proxy = None

    # ---- composite scores (higher = stronger pitcher) ----
    def _norm(v, lo, hi, reverse=False, default=50.0):
        if v is None:
            return default
        try:
            x = float(v)
        except Exception:
            return default
        x = max(lo, min(hi, x))
        pct = (x - lo) / (hi - lo) * 100.0
        return 100.0 - pct if reverse else pct

    # Convert xwoba like 0.265 -> 265 for normalization.
    xwoba_scaled = (xwoba * 1000.0) if (xwoba is not None and xwoba <= 1.0) else xwoba
    woba_scaled  = (woba  * 1000.0) if (woba  is not None and woba  <= 1.0) else woba

    # 35% xwOBA-against (lower good) + 25% K-BB% (higher) + 20% Whiff% (higher)
    # + 20% Barrel%-against (lower good)
    pitch_score = round(
        0.35 * _norm(xwoba_scaled, 250.0, 360.0, reverse=True)
        + 0.25 * _norm((k_pct or 0) - (bb_pct or 0), 5.0, 25.0)
        + 0.20 * _norm(whiff, 18.0, 33.0)
        + 0.20 * _norm(barrel, 4.0, 12.0, reverse=True),
        1,
    )
    strikeout_score = round(
        0.55 * _norm(k_pct,  18.0, 32.0)
        + 0.45 * _norm(whiff, 18.0, 33.0),
        1,
    )

    if side == "away":
        team_id = game_row["away_id"]; team_abbr = game_row["away_abbr"]; loc_marker = "@"
    else:
        team_id = game_row["home_id"]; team_abbr = game_row["home_abbr"]; loc_marker = "vs"

    def _r(v, n=1):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try: return round(float(v), n)
        except Exception: return None

    return {
        "_logo": logo_url(team_id) if team_id else "",
        "_player_id": int(pid),
        "Loc": loc_marker,
        "Team": team_abbr,
        "Pitcher": pname,
        "Throws": throws,
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
        "CSW%*": _r(csw_proxy, 1),
        "F-Strike%": _r(fstrike, 1),
        "Zone%": _r(in_zone, 1),
        "Barrel%": _r(barrel, 1),
        "HH%": _r(hardhit, 1),
        "FB%": _r(fb_pct, 1),
        "GB%": _r(gb_pct, 1),
        "Meatball%": _r(meatball, 1),
        "PA": int(pa) if pa is not None else None,
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
    "CSW%*":           (28.0, 34.0,   False),
    "F-Strike%":       (58.0, 68.0,   False),
    "Zone%":           (40.0, 52.0,   False),
    "Barrel%":         (4.0,  12.0,   True),
    "HH%":             (32.0, 45.0,   True),
    "FB%":             (30.0, 48.0,   False),
    "GB%":             (38.0, 55.0,   False),
    "Meatball%":       (5.0,  9.0,    True),
}

SLATE_PITCHER_FORMAT = {
    "Pitch Score": "{:.1f}", "Strikeout Score": "{:.1f}",
    "xwOBA": "{:.3f}", "wOBA": "{:.3f}",
    "K%": "{:.1f}", "BB%": "{:.1f}",
    "Whiff%": "{:.1f}", "SwStr%": "{:.1f}", "CSW%*": "{:.1f}",
    "F-Strike%": "{:.1f}", "Zone%": "{:.1f}",
    "Barrel%": "{:.1f}", "HH%": "{:.1f}",
    "FB%": "{:.1f}", "GB%": "{:.1f}", "Meatball%": "{:.1f}",
    "PA": "{:.0f}",
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

    show_cols = [c for c in df.columns if not c.startswith("_") and c != "Loc"]
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
                game_label = str(r.get("Game", ""))
                pid = r.get("_player_id")
                idx = label_to_idx.get(game_label)
                if idx is not None and pid:
                    # Deep-link: switch to Games view, select this game, jump
                    # to the Pitcher Zones anchor. The page's load handler
                    # below reads ?view=games&g=<idx>&section=pitcher_zones.
                    href = f"?view=games&g={idx}&section=pitcher_zones&p={pid}"
                    cells.append(
                        f'<td class="sp-pitcher"><a class="sp-pitcher-link" '
                        f'href="{href}" target="_self" '
                        f'title="Open {v} in Pitcher Zones">{v} →</a></td>'
                    )
                else:
                    cells.append(f'<td class="sp-pitcher">{v}</td>')
                continue
            if c in ("Throws", "Game", "Time"):
                cells.append(f"<td>{v if v not in (None, '') else '<span class=\"sp-na\">—</span>'}</td>")
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
        # Mini-card name links to the Pitcher Zones section for this game
        pid = row.get("_player_id")
        # The mini-card is rendered inside a single-game view, so we don't need
        # to switch games — a hash anchor scrolls to the Pitcher Zones tab.
        name_html = (
            f'<a href="#pitcher-zones-anchor" data-pid="{pid}">{name}</a>'
            if pid else name
        )
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

def build_hr_sleepers_table(_schedule_df, _batters_df, _pitchers_df):
    """Score every posted-lineup batter for HR sleeper potential and return a
    sorted DataFrame ready for rendering."""
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
        'transparent, free data (Open-Meteo · MLB Statsapi · The Odds API).</div>'
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
    if st.button("🔄 Refresh data", use_container_width=True, key="refresh_btn"):
        st.cache_data.clear()
        st.rerun()
with top_cols[2]:
    st.markdown('<div class="toolbar-spacer-label">.</div>', unsafe_allow_html=True)
    if st.button("📆 Today", use_container_width=True, key="today_btn"):
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
st.markdown(
    "<style>"
    # ---- Top-level view tabs: bold, mobile-friendly pills ----
    # Wrapper card: padded, rounded, flush against the brand bar above so it
    # reads as a navigation strip rather than a bare radio.
    # Gold strip to match the MrBets850 logo crown
    ".top-tab-row { margin: 8px 0 14px 0; padding: 10px; "
    "  background: linear-gradient(180deg, #fde68a 0%, #f59e0b 55%, #b45309 100%); "
    "  border-radius: 16px; border: 2px solid #92400e; "
    "  box-shadow: 0 2px 8px rgba(120,53,15,.25), inset 0 1px 0 rgba(255,255,255,.45); }"
    # Hide Streamlit's default 'View' label
    ".top-tab-row [data-testid=\"stRadio\"] > label { display:none; }"
    ".top-tab-row [data-testid=\"stWidgetLabel\"] { display:none; }"
    # The radio group: wrap on mobile, comfortable spacing
    ".top-tab-row [role=\"radiogroup\"] { gap: 10px; flex-wrap: wrap; "
    "  justify-content: flex-start; }"
    # Each pill: bold text, big tap target, clear unselected state with subtle
    # 'tap me' hint via slight lift
    ".top-tab-row [role=\"radiogroup\"] > label { "
    "  background: #ffffff; "
    "  padding: 10px 18px; "
    "  min-height: 44px; "          # iOS Apple HIG minimum tap target
    "  border-radius: 999px; "
    "  border: 2px solid #cbd5e1; "
    "  cursor: pointer; "
    "  font-weight: 800; "
    "  font-size: 0.98rem; "
    "  color: #0f172a; "
    "  transition: all .18s ease; "
    "  box-shadow: 0 1px 3px rgba(15,23,42,.06); "
    "  display: inline-flex; align-items: center; }"
    # Hover: lift slightly, highlight border
    ".top-tab-row [role=\"radiogroup\"] > label:hover { "
    "  border-color: #0f3a2e; "
    "  transform: translateY(-1px); "
    "  box-shadow: 0 4px 10px rgba(15,58,46,.12); }"
    # Selected pill: dark green gradient + gold text + glow ring — unmistakable
    ".top-tab-row [role=\"radiogroup\"] > label:has(input:checked) { "
    "  background: linear-gradient(110deg, #04130b 0%, #0f3a2e 60%, #1d5a3f 100%); "
    "  color: #facc15; "
    "  border-color: #facc15; "
    "  box-shadow: 0 0 0 3px rgba(250,204,21,.25), 0 6px 16px rgba(5,20,12,.35); "
    "  transform: translateY(-1px); }"
    # Hide the actual radio circle (we want pure pill UI)
    ".top-tab-row [role=\"radiogroup\"] > label > div:first-child { display:none !important; }"
    # Streamlit nests the text in extra divs — make sure the label text is bold,
    # readable, and inherits the pill's color (so gold-on-green works on selected)
    ".top-tab-row [role=\"radiogroup\"] > label p, "
    ".top-tab-row [role=\"radiogroup\"] > label span, "
    ".top-tab-row [role=\"radiogroup\"] > label div { "
    "  font-weight: 800 !important; "
    "  font-size: 0.98rem !important; "
    "  color: inherit !important; "
    "  letter-spacing: .01em; "
    "  line-height: 1.2; }"
    # Mobile (≤640px): bigger touch targets, full-width pills, larger text
    "@media (max-width: 640px) { "
    "  .top-tab-row { padding: 12px; } "
    "  .top-tab-row [role=\"radiogroup\"] { gap: 8px; } "
    "  .top-tab-row [role=\"radiogroup\"] > label { "
    "    flex: 1 1 calc(50% - 8px); "           # 2 pills per row on phones
    "    justify-content: center; "
    "    padding: 12px 10px; "
    "    min-height: 50px; "
    "    font-size: 1.0rem; } "
    "  .top-tab-row [role=\"radiogroup\"] > label p, "
    "  .top-tab-row [role=\"radiogroup\"] > label span, "
    "  .top-tab-row [role=\"radiogroup\"] > label div { "
    "    font-size: 1.0rem !important; } "
    "}"
    ".sp-legend { color:#64748b; font-size:.78rem; margin: 4px 0 12px 0; }"
    ".sp-legend code { background:#f1f5f9; padding: 1px 6px; border-radius:6px; "
    "  font-family: inherit; font-weight:700; color:#334155; }"
    "</style>",
    unsafe_allow_html=True,
)
# ---- Deep-link handler: ?view=games&g=<idx>&section=pitcher_zones&p=<pid> ----
# Read query params BEFORE the view radio is instantiated, since Streamlit
# forbids writing to st.session_state[<widget_key>] after the widget exists.
# This lets the Slate Pitchers table deep-link a clicked pitcher into that
# game's Pitcher Zones tab.
try:
    _qp_view = st.query_params.get("view", None)
    _qp_section = st.query_params.get("section", None)
except Exception:
    _qp_view = None
    _qp_section = None
if _qp_view == "games" and "top_view_tab" not in st.session_state:
    # Only set if the user hasn't already interacted with the radio this run.
    st.session_state["top_view_tab"] = "⚾ Games"
elif _qp_view == "games" and st.session_state.get("top_view_tab") != "⚾ Games":
    # Force a switch when arriving via deep-link, but only on the first such
    # arrival per click (use a one-shot flag).
    if not st.session_state.get("_deep_link_consumed"):
        st.session_state["top_view_tab"] = "⚾ Games"
        st.session_state["_deep_link_consumed"] = True
# Reset the consumed flag whenever ?view= is missing so a future click works.
if _qp_view is None:
    st.session_state.pop("_deep_link_consumed", None)

st.markdown('<div class="top-tab-row">', unsafe_allow_html=True)
_view = st.radio(
    "View",
    ["⚾ Games", "🥎 Slate Pitchers", "💎 HR Sleepers", "📊 Total Bases 1.5+", "🎯 HRR 1.5+", "🔥 2+ RBI"],
    horizontal=True,
    label_visibility="collapsed",
    key="top_view_tab",
)
st.markdown('</div>', unsafe_allow_html=True)

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

        # Highlight if any pitcher couldn't be matched to the CSV (use unfiltered df).
        unmatched = sp_df[sp_df["xwOBA"].isna() & sp_df["K%"].isna()]
        if not unmatched.empty and not _hide_unmatched:
            names = ", ".join(unmatched["Pitcher"].astype(str).tolist())
            st.caption(
                f"⚠️ No Savant CSV row found for: **{names}**. They’ll appear with blank metrics. "
                "Update `Data:savant_pitcher_stats.csv` to include them."
            )
        if sp_df_filtered.empty:
            st.info("No pitchers match the current filters. Try lowering Min PA or unchecking Hide TBD.")
        st.markdown(render_slate_pitcher_html(sp_df_filtered, schedule_df), unsafe_allow_html=True)
        st.markdown(
            '<div class="sp-legend">'
            'Sorted by <code>↓ Pitch Score</code> (35% xwOBA-against · 25% K-BB% · '
            '20% Whiff% · 20% Barrel%-against). Green = stronger pitcher, red = weaker. '
            '<code>SwStr%</code> is computed as Swing% × Whiff%. '
            '<code>CSW%*</code> is a proxy (called-strike portion estimated from F-Strike% '
            '× take-rate, plus SwStr%). All other metrics are direct from your '
            '<code>savant_pitcher_stats.csv</code>.'
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
            use_container_width=False,
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
            use_container_width=False,
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
                "openmeteo:weather",
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
            use_container_width=False,
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
                "openmeteo:weather",
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
            use_container_width=False,
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
                "openmeteo:weather",
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
            use_container_width=False,
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
away_matchup = build_matchup_table(ctx["away_lineup"], batters_df, pitchers_df, game_row["home_probable"], weather, game_row["park_factor"])
home_matchup = build_matchup_table(ctx["home_lineup"], batters_df, pitchers_df, game_row["away_probable"], weather, game_row["park_factor"])

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
tab_matchup, tab_rolling, tab_p_zones, tab_h_zones, tab_hot, tab_cold, tab_injuries = st.tabs(
    ["📊 Matchup", "📈 Rolling", "🎯 Pitcher Zones", "🌡️ Hitter Zones", "🔥 Hot Batters", "🧊 Cold Batters", "🏥 Injuries"]
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

    # away lineup
    render_lineup_banner(game_row["away_id"], game_row["away_abbr"], game_row["home_probable"], ctx["away_status"])
    if away_matchup.empty:
        st.info(f"{game_row['away_abbr']} lineup not available yet — not enough recent games on file to project.")
    else:
        st.dataframe(style_matchup_table(away_matchup), use_container_width=True, hide_index=True, height=min(440, 60 + 38*len(away_matchup)))
    # home lineup
    render_lineup_banner(game_row["home_id"], game_row["home_abbr"], game_row["away_probable"], ctx["home_status"])
    if home_matchup.empty:
        st.info(f"{game_row['home_abbr']} lineup not available yet — not enough recent games on file to project.")
    else:
        st.dataframe(style_matchup_table(home_matchup), use_container_width=True, hide_index=True, height=min(440, 60 + 38*len(home_matchup)))

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
            bat = r.get("Bat", "")
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
    else: st.dataframe(style_rolling_table(away_roll), use_container_width=True, hide_index=True)
    render_lineup_banner(game_row["home_id"], game_row["home_abbr"], game_row["away_probable"], ctx["home_status"])
    if home_roll.empty: st.info(f"{game_row['home_abbr']} lineup not posted yet.")
    else: st.dataframe(style_rolling_table(home_roll), use_container_width=True, hide_index=True)
    st.caption("Rolling form is derived from full-season Baseball Savant aggregates with the selected window weighting recency emphasis.")

# ============== Pitcher Zones tab ==============
with tab_p_zones:
    # Anchor target for slate-pitcher deep-links and mini-card pitcher names.
    st.markdown('<div id="pitcher-zones-anchor"></div>', unsafe_allow_html=True)
    # If the user arrived here via a Slate-Pitchers deep-link, scroll the
    # anchor into view. The streamlit tab content rerenders the anchor each
    # run, so injecting JS here is fine. Tabs are still client-side though,
    # so we only nudge when ?section=pitcher_zones is present.
    if _qp_section == "pitcher_zones":
        st.markdown(
            "<script>"
            "setTimeout(function(){"
            "  var a=document.getElementById('pitcher-zones-anchor');"
            "  if(a){a.scrollIntoView({behavior:'smooth',block:'start'});}"
            "}, 300);"
            "</script>",
            unsafe_allow_html=True,
        )
    pz1, pz2 = st.columns(2)
    with pz1:
        st.markdown(f'<div class="section-title">🎯 Away SP — {game_row["away_probable"]}</div>', unsafe_allow_html=True)
        ar = build_pitcher_zones_table(game_row["away_probable"], pitchers_df)
        if ar.empty: st.info("No arsenal data found for this pitcher.")
        else: st.dataframe(style_zones_table(ar, "pitcher"), use_container_width=True, hide_index=True)
    with pz2:
        st.markdown(f'<div class="section-title">🎯 Home SP — {game_row["home_probable"]}</div>', unsafe_allow_html=True)
        hr = build_pitcher_zones_table(game_row["home_probable"], pitchers_df)
        if hr.empty: st.info("No arsenal data found for this pitcher.")
        else: st.dataframe(style_zones_table(hr, "pitcher"), use_container_width=True, hide_index=True)
    st.caption("Velo / Spin / Break for each pitch type the pitcher uses regularly. Heatmap green = elite for that metric.")

# ============== Hitter Zones tab ==============
with tab_h_zones:
    away_zones = build_hitter_zones_table(ctx["away_lineup"], batters_df)
    home_zones = build_hitter_zones_table(ctx["home_lineup"], batters_df)
    render_lineup_banner(game_row["away_id"], game_row["away_abbr"], game_row["home_probable"], ctx["away_status"])
    if away_zones.empty: st.info(f"{game_row['away_abbr']} lineup not posted yet.")
    else: st.dataframe(style_zones_table(away_zones, "hitter"), use_container_width=True, hide_index=True)
    render_lineup_banner(game_row["home_id"], game_row["home_abbr"], game_row["away_probable"], ctx["home_status"])
    if home_zones.empty: st.info(f"{game_row['home_abbr']} lineup not posted yet.")
    else: st.dataframe(style_zones_table(home_zones, "hitter"), use_container_width=True, hide_index=True)
    st.caption("Pull/Oppo/FB/LD/GB tells you where each hitter does damage. Green columns = strengths to exploit.")

# ============== Hot / Cold Batters tabs (slate-wide) ==============
@st.cache_data(ttl=600, show_spinner=False)
def _build_slate_dataframe(_schedule_df, _batters_df, _pitchers_df, cache_key):
    """Score every batter in every posted lineup across the slate. Returns a single DataFrame.
    cache_key is a string used to invalidate the cache when the slate or data changes."""
    rows = []
    for _, g in _schedule_df.iterrows():
        try:
            cc = build_game_context(g)
            a = build_matchup_table(cc["away_lineup"], _batters_df, _pitchers_df, g["home_probable"], cc["weather"], g["park_factor"])
            h = build_matchup_table(cc["home_lineup"], _batters_df, _pitchers_df, g["away_probable"], cc["weather"], g["park_factor"])
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

_slate_cache_key = f"{selected_date}_{len(schedule_df)}"
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
    show_cols = [c for c in ["#", "Hitter", "Team", "Game", "Spot", "Bat", "OppPitcher",
                              "Matchup", "Test Score", "Ceiling", "Zone Fit", "HR Form", "kHR",
                              "ISO", "Barrel%", "HardHit%"] if c in ranked.columns]
    out = ranked[show_cols]
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
    st.dataframe(
        style_matchup_table(out),
        use_container_width=True,
        hide_index=True,
        height=min(640, 60 + 38 * len(out)),
    )
    csv = out.to_csv(index=False)
    st.download_button(
        f"⬇️ Download {title} CSV",
        csv,
        file_name=f"{selected_date}_{'hot' if top else 'cold'}_top{n}.csv",
        mime="text/csv",
        use_container_width=True,
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
