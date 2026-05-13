"""RBI Edge Model — daily RBI prop targets, parlays, and player deep dive.

Exposes ``render_rbi_model_page()`` so the main Streamlit app can wire this in
as a top-level tab. The page is designed to never crash: every external data
source is wrapped in a try/except, and a deterministic demo slate is used as
a fallback so the UI is always verifiable.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import math
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import streamlit as st


STADIUM_COORDS: Dict[str, Tuple[float, float]] = {
    "NYY": (40.8296, -73.9262), "NYM": (40.7571, -73.8458),
    "BOS": (42.3467, -71.0972), "TBR": (27.7683, -82.6534),
    "BAL": (39.2838, -76.6218), "TOR": (43.6414, -79.3894),
    "CHW": (41.8299, -87.6338), "CHC": (41.9484, -87.6553),
    "CLE": (41.4962, -81.6852), "DET": (42.3390, -83.0485),
    "KCR": (39.0517, -94.4803), "MIN": (44.9817, -93.2783),
    "HOU": (29.7573, -95.3555), "LAA": (33.8003, -117.8827),
    "OAK": (37.7516, -122.2005), "SEA": (47.5914, -122.3325),
    "TEX": (32.7473, -97.0845), "ATL": (33.8908, -84.4678),
    "MIA": (25.7781, -80.2197), "PHI": (39.9061, -75.1665),
    "WSN": (38.8730, -77.0074),
    "ARI": (33.4453, -112.0667), "COL": (39.7559, -104.9942),
    "LAD": (34.0739, -118.2400), "SDP": (32.7076, -117.1570),
    "SFG": (37.7786, -122.3893), "MIL": (43.0280, -87.9712),
    "PIT": (40.4469, -80.0057), "STL": (38.6226, -90.1928),
    "CIN": (39.0979, -84.5082),
}

SLOT_MAP = {1: 0.5, 2: 0.5, 3: 1.0, 4: 1.0, 5: 1.0, 6: 0.7, 7: 0.7, 8: 0.2, 9: 0.2}


def score_player(row: Dict[str, Any]) -> float:
    """Compute the RBI Edge raw score for a single hitter row."""
    batting_slot_score = SLOT_MAP.get(int(row.get("lineup_slot", 5) or 5), 0.5)

    team_obp_score = min(float(row.get("team_obp_l14", 0.320)) / 0.360, 1.0)
    sp_whip_score = 1.0 - min(float(row.get("sp_whip", 1.30)) / 2.0, 1.0)
    sp_bb9_score = min(float(row.get("sp_bb9", 3.0)) / 5.0, 1.0)
    total_score = min(float(row.get("game_total", 8.5)) / 12.0, 1.0)
    bullpen_score = 1.0 - min(float(row.get("bullpen_era_l10", 4.0)) / 6.0, 1.0)

    opportunity = (
        batting_slot_score * 0.233
        + team_obp_score * 0.167
        + sp_whip_score * 0.133
        + sp_bb9_score * 0.100
        + total_score * 0.117
        + bullpen_score * 0.083
    )

    xwoba_score = min(float(row.get("xwoba_l15", 0.320)) / 0.420, 1.0)
    xslg_gap_score = min(
        max(float(row.get("xslg", 0.400)) - float(row.get("slg", 0.400)), 0) / 0.080,
        1.0,
    )
    barrel_score = min(float(row.get("barrel_pct", 8.0)) / 18.0, 1.0)
    hh_score = min(float(row.get("hard_hit_pct", 38.0)) / 55.0, 1.0)
    k_score = 1.0 - min(float(row.get("k_pct", 22.0)) / 35.0, 1.0)
    iso_score = min(float(row.get("iso_l15", 0.150)) / 0.280, 1.0)
    risp_score = min(float(row.get("risp_avg", 0.260)) / 0.340, 1.0)

    skill = (
        xwoba_score * 0.267
        + xslg_gap_score * 0.156
        + barrel_score * 0.178
        + hh_score * 0.133
        + k_score * 0.111
        + iso_score * 0.133
        + risp_score * 0.133
    )

    platoon = 1.0 if row.get("platoon_advantage", False) else 0.6
    park = float(row.get("park_run_factor", 1.0))
    temp = min(max(float(row.get("temp_f", 72)) / 80.0, 0.7), 1.1)
    form = min(float(row.get("team_runs_l7", 4.5)) / 5.2, 1.15)
    stability = 1.0 if row.get("lineup_stable", True) else 0.6

    context_mult = (
        platoon * 0.25
        + park * 0.20
        + temp * 0.15
        + form * 0.20
        + stability * 0.20
    )

    raw_score = (opportunity * 0.50 + skill * 0.50) * context_mult
    return round(raw_score, 4)


def _component_scores(row: Dict[str, Any]) -> Dict[str, float]:
    """Return the three sub-scores used for the Player Deep Dive view."""
    batting_slot_score = SLOT_MAP.get(int(row.get("lineup_slot", 5) or 5), 0.5)
    team_obp_score = min(float(row.get("team_obp_l14", 0.320)) / 0.360, 1.0)
    sp_whip_score = 1.0 - min(float(row.get("sp_whip", 1.30)) / 2.0, 1.0)
    sp_bb9_score = min(float(row.get("sp_bb9", 3.0)) / 5.0, 1.0)
    total_score = min(float(row.get("game_total", 8.5)) / 12.0, 1.0)
    bullpen_score = 1.0 - min(float(row.get("bullpen_era_l10", 4.0)) / 6.0, 1.0)

    opportunity = (
        batting_slot_score * 0.233
        + team_obp_score * 0.167
        + sp_whip_score * 0.133
        + sp_bb9_score * 0.100
        + total_score * 0.117
        + bullpen_score * 0.083
    )

    xwoba_score = min(float(row.get("xwoba_l15", 0.320)) / 0.420, 1.0)
    xslg_gap_score = min(
        max(float(row.get("xslg", 0.400)) - float(row.get("slg", 0.400)), 0) / 0.080,
        1.0,
    )
    barrel_score = min(float(row.get("barrel_pct", 8.0)) / 18.0, 1.0)
    hh_score = min(float(row.get("hard_hit_pct", 38.0)) / 55.0, 1.0)
    k_score = 1.0 - min(float(row.get("k_pct", 22.0)) / 35.0, 1.0)
    iso_score = min(float(row.get("iso_l15", 0.150)) / 0.280, 1.0)
    risp_score = min(float(row.get("risp_avg", 0.260)) / 0.340, 1.0)

    skill = (
        xwoba_score * 0.267
        + xslg_gap_score * 0.156
        + barrel_score * 0.178
        + hh_score * 0.133
        + k_score * 0.111
        + iso_score * 0.133
        + risp_score * 0.133
    )

    platoon = 1.0 if row.get("platoon_advantage", False) else 0.6
    park = float(row.get("park_run_factor", 1.0))
    temp = min(max(float(row.get("temp_f", 72)) / 80.0, 0.7), 1.1)
    form = min(float(row.get("team_runs_l7", 4.5)) / 5.2, 1.15)
    stability = 1.0 if row.get("lineup_stable", True) else 0.6

    context_mult = (
        platoon * 0.25
        + park * 0.20
        + temp * 0.15
        + form * 0.20
        + stability * 0.20
    )

    return {
        "opportunity": float(opportunity),
        "skill": float(skill),
        "context": float(context_mult),
    }


def score_to_label(score: float) -> str:
    if score >= 0.80:
        return "🔥 Strong Edge"
    if score >= 0.65:
        return "✅ Moderate Edge"
    if score >= 0.50:
        return "⚠️ Marginal"
    return "❌ Fade"


def score_to_prob(score: float) -> str:
    if score >= 0.80:
        return "60–65%"
    if score >= 0.65:
        return "50–58%"
    if score >= 0.50:
        return "42–50%"
    return "<42%"


def _prob_midpoint(score: float) -> float:
    if score >= 0.80:
        return 0.625
    if score >= 0.65:
        return 0.54
    if score >= 0.50:
        return 0.46
    return 0.38


def _implied_odds(prob: float) -> str:
    prob = max(min(prob, 0.999), 0.001)
    if prob >= 0.5:
        odds = -(prob / (1 - prob)) * 100
        return f"{int(round(odds))}"
    odds = (1 / prob - 1) * 100
    return f"+{int(round(odds))}"


# ---------------------------------------------------------------------------
# Data fetching with robust fallbacks
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_schedule(date_iso: str) -> List[Dict[str, Any]]:
    """Return today's schedule (gamePk + home/away). Empty list on failure."""
    try:
        import statsapi  # type: ignore
        return statsapi.schedule(date=date_iso) or []
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_lineups(games: Tuple[int, ...]) -> Dict[int, Dict[str, Any]]:
    """Return mapping of gamePk → lineup info. Empty dict on failure."""
    out: Dict[int, Dict[str, Any]] = {}
    if not games:
        return out
    try:
        import statsapi  # type: ignore
    except Exception:
        return out
    for game_pk in games:
        try:
            data = statsapi.get("game", {"gamePk": game_pk}) or {}
            boxscore = data.get("liveData", {}).get("boxscore", {})
            teams = boxscore.get("teams", {})
            home = teams.get("home", {})
            away = teams.get("away", {})
            out[int(game_pk)] = {
                "home_order": home.get("battingOrder", []) or [],
                "away_order": away.get("battingOrder", []) or [],
                "home_players": home.get("players", {}) or {},
                "away_players": away.get("players", {}) or {},
            }
        except Exception:
            continue
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_expected_stats(season: int) -> pd.DataFrame:
    try:
        from pybaseball import statcast_batter_expected_stats  # type: ignore
        df = statcast_batter_expected_stats(season)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_team_batting(season: int) -> pd.DataFrame:
    try:
        from pybaseball import team_batting  # type: ignore
        df = team_batting(season)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pitching_stats(season: int) -> pd.DataFrame:
    try:
        from pybaseball import pitching_stats  # type: ignore
        df = pitching_stats(season)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_park_factors(season: int) -> pd.DataFrame:
    try:
        from pybaseball import team_park_factors  # type: ignore
        df = team_park_factors(pos="all", season=season)
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_game_totals(date_iso: str) -> Tuple[Dict[str, float], str]:
    """Return (team→total map, notice). Defaults to {} if no Odds API key."""
    try:
        api_key = st.secrets.get("odds_api_key")
    except Exception:
        api_key = None
    if not api_key:
        return {}, "Odds API key not configured — defaulting game totals to 8.5."
    try:
        import requests
        url = (
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
            f"?apiKey={api_key}&regions=us&markets=totals"
        )
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return {}, f"Odds API returned {resp.status_code} — using default total 8.5."
        data = resp.json() or []
        out: Dict[str, float] = {}
        for game in data:
            home = str(game.get("home_team", "")).strip()
            away = str(game.get("away_team", "")).strip()
            total = None
            for book in game.get("bookmakers", []) or []:
                for market in book.get("markets", []) or []:
                    if market.get("key") == "totals":
                        for o in market.get("outcomes", []) or []:
                            if o.get("point") is not None:
                                total = float(o["point"])
                                break
                        if total is not None:
                            break
                if total is not None:
                    break
            if total is not None and (home or away):
                if home:
                    out[home] = total
                if away:
                    out[away] = total
        return out, ""
    except Exception:
        return {}, "Odds API call failed — using default total 8.5."


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_weather(team_abbr: str) -> Tuple[float, str]:
    coords = STADIUM_COORDS.get(team_abbr)
    if not coords:
        return 72.0, ""
    try:
        import requests
        lat, lon = coords
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&hourly=temperature_2m&temperature_unit=fahrenheit&timezone=auto"
        )
        resp = requests.get(url, timeout=6)
        if resp.status_code != 200:
            return 72.0, ""
        js = resp.json() or {}
        temps = js.get("hourly", {}).get("temperature_2m", []) or []
        if temps:
            # Take a mid-afternoon-to-evening value (slot ~19 hours in).
            idx = min(19, len(temps) - 1)
            return float(temps[idx]), ""
        return 72.0, ""
    except Exception:
        return 72.0, ""


# ---------------------------------------------------------------------------
# Demo fallback slate
# ---------------------------------------------------------------------------

def _demo_slate() -> pd.DataFrame:
    """Deterministic demo slate so the UI is always verifiable."""
    demo: List[Dict[str, Any]] = [
        dict(player="Aaron Judge", team="NYY", opp="BOS", game="NYY @ BOS",
             lineup_slot=2, team_obp_l14=0.348, sp_whip=1.42, sp_bb9=3.6,
             game_total=9.5, bullpen_era_l10=4.10, xwoba_l15=0.438, xslg=0.612,
             slg=0.560, barrel_pct=22.4, hard_hit_pct=58.1, k_pct=27.0,
             iso_l15=0.290, risp_avg=0.310, platoon_advantage=True,
             park_run_factor=1.07, temp_f=74, team_runs_l7=5.4, lineup_stable=True),
        dict(player="Shohei Ohtani", team="LAD", opp="SDP", game="LAD @ SDP",
             lineup_slot=3, team_obp_l14=0.339, sp_whip=1.18, sp_bb9=2.9,
             game_total=8.5, bullpen_era_l10=3.85, xwoba_l15=0.421, xslg=0.604,
             slg=0.555, barrel_pct=19.8, hard_hit_pct=55.2, k_pct=24.1,
             iso_l15=0.286, risp_avg=0.290, platoon_advantage=True,
             park_run_factor=0.96, temp_f=70, team_runs_l7=5.1, lineup_stable=True),
        dict(player="Juan Soto", team="NYM", opp="PHI", game="NYM @ PHI",
             lineup_slot=2, team_obp_l14=0.341, sp_whip=1.31, sp_bb9=3.2,
             game_total=9.0, bullpen_era_l10=4.30, xwoba_l15=0.412, xslg=0.581,
             slg=0.540, barrel_pct=17.5, hard_hit_pct=53.7, k_pct=20.2,
             iso_l15=0.252, risp_avg=0.304, platoon_advantage=True,
             park_run_factor=1.04, temp_f=78, team_runs_l7=5.0, lineup_stable=True),
        dict(player="Bobby Witt Jr.", team="KCR", opp="MIN", game="KCR @ MIN",
             lineup_slot=2, team_obp_l14=0.327, sp_whip=1.27, sp_bb9=3.1,
             game_total=8.5, bullpen_era_l10=4.05, xwoba_l15=0.391, xslg=0.555,
             slg=0.515, barrel_pct=13.6, hard_hit_pct=50.8, k_pct=18.4,
             iso_l15=0.235, risp_avg=0.288, platoon_advantage=False,
             park_run_factor=1.01, temp_f=68, team_runs_l7=4.8, lineup_stable=True),
        dict(player="Yordan Alvarez", team="HOU", opp="TEX", game="HOU @ TEX",
             lineup_slot=3, team_obp_l14=0.335, sp_whip=1.36, sp_bb9=3.4,
             game_total=9.5, bullpen_era_l10=4.50, xwoba_l15=0.418, xslg=0.598,
             slg=0.548, barrel_pct=18.3, hard_hit_pct=54.4, k_pct=22.8,
             iso_l15=0.272, risp_avg=0.298, platoon_advantage=True,
             park_run_factor=1.06, temp_f=82, team_runs_l7=5.2, lineup_stable=True),
        dict(player="Mookie Betts", team="LAD", opp="SDP", game="LAD @ SDP",
             lineup_slot=1, team_obp_l14=0.339, sp_whip=1.18, sp_bb9=2.9,
             game_total=8.5, bullpen_era_l10=3.85, xwoba_l15=0.376, xslg=0.520,
             slg=0.490, barrel_pct=10.4, hard_hit_pct=46.7, k_pct=15.6,
             iso_l15=0.205, risp_avg=0.295, platoon_advantage=False,
             park_run_factor=0.96, temp_f=70, team_runs_l7=5.1, lineup_stable=True),
        dict(player="José Ramírez", team="CLE", opp="DET", game="CLE @ DET",
             lineup_slot=3, team_obp_l14=0.330, sp_whip=1.40, sp_bb9=3.5,
             game_total=8.0, bullpen_era_l10=4.20, xwoba_l15=0.385, xslg=0.548,
             slg=0.510, barrel_pct=12.8, hard_hit_pct=48.3, k_pct=12.5,
             iso_l15=0.240, risp_avg=0.300, platoon_advantage=True,
             park_run_factor=0.99, temp_f=66, team_runs_l7=4.6, lineup_stable=True),
        dict(player="Vladimir Guerrero Jr.", team="TOR", opp="BAL", game="TOR @ BAL",
             lineup_slot=4, team_obp_l14=0.332, sp_whip=1.29, sp_bb9=3.0,
             game_total=9.0, bullpen_era_l10=4.10, xwoba_l15=0.402, xslg=0.572,
             slg=0.525, barrel_pct=14.7, hard_hit_pct=52.6, k_pct=17.3,
             iso_l15=0.245, risp_avg=0.296, platoon_advantage=True,
             park_run_factor=1.05, temp_f=73, team_runs_l7=4.9, lineup_stable=True),
        dict(player="Corey Seager", team="TEX", opp="HOU", game="HOU @ TEX",
             lineup_slot=2, team_obp_l14=0.336, sp_whip=1.22, sp_bb9=2.8,
             game_total=9.5, bullpen_era_l10=3.95, xwoba_l15=0.395, xslg=0.560,
             slg=0.520, barrel_pct=15.0, hard_hit_pct=51.0, k_pct=20.5,
             iso_l15=0.250, risp_avg=0.282, platoon_advantage=True,
             park_run_factor=1.08, temp_f=82, team_runs_l7=5.0, lineup_stable=True),
        dict(player="Pete Alonso", team="NYM", opp="PHI", game="NYM @ PHI",
             lineup_slot=4, team_obp_l14=0.341, sp_whip=1.31, sp_bb9=3.2,
             game_total=9.0, bullpen_era_l10=4.30, xwoba_l15=0.358, xslg=0.520,
             slg=0.490, barrel_pct=15.6, hard_hit_pct=50.1, k_pct=24.5,
             iso_l15=0.232, risp_avg=0.260, platoon_advantage=False,
             park_run_factor=1.04, temp_f=78, team_runs_l7=5.0, lineup_stable=True),
        dict(player="Adolis García", team="TEX", opp="HOU", game="HOU @ TEX",
             lineup_slot=5, team_obp_l14=0.336, sp_whip=1.22, sp_bb9=2.8,
             game_total=9.5, bullpen_era_l10=3.95, xwoba_l15=0.341, xslg=0.495,
             slg=0.470, barrel_pct=12.3, hard_hit_pct=47.5, k_pct=29.6,
             iso_l15=0.220, risp_avg=0.245, platoon_advantage=False,
             park_run_factor=1.08, temp_f=82, team_runs_l7=5.0, lineup_stable=True),
        dict(player="Marcus Semien", team="TEX", opp="HOU", game="HOU @ TEX",
             lineup_slot=1, team_obp_l14=0.336, sp_whip=1.22, sp_bb9=2.8,
             game_total=9.5, bullpen_era_l10=3.95, xwoba_l15=0.336, xslg=0.470,
             slg=0.445, barrel_pct=8.4, hard_hit_pct=41.5, k_pct=18.0,
             iso_l15=0.180, risp_avg=0.255, platoon_advantage=True,
             park_run_factor=1.08, temp_f=82, team_runs_l7=5.0, lineup_stable=True),
    ]
    df = pd.DataFrame(demo)
    df["matchup"] = df["game"]
    return df


# ---------------------------------------------------------------------------
# Build today's slate (live → fallback)
# ---------------------------------------------------------------------------

def _build_live_slate(date_iso: str, season: int) -> Tuple[pd.DataFrame, List[str]]:
    """Best-effort live data assembly. Returns (df, notices)."""
    notices: List[str] = []
    games = _fetch_schedule(date_iso)
    if not games:
        notices.append("Live MLB schedule unavailable — showing demo slate.")
        return pd.DataFrame(), notices

    game_pks = tuple(int(g["game_id"]) for g in games if g.get("game_id"))
    lineups = _fetch_lineups(game_pks)
    if not lineups:
        notices.append(
            "Lineups not yet confirmed — showing demo slate. "
            "Check back after 12pm ET on a live slate day."
        )
        return pd.DataFrame(), notices

    expected = _fetch_expected_stats(season)
    team_bat = _fetch_team_batting(season)
    pitching = _fetch_pitching_stats(season)
    parks = _fetch_park_factors(season)
    totals_map, totals_notice = _fetch_game_totals(date_iso)
    if totals_notice:
        notices.append(totals_notice)

    # Build rows from each confirmed lineup.
    rows: List[Dict[str, Any]] = []
    for g in games:
        gpk = int(g.get("game_id", 0) or 0)
        info = lineups.get(gpk)
        if not info:
            continue
        home_abbr = str(g.get("home_name", ""))[:3].upper()
        away_abbr = str(g.get("away_name", ""))[:3].upper()
        matchup = f"{g.get('away_name', '')} @ {g.get('home_name', '')}"
        total_default = 8.5
        total = float(
            totals_map.get(g.get("home_name", ""), totals_map.get(g.get("away_name", ""), total_default))
        )

        for side, abbr in (("home", home_abbr), ("away", away_abbr)):
            order = info.get(f"{side}_order", [])
            players = info.get(f"{side}_players", {})
            for slot_idx, pid in enumerate(order[:9], start=1):
                player = players.get(f"ID{pid}") or players.get(str(pid)) or {}
                name = player.get("person", {}).get("fullName") or f"Player {pid}"
                rows.append({
                    "player": name,
                    "team": abbr,
                    "opp": away_abbr if side == "home" else home_abbr,
                    "game": matchup,
                    "matchup": matchup,
                    "lineup_slot": slot_idx,
                    "team_obp_l14": 0.320,
                    "sp_whip": 1.30,
                    "sp_bb9": 3.0,
                    "game_total": total,
                    "bullpen_era_l10": 4.00,
                    "xwoba_l15": 0.320,
                    "xslg": 0.400,
                    "slg": 0.400,
                    "barrel_pct": 8.0,
                    "hard_hit_pct": 38.0,
                    "k_pct": 22.0,
                    "iso_l15": 0.150,
                    "risp_avg": 0.260,
                    "platoon_advantage": False,
                    "park_run_factor": 1.0,
                    "temp_f": 72.0,
                    "team_runs_l7": 4.5,
                    "lineup_stable": True,
                })

    if not rows:
        notices.append("No confirmed lineups parsed — showing demo slate.")
        return pd.DataFrame(), notices

    df = pd.DataFrame(rows)

    # ---- Merge expected stats (xwOBA, xSLG, barrel%, hard-hit%) on name. ----
    if not expected.empty:
        try:
            ex = expected.copy()
            name_col = next((c for c in ex.columns if c.lower() in ("name", "player_name", "last_name, first_name")), None)
            if name_col is not None:
                ex["_join"] = ex[name_col].astype(str).str.lower()
                df["_join"] = df["player"].str.lower()
                cols = {c.lower(): c for c in ex.columns}
                pick = lambda *keys: next((cols[k] for k in keys if k in cols), None)
                xwoba_c = pick("xwoba", "est_woba")
                xslg_c = pick("xslg", "est_slg")
                slg_c = pick("slg")
                barrel_c = pick("barrel_rate", "barrel_pct", "brl_pa")
                hh_c = pick("hard_hit_percent", "hard_hit%", "hardhit_pct")
                merged = df.merge(ex[["_join"] + [c for c in [xwoba_c, xslg_c, slg_c, barrel_c, hh_c] if c]],
                                  on="_join", how="left", suffixes=("", "_ex"))
                if xwoba_c and xwoba_c in merged:
                    df["xwoba_l15"] = merged[xwoba_c].fillna(df["xwoba_l15"])
                if xslg_c and xslg_c in merged:
                    df["xslg"] = merged[xslg_c].fillna(df["xslg"])
                if slg_c and slg_c in merged:
                    df["slg"] = merged[slg_c].fillna(df["slg"])
                if barrel_c and barrel_c in merged:
                    df["barrel_pct"] = merged[barrel_c].fillna(df["barrel_pct"])
                if hh_c and hh_c in merged:
                    df["hard_hit_pct"] = merged[hh_c].fillna(df["hard_hit_pct"])
                df = df.drop(columns=["_join"], errors="ignore")
        except Exception:
            notices.append("Statcast merge failed — using defaults for some hitters.")

    # ---- Team OBP from team_batting (best-effort). ----
    if not team_bat.empty:
        try:
            tb = team_bat.copy()
            team_col = next((c for c in tb.columns if c.lower() in ("team", "tm")), None)
            obp_col = next((c for c in tb.columns if c.lower() == "obp"), None)
            if team_col and obp_col:
                obp_map = dict(zip(tb[team_col].astype(str).str.upper(), tb[obp_col].astype(float)))
                df["team_obp_l14"] = df["team"].map(obp_map).fillna(df["team_obp_l14"])
        except Exception:
            pass

    # ---- Park factor. ----
    if not parks.empty:
        try:
            pk = parks.copy()
            team_col = next((c for c in pk.columns if c.lower() in ("team", "tm")), None)
            pf_col = next((c for c in pk.columns if "factor" in c.lower() or c.lower() == "pf"), None)
            if team_col and pf_col:
                pf_map = {str(k).upper(): float(v) / 100.0 if float(v) > 5 else float(v)
                          for k, v in zip(pk[team_col], pk[pf_col])}
                df["park_run_factor"] = df["team"].map(pf_map).fillna(df["park_run_factor"])
        except Exception:
            pass

    # ---- Weather (per home team). ----
    try:
        for abbr in df["team"].unique():
            temp, _ = _fetch_weather(abbr)
            df.loc[df["team"] == abbr, "temp_f"] = temp
    except Exception:
        pass

    return df, notices


def _score_slate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["score"] = out.apply(lambda r: score_player(r.to_dict()), axis=1)
    out["label"] = out["score"].apply(score_to_label)
    out["prob"] = out["score"].apply(score_to_prob)
    out["prob_mid"] = out["score"].apply(_prob_midpoint)
    flags = []
    for _, r in out.iterrows():
        f = []
        if r.get("platoon_advantage"):
            f.append("★ Platoon")
        if float(r.get("park_run_factor", 1.0)) >= 1.05:
            f.append("🏟 Hitter park")
        if float(r.get("game_total", 8.5)) >= 9.0:
            f.append("📈 Total ≥9")
        if int(r.get("lineup_slot", 5) or 5) in (3, 4, 5):
            f.append("🎯 Heart")
        flags.append(" · ".join(f))
    out["flags"] = flags
    return out.sort_values("score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Streamlit rendering
# ---------------------------------------------------------------------------

def _label_style(label: str) -> str:
    if "Strong" in label:
        return "background:#dcfce7;color:#166534;font-weight:800;"
    if "Moderate" in label:
        return "background:#fef9c3;color:#854d0e;font-weight:800;"
    if "Marginal" in label:
        return "background:#ffedd5;color:#9a3412;font-weight:700;"
    return "background:#fee2e2;color:#991b1b;font-weight:700;"


def _render_leaderboard(scored: pd.DataFrame) -> None:
    st.markdown("### 🏆 Leaderboard — Today's RBI Edge Targets")

    games = sorted(scored["game"].unique().tolist())
    with st.sidebar:
        st.markdown("#### RBI Edge filters")
        min_score = st.slider("Min RBI Edge Score", 0.30, 0.95, 0.65, 0.01, key="rbi_edge_min_score")
        sel_games = st.multiselect("Game filter", games, default=games, key="rbi_edge_games")
        only_platoon = st.checkbox("Platoon advantage only", value=False, key="rbi_edge_platoon")
        only_hitter_park = st.checkbox("Hitter park only (≥1.05)", value=False, key="rbi_edge_park")

    f = scored[scored["score"] >= float(min_score)]
    if sel_games:
        f = f[f["game"].isin(sel_games)]
    if only_platoon:
        f = f[f["platoon_advantage"] == True]  # noqa: E712
    if only_hitter_park:
        f = f[f["park_run_factor"].astype(float) >= 1.05]

    if f.empty:
        st.info("No hitters match the current filters. Try lowering the score threshold.")
        return

    show = f[["player", "team", "lineup_slot", "matchup", "score", "label", "prob", "flags"]].copy()
    show.columns = ["Player", "Team", "Slot", "Matchup", "RBI Edge", "Tier", "Est. Prob", "Key Flags"]
    show["RBI Edge"] = show["RBI Edge"].astype(float).round(2)

    def _row_style(row: pd.Series) -> List[str]:
        tier = str(row.get("Tier", ""))
        css = _label_style(tier)
        styles = [""] * len(row)
        try:
            tier_idx = list(row.index).index("Tier")
            styles[tier_idx] = css
        except ValueError:
            pass
        return styles

    styled = show.style.apply(_row_style, axis=1).format({"RBI Edge": "{:.2f}"})
    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_parlays(scored: pd.DataFrame, n_legs: int) -> None:
    threshold = 0.70 if n_legs == 2 else 0.65
    pool = scored[scored["score"] >= threshold].copy()
    if len(pool) < n_legs:
        st.info(f"Not enough hitters with score ≥ {threshold:.2f} to build {n_legs}-leg combos right now.")
        return

    rows: List[Dict[str, Any]] = []
    for combo in itertools.combinations(pool.itertuples(index=False), n_legs):
        games = {c.game for c in combo}
        if len(games) < n_legs:
            continue
        prob = 1.0
        for c in combo:
            prob *= float(c.prob_mid)
        combined_score = sum(float(c.score) for c in combo) / n_legs
        entry = {f"Player {chr(65 + i)}": f"{c.player} ({c.team})" for i, c in enumerate(combo)}
        entry["Combined Score"] = round(combined_score, 2)
        entry["Est. Probability"] = f"{prob * 100:.1f}%"
        entry["Implied Odds"] = _implied_odds(prob)
        entry["_prob"] = prob
        rows.append(entry)

    if not rows:
        st.info("No cross-game combinations available — try adjusting the slate or thresholds.")
        return

    df = pd.DataFrame(rows).sort_values("_prob", ascending=False).head(10).reset_index(drop=True)
    df.insert(0, "Rank", df.index + 1)
    df.loc[df["Rank"] <= 3, "Rank"] = df.loc[df["Rank"] <= 3, "Rank"].astype(str) + " ⭐"
    df = df.drop(columns=["_prob"])
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        f"⭐ = Best Bet (top 3 by estimated probability). All legs are from different games "
        f"to avoid SGP correlation. Score floor for {n_legs}-leg pool: {threshold:.2f}."
    )


def _render_deep_dive(scored: pd.DataFrame) -> None:
    if scored.empty:
        st.info("Slate is empty — nothing to deep-dive.")
        return
    options = [f"{r.player} — {r.team} (score {r.score:.2f})" for r in scored.itertuples(index=False)]
    pick = st.selectbox("Choose a hitter", options, key="rbi_edge_deep_dive_pick")
    idx = options.index(pick)
    row = scored.iloc[idx].to_dict()
    comps = _component_scores(row)

    c1, c2, c3 = st.columns(3)
    c1.metric("Final RBI Edge", f"{row['score']:.2f}", row["label"])
    c2.metric("Estimated Probability", row["prob"])
    c3.metric("Lineup Slot", int(row.get("lineup_slot", 5) or 5))

    st.markdown("#### Score breakdown")
    b1, b2, b3 = st.columns(3)
    with b1:
        st.caption("Opportunity")
        st.progress(min(max(comps["opportunity"], 0.0), 1.0))
        st.caption(f"{comps['opportunity']:.2f} / 1.00")
    with b2:
        st.caption("Skill")
        st.progress(min(max(comps["skill"], 0.0), 1.0))
        st.caption(f"{comps['skill']:.2f} / 1.00")
    with b3:
        st.caption("Context multiplier")
        # progress only accepts 0..1, so scale 0.70..1.15 onto that range
        ctx = comps["context"]
        ctx_scaled = (ctx - 0.70) / (1.15 - 0.70)
        st.progress(min(max(ctx_scaled, 0.0), 1.0))
        st.caption(f"{ctx:.2f}× (range 0.70 – 1.15)")

    st.markdown("#### Feature inputs")
    thresholds = {
        "team_obp_l14": (0.330, "↑"),
        "sp_whip": (1.30, "↓"),
        "sp_bb9": (3.0, "↑"),
        "game_total": (8.5, "↑"),
        "bullpen_era_l10": (4.0, "↓"),
        "xwoba_l15": (0.330, "↑"),
        "xslg": (0.430, "↑"),
        "slg": (0.430, "↑"),
        "barrel_pct": (8.0, "↑"),
        "hard_hit_pct": (40.0, "↑"),
        "k_pct": (22.0, "↓"),
        "iso_l15": (0.170, "↑"),
        "risp_avg": (0.260, "↑"),
        "park_run_factor": (1.00, "↑"),
        "temp_f": (72.0, "↑"),
        "team_runs_l7": (4.5, "↑"),
    }
    rows_out: List[Dict[str, Any]] = []
    for k, (thr, direction) in thresholds.items():
        v = row.get(k)
        try:
            v_num = float(v)
        except (TypeError, ValueError):
            v_num = float("nan")
        good = (v_num >= thr) if direction == "↑" else (v_num <= thr)
        rows_out.append({
            "Feature": k,
            "Value": f"{v_num:.3f}" if not math.isnan(v_num) else "—",
            "Threshold": f"{thr:.3f} {direction}",
            "Signal": "✅ Above" if good else "❌ Below",
        })
    rows_out.append({"Feature": "platoon_advantage",
                     "Value": "Yes" if row.get("platoon_advantage") else "No",
                     "Threshold": "Yes",
                     "Signal": "✅ Above" if row.get("platoon_advantage") else "❌ Below"})
    rows_out.append({"Feature": "lineup_stable",
                     "Value": "Yes" if row.get("lineup_stable", True) else "No",
                     "Threshold": "Yes",
                     "Signal": "✅ Above" if row.get("lineup_stable", True) else "❌ Below"})

    st.dataframe(pd.DataFrame(rows_out), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def render_rbi_model_page() -> None:
    """Render the RBI Edge Model page inside the main Streamlit app."""
    st.markdown(
        '<div class="section-title" style="font-size:1.45rem;margin-top:8px;">'
        '⚾ RBI Edge Model — Daily Prop Targets</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="margin: 0 0 12px 0; color:#475569; font-size:0.92rem;">'
        'Today\'s top <b>2+ RBI</b> prop targets, scored by the RBI Edge Model. '
        'The score blends <b>Opportunity</b> (lineup slot, team OBP, opposing SP/bullpen, total) '
        'with <b>Skill</b> (xwOBA, xSLG gap, Barrel%, HardHit%, K%, ISO, RISP AVG) and a '
        '<b>Context multiplier</b> (platoon, park, temperature, team form, lineup stability). '
        'Tabs below: leaderboard, 2-leg parlays, 3-leg long shots, and a player deep dive.'
        '</div>',
        unsafe_allow_html=True,
    )

    # --- Header row: refresh + timestamp ---
    h1, h2 = st.columns([1, 4])
    with h1:
        if st.button("🔄 Refresh data", key="rbi_edge_refresh"):
            try:
                st.cache_data.clear()
            except Exception:
                pass
            st.rerun()
    with h2:
        ts = _dt.datetime.now().strftime("%b %d %Y %I:%M %p CT")
        st.caption(f"Last updated: {ts}")

    # --- Build slate (live → fallback) ---
    today = _dt.date.today()
    date_iso = today.strftime("%Y-%m-%d")
    season = today.year

    with st.spinner("Building today's RBI Edge slate…"):
        live_df, notices = _build_live_slate(date_iso, season)

    fallback_used = False
    if live_df.empty:
        fallback_used = True
        live_df = _demo_slate()

    scored = _score_slate(live_df)

    for n in notices:
        st.info(n)
    if fallback_used:
        st.warning(
            "Live data unavailable — showing a verifiable demo slate. "
            "On a live slate day with `statsapi` + `pybaseball` installed and lineups confirmed, "
            "this page will fill in with real hitters."
        )

    tab1, tab2, tab3, tab4 = st.tabs([
        "🏆 Leaderboard",
        "🎯 2-Leg Parlays",
        "🚀 3-Leg Long Shots",
        "🔍 Player Deep Dive",
    ])
    with tab1:
        _render_leaderboard(scored)
    with tab2:
        st.markdown("### 🎯 2-Leg Parlays — Best Cross-Game Pairs")
        _render_parlays(scored, n_legs=2)
    with tab3:
        st.markdown("### 🚀 3-Leg Long Shots — Cross-Game Triples")
        _render_parlays(scored, n_legs=3)
    with tab4:
        st.markdown("### 🔍 Player Deep Dive")
        _render_deep_dive(scored)

    with st.expander("Scoring formula"):
        st.markdown(
            "**Raw score = (Opportunity × 0.50 + Skill × 0.50) × Context multiplier**\n\n"
            "- **Opportunity (50%)**: lineup slot 23.3%, team OBP L14 16.7%, SP WHIP 13.3%, "
            "SP BB/9 10.0%, Vegas total 11.7%, opp bullpen ERA L10 8.3%\n"
            "- **Skill (50%)**: xwOBA L15 26.7%, xSLG−SLG gap 15.6%, Barrel% 17.8%, "
            "HardHit% 13.3%, K% 11.1%, ISO L15 13.3%, RISP AVG 13.3%\n"
            "- **Context multiplier (0.70–1.15)**: platoon 25%, park 20%, temperature 15%, "
            "team runs L7 20%, lineup stability 20%\n\n"
            "**Tier labels**: 🔥 Strong Edge ≥ 0.80 · ✅ Moderate Edge ≥ 0.65 · "
            "⚠️ Marginal ≥ 0.50 · ❌ Fade < 0.50"
        )
