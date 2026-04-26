import streamlit as st
import pandas as pd
import requests
from datetime import date
from difflib import get_close_matches
from pybaseball import batting_stats, pitching_stats

st.set_page_config(page_title="MLB Data App", layout="wide")

st.title("MLB Data App")
st.caption("Daily slate, batter data, pitcher data, matchups, park and weather")

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

NEUTRAL_PARK_FACTOR = 100

@st.cache_data(ttl=1800)
def get_schedule(selected_date):
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date": selected_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher"
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
                "park_factor": NEUTRAL_PARK_FACTOR
            })

    return pd.DataFrame(rows)

@st.cache_data(ttl=21600)
def get_batter_data(season):
    df = batting_stats(season, qual=50)
    wanted = ["Name", "Team", "AVG", "OBP", "SLG", "OPS", "HR", "RBI", "BB%", "K%", "ISO", "wOBA", "wRC+"]
    keep = [c for c in wanted if c in df.columns]
    return df[keep].copy()

@st.cache_data(ttl=21600)
def get_pitcher_data(season):
    df = pitching_stats(season, qual=20)
    wanted = ["Name", "Team", "W", "L", "ERA", "WHIP", "IP", "SO", "BB", "K/9", "BB/9", "HR/9", "FIP", "xFIP"]
    keep = [c for c in wanted if c in df.columns]
    return df[keep].copy()

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

def enrich_schedule_with_weather(schedule_df):
    if schedule_df.empty:
        return schedule_df

    weather_rows = []
    for _, row in schedule_df.iterrows():
        weather = get_weather(row["lat"], row["lon"], row["game_time_utc"])
        weather_rows.append(weather)

    weather_df = pd.DataFrame(weather_rows)
    return pd.concat([schedule_df.reset_index(drop=True), weather_df.reset_index(drop=True)], axis=1)

def find_pitcher_row(pitchers_df, pitcher_name):
    if not pitcher_name or pitcher_name == "TBD" or pitchers_df.empty:
        return None

    exact = pitchers_df[pitchers_df["Name"].str.lower() == pitcher_name.lower()]
    if not exact.empty:
        return exact.iloc[0]

    last_name = pitcher_name.split()[-1]
    contains = pitchers_df[pitchers_df["Name"].str.contains(last_name, case=False, na=False)]
    if not contains.empty:
        return contains.iloc[0]

    choices = pitchers_df["Name"].dropna().tolist()
    match = get_close_matches(pitcher_name, choices, n=1, cutoff=0.85)
    if match:
        return pitchers_df[pitchers_df["Name"] == match[0]].iloc[0]

    return None

def matchup_score(team_batters, opposing_pitcher, park_factor, temp_f, wind_mph):
    if team_batters.empty:
        return None

    top_batters = team_batters.sort_values("OPS", ascending=False).head(5) if "OPS" in team_batters.columns else team_batters.head(5)
    ops_score = top_batters["OPS"].mean() * 100 if "OPS" in top_batters.columns else 70
    hr_score = top_batters["HR"].mean() * 0.8 if "HR" in top_batters.columns else 0

    era_penalty = 16
    whip_penalty = 8

    if opposing_pitcher is not None:
        era = opposing_pitcher.get("ERA", 4.00)
        whip = opposing_pitcher.get("WHIP", 1.30)
        try:
            era_penalty = float(era) * 4
        except:
            era_penalty = 16
        try:
            whip_penalty = float(whip) * 6
        except:
            whip_penalty = 8

    temp_bonus = 0 if temp_f is None else max(0, (temp_f - 70) * 0.15)
    wind_bonus = 0 if wind_mph is None else max(0, (wind_mph - 8) * 0.10)
    park_bonus = (park_factor - 100) * 0.8

    score = ops_score + hr_score + temp_bonus + wind_bonus + park_bonus - era_penalty - whip_penalty
    return round(score, 2)

def build_matchups(schedule_df, batters_df, pitchers_df):
    if schedule_df.empty or batters_df.empty:
        return pd.DataFrame()

    rows = []
    for _, game in schedule_df.iterrows():
        away_batters = batters_df[batters_df["Team"] == game["away_abbr"]].copy() if "Team" in batters_df.columns else pd.DataFrame()
        home_batters = batters_df[batters_df["Team"] == game["home_abbr"]].copy() if "Team" in batters_df.columns else pd.DataFrame()

        home_pitcher = find_pitcher_row(pitchers_df, game["home_probable"])
        away_pitcher = find_pitcher_row(pitchers_df, game["away_probable"])

        away_score = matchup_score(away_batters, home_pitcher, game["park_factor"], game["temp_f"], game["wind_mph"])
        home_score = matchup_score(home_batters, away_pitcher, game["park_factor"], game["temp_f"], game["wind_mph"])

        away_targets = ", ".join(away_batters.sort_values("OPS", ascending=False)["Name"].head(3).tolist()) if not away_batters.empty else ""
        home_targets = ", ".join(home_batters.sort_values("OPS", ascending=False)["Name"].head(3).tolist()) if not home_batters.empty else ""

        rows.append({
            "game": f'{game["away_abbr"]} @ {game["home_abbr"]}',
            "away_hit_score": away_score,
            "home_hit_score": home_score,
            "better_hitting_side": game["away_abbr"] if (away_score or -999) > (home_score or -999) else game["home_abbr"],
            "away_targets": away_targets,
            "home_targets": home_targets,
            "away_vs_pitcher": game["home_probable"],
            "home_vs_pitcher": game["away_probable"],
            "venue": game["venue"],
            "temp_f": game["temp_f"],
            "wind_mph": game["wind_mph"],
            "rain_pct": game["rain_pct"]
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        out["top_game_score"] = out[["away_hit_score", "home_hit_score"]].max(axis=1)
        out = out.sort_values("top_game_score", ascending=False)
    return out

with st.sidebar:
    st.header("Filters")
    selected_date = st.date_input("Slate date", value=date.today())
    selected_season = st.selectbox("Season", [2026, 2025, 2024, 2023], index=0)
    st.info("Park factor is set to neutral 100 in this starter app. Later, replace it with your own park model.")

try:
    schedule_df = get_schedule(selected_date)
    schedule_df = enrich_schedule_with_weather(schedule_df)
except Exception as e:
    st.error(f"Schedule error: {e}")
    schedule_df = pd.DataFrame()

try:
    batters_df = get_batter_data(selected_season)
except Exception as e:
    st.error(f"Batter data error: {e}")
    batters_df = pd.DataFrame()

try:
    pitchers_df = get_pitcher_data(selected_season)
except Exception as e:
    st.error(f"Pitcher data error: {e}")
    pitchers_df = pd.DataFrame()

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "MLB Slate",
    "Batters",
    "Pitchers",
    "Best Matchups",
    "Park + Weather"
])

with tab1:
    st.subheader(f"Daily MLB Slate - {selected_date}")
    if schedule_df.empty:
        st.warning("No games found for this date.")
    else:
        show_cols = [
            "game_time_ct", "away_abbr", "home_abbr", "venue",
            "away_probable", "home_probable", "status"
        ]
        st.dataframe(schedule_df[show_cols], use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Batter Data")
    if batters_df.empty:
        st.warning("No batter data loaded.")
    else:
        teams = ["ALL"] + sorted(batters_df["Team"].dropna().unique().tolist())
        team_filter = st.selectbox("Filter batters by team", teams, key="bat_team")
        min_hr = st.slider("Minimum HR", 0, 60, 10)

        filtered = batters_df.copy()
        if team_filter != "ALL":
            filtered = filtered[filtered["Team"] == team_filter]
        if "HR" in filtered.columns:
            filtered = filtered[filtered["HR"] >= min_hr]

        sort_col = "OPS" if "OPS" in filtered.columns else filtered.columns[-1]
        filtered = filtered.sort_values(sort_col, ascending=False)
        st.dataframe(filtered, use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Pitcher Data")
    if pitchers_df.empty:
        st.warning("No pitcher data loaded.")
    else:
        teams = ["ALL"] + sorted(pitchers_df["Team"].dropna().unique().tolist())
        team_filter = st.selectbox("Filter pitchers by team", teams, key="pit_team")
        max_era = st.slider("Max ERA", 1.00, 10.00, 5.00)

        filtered = pitchers_df.copy()
        if team_filter != "ALL":
            filtered = filtered[filtered["Team"] == team_filter]
        if "ERA" in filtered.columns:
            filtered = filtered[pd.to_numeric(filtered["ERA"], errors="coerce") <= max_era]

        sort_col = "ERA" if "ERA" in filtered.columns else filtered.columns[-1]
        filtered = filtered.sort_values(sort_col, ascending=True)
        st.dataframe(filtered, use_container_width=True, hide_index=True)

with tab4:
    st.subheader("Best Matchups")
    if schedule_df.empty or batters_df.empty:
        st.warning("Need both schedule and batter data to build matchups.")
    else:
        matchup_df = build_matchups(schedule_df, batters_df, pitchers_df)
        if matchup_df.empty:
            st.warning("No matchup results yet.")
        else:
            st.dataframe(matchup_df, use_container_width=True, hide_index=True)
            st.caption("Starter matchup score = top team OPS/HR profile adjusted by opposing starter, weather, and neutral park factor. Tune this formula with your own model later.")

with tab5:
    st.subheader("Park + Weather")
    if schedule_df.empty:
        st.warning("No park/weather rows available.")
    else:
        park_weather = schedule_df[[
            "away_abbr", "home_abbr", "venue", "park_factor", "temp_f", "wind_mph", "rain_pct"
        ]].copy()
        st.dataframe(park_weather, use_container_width=True, hide_index=True)

st.markdown("---")
st.caption("Version 1 starter app built for GitHub + Streamlit Community Cloud deployment.")
