import streamlit as st
import pandas as pd
import requests
import re
import unicodedata
from datetime import date, timedelta
from difflib import get_close_matches
from pybaseball import batting_stats, batting_stats_range, pitching_stats

st.set_page_config(page_title="MLB Data App v2", layout="wide")

st.title("MLB Data App v2")
st.caption("Daily slate, confirmed lineups, handedness view, HR model, weather")

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
    "CHW": "CWS",
    "KCR": "KC",
    "SDP": "SD",
    "SFG": "SF",
    "TBR": "TB",
    "WSN": "WSH",
    "OAK": "ATH",
}

DEFAULT_PARK_FACTORS = {
    "ARI": 100, "ATL": 100, "BAL": 100, "BOS": 100, "CHC": 100, "CWS": 100,
    "CIN": 100, "CLE": 100, "COL": 100, "DET": 100, "HOU": 100, "KC": 100,
    "LAA": 100, "LAD": 100, "MIA": 100, "MIL": 100, "MIN": 100, "NYM": 100,
    "NYY": 100, "ATH": 100, "PHI": 100, "PIT": 100, "SD": 100, "SF": 100,
    "SEA": 100, "STL": 100, "TB": 100, "TEX": 100, "TOR": 100, "WSH": 100
}

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
    team = str(team).strip().upper()
    return TEAM_FIXES.get(team, team)

def safe_float(val, default=0.0):
    try:
        if pd.isna(val):
            return default
        return float(val)
    except:
        return default

def pick_team_col(df):
    if "Team" in df.columns:
        return "Team"
    if "Tm" in df.columns:
        return "Tm"
    return None

def fuzzy_find_name(df, target_name):
    if df.empty or not target_name:
        return None

    target_key = clean_name(target_name)
    exact = df[df["name_key"] == target_key]
    if not exact.empty:
        return exact.iloc[0]

    last_name = target_key.split(" ")[-1] if target_key else ""
    if last_name:
        contains = df[df["name_key"].str.contains(last_name, na=False)]
        if not contains.empty:
            return contains.iloc[0]

    choices = df["name_key"].dropna().tolist()
    match = get_close_matches(target_key, choices, n=1, cutoff=0.85)
    if match:
        return df[df["name_key"] == match[0]].iloc[0]

    return None

@st.cache_data(ttl=1800)
def get_schedule(selected_date):
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date": selected_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher,team"
    }
    r = requests.get(url, params=params, timeout=30)
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

            home_abbr = home_info["abbr"]
            rows.append({
                "game_pk": game.get("gamePk"),
                "game_time_ct": game_time_ct.strftime("%Y-%m-%d %I:%M %p CT"),
                "game_time_utc": game_time_utc,
                "status": game.get("status", {}).get("detailedState"),
                "away_team": away_name,
                "away_abbr": away_info["abbr"],
                "home_team": home_name,
                "home_abbr": home_abbr,
                "venue": game.get("venue", {}).get("name", "Unknown"),
                "away_probable": game.get("teams", {}).get("away", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "home_probable": game.get("teams", {}).get("home", {}).get("probablePitcher", {}).get("fullName", "TBD"),
                "lat": home_info["lat"],
                "lon": home_info["lon"],
                "park_factor": DEFAULT_PARK_FACTORS.get(home_abbr, 100),
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
    r = requests.get(url, params=params, timeout=30)
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

    weather_df = pd.DataFrame(weather_rows)
    return pd.concat([schedule_df.reset_index(drop=True), weather_df.reset_index(drop=True)], axis=1)

@st.cache_data(ttl=21600)
def get_batters_season(season):
    df = batting_stats(season, qual=1)
    team_col = pick_team_col(df)
    wanted = ["Name", team_col, "PA", "AVG", "OBP", "SLG", "OPS", "ISO", "HR", "wOBA", "wRC+", "BB%", "K%"]
    wanted = [c for c in wanted if c and c in df.columns]
    out = df[wanted].copy()
    if team_col and team_col in out.columns:
        out.rename(columns={team_col: "Team"}, inplace=True)
    out["name_key"] = out["Name"].apply(clean_name)
    if "Team" in out.columns:
        out["team_key"] = out["Team"].apply(norm_team)
    else:
        out["team_key"] = ""
    return out

@st.cache_data(ttl=21600)
def get_batters_recent(end_date):
    start_date = end_date - timedelta(days=14)
    df = batting_stats_range(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
    team_col = pick_team_col(df)
    wanted = ["Name", team_col, "PA", "AVG", "OBP", "SLG", "OPS", "ISO", "HR", "wOBA", "wRC+"]
    wanted = [c for c in wanted if c and c in df.columns]
    out = df[wanted].copy()
    if team_col and team_col in out.columns:
        out.rename(columns={team_col: "Team"}, inplace=True)
    out["name_key"] = out["Name"].apply(clean_name)
    if "Team" in out.columns:
        out["team_key"] = out["Team"].apply(norm_team)
    else:
        out["team_key"] = ""
    return out

@st.cache_data(ttl=21600)
def get_pitchers_season(season):
    df = pitching_stats(season, qual=1)
    team_col = pick_team_col(df)
    wanted = ["Name", team_col, "ERA", "WHIP", "IP", "SO", "BB", "K/9", "BB/9", "HR/9", "FIP", "xFIP"]
    wanted = [c for c in wanted if c and c in df.columns]
    out = df[wanted].copy()
    if team_col and team_col in out.columns:
        out.rename(columns={team_col: "Team"}, inplace=True)
    out["name_key"] = out["Name"].apply(clean_name)
    if "Team" in out.columns:
        out["team_key"] = out["Team"].apply(norm_team)
    else:
        out["team_key"] = ""
    return out

def prep_batter_model(season_df, recent_df):
    season_keep = ["name_key", "team_key", "Name", "Team", "PA", "AVG", "OBP", "SLG", "OPS", "ISO", "HR", "wOBA", "wRC+", "BB%", "K%"]
    season_keep = [c for c in season_keep if c in season_df.columns]
    season_df = season_df[season_keep].copy()
    season_rename = {c: f"{c}_season" for c in season_df.columns if c not in ["name_key", "team_key", "Name", "Team"]}
    season_df.rename(columns=season_rename, inplace=True)

    recent_keep = ["name_key", "team_key", "PA", "AVG", "OBP", "SLG", "OPS", "ISO", "HR", "wOBA", "wRC+"]
    recent_keep = [c for c in recent_keep if c in recent_df.columns]
    recent_df = recent_df[recent_keep].copy()
    recent_rename = {c: f"{c}_14d" for c in recent_df.columns if c not in ["name_key", "team_key"]}
    recent_df.rename(columns=recent_rename, inplace=True)

    merged = pd.merge(season_df, recent_df, on=["name_key", "team_key"], how="left")
    return merged

@st.cache_data(ttl=1800)
def get_boxscore(game_pk):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def roster_df_from_team_box(team_box, fallback_team_abbr):
    rows = []
    for pdata in team_box.get("players", {}).values():
        person = pdata.get("person", {})
        full_name = person.get("fullName", "")
        lineup_raw = str(pdata.get("battingOrder", "")).strip()
        lineup_spot = int(lineup_raw[0]) if lineup_raw[:1].isdigit() else None

        rows.append({
            "player_id": person.get("id"),
            "player_name": full_name,
            "name_key": clean_name(full_name),
            "team_abbr": norm_team(fallback_team_abbr),
            "position": pdata.get("position", {}).get("abbreviation", ""),
            "bat_side": pdata.get("batSide", {}).get("code", ""),
            "pitch_hand": pdata.get("pitchHand", {}).get("code", ""),
            "lineup_raw": lineup_raw,
            "lineup_spot": lineup_spot,
        })
    return pd.DataFrame(rows)

def lookup_pitch_hand(roster_df, pitcher_name):
    if roster_df.empty or not pitcher_name or pitcher_name == "TBD":
        return ""

    exact = roster_df[roster_df["name_key"] == clean_name(pitcher_name)]
    if not exact.empty:
        return exact.iloc[0]["pitch_hand"]

    last_name = clean_name(pitcher_name).split(" ")[-1]
    contains = roster_df[roster_df["name_key"].str.contains(last_name, na=False)]
    if not contains.empty:
        return contains.iloc[0]["pitch_hand"]

    return ""

def lineup_df_from_roster(roster_df, game_label, team_abbr, opponent_abbr, opponent_pitcher, opponent_pitch_hand):
    if roster_df.empty:
        return pd.DataFrame()

    lineup_df = roster_df[(roster_df["lineup_spot"].notna()) & (roster_df["position"] != "P")].copy()
    lineup_df = lineup_df.sort_values("lineup_spot")
    confirmed = len(lineup_df) >= 9

    if lineup_df.empty:
        return lineup_df

    lineup_df["game"] = game_label
    lineup_df["team"] = team_abbr
    lineup_df["opponent"] = opponent_abbr
    lineup_df["opposing_pitcher"] = opponent_pitcher
    lineup_df["opposing_pitch_hand"] = opponent_pitch_hand
    lineup_df["lineup_confirmed"] = confirmed
    lineup_df["lineup_status"] = "Confirmed" if confirmed else "Not Confirmed"
    return lineup_df

def build_lineups_and_context(schedule_df):
    if schedule_df.empty:
        return pd.DataFrame(), schedule_df

    lineup_rows = []
    updated_games = []

    for _, game in schedule_df.iterrows():
        try:
            box = get_boxscore(game["game_pk"])
            home_roster = roster_df_from_team_box(box.get("teams", {}).get("home", {}), game["home_abbr"])
            away_roster = roster_df_from_team_box(box.get("teams", {}).get("away", {}), game["away_abbr"])
        except Exception:
            home_roster = pd.DataFrame()
            away_roster = pd.DataFrame()

        home_pitch_hand = lookup_pitch_hand(home_roster, game["home_probable"])
        away_pitch_hand = lookup_pitch_hand(away_roster, game["away_probable"])

        game_label = f'{game["away_abbr"]} @ {game["home_abbr"]}'

        away_lineup = lineup_df_from_roster(
            away_roster,
            game_label,
            game["away_abbr"],
            game["home_abbr"],
            game["home_probable"],
            home_pitch_hand
        )
        home_lineup = lineup_df_from_roster(
            home_roster,
            game_label,
            game["home_abbr"],
            game["away_abbr"],
            game["away_probable"],
            away_pitch_hand
        )

        if not away_lineup.empty:
            away_lineup["game_pk"] = game["game_pk"]
            away_lineup["temp_f"] = game.get("temp_f")
            away_lineup["wind_mph"] = game.get("wind_mph")
            away_lineup["rain_pct"] = game.get("rain_pct")
            away_lineup["park_factor"] = game.get("park_factor", 100)
            away_lineup["venue"] = game.get("venue")
            lineup_rows.append(away_lineup)

        if not home_lineup.empty:
            home_lineup["game_pk"] = game["game_pk"]
            home_lineup["temp_f"] = game.get("temp_f")
            home_lineup["wind_mph"] = game.get("wind_mph")
            home_lineup["rain_pct"] = game.get("rain_pct")
            home_lineup["park_factor"] = game.get("park_factor", 100)
            home_lineup["venue"] = game.get("venue")
            lineup_rows.append(home_lineup)

        game_copy = game.copy()
        game_copy["home_pitch_hand"] = home_pitch_hand
        game_copy["away_pitch_hand"] = away_pitch_hand
        game_copy["home_lineup_status"] = "Confirmed" if len(home_lineup) >= 9 else "Not Confirmed"
        game_copy["away_lineup_status"] = "Confirmed" if len(away_lineup) >= 9 else "Not Confirmed"
        updated_games.append(game_copy)

    lineup_df = pd.concat(lineup_rows, ignore_index=True) if lineup_rows else pd.DataFrame()
    context_df = pd.DataFrame(updated_games)
    return lineup_df, context_df

def merge_lineups_with_stats(lineup_df, batter_model_df, pitchers_df):
    if lineup_df.empty:
        return lineup_df

    hitters = lineup_df.copy()
    hitters["name_key"] = hitters["player_name"].apply(clean_name)
    hitters["team_key"] = hitters["team"].apply(norm_team)

    merged = pd.merge(
        hitters,
        batter_model_df,
        on=["name_key", "team_key"],
        how="left"
    )

    pitchers = pitchers_df.copy()
    pitchers = pitchers.rename(columns={
        "Name": "pitcher_name",
        "Team": "pitcher_team",
        "team_key": "pitcher_team_key"
    })

    merged["opp_pitcher_key"] = merged["opposing_pitcher"].apply(clean_name)
    pitchers["opp_pitcher_key"] = pitchers["name_key"]

    merged = pd.merge(
        merged,
        pitchers[["opp_pitcher_key", "pitcher_name", "pitcher_team", "ERA", "WHIP", "IP", "SO", "BB", "K/9", "BB/9", "HR/9", "FIP", "xFIP"]],
        on="opp_pitcher_key",
        how="left"
    )

    return merged

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
    val = platoon_value(bat_side, pitch_hand)
    if val >= 0.8:
        return "Platoon Edge"
    if val > 0:
        return "Switch Advantage"
    if val < 0:
        return "Same-Side"
    return "Neutral"

def calculate_hr_score(row):
    season_iso = safe_float(row.get("ISO_season"), 0.170)
    season_slg = safe_float(row.get("SLG_season"), 0.400)
    season_ops = safe_float(row.get("OPS_season"), 0.720)
    season_hr = safe_float(row.get("HR_season"), 10)
    season_wrc = safe_float(row.get("wRC+_season"), 100)

    recent_iso = safe_float(row.get("ISO_14d"), season_iso)
    recent_slg = safe_float(row.get("SLG_14d"), season_slg)
    recent_ops = safe_float(row.get("OPS_14d"), season_ops)
    recent_hr = safe_float(row.get("HR_14d"), 0)

    pitcher_hr9 = safe_float(row.get("HR/9"), 1.10)
    pitcher_fip = safe_float(row.get("FIP"), 4.00)
    pitcher_whip = safe_float(row.get("WHIP"), 1.30)
    pitcher_k9 = safe_float(row.get("K/9"), 8.50)

    temp_f = safe_float(row.get("temp_f"), 72)
    wind_mph = safe_float(row.get("wind_mph"), 8)
    rain_pct = safe_float(row.get("rain_pct"), 0)
    park_factor = safe_float(row.get("park_factor"), 100)
    lineup_spot = safe_float(row.get("lineup_spot"), 9)

    split_boost = platoon_value(row.get("bat_side"), row.get("opposing_pitch_hand")) * 8
    weather_bonus = max(0, temp_f - 68) * 0.30 + max(0, wind_mph - 7) * 0.20 - rain_pct * 0.03
    park_bonus = (park_factor - 100) * 0.45
    slot_bonus = max(0, 10 - lineup_spot) * 1.20
    confirm_bonus = 4 if bool(row.get("lineup_confirmed")) else 0

    score = (
        season_iso * 140
        + season_slg * 20
        + season_ops * 18
        + season_hr * 0.60
        + season_wrc * 0.12
        + recent_iso * 180
        + recent_slg * 15
        + recent_ops * 10
        + recent_hr * 1.80
        + pitcher_hr9 * 14
        + pitcher_fip * 2.2
        + pitcher_whip * 3.2
        - pitcher_k9 * 1.0
        + split_boost
        + weather_bonus
        + park_bonus
        + slot_bonus
        + confirm_bonus
    )
    return round(score, 2)

def add_hr_model_columns(df):
    if df.empty:
        return df

    out = df.copy()
    out["handedness_split"] = out.apply(lambda r: handedness_label(r.get("bat_side"), r.get("opposing_pitch_hand")), axis=1)
    out["split_edge"] = out.apply(lambda r: platoon_text(r.get("bat_side"), r.get("opposing_pitch_hand")), axis=1)
    out["hr_matchup_score"] = out.apply(calculate_hr_score, axis=1)

    def hr_tier(score):
        if score >= 90:
            return "Elite"
        if score >= 78:
            return "Strong"
        if score >= 66:
            return "Good"
        return "Secondary"

    out["hr_tier"] = out["hr_matchup_score"].apply(hr_tier)
    return out

def build_team_stack_table(hitters_df):
    if hitters_df.empty:
        return pd.DataFrame()

    stack = (
        hitters_df.sort_values(["team", "hr_matchup_score"], ascending=[True, False])
        .groupby(["game", "team", "opponent", "venue"], as_index=False)
        .agg(
            avg_hr_score=("hr_matchup_score", "mean"),
            top5_hr_score=("hr_matchup_score", lambda s: round(s.nlargest(5).sum(), 2)),
            confirmed_hitters=("lineup_confirmed", lambda s: int(sum(bool(x) for x in s))),
            top_targets=("player_name", lambda s: ", ".join(list(s.head(3))))
        )
        .sort_values("top5_hr_score", ascending=False)
    )
    return stack

with st.sidebar:
    st.header("Settings")
    selected_date = st.date_input("Slate date", value=date.today())
    selected_season = st.selectbox("Season", [2026, 2025, 2024, 2023], index=0)
    st.caption("Starter park factors are neutral 100 by default. Replace later with your own park model for a sharper HR edge.")

try:
    schedule_df = get_schedule(selected_date)
    schedule_df = add_weather(schedule_df)
except Exception as e:
    st.error(f"Schedule load failed: {e}")
    schedule_df = pd.DataFrame()

try:
    batters_season_df = get_batters_season(selected_season)
except Exception as e:
    st.error(f"Season batter load failed: {e}")
    batters_season_df = pd.DataFrame()

try:
    batters_recent_df = get_batters_recent(selected_date)
except Exception as e:
    st.error(f"14-day batter load failed: {e}")
    batters_recent_df = pd.DataFrame()

try:
    pitchers_df = get_pitchers_season(selected_season)
except Exception as e:
    st.error(f"Pitcher load failed: {e}")
    pitchers_df = pd.DataFrame()

batter_model_df = prep_batter_model(batters_season_df, batters_recent_df) if not batters_season_df.empty else pd.DataFrame()

lineup_df, context_df = build_lineups_and_context(schedule_df) if not schedule_df.empty else (pd.DataFrame(), pd.DataFrame())
hitters_model_df = merge_lineups_with_stats(lineup_df, batter_model_df, pitchers_df) if not lineup_df.empty and not batter_model_df.empty else pd.DataFrame()
hitters_model_df = add_hr_model_columns(hitters_model_df) if not hitters_model_df.empty else pd.DataFrame()
team_stack_df = build_team_stack_table(hitters_model_df) if not hitters_model_df.empty else pd.DataFrame()

tabs = st.tabs([
    "Slate",
    "Confirmed Lineups",
    "Batters",
    "Pitchers",
    "HR Matchups",
    "Park + Weather"
])

with tabs[0]:
    st.subheader(f"MLB Slate - {selected_date}")
    if context_df.empty:
        st.warning("No games found for this date.")
    else:
        show_cols = [
            "game_time_ct", "away_abbr", "home_abbr", "venue",
            "away_probable", "away_pitch_hand", "home_probable", "home_pitch_hand",
            "away_lineup_status", "home_lineup_status", "status"
        ]
        available = [c for c in show_cols if c in context_df.columns]
        st.dataframe(context_df[available], use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader("Confirmed Lineups + Handedness View")
    if lineup_df.empty:
        st.warning("No lineup rows found yet. MLB may not have posted lineups yet.")
    else:
        show = hitters_model_df.copy() if not hitters_model_df.empty else lineup_df.copy()
        cols = [
            "game", "team", "lineup_spot", "player_name", "position", "bat_side",
            "opposing_pitcher", "opposing_pitch_hand", "handedness_split",
            "split_edge", "lineup_status"
        ]
        available = [c for c in cols if c in show.columns]
        st.dataframe(
            show.sort_values(["game", "team", "lineup_spot"])[available],
            use_container_width=True,
            hide_index=True
        )

with tabs[2]:
    st.subheader("Batter Data")
    if batters_season_df.empty:
        st.warning("No batter season data loaded.")
    else:
        teams = ["ALL"] + sorted([t for t in batters_season_df["Team"].dropna().unique().tolist()])
        team_filter = st.selectbox("Filter hitters by team", teams, key="bat_team")
        min_hr = st.slider("Minimum season HR", 0, 60, 5, key="bat_hr")

        view = batter_model_df.copy()
        if team_filter != "ALL":
            view = view[view["Team"] == team_filter]

        if "HR_season" in view.columns:
            view = view[pd.to_numeric(view["HR_season"], errors="coerce") >= min_hr]

        keep = [
            "Name", "Team", "PA_season", "AVG_season", "OBP_season", "SLG_season",
            "OPS_season", "ISO_season", "HR_season", "wRC+_season",
            "AVG_14d", "SLG_14d", "OPS_14d", "ISO_14d", "HR_14d"
        ]
        keep = [c for c in keep if c in view.columns]
        sort_col = "OPS_season" if "OPS_season" in view.columns else keep[-1]
        st.dataframe(view.sort_values(sort_col, ascending=False)[keep], use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader("Pitcher Data")
    if pitchers_df.empty:
        st.warning("No pitcher data loaded.")
    else:
        teams = ["ALL"] + sorted([t for t in pitchers_df["Team"].dropna().unique().tolist()])
        team_filter = st.selectbox("Filter pitchers by team", teams, key="pit_team")
        max_era = st.slider("Max ERA", 1.00, 10.00, 5.00, key="pit_era")

        view = pitchers_df.copy()
        if team_filter != "ALL":
            view = view[view["Team"] == team_filter]
        if "ERA" in view.columns:
            view = view[pd.to_numeric(view["ERA"], errors="coerce") <= max_era]

        keep = ["Name", "Team", "ERA", "WHIP", "K/9", "BB/9", "HR/9", "FIP", "xFIP", "IP", "SO"]
        keep = [c for c in keep if c in view.columns]
        st.dataframe(view.sort_values("ERA", ascending=True)[keep], use_container_width=True, hide_index=True)

with tabs[4]:
    st.subheader("HR Matchups")
    if hitters_model_df.empty:
        st.warning("Need lineups plus hitter and pitcher data to build the HR model.")
    else:
        st.markdown("### Top HR hitters")
        hitter_cols = [
            "game", "team", "lineup_spot", "player_name", "opposing_pitcher",
            "bat_side", "opposing_pitch_hand", "handedness_split", "split_edge",
            "HR_season", "ISO_season", "HR_14d", "ISO_14d",
            "HR/9", "FIP", "temp_f", "wind_mph", "park_factor",
            "hr_matchup_score", "hr_tier", "lineup_status"
        ]
        hitter_cols = [c for c in hitter_cols if c in hitters_model_df.columns]
        st.dataframe(
            hitters_model_df.sort_values("hr_matchup_score", ascending=False)[hitter_cols],
            use_container_width=True,
            hide_index=True
        )

        st.markdown("### Team stack rankings")
        if not team_stack_df.empty:
            st.dataframe(team_stack_df, use_container_width=True, hide_index=True)

with tabs[5]:
    st.subheader("Park + Weather")
    if context_df.empty:
        st.warning("No park/weather rows available.")
    else:
        keep = [
            "away_abbr", "home_abbr", "venue", "park_factor",
            "temp_f", "wind_mph", "rain_pct",
            "away_lineup_status", "home_lineup_status"
        ]
        keep = [c for c in keep if c in context_df.columns]
        st.dataframe(context_df[keep], use_container_width=True, hide_index=True)

st.markdown("---")
st.caption("Version 2 adds lineup confirmation, handedness matchup view, 14-day hitter form, and a stronger HR model.")
