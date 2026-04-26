import streamlit as st
import pandas as pd
import requests
import io
import os
import re
import unicodedata
from datetime import date

st.set_page_config(page_title="MLB Savant App", layout="wide")
st.title("MLB Savant App")
st.caption("Slate, confirmed lineups, handedness, HR model, park and weather")

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
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lower_map = {c.lower(): c for c in df.columns}

    if "player_name" in lower_map:
        df.rename(columns={lower_map["player_name"]: "Name"}, inplace=True)
    elif "name" in lower_map:
        df.rename(columns={lower_map["name"]: "Name"}, inplace=True)
    elif "player" in lower_map:
        df.rename(columns={lower_map["player"]: "Name"}, inplace=True)
    elif "first_name" in lower_map and "last_name" in lower_map:
        df["Name"] = df[lower_map["first_name"]].astype(str) + " " + df[lower_map["last_name"]].astype(str)

    for team_col in ["team", "team_abbr", "teamname", "team_name"]:
        if team_col in lower_map:
            df.rename(columns={lower_map[team_col]: "Team"}, inplace=True)
            break

    rename_candidates = {
        "bats": "bat_side",
        "stand": "bat_side",
        "throws": "pitch_hand",
        "p_throws": "pitch_hand",
        "pa": "PA",
        "hr": "HR",
        "slg": "SLG",
        "xslg": "xSLG",
        "iso": "ISO",
        "woba": "wOBA",
        "xwoba": "xwOBA",
        "barrel_batted_rate": "Barrel%",
        "barrel%": "Barrel%",
        "barrels_per_bbe_percent": "Barrel%",
        "hard_hit_percent": "HardHit%",
        "hardhit%": "HardHit%",
        "hard_hit_rate": "HardHit%",
        "exit_velocity_avg": "EV",
        "avg_hit_speed": "EV",
        "launch_angle_avg": "LA",
        "launch_angle": "LA",
        "k_percent": "K%",
        "bb_percent": "BB%",
        "whiff_percent": "Whiff%",
        "sweet_spot_percent": "SweetSpot%",
        "xba": "xBA",
        "obp": "OBP",
        "ops": "OPS",
        "avg": "AVG",
        "iso_value": "ISO",
        "barrels": "Barrels",
        "hard_hit": "HardHit"
    }

    for raw, final in rename_candidates.items():
        if raw in lower_map and final not in df.columns:
            df.rename(columns={lower_map[raw]: final}, inplace=True)

    if "Team" in df.columns:
        df["team_key"] = df["Team"].apply(norm_team)
    else:
        df["Team"] = ""
        df["team_key"] = ""

    if "Name" in df.columns:
        df["name_key"] = df["Name"].apply(clean_name)
    else:
        df["Name"] = ""
        df["name_key"] = ""

    return df

@st.cache_data(ttl=1800)
def load_csv_source(url_text, local_path):
    if url_text:
        r = requests.get(url_text, headers=HEADERS, timeout=60)
        r.raise_for_status()
        return pd.read_csv(io.StringIO(r.text))
    if os.path.exists(local_path):
        return pd.read_csv(local_path)
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

def add_weather(schedule_df):
    if schedule_df.empty:
        return schedule_df
    weather_rows = []
    for _, row in schedule_df.iterrows():
        weather_rows.append(get_weather(row["lat"], row["lon"], row["game_time_utc"]))
    return pd.concat([schedule_df.reset_index(drop=True), pd.DataFrame(weather_rows)], axis=1)

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
            "player_id": person.get("id"),
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

def build_lineups(schedule_df):
    all_rows = []
    game_rows = []

    for _, game in schedule_df.iterrows():
        try:
            box = get_boxscore(game["game_pk"])
            away_roster = roster_df_from_box(box.get("teams", {}).get("away", {}), game["away_abbr"])
            home_roster = roster_df_from_box(box.get("teams", {}).get("home", {}), game["home_abbr"])
        except:
            away_roster = pd.DataFrame()
            home_roster = pd.DataFrame()

        home_pitch_hand = lookup_pitch_hand(home_roster, game["home_probable"])
        away_pitch_hand = lookup_pitch_hand(away_roster, game["away_probable"])

        away_lineup = away_roster[(away_roster["lineup_spot"].notna()) & (away_roster["position"] != "P")].copy()
        home_lineup = home_roster[(home_roster["lineup_spot"].notna()) & (home_roster["position"] != "P")].copy()

        away_confirmed = len(away_lineup) >= 9
        home_confirmed = len(home_lineup) >= 9

        if not away_lineup.empty:
            away_lineup["game"] = f'{game["away_abbr"]} @ {game["home_abbr"]}'
            away_lineup["opponent"] = game["home_abbr"]
            away_lineup["opposing_pitcher"] = game["home_probable"]
            away_lineup["opposing_pitch_hand"] = home_pitch_hand
            away_lineup["venue"] = game["venue"]
            away_lineup["temp_f"] = game.get("temp_f")
            away_lineup["wind_mph"] = game.get("wind_mph")
            away_lineup["rain_pct"] = game.get("rain_pct")
            away_lineup["park_factor"] = game.get("park_factor", 100)
            away_lineup["lineup_status"] = "Confirmed" if away_confirmed else "Not Confirmed"
            all_rows.append(away_lineup.sort_values("lineup_spot"))

        if not home_lineup.empty:
            home_lineup["game"] = f'{game["away_abbr"]} @ {game["home_abbr"]}'
            home_lineup["opponent"] = game["away_abbr"]
            home_lineup["opposing_pitcher"] = game["away_probable"]
            home_lineup["opposing_pitch_hand"] = away_pitch_hand
            home_lineup["venue"] = game["venue"]
            home_lineup["temp_f"] = game.get("temp_f")
            home_lineup["wind_mph"] = game.get("wind_mph")
            home_lineup["rain_pct"] = game.get("rain_pct")
            home_lineup["park_factor"] = game.get("park_factor", 100)
            home_lineup["lineup_status"] = "Confirmed" if home_confirmed else "Not Confirmed"
            all_rows.append(home_lineup.sort_values("lineup_spot"))

        g = game.copy()
        g["away_lineup_status"] = "Confirmed" if away_confirmed else "Not Confirmed"
        g["home_lineup_status"] = "Confirmed" if home_confirmed else "Not Confirmed"
        g["home_pitch_hand"] = home_pitch_hand
        g["away_pitch_hand"] = away_pitch_hand
        game_rows.append(g)

    lineup_df = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    games_df = pd.DataFrame(game_rows)
    return lineup_df, games_df

def handedness_label(bat_side, pitch_hand):
    bat_side = str(bat_side).upper()
    pitch_hand = str(pitch_hand).upper()
    if bat_side == "S" and pitch_hand:
        return f"SH vs {pitch_hand}HP"
    if bat_side and pitch_hand:
        return f"{bat_side}HB vs {pitch_hand}HP"
    return "Unknown"

def platoon_value(bat_side, pitch_hand):
    bat_side = str(bat_side).upper()
    pitch_hand = str(pitch_hand).upper()
    if bat_side == "S" and pitch_hand in ["L", "R"]:
        return 1.0
    if bat_side in ["L", "R"] and pitch_hand in ["L", "R"]:
        return 0.8 if bat_side != pitch_hand else -0.35
    return 0.0

def platoon_text(bat_side, pitch_hand):
    v = platoon_value(bat_side, pitch_hand)
    if v >= 0.8:
        return "Platoon Edge"
    if v > 0:
        return "Switch Edge"
    if v < 0:
        return "Same-Side"
    return "Neutral"

def merge_hitters_with_savant(lineup_df, batters_df, pitchers_df):
    if lineup_df.empty:
        return lineup_df

    batters = batters_df.copy()
    pitchers = pitchers_df.copy()

    merged = pd.merge(
        lineup_df,
        batters,
        left_on=["name_key", "team"],
        right_on=["name_key", "team_key"],
        how="left",
        suffixes=("", "_bat")
    )

    if not pitchers.empty:
        pitchers = pitchers.rename(columns={"Name": "pitcher_name", "Team": "pitcher_team"})
        merged["opp_pitcher_key"] = merged["opposing_pitcher"].apply(clean_name)
        merged = pd.merge(
            merged,
            pitchers,
            left_on="opp_pitcher_key",
            right_on="name_key",
            how="left",
            suffixes=("", "_pit")
        )

    merged["handedness_split"] = merged.apply(lambda r: handedness_label(r.get("bat_side"), r.get("opposing_pitch_hand")), axis=1)
    merged["split_edge"] = merged.apply(lambda r: platoon_text(r.get("bat_side"), r.get("opposing_pitch_hand")), axis=1)
    return merged

def calc_hr_score(row):
    hr = safe_float(row.get("HR"), 10)
    iso = safe_float(row.get("ISO"), 0.170)
    slg = safe_float(row.get("SLG"), 0.400)
    xslg = safe_float(row.get("xSLG"), slg)
    barrel = safe_float(row.get("Barrel%"), 8.0)
    hardhit = safe_float(row.get("HardHit%"), 38.0)
    ev = safe_float(row.get("EV"), 89.0)

    p_xslg = safe_float(row.get("xSLG_pit"), 0.420)
    p_barrel = safe_float(row.get("Barrel%_pit"), 8.0)
    p_hardhit = safe_float(row.get("HardHit%_pit"), 38.0)
    p_k = safe_float(row.get("K%_pit"), 22.0)
    p_bb = safe_float(row.get("BB%_pit"), 8.0)

    temp_f = safe_float(row.get("temp_f"), 72)
    wind_mph = safe_float(row.get("wind_mph"), 8)
    rain_pct = safe_float(row.get("rain_pct"), 0)
    park_factor = safe_float(row.get("park_factor"), 100)
    lineup_spot = safe_float(row.get("lineup_spot"), 9)

    split_boost = platoon_value(row.get("bat_side"), row.get("opposing_pitch_hand")) * 7
    weather_bonus = max(0, temp_f - 68) * 0.28 + max(0, wind_mph - 7) * 0.18 - rain_pct * 0.03
    park_bonus = (park_factor - 100) * 0.50
    slot_bonus = max(0, 10 - lineup_spot) * 1.10
    confirm_bonus = 4 if row.get("lineup_status") == "Confirmed" else 0

    score = (
        hr * 0.8
        + iso * 160
        + slg * 22
        + xslg * 26
        + barrel * 2.1
        + hardhit * 0.65
        + (ev - 85) * 1.1
        + p_xslg * 24
        + p_barrel * 1.8
        + p_hardhit * 0.40
        - p_k * 0.45
        + p_bb * 0.35
        + split_boost
        + weather_bonus
        + park_bonus
        + slot_bonus
        + confirm_bonus
    )
    return round(score, 2)

def add_hr_columns(df):
    if df.empty:
        return df
    out = df.copy()
    out["hr_matchup_score"] = out.apply(calc_hr_score, axis=1)

    def tier(v):
        if v >= 95:
            return "Elite"
        if v >= 82:
            return "Strong"
        if v >= 70:
            return "Good"
        return "Secondary"

    out["hr_tier"] = out["hr_matchup_score"].apply(tier)
    return out

def build_team_stacks(df):
    if df.empty:
        return pd.DataFrame()
    out = (
        df.sort_values(["game", "team", "hr_matchup_score"], ascending=[True, True, False])
          .groupby(["game", "team", "opponent", "venue"], as_index=False)
          .agg(
              avg_hr_score=("hr_matchup_score", "mean"),
              top5_hr_score=("hr_matchup_score", lambda s: round(s.nlargest(5).sum(), 2)),
              top_targets=("player_name", lambda s: ", ".join(list(s.head(3))))
          )
          .sort_values("top5_hr_score", ascending=False)
    )
    return out

with st.sidebar:
    st.header("Controls")
    selected_date = st.date_input("Slate date", value=date.today())

    st.markdown("### Savant CSV source")
    batter_csv_url = st.text_input("Optional Savant batter CSV URL", "")
    pitcher_csv_url = st.text_input("Optional Savant pitcher CSV URL", "")
    st.caption("If blank, app will use local files: data/savant_batters.csv and data/savant_pitchers.csv")

try:
    schedule_df = add_weather(get_schedule(selected_date))
except Exception as e:
    st.error(f"Schedule load failed: {e}")
    schedule_df = pd.DataFrame()

try:
    raw_batters = load_csv_source(batter_csv_url, "data/savant_batters.csv")
    batters_df = standardize_columns(raw_batters)
except Exception as e:
    st.error(f"Savant batter load failed: {e}")
    batters_df = pd.DataFrame()

try:
    raw_pitchers = load_csv_source(pitcher_csv_url, "data/savant_pitchers.csv")
    pitchers_df = standardize_columns(raw_pitchers)
except Exception as e:
    st.error(f"Savant pitcher load failed: {e}")
    pitchers_df = pd.DataFrame()

lineup_df, games_df = build_lineups(schedule_df) if not schedule_df.empty else (pd.DataFrame(), pd.DataFrame())
model_df = merge_hitters_with_savant(lineup_df, batters_df, pitchers_df) if not lineup_df.empty else pd.DataFrame()
model_df = add_hr_columns(model_df) if not model_df.empty else pd.DataFrame()
stack_df = build_team_stacks(model_df) if not model_df.empty else pd.DataFrame()

tabs = st.tabs(["Slate", "Confirmed Lineups", "Savant Batters", "Savant Pitchers", "HR Matchups", "Park + Weather"])

with tabs[0]:
    st.subheader(f"MLB Slate - {selected_date}")
    if games_df.empty:
        st.warning("No games found.")
    else:
        cols = [
            "game_time_ct", "away_abbr", "home_abbr", "venue",
            "away_probable", "home_probable",
            "away_lineup_status", "home_lineup_status", "status"
        ]
        st.dataframe(games_df[cols], use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader("Confirmed Lineups + Handedness")
    if model_df.empty and lineup_df.empty:
        st.warning("No lineup data available yet.")
    else:
        view = model_df if not model_df.empty else lineup_df
        cols = [
            "game", "team", "lineup_spot", "player_name", "position", "bat_side",
            "opposing_pitcher", "opposing_pitch_hand", "handedness_split",
            "split_edge", "lineup_status"
        ]
        cols = [c for c in cols if c in view.columns]
        st.dataframe(view.sort_values(["game", "team", "lineup_spot"])[cols], use_container_width=True, hide_index=True)

with tabs[2]:
    st.subheader("Baseball Savant Batters")
    if batters_df.empty:
        st.warning("No Savant batter CSV loaded.")
    else:
        cols = ["Name", "Team", "PA", "HR", "AVG", "OBP", "SLG", "OPS", "ISO", "xSLG", "wOBA", "xwOBA", "Barrel%", "HardHit%", "EV", "LA"]
        cols = [c for c in cols if c in batters_df.columns]
        st.dataframe(batters_df[cols], use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader("Baseball Savant Pitchers")
    if pitchers_df.empty:
        st.warning("No Savant pitcher CSV loaded.")
    else:
        cols = ["Name", "Team", "pitch_hand", "K%", "BB%", "xSLG", "xwOBA", "Barrel%", "HardHit%", "EV"]
        cols = [c for c in cols if c in pitchers_df.columns]
        st.dataframe(pitchers_df[cols], use_container_width=True, hide_index=True)

with tabs[4]:
    st.subheader("HR Matchups")
    if model_df.empty:
        st.warning("Need lineups plus Savant batter/pitcher CSVs.")
    else:
        hitter_cols = [
            "game", "team", "lineup_spot", "player_name", "opposing_pitcher",
            "bat_side", "opposing_pitch_hand", "handedness_split", "split_edge",
            "HR", "ISO", "SLG", "xSLG", "Barrel%", "HardHit%",
            "xSLG_pit", "Barrel%_pit", "HardHit%_pit", "K%_pit",
            "temp_f", "wind_mph", "park_factor", "hr_matchup_score", "hr_tier", "lineup_status"
        ]
        hitter_cols = [c for c in hitter_cols if c in model_df.columns]
        st.dataframe(model_df.sort_values("hr_matchup_score", ascending=False)[hitter_cols], use_container_width=True, hide_index=True)

        st.markdown("### Team stack ranks")
        if not stack_df.empty:
            st.dataframe(stack_df, use_container_width=True, hide_index=True)

with tabs[5]:
    st.subheader("Park + Weather")
    if games_df.empty:
        st.warning("No park/weather data.")
    else:
        cols = [
            "away_abbr", "home_abbr", "venue", "park_factor",
            "temp_f", "wind_mph", "rain_pct",
            "away_lineup_status", "home_lineup_status"
        ]
        st.dataframe(games_df[cols], use_container_width=True, hide_index=True)
