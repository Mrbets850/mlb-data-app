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
from datetime import date, timedelta

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

ABBR_TO_ID = {info["abbr"]: info["id"] for info in TEAM_INFO.values()}

def logo_url(team_id: int, size: int = 60) -> str:
    """MLB official CDN team logo."""
    return f"https://www.mlbstatic.com/team-logos/{team_id}.svg"

HEADERS = {"User-Agent": "Mozilla/5.0"}

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
.block-container { padding-top: 0.4rem; padding-bottom: 3rem; max-width: 1280px; }
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, Arial, sans-serif;
    color: #0f172a;
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
.section-title {
    color: #0f172a; font-size: 1.1rem; font-weight: 900;
    margin: 0 0 10px; letter-spacing: -0.01em;
    display: flex; align-items: center; gap: 10px;
}
.section-title img { width: 28px; height: 28px; }

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

/* ---- streamlit tabs styling (Matchup / Rolling / Zones / Exports) ---- */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px; background: #f1f5f9; padding: 4px; border-radius: 12px;
}
.stTabs [data-baseweb="tab"] {
    background: transparent; border-radius: 10px; padding: 8px 16px;
    font-weight: 800; color: #475569;
}
.stTabs [aria-selected="true"] {
    background: #ffffff !important; color: #0f172a !important;
    box-shadow: 0 1px 3px rgba(15,23,42,0.08);
}

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
    try:
        df = pd.read_csv(url)
    except Exception as exc:
        st.warning(f"CSV load failed: {url} :: {exc}")
        return pd.DataFrame()
    if df.empty: return df
    name_col = None
    for c in df.columns:
        if str(c).strip().lower() in ("last_name, first_name", "player_name", "name", "player"):
            name_col = c; break
    if name_col is not None:
        df[name_col] = df[name_col].apply(_flip_last_first)
        if name_col != "Name":
            df = df.rename(columns={name_col: "Name"})
    return df

@st.cache_data(ttl=1800, show_spinner=False)
def load_all_csvs():
    return {label: load_remote_csv(url) for label, url in CSV_URLS.items()}

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
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
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
                "away_probable": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "home_probable": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "away_probable_id": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("id"),
                "home_probable_id": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("id"),
                "lat": home_info["lat"], "lon": home_info["lon"],
                "park_factor": DEFAULT_PARK_FACTORS.get(home_info["abbr"], 100),
            })
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600)
def get_weather(lat, lon, game_time_utc):
    if lat is None or lon is None or not game_time_utc:
        return {"temp_f": None, "wind_mph": None, "rain_pct": None}
    url = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude": lat, "longitude": lon,
              "hourly": "temperature_2m,wind_speed_10m,precipitation_probability",
              "forecast_days": 7, "timezone": "UTC"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30); r.raise_for_status()
        data = r.json()
    except Exception:
        return {"temp_f": None, "wind_mph": None, "rain_pct": None}
    hourly = pd.DataFrame(data.get("hourly", {}))
    if hourly.empty or "time" not in hourly.columns:
        return {"temp_f": None, "wind_mph": None, "rain_pct": None}
    hourly["time"] = pd.to_datetime(hourly["time"])
    game_time = pd.to_datetime(game_time_utc, utc=True).tz_convert(None)
    idx = (hourly["time"] - game_time).abs().idxmin()
    row = hourly.loc[idx]
    temp_c = row.get("temperature_2m")
    return {"temp_f": None if pd.isna(temp_c) else round((temp_c * 9/5) + 32, 1),
            "wind_mph": row.get("wind_speed_10m"),
            "rain_pct": row.get("precipitation_probability")}

@st.cache_data(ttl=1800)
def get_boxscore(game_pk):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
    return r.json()

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

def render_slate_pitcher_html(df):
    """Render a custom HTML table with team logos in the Team cell and a
    green/red heatmap on the metric columns. Mirrors the design in the
    user-supplied screenshot."""
    if df.empty:
        return "<div class='sp-empty'>No probable starters posted yet.</div>"

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
            <div>Live data · Auto-refresh 30 min</div>
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

def render_game_header(game_row, ctx, weather):
    rain = weather.get("rain_pct"); rain_str = f"{int(rain)}%" if rain is not None else "N/A"
    temp = weather.get("temp_f");   temp_str = f"{temp}°F" if temp is not None else "N/A"
    wind = weather.get("wind_mph"); wind_str = f"{round(float(wind))} mph" if wind is not None else "N/A"
    away_logo = logo_url(game_row["away_id"]) if game_row["away_id"] else ""
    home_logo = logo_url(game_row["home_id"]) if game_row["home_id"] else ""
    def _status_pill(s):
        if s == "Confirmed": return "tier-strong"
        if s == "Projected": return "tier-ok"
        return "tier-avoid"
    away_pill = _status_pill(ctx["away_status"])
    home_pill = _status_pill(ctx["home_status"])
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
        <div class="kpi-row">
            <div class="kpi"><span class="k">Park</span>{game_row['park_factor']}</div>
            <div class="kpi"><span class="k">Temp</span>{temp_str}</div>
            <div class="kpi"><span class="k">Wind</span>{wind_str}</div>
            <div class="kpi"><span class="k">Rain</span>{rain_str}</div>
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

def render_pitcher_panel(label, pitcher_name, pitch_hand, p_row):
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
    st.session_state["slate_date_picker"] = date.today()
    st.session_state["_selected_idx"] = 0

top_cols = st.columns([2.2, 1, 1])
with top_cols[0]:
    selected_date = st.date_input("📅 Slate date", value=date.today(), key="slate_date_picker")
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

# ===========================================================================
# Top-level tab switcher: "⚾ Games" vs "🥎 Slate Pitchers"
# Implemented as a styled radio so we can toggle large sections of the page
# without re-indenting the entire game flow.
# ===========================================================================
st.markdown(
    "<style>"
    ".top-tab-row { margin: 4px 0 10px 0; }"
    ".top-tab-row [data-testid=\"stRadio\"] > label { display:none; }"
    ".top-tab-row [role=\"radiogroup\"] { gap: 8px; }"
    ".top-tab-row [role=\"radiogroup\"] > label { background:#f1f5f9; padding: 8px 16px; "
    "  border-radius: 999px; border:1px solid #e2e8f0; cursor:pointer; font-weight:800; "
    "  color:#475569; transition: all .15s ease; }"
    ".top-tab-row [role=\"radiogroup\"] > label:has(input:checked) { "
    "  background: linear-gradient(110deg, #04130b 0%, #133a23 100%); color:#facc15; "
    "  border-color: rgba(250,204,21,0.55); box-shadow: 0 4px 12px rgba(5,20,12,.25); }"
    ".top-tab-row [role=\"radiogroup\"] > label > div:first-child { display:none; }"
    ".sp-legend { color:#64748b; font-size:.78rem; margin: 4px 0 12px 0; }"
    ".sp-legend code { background:#f1f5f9; padding: 1px 6px; border-radius:6px; "
    "  font-family: inherit; font-weight:700; color:#334155; }"
    "</style>",
    unsafe_allow_html=True,
)
st.markdown('<div class="top-tab-row">', unsafe_allow_html=True)
_view = st.radio(
    "View",
    ["⚾ Games", "🥎 Slate Pitchers"],
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
        # Highlight if any pitcher couldn't be matched to the CSV.
        unmatched = sp_df[sp_df["xwOBA"].isna() & sp_df["K%"].isna()]
        if not unmatched.empty:
            names = ", ".join(unmatched["Pitcher"].astype(str).tolist())
            st.caption(
                f"⚠️ No Savant CSV row found for: **{names}**. They’ll appear with blank metrics. "
                "Update `Data:savant_pitcher_stats.csv` to include them."
            )
        st.markdown(render_slate_pitcher_html(sp_df), unsafe_allow_html=True)
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
        csv_bytes = sp_df.drop(columns=[c for c in sp_df.columns if c.startswith("_")], errors="ignore").to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download Slate Pitchers (CSV)",
            data=csv_bytes,
            file_name=f"slate_pitchers_{selected_date}.csv",
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
tab_matchup, tab_rolling, tab_p_zones, tab_h_zones, tab_hot, tab_cold = st.tabs(
    ["📊 Matchup", "📈 Rolling", "🎯 Pitcher Zones", "🌡️ Hitter Zones", "🔥 Hot Batters", "🧊 Cold Batters"]
)

# ============== Matchup tab ==============
with tab_matchup:
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
                f'</div>'
            )
        st.markdown('<div class="top3-row">' + "".join(cards) + '</div>', unsafe_allow_html=True)

    # Pitcher panels under matchup tab
    st.markdown('<div class="section-title" style="margin-top:14px;">🎯 Pitcher Vulnerability</div>', unsafe_allow_html=True)
    pc1, pc2 = st.columns(2)
    with pc1:
        render_pitcher_panel(f"Away SP — {game_row['away_abbr']}", game_row["away_probable"],
                              ctx["away_pitch_hand"], find_pitcher_row(pitchers_df, game_row["away_probable"]))
    with pc2:
        render_pitcher_panel(f"Home SP — {game_row['home_abbr']}", game_row["home_probable"],
                              ctx["home_pitch_hand"], find_pitcher_row(pitchers_df, game_row["home_probable"]))

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

# ----- Data status -----
with st.expander("📊 Data status & sources", expanded=False):
    c1, c2 = st.columns(2)
    with c1: st.metric("Batters loaded", len(batters_df))
    with c2: st.metric("Pitchers loaded", len(pitchers_df))
    st.caption("Source: raw GitHub URLs (auto-refresh 30 min). Update data by committing new CSVs to the repo and clicking Refresh data.")
    for label, url in CSV_URLS.items():
        st.markdown(f"- **{label}**: [{CSV_FILES[label]}]({url})")

st.markdown(
    '<div class="footer">⚾ <b>MrBets850 MLB Edge</b> · '
    'Powered by Baseball Savant + MLB StatsAPI + Open-Meteo · '
    'Tiers: Elite 🟢 (≥130) · Strong 🟢 (110-129) · OK 🟡 (95-109) · Avoid 🔴 (&lt;95) · '
    'Heatmaps: Green = Strong · Yellow = OK · Red = Avoid · For research purposes only.</div>',
    unsafe_allow_html=True,
)
