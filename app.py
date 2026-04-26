import streamlit as st
import pandas as pd
import numpy as np
import requests
import re
import unicodedata
import urllib.parse
import io
from datetime import date

# ===========================================================================
# Config
# ===========================================================================
GITHUB_USER = "Mrbets850"
GITHUB_REPO = "mlb-data-app"
GITHUB_BRANCH = "main"

CSV_FILES = {
    "batters":  "Data:savant_batters.csv.csv",
    "pitchers": "Data:savant_pitchers.csv.csv",
}

def raw_github_url(path: str) -> str:
    encoded = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{encoded}"

CSV_URLS = {label: raw_github_url(name) for label, name in CSV_FILES.items()}

st.set_page_config(
    page_title="MrBets850 — MLB Edge",
    page_icon="⚾",
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
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 22px; border-radius: 18px;
    background: linear-gradient(110deg, #0b1437 0%, #0a2d6e 55%, #0b4ea2 100%);
    box-shadow: 0 12px 28px rgba(7,18,55,0.22);
    margin-bottom: 14px; color: #fff;
}
.brand-name { font-size: 1.55rem; font-weight: 900; letter-spacing: 0.04em; line-height: 1.05; color:#fff; }
.brand-tag  { color: #c7dafe; font-size: 0.78rem; letter-spacing: 0.18em; text-transform: uppercase; font-weight: 700; }
.brand-meta { text-align: right; color: #dbeafe; font-size: 0.92rem; font-weight: 700; }
.brand-meta .big { font-size: 1.1rem; color:#fff; font-weight: 800; }

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
}
.game-pill:hover { border-color: #94a3b8; background: #f1f5f9; }
.game-pill.active {
    border-color: #1d4ed8;
    background: linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
    box-shadow: 0 4px 14px rgba(29,78,216,0.18);
}
.game-pill .logos {
    display: flex; align-items: center; justify-content: center; gap: 6px;
    margin-bottom: 4px;
}
.game-pill .logos img { width: 38px; height: 38px; object-fit: contain; }
.game-pill .at { color: #64748b; font-weight: 800; font-size: 1.05rem; }
.game-pill .matchup-text {
    color: #0f172a; font-weight: 800; font-size: 0.85rem; letter-spacing: 0.02em;
}
.game-pill .time {
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
            "lineup_spot": lineup_spot
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
    away_lineup = away_roster[(away_roster["lineup_spot"].notna()) & (away_roster["position"] != "P")].copy()
    home_lineup = home_roster[(home_roster["lineup_spot"].notna()) & (home_roster["position"] != "P")].copy()
    if not away_lineup.empty: away_lineup = away_lineup.sort_values("lineup_spot")
    if not home_lineup.empty: home_lineup = home_lineup.sort_values("lineup_spot")
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
        "away_status": "Confirmed" if len(away_lineup) >= 9 else "Not Confirmed",
        "home_status": "Confirmed" if len(home_lineup) >= 9 else "Not Confirmed",
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
    show_cols = [c for c in df.columns if c != "_HR Form Num"]
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
# UI components
# ===========================================================================
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

def render_game_carousel(schedule_df, selected_idx):
    """Horizontal scrolling logo carousel — display-only (HTML).
    Selection itself is handled by a hidden radio below to keep Streamlit reactive."""
    if schedule_df.empty:
        return
    pills = []
    for i, g in schedule_df.iterrows():
        active = "active" if i == selected_idx else ""
        away_logo = logo_url(g["away_id"]) if g["away_id"] else ""
        home_logo = logo_url(g["home_id"]) if g["home_id"] else ""
        pills.append(
            f'<div class="game-pill {active}">'
            f'<div class="logos">'
            f'<img src="{away_logo}" alt="{g["away_abbr"]}" />'
            f'<span class="at">@</span>'
            f'<img src="{home_logo}" alt="{g["home_abbr"]}" />'
            f'</div>'
            f'<div class="matchup-text">{g["away_abbr"]} @ {g["home_abbr"]}</div>'
            f'<div class="time">{g["time_short"]}</div>'
            f'</div>'
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
    away_pill = "tier-strong" if ctx["away_status"] == "Confirmed" else "tier-ok"
    home_pill = "tier-strong" if ctx["home_status"] == "Confirmed" else "tier-ok"
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
    pill_cls = "tier-strong" if status == "Confirmed" else "tier-ok"
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

# top controls
top_cols = st.columns([2.5, 1])
with top_cols[0]:
    selected_date = st.date_input("📅 Slate date", value=date.today(), label_visibility="collapsed")
with top_cols[1]:
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

try:
    schedule_df = get_schedule(selected_date)
except Exception as e:
    render_brand_bar(0)
    st.error(f"Schedule load failed: {e}")
    st.stop()

render_brand_bar(len(schedule_df))

if batters_df.empty and pitchers_df.empty:
    st.error(f"No CSV data could be loaded. Check https://github.com/{GITHUB_USER}/{GITHUB_REPO}.")

if schedule_df.empty:
    st.warning("No games found for this date.")
    st.stop()

# ----- Game selector: HTML carousel + invisible radio for state -----
# Streamlit needs a real widget for state; we render the pretty carousel above
# and a compact radio below it that drives selection.
labels = schedule_df["label"].tolist()
selected_label = st.radio("Game selector", labels, horizontal=True, label_visibility="collapsed", key="game_pick")
selected_idx = labels.index(selected_label) if selected_label in labels else 0

render_game_carousel(schedule_df, selected_idx)

game_row = schedule_df.iloc[selected_idx]
ctx = build_game_context(game_row)
weather = ctx["weather"]
render_game_header(game_row, ctx, weather)

# ----- Build all tables once -----
away_matchup = build_matchup_table(ctx["away_lineup"], batters_df, pitchers_df, game_row["home_probable"], weather, game_row["park_factor"])
home_matchup = build_matchup_table(ctx["home_lineup"], batters_df, pitchers_df, game_row["away_probable"], weather, game_row["park_factor"])

# ----- Tabs -----
tab_matchup, tab_rolling, tab_p_zones, tab_h_zones, tab_export = st.tabs(
    ["📊 Matchup", "📈 Rolling", "🎯 Pitcher Zones", "🌡️ Hitter Zones", "💾 Exports"]
)

# ============== Matchup tab ==============
with tab_matchup:
    # away lineup
    render_lineup_banner(game_row["away_id"], game_row["away_abbr"], game_row["home_probable"], ctx["away_status"])
    if away_matchup.empty:
        st.info(f"{game_row['away_abbr']} lineup not posted yet — check back closer to first pitch.")
    else:
        st.dataframe(style_matchup_table(away_matchup), use_container_width=True, hide_index=True, height=min(440, 60 + 38*len(away_matchup)))
    # home lineup
    render_lineup_banner(game_row["home_id"], game_row["home_abbr"], game_row["away_probable"], ctx["home_status"])
    if home_matchup.empty:
        st.info(f"{game_row['home_abbr']} lineup not posted yet — check back closer to first pitch.")
    else:
        st.dataframe(style_matchup_table(home_matchup), use_container_width=True, hide_index=True, height=min(440, 60 + 38*len(home_matchup)))

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

# ============== Exports tab ==============
with tab_export:
    st.markdown(f'<div class="section-title">💾 Download Game Data</div>', unsafe_allow_html=True)
    st.caption("Export the current game's tables as CSV for client decks, parlays, or further analysis.")
    cols_dl = st.columns(2)
    if not away_matchup.empty:
        with cols_dl[0]:
            csv = away_matchup.drop(columns=[c for c in away_matchup.columns if c.startswith("_")], errors="ignore").to_csv(index=False)
            st.download_button(f"⬇️ {game_row['away_abbr']} Matchup CSV", csv, file_name=f"{selected_date}_{game_row['away_abbr']}_matchup.csv", mime="text/csv", use_container_width=True)
    if not home_matchup.empty:
        with cols_dl[1]:
            csv = home_matchup.drop(columns=[c for c in home_matchup.columns if c.startswith("_")], errors="ignore").to_csv(index=False)
            st.download_button(f"⬇️ {game_row['home_abbr']} Matchup CSV", csv, file_name=f"{selected_date}_{game_row['home_abbr']}_matchup.csv", mime="text/csv", use_container_width=True)

    # Slate-wide export (top targets across all games)
    st.markdown('<div class="section-title" style="margin-top:18px;">🌡️ Slate-wide Top Targets</div>', unsafe_allow_html=True)
    if st.button("Compute slate-wide rankings (all games)"):
        with st.spinner("Scoring all games..."):
            rows = []
            for _, g in schedule_df.iterrows():
                try:
                    cc = build_game_context(g)
                    a = build_matchup_table(cc["away_lineup"], batters_df, pitchers_df, g["home_probable"], cc["weather"], g["park_factor"])
                    h = build_matchup_table(cc["home_lineup"], batters_df, pitchers_df, g["away_probable"], cc["weather"], g["park_factor"])
                    for _, r in a.iterrows():
                        d = r.to_dict(); d["Game"] = g["short_label"]; d["OppPitcher"] = g["home_probable"]; rows.append(d)
                    for _, r in h.iterrows():
                        d = r.to_dict(); d["Game"] = g["short_label"]; d["OppPitcher"] = g["away_probable"]; rows.append(d)
                except Exception:
                    pass
            slate = pd.DataFrame(rows)
            if slate.empty:
                st.info("No lineups posted yet across the slate.")
            else:
                slate = slate.drop(columns=[c for c in slate.columns if c.startswith("_")], errors="ignore")
                slate = slate.sort_values("Matchup", ascending=False).head(25).reset_index(drop=True)
                show = slate[["Game", "Hitter", "Team", "Spot", "Matchup", "Test Score", "Ceiling", "Zone Fit", "HR Form", "kHR"]]
                st.dataframe(style_matchup_table(show.assign(Bat="").rename(columns={"Game":"Game"}).drop(columns=["Bat"], errors="ignore")), use_container_width=True, hide_index=True)
                csv = show.to_csv(index=False)
                st.download_button("⬇️ Download slate top-25 CSV", csv, file_name=f"{selected_date}_slate_top25.csv", mime="text/csv", use_container_width=True)

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
