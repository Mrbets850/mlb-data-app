import streamlit as st
import pandas as pd
import requests
import os
import re
import unicodedata
import urllib.parse
from datetime import date

# ---------------------------------------------------------------------------
# Remote CSV configuration
# ---------------------------------------------------------------------------
# All Baseball Savant "trash" CSV exports are hosted in the public GitHub repo
# https://github.com/Mrbets850/mlb-data-app and pulled at runtime via raw URLs.
# Filenames contain a colon, which must be URL-encoded for raw.githubusercontent.com.
GITHUB_USER = "Mrbets850"
GITHUB_REPO = "mlb-data-app"
GITHUB_BRANCH = "main"

CSV_FILES = {
    "batters":  "Data:savant_batters.csv.csv",
    "pitchers": "Data:savant_pitchers.csv.csv",
}

def raw_github_url(path: str) -> str:
    """Build a raw.githubusercontent.com URL with proper percent-encoding."""
    encoded = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{encoded}"

CSV_URLS = {label: raw_github_url(name) for label, name in CSV_FILES.items()}

# ---------------------------------------------------------------------------
# Page config + global styles
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="MrBets850 — MLB Matchup Board",
    page_icon="⚾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* ---------- base ---------- */
.block-container {
    padding-top: 0.6rem;
    padding-bottom: 3rem;
    max-width: 1180px;
}
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, Arial, sans-serif;
    color: #0f172a;
}

/* ---------- brand bar ---------- */
.brand-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 22px;
    border-radius: 18px;
    background: linear-gradient(110deg, #0b1437 0%, #0a2d6e 55%, #0b4ea2 100%);
    box-shadow: 0 12px 28px rgba(7,18,55,0.22);
    margin-bottom: 16px;
    color: #ffffff;
}
.brand-name {
    font-size: 1.55rem;
    font-weight: 900;
    letter-spacing: 0.04em;
    color: #ffffff;
    line-height: 1.05;
}
.brand-tag {
    color: #c7dafe;
    font-size: 0.78rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    font-weight: 700;
}
.brand-meta {
    text-align: right;
    color: #dbeafe;
    font-size: 0.92rem;
    font-weight: 700;
}
.brand-meta .big { font-size: 1.15rem; color: #ffffff; font-weight: 800; }

/* ---------- section card ---------- */
.section-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 18px;
    padding: 18px 20px;
    margin-bottom: 14px;
    box-shadow: 0 6px 18px rgba(15,23,42,0.05);
}
/* Hide Streamlit's empty wrapper divs that ship with empty markdown blocks */
.element-container:has(> .stMarkdown:only-child > [data-testid="stMarkdownContainer"]:empty) { display: none; }
.stMarkdown div[data-testid="stMarkdownContainer"] > div:empty { display: none; }
.section-card.dark {
    background: linear-gradient(180deg, #0b1437 0%, #0a2350 100%);
    border: 1px solid #1e3a8a;
    color: #ffffff;
}
.section-card.dark .section-label,
.section-card.dark .metric-k { color: #c7dafe; }
.section-card.dark .metric-v { color: #ffffff; }

.section-label {
    color: #64748b;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 10px;
    font-weight: 800;
}
.section-title {
    color: #0f172a;
    font-size: 1.25rem;
    font-weight: 900;
    margin-bottom: 12px;
    letter-spacing: -0.01em;
}

/* ---------- game card ---------- */
.game-card {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 14px;
    align-items: center;
}
.matchup {
    font-size: 2.4rem;
    font-weight: 900;
    color: #ffffff;
    letter-spacing: 0.01em;
    line-height: 1.1;
}
.matchup .vs { color: #94b8ff; font-weight: 700; padding: 0 10px; }
.game-meta {
    color: #c7dafe;
    font-size: 0.95rem;
    font-weight: 600;
    margin-top: 4px;
}
.kpi-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 14px;
}
.kpi {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 12px;
    padding: 8px 12px;
    color: #ffffff;
    font-size: 0.9rem;
    font-weight: 700;
}
.kpi .k {
    color: #c7dafe;
    font-size: 0.7rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    display: block;
    margin-bottom: 2px;
    font-weight: 700;
}

/* ---------- tier pills (Elite / Strong / OK / Avoid) ---------- */
.tier {
    display: inline-block;
    padding: 4px 11px;
    border-radius: 999px;
    font-weight: 800;
    font-size: 0.78rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    border: 1px solid transparent;
}
.tier-elite   { background:#dcfce7; color:#14532d; border-color:#86efac; }
.tier-strong  { background:#d1fae5; color:#065f46; border-color:#6ee7b7; }
.tier-ok      { background:#fef3c7; color:#78350f; border-color:#fcd34d; }
.tier-avoid   { background:#fee2e2; color:#7f1d1d; border-color:#fca5a5; }
.tier-neutral { background:#e2e8f0; color:#334155; border-color:#cbd5e1; }

/* ---------- hot batter tile ---------- */
.batter-tile {
    border-radius: 14px;
    padding: 14px 14px 12px;
    border: 1px solid #e2e8f0;
    background: #ffffff;
    height: 100%;
    box-shadow: 0 4px 10px rgba(15,23,42,0.04);
}
.batter-tile.elite  { border-left: 6px solid #16a34a; background: #f0fdf4; }
.batter-tile.strong { border-left: 6px solid #22c55e; background: #f7fee7; }
.batter-tile.ok     { border-left: 6px solid #f59e0b; background: #fffbeb; }
.batter-tile.avoid  { border-left: 6px solid #ef4444; background: #fef2f2; }

.batter-tile .rank {
    color: #64748b;
    font-size: 0.7rem;
    letter-spacing: 0.18em;
    font-weight: 800;
    text-transform: uppercase;
}
.batter-tile .name {
    font-size: 1.15rem;
    font-weight: 900;
    color: #0f172a;
    margin: 2px 0 2px;
    line-height: 1.15;
}
.batter-tile .vs {
    color: #475569;
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 8px;
}
.batter-tile .score {
    font-size: 1.7rem;
    font-weight: 900;
    color: #0f172a;
    letter-spacing: -0.02em;
    margin: 4px 0 6px;
}
.batter-tile .stats {
    color: #334155;
    font-size: 0.8rem;
    font-weight: 700;
    line-height: 1.5;
}
.batter-tile .angle {
    margin-top: 8px;
    background: #ffffff;
    border: 1px dashed #cbd5e1;
    border-radius: 10px;
    padding: 7px 10px;
    color: #0f172a;
    font-size: 0.82rem;
    font-weight: 700;
}

/* ---------- pitcher tile ---------- */
.pitcher-tile {
    border-radius: 14px;
    padding: 14px;
    border: 1px solid #e2e8f0;
    background: #ffffff;
    box-shadow: 0 4px 10px rgba(15,23,42,0.04);
}
.pitcher-tile.vuln-elite { border-left: 6px solid #ef4444; background: #fef2f2; }
.pitcher-tile.vuln-strong{ border-left: 6px solid #f59e0b; background: #fffbeb; }
.pitcher-tile.vuln-ok    { border-left: 6px solid #94a3b8; background: #f8fafc; }
.pitcher-tile.vuln-avoid { border-left: 6px solid #16a34a; background: #f0fdf4; }
.pitcher-tile h4 {
    margin: 0 0 4px; font-size: 1.05rem; font-weight: 900; color: #0f172a;
}
.pitcher-tile .meta { color:#475569; font-size:0.85rem; font-weight:600; }
.pitcher-tile .grid {
    display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 10px; margin-top: 10px;
}
.pitcher-tile .k { color:#64748b; font-size:0.72rem; letter-spacing:0.08em; text-transform:uppercase; font-weight:700; }
.pitcher-tile .v { color:#0f172a; font-size:0.95rem; font-weight:800; }

/* ---------- inputs ---------- */
div[data-baseweb="select"] > div,
div[data-baseweb="base-input"] > div {
    border-radius: 12px !important;
    border: 1px solid #cbd5e1 !important;
    background: #ffffff !important;
}
.stDateInput label, .stSelectbox label, .stRadio label {
    font-weight: 800 !important;
    color: #0f172a !important;
}
.stButton > button {
    border-radius: 12px;
    font-weight: 800;
    border: 1px solid #1d4ed8;
    background: #1d4ed8;
    color: #ffffff;
    padding: 8px 16px;
}
.stButton > button:hover { background: #1e40af; border-color: #1e40af; color:#ffffff; }

/* ---------- dataframe polish ---------- */
[data-testid="stDataFrame"] { border-radius: 14px; overflow: hidden; }
thead tr th { font-size: 0.92rem !important; font-weight: 800 !important; }
tbody tr td { font-size: 0.95rem !important; font-weight: 700 !important; }

/* ---------- footer ---------- */
.footer {
    margin-top: 18px;
    padding: 12px 16px;
    border-radius: 12px;
    background: #f1f5f9;
    color: #475569;
    font-size: 0.82rem;
    text-align: center;
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
TEAM_INFO = {
    "Arizona Diamondbacks": {"abbr": "ARI", "lat": 33.4484, "lon": -112.0740},
    "Atlanta Braves": {"abbr": "ATL", "lat": 33.7490, "lon": -84.3880},
    "Baltimore Orioles": {"abbr": "BAL", "lat": 39.2904, "lon": -76.6122},
    "Boston Red Sox": {"abbr": "BOS", "lat": 42.3601, "lon": -71.0589},
    "Chicago Cubs": {"abbr": "CHC", "lat": 41.8781, "lon": -87.6298},
    "Chicago White Sox": {"abbr": "CWS", "lat": 41.8781, "lon": -87.6298},
    "Cincinnati Reds": {"abbr": "CIN", "lat": 39.1031, "lon": -84.5120},
    "Cleveland Guardians": {"abbr": "CLE", "lat": 41.4993, "lon": -81.6944},
    "Colorado Rockies": {"abbr": "COL", "lat": 39.7392, "lon": -104.9903},
    "Detroit Tigers": {"abbr": "DET", "lat": 42.3314, "lon": -83.0458},
    "Houston Astros": {"abbr": "HOU", "lat": 29.7604, "lon": -95.3698},
    "Kansas City Royals": {"abbr": "KC", "lat": 39.0997, "lon": -94.5786},
    "Los Angeles Angels": {"abbr": "LAA", "lat": 33.8366, "lon": -117.9143},
    "Los Angeles Dodgers": {"abbr": "LAD", "lat": 34.0522, "lon": -118.2437},
    "Miami Marlins": {"abbr": "MIA", "lat": 25.7617, "lon": -80.1918},
    "Milwaukee Brewers": {"abbr": "MIL", "lat": 43.0389, "lon": -87.9065},
    "Minnesota Twins": {"abbr": "MIN", "lat": 44.9778, "lon": -93.2650},
    "New York Mets": {"abbr": "NYM", "lat": 40.7128, "lon": -74.0060},
    "New York Yankees": {"abbr": "NYY", "lat": 40.7128, "lon": -74.0060},
    "Athletics": {"abbr": "ATH", "lat": 38.5816, "lon": -121.4944},
    "Philadelphia Phillies": {"abbr": "PHI", "lat": 39.9526, "lon": -75.1652},
    "Pittsburgh Pirates": {"abbr": "PIT", "lat": 40.4406, "lon": -79.9959},
    "San Diego Padres": {"abbr": "SD", "lat": 32.7157, "lon": -117.1611},
    "San Francisco Giants": {"abbr": "SF", "lat": 37.7749, "lon": -122.4194},
    "Seattle Mariners": {"abbr": "SEA", "lat": 47.6062, "lon": -122.3321},
    "St. Louis Cardinals": {"abbr": "STL", "lat": 38.6270, "lon": -90.1994},
    "Tampa Bay Rays": {"abbr": "TB", "lat": 27.9506, "lon": -82.4572},
    "Texas Rangers": {"abbr": "TEX", "lat": 32.7767, "lon": -96.7970},
    "Toronto Blue Jays": {"abbr": "TOR", "lat": 43.6532, "lon": -79.3832},
    "Washington Nationals": {"abbr": "WSH", "lat": 38.9072, "lon": -77.0369},
}

TEAM_FIXES = {
    "CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF",
    "TBR": "TB", "WSN": "WSH", "OAK": "ATH"
}

DEFAULT_PARK_FACTORS = {
    "ARI": 100, "ATL": 100, "BAL": 100, "BOS": 100, "CHC": 100, "CWS": 100,
    "CIN": 100, "CLE": 100, "COL": 112, "DET": 98, "HOU": 101, "KC": 99,
    "LAA": 99, "LAD": 102, "MIA": 95, "MIL": 102, "MIN": 101, "NYM": 98,
    "NYY": 103, "ATH": 98, "PHI": 104, "PIT": 98, "SD": 95, "SF": 94,
    "SEA": 95, "STL": 100, "TB": 97, "TEX": 106, "TOR": 103, "WSH": 101
}

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ---------------------------------------------------------------------------
# Tier system (single source of truth — used in tiles, tables, and props)
# ---------------------------------------------------------------------------
def score_tier(score):
    """Return (tier_key, label, css_class) for any matchup score."""
    try:
        v = float(score)
    except Exception:
        return ("neutral", "N/A", "tier-neutral")
    if v >= 90:  return ("elite",  "Elite",  "tier-elite")
    if v >= 78:  return ("strong", "Strong", "tier-strong")
    if v >= 65:  return ("ok",     "OK",     "tier-ok")
    return ("avoid", "Avoid", "tier-avoid")

def hr_tier(score, barrel, hr):
    """HR-likelihood tier using score + barrel% + HR count."""
    try:
        s = float(score); b = float(barrel); h = float(hr)
    except Exception:
        return ("neutral", "N/A")
    if s >= 88 and b >= 12:                   return ("elite",  "Elite HR")
    if s >= 78 and (b >= 10 or h >= 8):       return ("strong", "Strong HR")
    if s >= 65:                               return ("ok",     "OK HR")
    return ("avoid", "Cold")

def hit_tier(score, hardhit):
    try:
        s = float(score); h = float(hardhit)
    except Exception:
        return ("neutral", "N/A")
    if s >= 85 and h >= 45: return ("elite",  "Elite Hit")
    if s >= 75 and h >= 40: return ("strong", "Strong Hit")
    if s >= 62:             return ("ok",     "OK Hit")
    return ("avoid", "Avoid")

def suggested_angle(score, barrel, hr, hardhit):
    """Quick prop angle for clients."""
    try:
        s = float(score); b = float(barrel); h = float(hr); hh = float(hardhit)
    except Exception:
        return "—"
    if s >= 90 and b >= 12 and h >= 8:
        return "🎯 HR + Over 1.5 TB"
    if s >= 85 and b >= 10:
        return "💣 HR / Over 1.5 TB"
    if s >= 78 and hh >= 42:
        return "✅ Over 1.5 Total Bases"
    if s >= 70:
        return "👀 Hit + RBI lean"
    if s >= 60:
        return "Pass / lineup-only"
    return "❌ Fade"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def clean_name(name):
    if pd.isna(name):
        return ""
    txt = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    txt = re.sub(r"[^A-Za-z0-9 ]+", "", txt).lower().strip()
    txt = re.sub(r"\s+", " ", txt)
    return txt

def norm_team(team):
    if pd.isna(team):
        return ""
    t = str(team).strip().upper()
    return TEAM_FIXES.get(t, t)

def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except:
        return default

def standardize_columns(df):
    if df.empty:
        return df
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lower_map = {c.lower(): c for c in df.columns}

    if "player_name" in lower_map:
        df.rename(columns={lower_map["player_name"]: "Name"}, inplace=True)
    elif "name" in lower_map:
        df.rename(columns={lower_map["name"]: "Name"}, inplace=True)
    elif "player" in lower_map:
        df.rename(columns={lower_map["player"]: "Name"}, inplace=True)

    for col in ["team", "team_abbr", "team_name"]:
        if col in lower_map:
            df.rename(columns={lower_map[col]: "Team"}, inplace=True)
            break

    rename_map = {
        "hr": "HR",
        "home_run": "HR",
        "home_runs": "HR",
        "avg": "AVG",
        "batting_avg": "AVG",
        "obp": "OBP",
        "on_base_percent": "OBP",
        "slg": "SLG",
        "slg_percent": "SLG",
        "ops": "OPS",
        "on_base_plus_slg": "OPS",
        "iso": "ISO",
        "isolated_power": "ISO",
        "xiso": "xISO",
        "woba": "wOBA",
        "xwoba": "xwOBA",
        "xobp": "xOBP",
        "xslg": "xSLG",
        "xba": "xBA",
        "barrel%": "Barrel%",
        "barrel_batted_rate": "Barrel%",
        "barrels_per_bbe_percent": "Barrel%",
        "hardhit%": "HardHit%",
        "hard_hit_percent": "HardHit%",
        "hard_hit_rate": "HardHit%",
        "exit_velocity_avg": "EV",
        "avg_hit_speed": "EV",
        "avg_best_speed": "EV",
        "launch_angle_avg": "LA",
        "launch_angle": "LA",
        "flyballs_percent": "FB%",
        "flyball_percent": "FB%",
        "fb_percent": "FB%",
        "k_percent": "K%",
        "bb_percent": "BB%",
        "sweet_spot_percent": "SweetSpot%",
        "whiff_percent": "Whiff%",
        "swing_percent": "Swing%",
        "avg_swing_speed": "SwingSpeed",
        "avg_hyper_speed": "HyperSpeed",
        "p_throws": "pitch_hand",
        "throws": "pitch_hand",
        "stand": "bat_side",
        "bats": "bat_side"
    }

    for raw, final in rename_map.items():
        if raw in lower_map and final not in df.columns:
            df.rename(columns={lower_map[raw]: final}, inplace=True)

    if "Name" not in df.columns:
        df["Name"] = ""
    if "Team" not in df.columns:
        df["Team"] = ""

    df["name_key"] = df["Name"].apply(clean_name)
    df["team_key"] = df["Team"].apply(norm_team)
    return df

def _flip_last_first(name):
    """Convert 'Last, First' (Baseball Savant default) to 'First Last'."""
    if pd.isna(name):
        return ""
    s = str(name).strip()
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[1]} {parts[0]}"
    return s

@st.cache_data(ttl=1800, show_spinner=False)
def load_remote_csv(url):
    """Fetch a CSV from a public URL using pandas.read_csv. Returns empty
    DataFrame on failure so the rest of the app can still render.
    """
    try:
        df = pd.read_csv(url)
    except Exception as exc:
        st.warning(f"Failed to load CSV from {url}: {exc}")
        return pd.DataFrame()

    if df.empty:
        return df

    name_col = None
    for c in df.columns:
        if str(c).strip().lower() in ("last_name, first_name", "player_name", "name", "player"):
            name_col = c
            break
    if name_col is not None:
        df[name_col] = df[name_col].apply(_flip_last_first)
        if name_col != "Name":
            df = df.rename(columns={name_col: "Name"})
    return df

@st.cache_data(ttl=1800, show_spinner=False)
def load_all_csvs():
    return {label: load_remote_csv(url) for label, url in CSV_URLS.items()}

@st.cache_data(ttl=1800)
def get_schedule(selected_date):
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {"sportId": 1, "date": selected_date.strftime("%Y-%m-%d"), "hydrate": "probablePitcher,team"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    rows = []
    for d in data.get("dates", []):
        for game in d.get("games", []):
            away_name = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
            home_name = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
            away_info = TEAM_INFO.get(away_name, {"abbr": away_name[:3].upper(), "lat": None, "lon": None})
            home_info = TEAM_INFO.get(home_name, {"abbr": home_name[:3].upper(), "lat": None, "lon": None})
            game_time_utc = game.get("gameDate")
            game_time_ct = pd.to_datetime(game_time_utc, utc=True).tz_convert("America/Chicago")

            rows.append({
                "game_pk": game.get("gamePk"),
                "label": f'{away_info["abbr"]} @ {home_info["abbr"]} · {game_time_ct.strftime("%-I:%M %p CT")}',
                "short_label": f'{away_info["abbr"]} @ {home_info["abbr"]}',
                "game_time_ct": game_time_ct.strftime("%a %b %-d · %-I:%M %p CT"),
                "game_time_utc": game_time_utc,
                "status": game.get("status", {}).get("detailedState"),
                "away_team": away_name,
                "away_abbr": away_info["abbr"],
                "home_team": home_name,
                "home_abbr": home_info["abbr"],
                "venue": game.get("venue", {}).get("name", "Unknown"),
                "away_probable": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "home_probable": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "lat": home_info["lat"],
                "lon": home_info["lon"],
                "park_factor": DEFAULT_PARK_FACTORS.get(home_info["abbr"], 100)
            })
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600)
def get_weather(lat, lon, game_time_utc):
    if lat is None or lon is None or not game_time_utc:
        return {"temp_f": None, "wind_mph": None, "rain_pct": None}
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,precipitation_probability",
        "forecast_days": 7, "timezone": "UTC"
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
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
    temp_f = None if pd.isna(temp_c) else round((temp_c * 9/5) + 32, 1)
    return {
        "temp_f": temp_f,
        "wind_mph": row.get("wind_speed_10m"),
        "rain_pct": row.get("precipitation_probability")
    }

@st.cache_data(ttl=1800)
def get_boxscore(game_pk):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def roster_df_from_box(team_box, fallback_team):
    rows = []
    for pdata in team_box.get("players", {}).values():
        person = pdata.get("person", {})
        lineup_raw = str(pdata.get("battingOrder", "")).strip()
        lineup_spot = int(lineup_raw[0]) if lineup_raw[:1].isdigit() else None
        rows.append({
            "player_name": person.get("fullName", ""),
            "name_key": clean_name(person.get("fullName", "")),
            "player_id": person.get("id", ""),  # MLB Stats API id (= Savant id)
            "team": norm_team(fallback_team),
            "position": pdata.get("position", {}).get("abbreviation", ""),
            "bat_side": pdata.get("batSide", {}).get("code", ""),
            "pitch_hand": pdata.get("pitchHand", {}).get("code", ""),
            "lineup_spot": lineup_spot
        })
    return pd.DataFrame(rows)

def lookup_pitcher_mlb_id(roster_df, pitchers_df, pitcher_name):
    """Resolve a probable pitcher's MLB/Savant numeric id, preferring the
    live boxscore roster (always current) and falling back to the Savant
    pitchers CSV which already carries player_id."""
    if not pitcher_name or pitcher_name == "TBD":
        return None
    key = clean_name(pitcher_name)
    if roster_df is not None and not roster_df.empty:
        hit = roster_df[roster_df["name_key"] == key]
        if not hit.empty and hit.iloc[0].get("player_id"):
            try:
                return int(hit.iloc[0]["player_id"])
            except Exception:
                pass
    if pitchers_df is not None and not pitchers_df.empty and "player_id" in pitchers_df.columns:
        hit = pitchers_df[pitchers_df["name_key"] == key]
        if not hit.empty:
            try:
                return int(hit.iloc[0]["player_id"])
            except Exception:
                pass
    return None

def lookup_pitch_hand(roster_df, pitcher_name):
    if roster_df.empty or not pitcher_name or pitcher_name == "TBD":
        return ""
    exact = roster_df[roster_df["name_key"] == clean_name(pitcher_name)]
    if not exact.empty:
        return exact.iloc[0]["pitch_hand"]
    return ""

def build_game_context(game_row):
    weather = get_weather(game_row["lat"], game_row["lon"], game_row["game_time_utc"])
    try:
        box = get_boxscore(game_row["game_pk"])
        away_roster = roster_df_from_box(box.get("teams", {}).get("away", {}), game_row["away_abbr"])
        home_roster = roster_df_from_box(box.get("teams", {}).get("home", {}), game_row["home_abbr"])
    except:
        away_roster = pd.DataFrame()
        home_roster = pd.DataFrame()

    away_lineup = away_roster[(away_roster["lineup_spot"].notna()) & (away_roster["position"] != "P")].copy()
    home_lineup = home_roster[(home_roster["lineup_spot"].notna()) & (home_roster["position"] != "P")].copy()

    if not away_lineup.empty:
        away_lineup = away_lineup.sort_values("lineup_spot")
    if not home_lineup.empty:
        home_lineup = home_lineup.sort_values("lineup_spot")

    home_pitch_hand = lookup_pitch_hand(home_roster, game_row["home_probable"])
    away_pitch_hand = lookup_pitch_hand(away_roster, game_row["away_probable"])

    if not away_lineup.empty:
        away_lineup["opposing_pitcher"] = game_row["home_probable"]
        away_lineup["opposing_pitch_hand"] = home_pitch_hand
        away_lineup["lineup_status"] = "Confirmed" if len(away_lineup) >= 9 else "Not Confirmed"

    if not home_lineup.empty:
        home_lineup["opposing_pitcher"] = game_row["away_probable"]
        home_lineup["opposing_pitch_hand"] = away_pitch_hand
        home_lineup["lineup_status"] = "Confirmed" if len(home_lineup) >= 9 else "Not Confirmed"

    return {
        "weather": weather,
        "away_lineup": away_lineup,
        "home_lineup": home_lineup,
        "away_roster": away_roster,
        "home_roster": home_roster,
        "away_status": "Confirmed" if len(away_lineup) >= 9 else "Not Confirmed",
        "home_status": "Confirmed" if len(home_lineup) >= 9 else "Not Confirmed",
        "home_pitch_hand": home_pitch_hand,
        "away_pitch_hand": away_pitch_hand
    }

def find_player_row(df, name_key, team):
    if df.empty:
        return None
    exact = df[(df["name_key"] == name_key) & (df["team_key"] == norm_team(team))]
    if not exact.empty:
        return exact.iloc[0]
    exact2 = df[df["name_key"] == name_key]
    if not exact2.empty:
        return exact2.iloc[0]
    return None

def find_pitcher_row(df, pitcher_name):
    if df.empty or not pitcher_name or pitcher_name == "TBD":
        return None
    key = clean_name(pitcher_name)
    exact = df[df["name_key"] == key]
    if not exact.empty:
        return exact.iloc[0]
    last_name = key.split(" ")[-1]
    contains = df[df["name_key"].str.contains(last_name, na=False)]
    if not contains.empty:
        return contains.iloc[0]
    return None

def handedness_label(bat_side, pitch_hand):
    b = str(bat_side).upper(); p = str(pitch_hand).upper()
    if b == "S" and p: return f"SH vs {p}HP"
    if b and p:        return f"{b}HB vs {p}HP"
    return "Unknown"

def platoon_value(bat_side, pitch_hand):
    b = str(bat_side).upper(); p = str(pitch_hand).upper()
    if b == "S" and p in ["L", "R"]: return 1.0
    if b in ["L", "R"] and p in ["L", "R"]:
        return 0.8 if b != p else -0.35
    return 0.0

def matchup_score(batter_row, pitcher_row, lineup_spot, weather, park_factor, bat_side, opp_pitch_hand):
    hr = safe_float(batter_row.get("HR") if batter_row is not None else None, 10)
    iso = safe_float(batter_row.get("ISO") if batter_row is not None else None, 0.170)
    xslg = safe_float(batter_row.get("xSLG") if batter_row is not None else None, 0.420)
    barrel = safe_float(batter_row.get("Barrel%") if batter_row is not None else None, 8.0)
    hardhit = safe_float(batter_row.get("HardHit%") if batter_row is not None else None, 38.0)

    p_xslg = safe_float(pitcher_row.get("xSLG") if pitcher_row is not None else None, 0.420)
    p_barrel = safe_float(pitcher_row.get("Barrel%") if pitcher_row is not None else None, 8.0)
    p_hardhit = safe_float(pitcher_row.get("HardHit%") if pitcher_row is not None else None, 38.0)
    p_k = safe_float(pitcher_row.get("K%") if pitcher_row is not None else None, 22.0)

    temp_f = safe_float(weather.get("temp_f"), 72)
    wind_mph = safe_float(weather.get("wind_mph"), 8)
    rain_pct = safe_float(weather.get("rain_pct"), 0)

    split_boost = platoon_value(bat_side, opp_pitch_hand) * 7
    weather_bonus = max(0, temp_f - 68) * 0.28 + max(0, wind_mph - 7) * 0.18 - rain_pct * 0.03
    park_bonus = (park_factor - 100) * 0.50
    slot_bonus = max(0, 10 - safe_float(lineup_spot, 9)) * 1.15

    score = (
        hr * 0.75
        + iso * 155
        + xslg * 28
        + barrel * 2.1
        + hardhit * 0.65
        + p_xslg * 25
        + p_barrel * 1.8
        + p_hardhit * 0.45
        - p_k * 0.45
        + split_boost
        + weather_bonus
        + park_bonus
        + slot_bonus
    )
    return round(score, 2)

def pitcher_vulnerability(pitcher_row):
    """Score 0-100: higher = more vulnerable (good for hitters)."""
    if pitcher_row is None:
        return (60, "ok", "OK")
    p_xslg = safe_float(pitcher_row.get("xSLG"), 0.420)
    p_barrel = safe_float(pitcher_row.get("Barrel%"), 8.0)
    p_hardhit = safe_float(pitcher_row.get("HardHit%"), 38.0)
    p_k = safe_float(pitcher_row.get("K%"), 22.0)
    p_hr = safe_float(pitcher_row.get("HR"), 0)
    score = (
        (p_xslg - 0.380) * 120
        + (p_barrel - 7.0) * 3.0
        + (p_hardhit - 35.0) * 0.6
        - (p_k - 22.0) * 0.8
        + p_hr * 0.4
        + 60
    )
    score = max(0, min(100, score))
    if score >= 78:  return (round(score,1), "elite",  "Highly Vulnerable")
    if score >= 65:  return (round(score,1), "strong", "Exploitable")
    if score >= 50:  return (round(score,1), "ok",     "Average")
    return (round(score,1), "avoid", "Tough")

def build_team_table(lineup_df, batters_df, pitchers_df, pitcher_name, weather, park_factor):
    # NOTE: Bat/Split removed (StatsAPI lineup feed often returns blank batSide
    # before lineups are official). Stats CSV now (Apr 2026 update) carries
    # home_run / xslg / xba / exit_velocity_avg, so HR + xSLG are real numbers
    # again, not defaults. Columns: HR, ISO, xSLG, xwOBA, Barrel%, HardHit%, K%.
    empty_cols = [
        "Spot", "Player", "Pos", "Tier",
        "HR", "ISO", "xSLG", "xwOBA", "Barrel%", "HardHit%", "K%", "Score", "Angle"
    ]
    if lineup_df.empty:
        return pd.DataFrame(columns=empty_cols)

    pitch_row = find_pitcher_row(pitchers_df, pitcher_name)
    out_rows = []

    for _, row in lineup_df.iterrows():
        batter_row = find_player_row(batters_df, row["name_key"], row["team"])
        iso = safe_float(batter_row.get("ISO") if batter_row is not None else None, 0.170)
        xslg = safe_float(batter_row.get("xSLG") if batter_row is not None else None, 0.420)
        barrel = safe_float(batter_row.get("Barrel%") if batter_row is not None else None, 8.0)
        hardhit = safe_float(batter_row.get("HardHit%") if batter_row is not None else None, 38.0)
        hr = safe_float(batter_row.get("HR") if batter_row is not None else None, 0)
        opp_pitch_hand = row["opposing_pitch_hand"] if "opposing_pitch_hand" in row else ""

        score = matchup_score(
            batter_row, pitch_row, row["lineup_spot"], weather,
            park_factor, row["bat_side"], opp_pitch_hand
        )
        _, tier_label, _ = score_tier(score)
        angle = suggested_angle(score, barrel, hr, hardhit)

        # extra populated stats from the (Apr 2026) batters CSV
        xwoba = safe_float(batter_row.get("xwOBA") if batter_row is not None else None, 0.310)
        k_pct = safe_float(batter_row.get("K%")    if batter_row is not None else None, 22.0)
        ev    = safe_float(batter_row.get("EV")    if batter_row is not None else None, 0.0)
        fb_pct= safe_float(batter_row.get("FB%")   if batter_row is not None else None, 0.0)

        out_rows.append({
            "Spot": int(row["lineup_spot"]) if pd.notna(row["lineup_spot"]) else 99,
            "Player": row["player_name"],
            "Pos": row["position"],
            "Tier": tier_label,
            "HR": hr,
            "ISO": iso,
            "xSLG": xslg,
            "xwOBA": xwoba,
            "Barrel%": barrel,
            "HardHit%": hardhit,
            "K%": k_pct,
            "EV": ev,
            "FB%": fb_pct,
            "Score": score,
            "Angle": angle,
        })

    df = pd.DataFrame(out_rows)
    if df.empty:
        return pd.DataFrame(columns=empty_cols)
    if "Spot" in df.columns:
        df = df.sort_values("Spot")
    return df

# ---------------------------------------------------------------------------
# Styling helpers (color cells in dataframes by tier)
# ---------------------------------------------------------------------------
TIER_BG = {
    "Elite":  "background-color: #dcfce7; color: #14532d; font-weight: 800;",
    "Strong": "background-color: #d1fae5; color: #065f46; font-weight: 800;",
    "OK":     "background-color: #fef3c7; color: #78350f; font-weight: 800;",
    "Avoid":  "background-color: #fee2e2; color: #7f1d1d; font-weight: 800;",
    "N/A":    "background-color: #e2e8f0; color: #334155; font-weight: 800;",
}

# Score thresholds used by color_score() — must match the legend at the
# bottom of the page (Elite ≥130 · Strong 110-129 · OK 95-109 · Avoid <95)
TIER_ELITE  = 130
TIER_STRONG = 110
TIER_OK     = 95

def color_metric(value, low, high):
    try:
        v = float(value)
    except:
        return ""
    if v >= high:
        return "background-color: #dcfce7; color: #14532d; font-weight: 800;"
    if v >= low:
        return "background-color: #fef3c7; color: #78350f; font-weight: 800;"
    return "background-color: #fee2e2; color: #7f1d1d; font-weight: 800;"

def color_metric_inverse(value, good_max, bad_min):
    """For stats where LOWER is better (e.g. K% from a hitter's perspective).
    `good_max`: at-or-below this value = green. `bad_min`: at-or-above = red."""
    try:
        v = float(value)
    except:
        return ""
    if v <= good_max:
        return "background-color: #dcfce7; color: #14532d; font-weight: 800;"
    if v <= bad_min:
        return "background-color: #fef3c7; color: #78350f; font-weight: 800;"
    return "background-color: #fee2e2; color: #7f1d1d; font-weight: 800;"

def color_score(value):
    try:
        v = float(value)
    except:
        return ""
    if v >= TIER_ELITE:  return "background-color: #16a34a; color: #ffffff; font-weight: 900;"
    if v >= TIER_STRONG: return "background-color: #86efac; color: #14532d; font-weight: 900;"
    if v >= TIER_OK:     return "background-color: #fde68a; color: #78350f; font-weight: 900;"
    return "background-color: #fecaca; color: #7f1d1d; font-weight: 900;"

def color_tier_cell(value):
    return TIER_BG.get(str(value), "")

def style_lineup_table(df):
    if df.empty:
        return df
    fmt = {
        "ISO": "{:.3f}", "xSLG": "{:.3f}", "xwOBA": "{:.3f}",
        "Barrel%": "{:.1f}", "HardHit%": "{:.1f}",
        "K%": "{:.1f}", "Score": "{:.1f}", "HR": "{:.0f}",
    }
    # only format columns that actually exist in the slice being rendered
    fmt = {k: v for k, v in fmt.items() if k in df.columns}
    styler = df.style.format(fmt)
    base_text = [
        {"selector": "th", "props": [("color", "#0f172a"), ("font-size", "13px"),
                                     ("font-weight", "800"), ("background-color", "#f1f5f9"),
                                     ("text-transform", "uppercase"), ("letter-spacing", "0.05em")]},
        {"selector": "td", "props": [("color", "#0f172a"), ("font-size", "14px"), ("font-weight", "700")]},
    ]
    styler = styler.set_table_styles(base_text)
    if "ISO" in df.columns:
        styler = styler.map(lambda x: color_metric(x, 0.170, 0.220), subset=["ISO"])
    if "xSLG" in df.columns:
        styler = styler.map(lambda x: color_metric(x, 0.420, 0.500), subset=["xSLG"])
    if "xwOBA" in df.columns:
        styler = styler.map(lambda x: color_metric(x, 0.310, 0.360), subset=["xwOBA"])
    if "Barrel%" in df.columns:
        styler = styler.map(lambda x: color_metric(x, 8.0, 12.0), subset=["Barrel%"])
    if "HardHit%" in df.columns:
        styler = styler.map(lambda x: color_metric(x, 38.0, 45.0), subset=["HardHit%"])
    if "K%" in df.columns:
        # K% is INVERSE: lower is better for the hitter, so flip the thresholds
        styler = styler.map(lambda x: color_metric_inverse(x, 18.0, 25.0), subset=["K%"])
    if "Score" in df.columns:
        styler = styler.map(color_score, subset=["Score"])
    if "Tier" in df.columns:
        styler = styler.map(color_tier_cell, subset=["Tier"])
    return styler

# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------
def render_brand_bar(slate_count):
    st.markdown(f"""
    <div class="brand-bar">
        <div>
            <div class="brand-tag">⚾ MrBets850 · MLB Edge</div>
            <div class="brand-name">MLB Matchup Board</div>
        </div>
        <div class="brand-meta">
            <div class="big">{slate_count} {'game' if slate_count == 1 else 'games'} on slate</div>
            <div>Live data · Auto-refresh 30 min</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_game_card(game_row, context, weather):
    rain = weather.get("rain_pct"); rain_str = f"{int(rain)}%" if rain is not None else "N/A"
    temp = weather.get("temp_f");   temp_str = f"{temp}°F" if temp is not None else "N/A"
    wind = weather.get("wind_mph"); wind_str = f"{round(float(wind))} mph" if wind is not None else "N/A"
    away_status = context["away_status"]; home_status = context["home_status"]
    away_pill = "tier-strong" if away_status == "Confirmed" else "tier-ok"
    home_pill = "tier-strong" if home_status == "Confirmed" else "tier-ok"

    st.markdown(f"""
    <div class="section-card dark">
        <div class="game-card">
            <div>
                <div class="matchup">
                    {game_row["away_abbr"]} <span class="vs">@</span> {game_row["home_abbr"]}
                </div>
                <div class="game-meta">{game_row["game_time_ct"]} · {game_row["venue"]} · {game_row["status"]}</div>
            </div>
            <div style="text-align:right;">
                <div style="color:#c7dafe; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.1em; font-weight:800;">Probables</div>
                <div style="color:#fff; font-weight:800; font-size:0.95rem; margin-top:2px;">
                    {game_row["away_probable"]} <span style="color:#94b8ff;">({context["away_pitch_hand"] or "?"})</span>
                </div>
                <div style="color:#fff; font-weight:800; font-size:0.95rem;">
                    vs {game_row["home_probable"]} <span style="color:#94b8ff;">({context["home_pitch_hand"] or "?"})</span>
                </div>
            </div>
        </div>
        <div class="kpi-row">
            <div class="kpi"><span class="k">Park Factor</span>{game_row["park_factor"]}</div>
            <div class="kpi"><span class="k">Temp</span>{temp_str}</div>
            <div class="kpi"><span class="k">Wind</span>{wind_str}</div>
            <div class="kpi"><span class="k">Rain</span>{rain_str}</div>
            <div class="kpi"><span class="tier {away_pill}">{game_row["away_abbr"]}: {away_status}</span></div>
            <div class="kpi"><span class="tier {home_pill}">{game_row["home_abbr"]}: {home_status}</span></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_hot_batter_tile(rank, row, opposing_pitcher, opposing_team_abbr):
    score = float(row.get("Score", 0) or 0)
    barrel = float(row.get("Barrel%", 0) or 0)
    hardhit = float(row.get("HardHit%", 0) or 0)
    hr = float(row.get("HR", 0) or 0)
    iso = float(row.get("ISO", 0) or 0)
    ev = float(row.get("EV", 0) or 0)
    fb_pct = float(row.get("FB%", 0) or 0)
    tier_key, tier_label, _ = score_tier(score)
    hr_key, hr_label = hr_tier(score, barrel, hr)
    angle = suggested_angle(score, barrel, hr, hardhit)
    spot = int(row.get("Spot", 0) or 0)

    ev_str = f"{ev:.1f}" if ev > 0 else "—"
    fb_str = f"{fb_pct:.1f}%" if fb_pct > 0 else "—"

    st.markdown(f"""
    <div class="batter-tile {tier_key}">
        <div class="rank">#{rank} · {tier_label} · Bat {spot}</div>
        <div class="name">{row.get("Player", "")}</div>
        <div class="vs">vs {opposing_pitcher} ({opposing_team_abbr})</div>
        <div class="score">{score:.1f}<span style="font-size:0.7rem; color:#64748b; font-weight:700; margin-left:6px;">SCORE</span></div>
        <div class="stats">
            HR <b>{int(hr)}</b> · ISO <b>{iso:.3f}</b> · Barrel <b>{barrel:.1f}%</b> · HardHit <b>{hardhit:.1f}%</b>
        </div>
        <div class="stats" style="margin-top:4px;">
            EV <b>{ev_str}</b> · FB% <b>{fb_str}</b>
        </div>
        <div class="angle">{angle} · <span class="tier tier-{hr_key}" style="font-size:0.7rem; padding:2px 8px;">{hr_label}</span></div>
    </div>
    """, unsafe_allow_html=True)

def cold_angle(score, k_pct, xwoba, barrel):
    """Suggest a fade angle for a cold-matchup batter. The lower the score and
    the higher the K% the more aggressive the fade language."""
    s = float(score or 0); k = float(k_pct or 22); x = float(xwoba or 0.310); b = float(barrel or 0)
    if s < 70 and k >= 28:               return "🚫 Strong fade · K prop lean"
    if s < 80 and (k >= 25 or x < 0.290): return "🚫 Fade HR · Under TB lean"
    if s < 95:                            return "⚠️ Avoid · Tough matchup"
    return "⚠️ Below avg · Skip"

def render_cold_batter_tile(rank, row, opposing_pitcher, opposing_team_abbr):
    score = float(row.get("Score", 0) or 0)
    barrel = float(row.get("Barrel%", 0) or 0)
    hardhit = float(row.get("HardHit%", 0) or 0)
    k_pct = float(row.get("K%", 0) or 0)
    xwoba = float(row.get("xwOBA", 0) or 0)
    iso = float(row.get("ISO", 0) or 0)
    spot = int(row.get("Spot", 0) or 0)
    angle = cold_angle(score, k_pct, xwoba, barrel)

    # All cold-board tiles use the red/avoid styling regardless of underlying tier
    st.markdown(f"""
    <div class="batter-tile avoid">
        <div class="rank">#{rank} · Cold · Bat {spot}</div>
        <div class="name">{row.get("Player", "")}</div>
        <div class="vs">vs {opposing_pitcher} ({opposing_team_abbr})</div>
        <div class="score">{score:.1f}<span style="font-size:0.7rem; color:#64748b; font-weight:700; margin-left:6px;">SCORE</span></div>
        <div class="stats">
            K% <b>{k_pct:.1f}</b> · xwOBA <b>{xwoba:.3f}</b> · ISO <b>{iso:.3f}</b> · Barrel <b>{barrel:.1f}%</b>
        </div>
        <div class="angle">{angle}</div>
    </div>
    """, unsafe_allow_html=True)

def render_pitcher_tile(label, pitcher_name, pitch_hand, pitcher_row):
    score, key, verdict = pitcher_vulnerability(pitcher_row)
    if pitcher_row is None:
        k = bb = era_w = barrel = hardhit = "—"
        sub = "No Savant data"
    else:
        k = f"{safe_float(pitcher_row.get('K%')):.1f}%"
        bb = f"{safe_float(pitcher_row.get('BB%')):.1f}%"
        era_w = f"{safe_float(pitcher_row.get('xwOBA')):.3f}"
        barrel = f"{safe_float(pitcher_row.get('Barrel%')):.1f}%"
        hardhit = f"{safe_float(pitcher_row.get('HardHit%')):.1f}%"
        sub = "Baseball Savant"

    st.markdown(f"""
    <div class="pitcher-tile vuln-{key}">
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <div>
                <div style="font-size:0.7rem; color:#64748b; text-transform:uppercase; letter-spacing:0.1em; font-weight:800;">{label}</div>
                <h4>{pitcher_name or "TBD"} <span style="color:#64748b; font-size:0.85rem;">({pitch_hand or "?"})</span></h4>
                <div class="meta">{sub}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:1.6rem; font-weight:900; color:#0f172a; line-height:1;">{score}</div>
                <span class="tier tier-{('elite' if key=='elite' else 'strong' if key=='strong' else 'ok' if key=='ok' else 'avoid')}">{verdict}</span>
            </div>
        </div>
        <div class="grid">
            <div><div class="k">K%</div><div class="v">{k}</div></div>
            <div><div class="k">BB%</div><div class="v">{bb}</div></div>
            <div><div class="k">xwOBA</div><div class="v">{era_w}</div></div>
            <div><div class="k">Barrel%</div><div class="v">{barrel}</div></div>
            <div><div class="k">HardHit%</div><div class="v">{hardhit}</div></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Pitch-mix (arsenal) lookup from Baseball Savant player page
# ---------------------------------------------------------------------------
# The savant-player HTML embeds the current-season pitch usage breakdown as
# a series of paired <div> blocks: the pitch name (colored) followed by the
# usage percentage in parentheses. We parse the most recent season block
# (the page lists years descending). Cached for 6 hours so we make at most
# two calls per game-card view per slate.
# Note: Savant's player page uses friendly labels like "Four Seamer" (not
# "4-Seam Fastball") for inline pitch breakdowns; we map both forms.
PITCH_NAME_TO_CAT = {
    "Four Seamer":         ("Fastball", "FF"),
    "4-Seam Fastball":     ("Fastball", "FF"),
    "Sinker":              ("Fastball", "SI"),
    "Cutter":              ("Fastball", "FC"),
    "Changeup":            ("Offspeed", "CH"),
    "Splitter":            ("Offspeed", "FS"),
    "Split-Finger":        ("Offspeed", "FS"),
    "Forkball":            ("Offspeed", "FO"),
    "Screwball":           ("Offspeed", "SC"),
    "Slider":              ("Breaking", "SL"),
    "Curveball":           ("Breaking", "CU"),
    "Knuckle Curve":       ("Breaking", "KC"),
    "Sweeper":             ("Breaking", "ST"),
    "Slurve":              ("Breaking", "SV"),
    "Knuckleball":         ("Breaking", "KN"),
    "Eephus":              ("Other",    "EP"),
}
CATEGORY_COLOR = {"Fastball": "#ef4444", "Offspeed": "#22c55e",
                  "Breaking": "#3b82f6", "Other":    "#94a3b8"}

# Mapping from the Savant CSV's n_*_formatted columns to a display name and
# Savant-style color. Velocity column name is included so chips can show MPH.
CSV_ARSENAL_COLS = [
    # (usage_col,             velocity_col,    display_name,       color)
    ("n_ff_formatted",        "ff_avg_speed",  "4-Seam Fastball",  "#D22D49"),
    ("n_si_formatted",        "si_avg_speed",  "Sinker",           "#FE9D00"),
    ("n_fc_formatted",        "fc_avg_speed",  "Cutter",           "#933F2C"),
    ("n_sl_formatted",        "sl_avg_speed",  "Slider",           "#C3BD0E"),
    ("n_cu_formatted",        "cu_avg_speed",  "Curveball",        "#00D1ED"),
    ("n_ch_formatted",        "ch_avg_speed",  "Changeup",         "#1DBE3A"),
]

def arsenal_from_pitcher_row(pitcher_row):
    """Build the same (name, pct, category, color, velo) tuple list directly
    from the Savant pitcher CSV row. Returns [] if no usage data is found,
    so the caller can fall back to the web scraper."""
    if pitcher_row is None:
        return []
    out = []
    for usage_col, velo_col, name, color in CSV_ARSENAL_COLS:
        pct = safe_float(pitcher_row.get(usage_col), 0)
        if pct <= 0:
            continue
        velo = safe_float(pitcher_row.get(velo_col), 0)
        cat, _ = PITCH_NAME_TO_CAT.get(name, ("Other", ""))
        out.append((name, float(pct), cat, color, float(velo) if velo > 0 else None))
    out.sort(key=lambda x: x[1], reverse=True)
    return out

@st.cache_data(ttl=21600, show_spinner=False)  # 6 hours
def get_pitch_arsenal(mlb_id):
    """Return list[(pitch_name, usage_pct, category, color_hex)] for a pitcher,
    most recent season first. Returns [] on any failure.
    Used only as a fallback when the pitcher row in the CSV has no n_*_formatted
    data (rare — e.g. brand-new call-ups)."""
    if not mlb_id:
        return []
    url = f"https://baseballsavant.mlb.com/savant-player/{int(mlb_id)}?stats=statcast-r-pitching-mlb"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        html = resp.text
    except Exception:
        return []

    # The page lists pitches as:
    #   <div style="display: inline-block; color: #XXXXXX;">Sinker</div>
    #   <div style="display: inline-block;">(18.2%) ...
    pattern = re.compile(
        r'<div style="display: inline-block; color: #([0-9A-Fa-f]{6});">'
        r'([^<]+)</div>\s*<div style="display: inline-block;">\((\d+\.?\d*)%\)',
        re.IGNORECASE
    )
    matches = pattern.findall(html)
    if not matches:
        return []

    # The page repeats the block for each season (current year first). Cap at
    # the first ~10 hits which represent the current-season arsenal.
    seen = set()
    out = []
    for color, name, pct in matches:
        clean = name.strip()
        if clean in seen:
            # second occurrence = previous season -> stop
            break
        seen.add(clean)
        cat, _abbr = PITCH_NAME_TO_CAT.get(clean, ("Other", ""))
        # 5-tuple to match arsenal_from_pitcher_row (no velocity from HTML)
        out.append((clean, float(pct), cat, f"#{color}", None))
    # sort high -> low usage
    out.sort(key=lambda x: x[1], reverse=True)
    return out

def render_pitch_mix_tile(pitcher_name, mlb_id, pitcher_row=None):
    """Render the pitch-mix card. Tries the CSV row first (fast, includes
    velocity), then falls back to scraping the Savant player page."""
    arsenal = arsenal_from_pitcher_row(pitcher_row)
    source = "Baseball Savant CSV"
    if not arsenal:
        arsenal = get_pitch_arsenal(mlb_id)
        source = "Baseball Savant (live)"

    if not arsenal:
        st.markdown(f"""
        <div class="section-card" style="padding:14px 18px;">
            <div style="font-size:0.78rem; color:#64748b; text-transform:uppercase; letter-spacing:0.1em; font-weight:800;">Pitch Mix</div>
            <div style="color:#64748b; font-size:0.9rem; margin-top:4px;">No arsenal data available for {pitcher_name or "TBD"}.</div>
        </div>
        """, unsafe_allow_html=True)
        return

    # Build a horizontal stacked-bar visualization
    bar_segments = "".join(
        f'<div title="{name} {pct:.1f}%" style="flex:{pct}; background:{color}; '
        f'min-width:1px; border-right:1px solid #fff;"></div>'
        for (name, pct, _cat, color, _velo) in arsenal
    )
    # Each chip optionally shows the average velocity (MPH) from the CSV.
    chips = "".join(
        f'<span style="display:inline-flex; align-items:center; gap:6px; '
        f'padding:4px 10px; border-radius:999px; background:#f1f5f9; '
        f'border:1px solid #e2e8f0; font-size:0.82rem; font-weight:700; '
        f'color:#0f172a; margin:3px 4px 3px 0;">'
        f'<span style="width:10px; height:10px; border-radius:2px; background:{color};"></span>'
        f'{name} <b style="color:#475569;">{pct:.1f}%</b>'
        + (f' <span style="color:#94a3b8; font-weight:600;">· {velo:.1f} mph</span>' if velo else '')
        + '</span>'
        for (name, pct, _cat, color, velo) in arsenal
    )
    st.markdown(f"""
    <div class="section-card" style="padding:14px 18px;">
        <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px;">
            <div style="font-size:0.78rem; color:#64748b; text-transform:uppercase; letter-spacing:0.1em; font-weight:800;">Pitch Mix · {pitcher_name}</div>
            <div style="font-size:0.72rem; color:#94a3b8; font-weight:700;">Current season · {source}</div>
        </div>
        <div style="display:flex; height:14px; border-radius:7px; overflow:hidden; box-shadow:inset 0 0 0 1px #e2e8f0;">
            {bar_segments}
        </div>
        <div style="margin-top:10px;">{chips}</div>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
with st.spinner("Loading Baseball Savant data from GitHub..."):
    csvs = load_all_csvs()
batters_df = standardize_columns(csvs.get("batters", pd.DataFrame()))
pitchers_df = standardize_columns(csvs.get("pitchers", pd.DataFrame()))

# --- Top controls row -------------------------------------------------------
top_cols = st.columns([2.2, 1, 1])
with top_cols[0]:
    selected_date = st.date_input("📅 Slate date", value=date.today())
with top_cols[1]:
    if st.button("🔄 Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
with top_cols[2]:
    show_avoids = st.toggle("Show 'Avoid' tier", value=True, help="Hide red-tier batters from the Hot board")

# --- Schedule ---------------------------------------------------------------
try:
    schedule_df = get_schedule(selected_date)
except Exception as e:
    render_brand_bar(0)
    st.error(f"Schedule load failed: {e}")
    st.stop()

render_brand_bar(len(schedule_df))

if batters_df.empty and pitchers_df.empty:
    st.error(
        f"No CSV data could be loaded from GitHub. Verify the repo "
        f"https://github.com/{GITHUB_USER}/{GITHUB_REPO} is public and CSV_FILES paths are correct."
    )

if schedule_df.empty:
    st.warning("No games found for this date.")
    st.stop()

# --- Game picker (radio pills) ---------------------------------------------
st.markdown('<div class="section-label">Choose a game</div>', unsafe_allow_html=True)
game_label = st.radio(
    "Choose game",
    schedule_df["label"].tolist(),
    horizontal=True,
    label_visibility="collapsed",
)
game_row = schedule_df[schedule_df["label"] == game_label].iloc[0]
context = build_game_context(game_row)
weather = context["weather"]

# --- Selected game card -----------------------------------------------------
render_game_card(game_row, context, weather)

# --- Build tables for both teams -------------------------------------------
away_table = build_team_table(
    context["away_lineup"], batters_df, pitchers_df,
    game_row["home_probable"], weather, game_row["park_factor"]
)
home_table = build_team_table(
    context["home_lineup"], batters_df, pitchers_df,
    game_row["away_probable"], weather, game_row["park_factor"]
)

# Tag combined for hot-batter board
combined_rows = []
for _, r in away_table.iterrows():
    rr = r.to_dict(); rr["TeamAbbr"] = game_row["away_abbr"]
    rr["OppPitcher"] = game_row["home_probable"]; rr["OppTeamAbbr"] = game_row["home_abbr"]
    combined_rows.append(rr)
for _, r in home_table.iterrows():
    rr = r.to_dict(); rr["TeamAbbr"] = game_row["home_abbr"]
    rr["OppPitcher"] = game_row["away_probable"]; rr["OppTeamAbbr"] = game_row["away_abbr"]
    combined_rows.append(rr)
combined_df = pd.DataFrame(combined_rows)

# --- 🔥 Hot Batters board (top 8 by score) ----------------------------------
st.markdown(
    '<div class="section-card">'
    '<div class="section-title">🔥 Hot Batters · Top Targets This Game</div>'
    '<div style="margin-bottom:10px;">'
    '<span class="tier tier-elite">Elite ≥130</span> &nbsp;'
    '<span class="tier tier-strong">Strong 110-129</span> &nbsp;'
    '<span class="tier tier-ok">OK 95-109</span> &nbsp;'
    '<span class="tier tier-avoid">Avoid &lt;95</span>'
    '</div></div>', unsafe_allow_html=True
)

if combined_df.empty:
    st.info("Lineups not posted yet. Hot batters will appear once today's lineups are confirmed.")
else:
    df_hot = combined_df.copy()
    if not show_avoids:
        df_hot = df_hot[df_hot["Score"] >= 65]
    df_hot = df_hot.sort_values("Score", ascending=False).head(8).reset_index(drop=True)
    if df_hot.empty:
        st.info("No qualifying batters at this filter level. Toggle 'Show Avoid tier' to see all.")
    else:
        cols_per_row = 4
        rows = [df_hot.iloc[i:i+cols_per_row] for i in range(0, len(df_hot), cols_per_row)]
        for row_chunk in rows:
            cols = st.columns(len(row_chunk))
            for i, (_, r) in enumerate(row_chunk.iterrows()):
                with cols[i]:
                    render_hot_batter_tile(
                        rank=int(r.name)+1 if hasattr(r, 'name') else i+1,
                        row=r,
                        opposing_pitcher=r["OppPitcher"],
                        opposing_team_abbr=r["OppTeamAbbr"],
                    )

# --- 🧊 Cold Batters board (bottom 6 by score) ------------------------------
st.markdown(
    '<div class="section-card">'
    '<div class="section-title">🧊 Cold Batters · Fade Targets This Game</div>'
    '<div style="font-size:0.78rem; color:#64748b;">'
    'Worst matchups for the slate — strong K%/low xwOBA hitters facing tough pitching. '
    'Good for K-prop overs, under TB / under hits angles.'
    '</div></div>', unsafe_allow_html=True
)
if combined_df.empty:
    st.info("Lineups not posted yet. Cold batters will appear once today's lineups are confirmed.")
else:
    df_cold = combined_df.copy().sort_values("Score", ascending=True).head(6).reset_index(drop=True)
    if df_cold.empty:
        st.info("No batter data available yet.")
    else:
        cols_per_row = 3
        rows = [df_cold.iloc[i:i+cols_per_row] for i in range(0, len(df_cold), cols_per_row)]
        for row_chunk in rows:
            cols = st.columns(len(row_chunk))
            for i, (_, r) in enumerate(row_chunk.iterrows()):
                with cols[i]:
                    render_cold_batter_tile(
                        rank=int(r.name)+1 if hasattr(r, 'name') else i+1,
                        row=r,
                        opposing_pitcher=r["OppPitcher"],
                        opposing_team_abbr=r["OppTeamAbbr"],
                    )

# --- Pitcher Vulnerability panel -------------------------------------------
st.markdown(
    '<div class="section-card">'
    '<div class="section-title">🎯 Pitcher Vulnerability · Who To Attack</div>'
    '</div>', unsafe_allow_html=True
)
pcols = st.columns(2)
with pcols[0]:
    render_pitcher_tile(
        label=f"Away SP — {game_row['away_abbr']}",
        pitcher_name=game_row["away_probable"],
        pitch_hand=context["away_pitch_hand"],
        pitcher_row=find_pitcher_row(pitchers_df, game_row["away_probable"]),
    )
with pcols[1]:
    render_pitcher_tile(
        label=f"Home SP — {game_row['home_abbr']}",
        pitcher_name=game_row["home_probable"],
        pitch_hand=context["home_pitch_hand"],
        pitcher_row=find_pitcher_row(pitchers_df, game_row["home_probable"]),
    )

# --- Pitch Mix (arsenal) for both probable starters ------------------------
st.markdown(
    '<div class="section-card">'
    '<div class="section-title">🎯 Pitch Mix · What They Throw</div>'
    '<div style="font-size:0.78rem; color:#64748b;">'
    'Current-season usage from Baseball Savant. Hover any segment for the exact percentage.'
    '</div></div>', unsafe_allow_html=True
)
mcols = st.columns(2)
with mcols[0]:
    away_id = lookup_pitcher_mlb_id(context.get("away_roster"), pitchers_df, game_row["away_probable"])
    away_p_row = find_pitcher_row(pitchers_df, game_row["away_probable"])
    render_pitch_mix_tile(game_row["away_probable"], away_id, away_p_row)
with mcols[1]:
    home_id = lookup_pitcher_mlb_id(context.get("home_roster"), pitchers_df, game_row["home_probable"])
    home_p_row = find_pitcher_row(pitchers_df, game_row["home_probable"])
    render_pitch_mix_tile(game_row["home_probable"], home_id, home_p_row)

# --- Lineup tables ----------------------------------------------------------
def render_lineup_section(team_abbr, opp_pitcher, table):
    st.markdown(
        f'<div class="section-card">'
        f'<div class="section-title">📋 {team_abbr} Lineup vs {opp_pitcher}</div>'
        f'</div>', unsafe_allow_html=True
    )
    if table.empty or "Spot" not in table.columns:
        st.info(f"{team_abbr} lineup not posted yet — check back closer to first pitch.")
    else:
        display = table[["Spot", "Player", "Pos", "Tier",
                         "HR", "ISO", "xSLG", "xwOBA",
                         "Barrel%", "HardHit%", "K%", "Score", "Angle"]]
        st.dataframe(style_lineup_table(display), width="stretch", hide_index=True)

render_lineup_section(game_row["away_abbr"], game_row["home_probable"], away_table)
render_lineup_section(game_row["home_abbr"], game_row["away_probable"], home_table)

# --- Slate-wide hot board (across all games) -------------------------------
with st.expander("🌡️ Slate-wide Hot Batter Board (all games today)", expanded=False):
    st.caption("Top batters across the entire slate, regardless of game. Loads only when expanded.")
    slate_rows = []
    progress = st.progress(0.0, text="Scoring slate...")
    for idx, (_, g_row) in enumerate(schedule_df.iterrows()):
        try:
            ctx = build_game_context(g_row)
            wx = ctx["weather"]
            at = build_team_table(ctx["away_lineup"], batters_df, pitchers_df, g_row["home_probable"], wx, g_row["park_factor"])
            ht = build_team_table(ctx["home_lineup"], batters_df, pitchers_df, g_row["away_probable"], wx, g_row["park_factor"])
            for _, r in at.iterrows():
                d = r.to_dict(); d["TeamAbbr"] = g_row["away_abbr"]; d["Game"] = g_row["short_label"]
                d["OppPitcher"] = g_row["home_probable"]; d["OppTeamAbbr"] = g_row["home_abbr"]
                slate_rows.append(d)
            for _, r in ht.iterrows():
                d = r.to_dict(); d["TeamAbbr"] = g_row["home_abbr"]; d["Game"] = g_row["short_label"]
                d["OppPitcher"] = g_row["away_probable"]; d["OppTeamAbbr"] = g_row["away_abbr"]
                slate_rows.append(d)
        except Exception:
            pass
        progress.progress((idx+1)/max(len(schedule_df),1), text=f"Scored {idx+1}/{len(schedule_df)} games")
    progress.empty()

    slate_df = pd.DataFrame(slate_rows)
    if slate_df.empty:
        st.info("No lineups posted yet across the slate.")
    else:
        slate_df = slate_df.sort_values("Score", ascending=False).head(15).reset_index(drop=True)
        slate_display = slate_df[["Game", "TeamAbbr", "Player", "Spot", "Tier",
                                  "HR", "ISO", "xwOBA", "Barrel%", "HardHit%", "K%", "Score", "Angle"]].rename(
            columns={"TeamAbbr": "Team"}
        )
        st.dataframe(style_lineup_table(slate_display), width="stretch", hide_index=True)

# --- Footer / data status ---------------------------------------------------
with st.expander("📊 Data status & sources", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Batters loaded", len(batters_df))
    with c2:
        st.metric("Pitchers loaded", len(pitchers_df))
    st.caption("Source: raw GitHub URLs (auto-refreshes every 30 min). To update data, commit new CSVs to the repo and click 'Refresh data'.")
    for label, url in CSV_URLS.items():
        st.markdown(f"- **{label}**: [{CSV_FILES[label]}]({url})")

st.markdown(
    '<div class="footer">⚾ <b>MrBets850 MLB Edge</b> · '
    'Powered by Baseball Savant + MLB StatsAPI + Open-Meteo · '
    'Color tiers: Elite 🟢 (≥130) · Strong 🟢 (110-129) · OK 🟡 (95-109) · Avoid 🔴 (&lt;95) · '
    'For research purposes only.</div>',
    unsafe_allow_html=True,
)
