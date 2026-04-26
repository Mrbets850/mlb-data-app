import streamlit as st
import pandas as pd
import requests
import os
import io
import re
import unicodedata
from datetime import date

st.set_page_config(page_title="MLB Matchup Board", layout="centered")

# -----------------------------
# STYLE
# -----------------------------
st.markdown("""
<style>
.block-container {
    padding-top: 1rem;
    padding-bottom: 3rem;
    max-width: 820px;
}
html, body, [class*="css"]  {
    font-family: Inter, system-ui, sans-serif;
}
.main-title {
    font-size: 2rem;
    font-weight: 800;
    margin-bottom: 0.2rem;
    color: #f8fafc;
}
.sub-title {
    color: #94a3b8;
    margin-bottom: 1rem;
}
.top-card, .section-card {
    background: linear-gradient(180deg, #111827 0%, #0f172a 100%);
    border: 1px solid #1f2937;
    border-radius: 18px;
    padding: 16px;
    margin-bottom: 14px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.18);
}
.section-label {
    color: #cbd5e1;
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
}
.big-matchup {
    font-size: 1.55rem;
    font-weight: 800;
    color: #ffffff;
    margin-bottom: 4px;
}
.mid-text {
    color: #cbd5e1;
    font-size: 0.95rem;
}
.pill-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
}
.pill-good {
    background: rgba(22,163,74,0.18);
    color: #86efac;
    border: 1px solid rgba(22,163,74,0.35);
    border-radius: 999px;
    padding: 6px 10px;
    font-size: 0.82rem;
    font-weight: 700;
}
.pill-bad {
    background: rgba(220,38,38,0.16);
    color: #fca5a5;
    border: 1px solid rgba(220,38,38,0.35);
    border-radius: 999px;
    padding: 6px 10px;
    font-size: 0.82rem;
    font-weight: 700;
}
.pill-neutral {
    background: rgba(234,179,8,0.16);
    color: #fde68a;
    border: 1px solid rgba(234,179,8,0.35);
    border-radius: 999px;
    padding: 6px 10px;
    font-size: 0.82rem;
    font-weight: 700;
}
.metric-box {
    background: #0b1220;
    border: 1px solid #1e293b;
    border-radius: 14px;
    padding: 12px;
    margin-bottom: 10px;
}
.metric-k {
    color: #94a3b8;
    font-size: 0.82rem;
    margin-bottom: 2px;
}
.metric-v {
    color: #f8fafc;
    font-size: 1.05rem;
    font-weight: 800;
}
div[data-baseweb="select"] > div,
div[data-baseweb="base-input"] > div {
    border-radius: 14px !important;
    border: 1px solid #334155 !important;
    background: #111827 !important;
}
.stDateInput label, .stSelectbox label {
    font-weight: 700 !important;
}
[data-testid="stDataFrame"] {
    border-radius: 14px;
    overflow: hidden;
}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# CONSTANTS
# -----------------------------
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

# -----------------------------
# HELPERS
# -----------------------------
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
        "avg": "AVG",
        "obp": "OBP",
        "slg": "SLG",
        "ops": "OPS",
        "iso": "ISO",
        "woba": "wOBA",
        "xwoba": "xwOBA",
        "xslg": "xSLG",
        "barrel%": "Barrel%",
        "barrel_batted_rate": "Barrel%",
        "barrels_per_bbe_percent": "Barrel%",
        "hardhit%": "HardHit%",
        "hard_hit_percent": "HardHit%",
        "hard_hit_rate": "HardHit%",
        "exit_velocity_avg": "EV",
        "avg_hit_speed": "EV",
        "launch_angle_avg": "LA",
        "launch_angle": "LA",
        "k_percent": "K%",
        "bb_percent": "BB%",
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

@st.cache_data(ttl=1800)
def load_local_csv(path):
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame()

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
                "label": f'{away_info["abbr"]} @ {home_info["abbr"]} - {game_time_ct.strftime("%I:%M %p CT")}',
                "game_time_ct": game_time_ct.strftime("%Y-%m-%d %I:%M %p CT"),
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
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,precipitation_probability",
        "forecast_days": 7,
        "timezone": "UTC"
    }
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

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
            "team": norm_team(fallback_team),
            "position": pdata.get("position", {}).get("abbreviation", ""),
            "bat_side": pdata.get("batSide", {}).get("code", ""),
            "pitch_hand": pdata.get("pitchHand", {}).get("code", ""),
            "lineup_spot": lineup_spot
        })
    return pd.DataFrame(rows)

def lookup_pitch_hand(roster_df, pitcher_name):
    if roster_df.empty or not pitcher_name or pitcher_name == "TBD":
        return ""
    exact = roster_df[roster_df["name_key"] == clean_name(pitcher_name)]
    if not exact.empty:
        return exact.iloc[0]["pitch_hand"]
    return ""

def build_game_context(game_row):
    weather = get_weather(game_row["lat"], game_row["lon"], game_row["game_time_utc"])
    box = get_boxscore(game_row["game_pk"])

    away_roster = roster_df_from_box(box.get("teams", {}).get("away", {}), game_row["away_abbr"])
    home_roster = roster_df_from_box(box.get("teams", {}).get("home", {}), game_row["home_abbr"])

    away_lineup = away_roster[(away_roster["lineup_spot"].notna()) & (away_roster["position"] != "P")].copy().sort_values("lineup_spot")
    home_lineup = home_roster[(home_roster["lineup_spot"].notna()) & (home_roster["position"] != "P")].copy().sort_values("lineup_spot")

    home_pitch_hand = lookup_pitch_hand(home_roster, game_row["home_probable"])
    away_pitch_hand = lookup_pitch_hand(away_roster, game_row["away_probable"])

    away_lineup["opposing_pitcher"] = game_row["home_probable"]
    away_lineup["opposing_pitch_hand"] = home_pitch_hand
    away_lineup["lineup_status"] = "Confirmed" if len(away_lineup) >= 9 else "Not Confirmed"

    home_lineup["opposing_pitcher"] = game_row["away_probable"]
    home_lineup["opposing_pitch_hand"] = away_pitch_hand
    home_lineup["lineup_status"] = "Confirmed" if len(home_lineup) >= 9 else "Not Confirmed"

    context = {
        "weather": weather,
        "away_lineup": away_lineup,
        "home_lineup": home_lineup,
        "away_status": "Confirmed" if len(away_lineup) >= 9 else "Not Confirmed",
        "home_status": "Confirmed" if len(home_lineup) >= 9 else "Not Confirmed",
        "home_pitch_hand": home_pitch_hand,
        "away_pitch_hand": away_pitch_hand
    }
    return context

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
    b = str(bat_side).upper()
    p = str(pitch_hand).upper()
    if b == "S" and p:
        return f"SH vs {p}HP"
    if b and p:
        return f"{b}HB vs {p}HP"
    return "Unknown"

def platoon_value(bat_side, pitch_hand):
    b = str(bat_side).upper()
    p = str(pitch_hand).upper()
    if b == "S" and p in ["L", "R"]:
        return 1.0
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

def build_team_table(lineup_df, batters_df, pitchers_df, pitcher_name, weather, park_factor):
    pitch_row = find_pitcher_row(pitchers_df, pitcher_name)
    out_rows = []

    for _, row in lineup_df.iterrows():
        batter_row = find_player_row(batters_df, row["name_key"], row["team"])
        iso = safe_float(batter_row.get("ISO") if batter_row is not None else None, 0.170)
        xslg = safe_float(batter_row.get("xSLG") if batter_row is not None else None, 0.420)
        barrel = safe_float(batter_row.get("Barrel%") if batter_row is not None else None, 8.0)
        hardhit = safe_float(batter_row.get("HardHit%") if batter_row is not None else None, 38.0)
        hr = safe_float(batter_row.get("HR") if batter_row is not None else None, 0)

        score = matchup_score(
            batter_row,
            pitch_row,
            row["lineup_spot"],
            weather,
            park_factor,
            row["bat_side"],
            row["opposing_pitch_hand"]
        )

        out_rows.append({
            "Spot": int(row["lineup_spot"]) if pd.notna(row["lineup_spot"]) else None,
            "Player": row["player_name"],
            "Pos": row["position"],
            "Bat": row["bat_side"],
            "Split": handedness_label(row["bat_side"], row["opposing_pitch_hand"]),
            "Edge": "Good" if platoon_value(row["bat_side"], row["opposing_pitch_hand"]) > 0 else "Bad",
            "HR": hr,
            "ISO": iso,
            "xSLG": xslg,
            "Barrel%": barrel,
            "HardHit%": hardhit,
            "Score": score
        })

    return pd.DataFrame(out_rows).sort_values("Spot")

def color_metric(value, low, high):
    try:
        v = float(value)
    except:
        return ""
    if v >= high:
        return "background-color: rgba(22,163,74,0.28); color: #dcfce7; font-weight: 700;"
    if v >= low:
        return "background-color: rgba(234,179,8,0.22); color: #fef3c7; font-weight: 700;"
    return "background-color: rgba(220,38,38,0.22); color: #fee2e2; font-weight: 700;"

def color_edge(value):
    if value == "Good":
        return "background-color: rgba(22,163,74,0.28); color: #dcfce7; font-weight: 700;"
    return "background-color: rgba(220,38,38,0.22); color: #fee2e2; font-weight: 700;"

def style_table(df):
    if df.empty:
        return df
    styler = (
        df.style
        .format({
            "ISO": "{:.3f}",
            "xSLG": "{:.3f}",
            "Barrel%": "{:.1f}",
            "HardHit%": "{:.1f}",
            "Score": "{:.1f}",
            "HR": "{:.0f}",
        })
        .applymap(lambda x: color_metric(x, 0.170, 0.220), subset=["ISO"])
        .applymap(lambda x: color_metric(x, 0.420, 0.500), subset=["xSLG"])
        .applymap(lambda x: color_metric(x, 8.0, 12.0), subset=["Barrel%"])
        .applymap(lambda x: color_metric(x, 38.0, 45.0), subset=["HardHit%"])
        .applymap(lambda x: color_metric(x, 70, 85), subset=["Score"])
        .applymap(color_edge, subset=["Edge"])
    )
    return styler

def top_targets(df):
    if df.empty:
        return []
    return df.sort_values("Score", ascending=False).head(3)[["Player", "Score"]].values.tolist()

def pill_class(val, good_when="Confirmed"):
    if val == good_when:
        return "pill-good"
    if val in ["Neutral", "Watch"]:
        return "pill-neutral"
    return "pill-bad"

# -----------------------------
# LOAD DATA
# -----------------------------
batters_df = standardize_columns(load_local_csv("data/savant_batters.csv"))
pitchers_df = standardize_columns(load_local_csv("data/savant_pitchers.csv"))

# -----------------------------
# HEADER
# -----------------------------
st.markdown('<div class="main-title">MLB Matchup Board</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Pick one game at the top, then scroll down for a clean vertical breakdown.</div>', unsafe_allow_html=True)

selected_date = st.date_input("Slate date", value=date.today())

schedule_df = get_schedule(selected_date)

if schedule_df.empty:
    st.warning("No games found for this date.")
    st.stop()

game_label = st.selectbox("Choose game", schedule_df["label"].tolist(), index=0)
game_row = schedule_df[schedule_df["label"] == game_label].iloc[0]

context = build_game_context(game_row)
weather = context["weather"]

away_table = build_team_table(
    context["away_lineup"],
    batters_df,
    pitchers_df,
    game_row["home_probable"],
    weather,
    game_row["park_factor"]
)
home_table = build_team_table(
    context["home_lineup"],
    batters_df,
    pitchers_df,
    game_row["away_probable"],
    weather,
    game_row["park_factor"]
)

# -----------------------------
# TOP MATCHUP CARD
# -----------------------------
st.markdown(f"""
<div class="top-card">
    <div class="section-label">Selected Game</div>
    <div class="big-matchup">{game_row["away_abbr"]} @ {game_row["home_abbr"]}</div>
    <div class="mid-text">{game_row["game_time_ct"]} · {game_row["venue"]} · Status: {game_row["status"]}</div>
    <div class="pill-row">
        <div class="{pill_class(context["away_status"])}">{game_row["away_abbr"]} Lineup: {context["away_status"]}</div>
        <div class="{pill_class(context["home_status"])}">{game_row["home_abbr"]} Lineup: {context["home_status"]}</div>
        <div class="pill-neutral">Park Factor: {game_row["park_factor"]}</div>
        <div class="pill-neutral">Temp: {weather.get("temp_f") if weather.get("temp_f") is not None else "N/A"} F</div>
        <div class="pill-neutral">Wind: {weather.get("wind_mph") if weather.get("wind_mph") is not None else "N/A"} mph</div>
    </div>
</div>
""", unsafe_allow_html=True)

# -----------------------------
# CONDITIONS
# -----------------------------
st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-label">Game Notes</div>', unsafe_allow_html=True)

for label, value in [
    ("Away Probable", game_row["away_probable"]),
    ("Home Probable", game_row["home_probable"]),
    ("Away Pitch Hand", context["away_pitch_hand"] if context["away_pitch_hand"] else "N/A"),
    ("Home Pitch Hand", context["home_pitch_hand"] if context["home_pitch_hand"] else "N/A"),
    ("Rain %", weather.get("rain_pct") if weather.get("rain_pct") is not None else "N/A"),
]:
    st.markdown(f"""
    <div class="metric-box">
        <div class="metric-k">{label}</div>
        <div class="metric-v">{value}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# TOP TARGETS
# -----------------------------
st.markdown('<div class="section-card">', unsafe_allow_html=True)
st.markdown('<div class="section-label">Top Targets</div>', unsafe_allow_html=True)

away_targets = top_targets(away_table)
home_targets = top_targets(home_table)

st.markdown(f"""
<div class="metric-box">
    <div class="metric-k">{game_row["away_abbr"]} best bats vs {game_row["home_probable"]}</div>
    <div class="metric-v">{", ".join([f"{p} ({round(s,1)})" for p, s in away_targets]) if away_targets else "No lineup yet"}</div>
</div>
<div class="metric-box">
    <div class="metric-k">{game_row["home_abbr"]} best bats vs {game_row["away_probable"]}</div>
    <div class="metric-v">{", ".join([f"{p} ({round(s,1)})" for p, s in home_targets]) if home_targets else "No lineup yet"}</div>
</div>
""", unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------
# AWAY TABLE
# -----------------------------
st.markdown(f'<div class="section-card"><div class="section-label">{game_row["away_abbr"]} lineup breakdown</div></div>', unsafe_allow_html=True)
if away_table.empty:
    st.warning(f"{game_row['away_abbr']} lineup not posted yet.")
else:
    st.dataframe(style_table(away_table), use_container_width=True, hide_index=True)

# -----------------------------
# HOME TABLE
# -----------------------------
st.markdown(f'<div class="section-card"><div class="section-label">{game_row["home_abbr"]} lineup breakdown</div></div>', unsafe_allow_html=True)
if home_table.empty:
    st.warning(f"{game_row['home_abbr']} lineup not posted yet.")
else:
    st.dataframe(style_table(home_table), use_container_width=True, hide_index=True)

# -----------------------------
# DATA CHECK
# -----------------------------
with st.expander("CSV status"):
    st.write("Batters loaded:", len(batters_df))
    st.write("Pitchers loaded:", len(pitchers_df))
    st.write("Expected files:")
    st.code("data/savant_batters.csv\ndata/savant_pitchers.csv")
