"""RBI Edge Model — daily RBI prop targets, parlays, and player deep dive.

Exposes ``render_rbi_model_page()`` so the main Streamlit app can wire this in
as a top-level tab.

Slate sources (in order, no fake/demo data):
  1. **Confirmed lineups** — from the host app's boxscore helpers.
  2. **Projected lineups** — derived from each team's most-used 9 over recent
     completed games (via the app's ``get_projected_lineup`` helper). Used
     automatically for any game whose lineup has not yet been posted.

Every row carries a ``lineup_status`` of ``Confirmed`` or ``Projected``. If
neither is available we render a polished empty state explaining what is
missing, matching the other generators in the app.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import math
import os
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

SLOT_MAP = {1: 0.65, 2: 0.70, 3: 1.0, 4: 1.0, 5: 0.95, 6: 0.75, 7: 0.65, 8: 0.40, 9: 0.35}


def _compute_components(row: Dict[str, Any]) -> Dict[str, float]:
    """Shared scoring math used by score_player and _component_scores.

    Tuned 2026-05-13 to broaden the candidate pool: normalizing denominators
    sit closer to "above average" rather than "elite ceiling", missing
    optional context defaults are neutral (~1.0 multiplier) instead of
    punitive, and the projected-lineup penalty is reduced so a projected
    Judge does not get crushed by the context multiplier.
    """
    batting_slot_score = SLOT_MAP.get(int(row.get("lineup_slot", 5) or 5), 0.6)

    team_obp_score = min(float(row.get("team_obp_l14", 0.320)) / 0.345, 1.0)
    sp_whip_score = 1.0 - min(float(row.get("sp_whip", 1.30)) / 1.60, 1.0)
    sp_bb9_score = min(float(row.get("sp_bb9", 3.0)) / 4.0, 1.0)
    total_score = min(float(row.get("game_total", 8.5)) / 10.5, 1.0)
    bullpen_score = 1.0 - min(float(row.get("bullpen_era_l10", 4.0)) / 5.5, 1.0)

    opportunity = (
        batting_slot_score * 0.233
        + team_obp_score * 0.167
        + sp_whip_score * 0.133
        + sp_bb9_score * 0.100
        + total_score * 0.117
        + bullpen_score * 0.083
    )

    xwoba_score = min(float(row.get("xwoba_l15", 0.320)) / 0.380, 1.0)
    xslg_gap_score = min(
        max(float(row.get("xslg", 0.400)) - float(row.get("slg", 0.400)), 0) / 0.060,
        1.0,
    )
    barrel_score = min(float(row.get("barrel_pct", 8.0)) / 14.0, 1.0)
    hh_score = min(float(row.get("hard_hit_pct", 38.0)) / 48.0, 1.0)
    k_score = 1.0 - min(float(row.get("k_pct", 22.0)) / 30.0, 1.0)
    iso_score = min(float(row.get("iso_l15", 0.150)) / 0.220, 1.0)
    risp_score = min(float(row.get("risp_avg", 0.260)) / 0.310, 1.0)

    skill = (
        xwoba_score * 0.267
        + xslg_gap_score * 0.156
        + barrel_score * 0.178
        + hh_score * 0.133
        + k_score * 0.111
        + iso_score * 0.133
        + risp_score * 0.133
    )

    # Platoon unknown → neutral, not punitive (was 0.6, now 0.85).
    if "platoon_advantage" not in row or row.get("platoon_advantage") is None:
        platoon = 0.90
    else:
        platoon = 1.0 if row.get("platoon_advantage") else 0.85
    park = float(row.get("park_run_factor", 1.0))
    temp = min(max(float(row.get("temp_f", 72)) / 78.0, 0.85), 1.1)
    form = min(float(row.get("team_runs_l7", 4.5)) / 4.8, 1.12)
    # Projected lineups should be mildly discounted, not crushed (was 0.6).
    stability = 1.0 if row.get("lineup_stable", True) else 0.92

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


def score_player(row: Dict[str, Any]) -> float:
    """Compute the RBI Edge raw score for a single hitter row."""
    comps = _compute_components(row)
    raw_score = (comps["opportunity"] * 0.50 + comps["skill"] * 0.50) * comps["context"]
    return round(raw_score, 4)


def _component_scores(row: Dict[str, Any]) -> Dict[str, float]:
    """Return the three sub-scores used for the Player Deep Dive view."""
    return _compute_components(row)


# Label / probability bands tuned 2026-05-13 to match the broader score
# distribution. Strong Edge stays reserved for genuine standouts; Moderate
# covers the "good RBI bet" tier where most leaderboard rows live; Marginal
# remains visible but de-emphasized.
def score_to_label(score: float) -> str:
    if score >= 0.72:
        return "🔥 Strong Edge"
    if score >= 0.58:
        return "✅ Moderate Edge"
    if score >= 0.45:
        return "⚠️ Marginal"
    return "❌ Fade"


def score_to_prob(score: float) -> str:
    if score >= 0.72:
        return "58–65%"
    if score >= 0.58:
        return "48–57%"
    if score >= 0.45:
        return "40–48%"
    return "<40%"


def _prob_midpoint(score: float) -> float:
    if score >= 0.72:
        return 0.615
    if score >= 0.58:
        return 0.525
    if score >= 0.45:
        return 0.44
    return 0.37


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
    """Return (team→total map, notice). Empty {} = fall back to Model Est. total.

    Missing/expired Odds API key is **not** a scary warning anymore — the
    caller computes an internal model estimate from app-native team/pitcher
    context. We only emit a notice if the API itself errored after a real
    attempt, and the wording is informational (caption, not warning).
    """
    api_key = None
    for key_name in (
        "odds_api_key",
        "ODDS_API_KEY",
        "THE_ODDS_API_KEY",
        "THE_ODDS_API",
        "ODDSAPI_KEY",
    ):
        try:
            candidate = st.secrets.get(key_name)
        except Exception:
            candidate = None
        if not candidate:
            candidate = os.environ.get(key_name)
        if candidate:
            api_key = str(candidate).strip()
            if api_key:
                break
    if not api_key:
        return {}, ""  # silent — caller will use Model Est. total
    try:
        import requests
        url = (
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
            f"?apiKey={api_key}&regions=us&markets=totals"
        )
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return {}, f"Odds API returned {resp.status_code} — using Model Est. game totals."
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
        return {}, "Odds API call failed — using Model Est. game totals."


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
# Build today's slate (confirmed → projected)
# ---------------------------------------------------------------------------

def _row_from_app_batter(name: str, team: str, lineup_spot: int, b_row: Any,
                          p_row: Any, game_row: Any, weather_temp: float,
                          bat_side: str, pitch_hand: str) -> Dict[str, Any]:
    """Build a scoring row from the app's pre-loaded batters_df / pitchers_df.

    Missing values use the same defaults the score function expects, so any
    gap in the CSV degrades gracefully instead of crashing.
    """
    def _f(v, default):
        try:
            if v is None:
                return float(default)
            fv = float(v)
            if pd.isna(fv):
                return float(default)
            return fv
        except (TypeError, ValueError):
            return float(default)

    if b_row is not None:
        xwoba = _f(b_row.get("xwOBA"), 0.320)
        xslg = _f(b_row.get("xSLG"), 0.400)
        slg = _f(b_row.get("SLG"), 0.400)
        barrel = _f(b_row.get("Barrel%"), 8.0)
        hard = _f(b_row.get("HardHit%"), 38.0)
        kpct = _f(b_row.get("K%"), 22.0)
        iso = _f(b_row.get("ISO"), 0.150)
        # OPS — surfaced on the RBI Edge cards alongside xwOBA / xSLG. Try the
        # canonical column first; fall back to OBP + SLG when the feed only
        # ships the components so display never collapses to "—".
        ops_raw = b_row.get("OPS")
        try:
            if ops_raw is None or pd.isna(float(ops_raw)):
                obp_v = b_row.get("OBP"); slg_v = b_row.get("SLG")
                if obp_v is not None and slg_v is not None and not (
                    pd.isna(float(obp_v)) or pd.isna(float(slg_v))
                ):
                    ops = float(obp_v) + float(slg_v)
                else:
                    ops = 0.720
            else:
                ops = float(ops_raw)
        except (TypeError, ValueError):
            ops = 0.720
    else:
        xwoba, xslg, slg, barrel, hard, kpct, iso = 0.320, 0.400, 0.400, 8.0, 38.0, 22.0, 0.150
        ops = 0.720

    sp_whip = _f(p_row.get("WHIP") if p_row is not None else None, 1.30)
    sp_era = _f(p_row.get("ERA") if p_row is not None else None, 4.00)
    sp_bb9 = _f(p_row.get("BB/9") if p_row is not None else
                (p_row.get("BB9") if p_row is not None else None), 3.0)

    home_park_factor = 1.0
    try:
        pf = float(game_row.get("park_factor", 100))
        # The app stores park factor as 100-centered ints (e.g. 105). Scale to ~1.0.
        home_park_factor = pf / 100.0 if pf > 5 else pf
    except Exception:
        pass

    # Platoon: hitter L vs RHP or hitter R vs LHP is an advantage.
    bs = (bat_side or "").upper()[:1]
    ph = (pitch_hand or "").upper()[:1]
    platoon = (bs == "L" and ph == "R") or (bs == "R" and ph == "L")

    return {
        "player": name,
        "team": team,
        "opp": "",  # filled by caller
        "game": "",  # filled by caller
        "matchup": "",  # filled by caller
        "lineup_slot": int(lineup_spot) if lineup_spot else 5,
        "team_obp_l14": 0.320,
        "sp_whip": sp_whip,
        "sp_bb9": sp_bb9,
        "game_total": 8.5,
        "bullpen_era_l10": sp_era,  # use SP ERA as a rough proxy when bullpen unknown
        "xwoba_l15": xwoba,
        "xslg": xslg,
        "slg": slg,
        "ops": ops,
        "barrel_pct": barrel,
        "hard_hit_pct": hard,
        "k_pct": kpct,
        "iso_l15": iso,
        "risp_avg": 0.260,
        "platoon_advantage": platoon,
        "park_run_factor": home_park_factor,
        "temp_f": float(weather_temp) if weather_temp else 72.0,
        "team_runs_l7": 4.5,
        "lineup_stable": True,
    }


def _index_by_name_key(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Return a {name_key: row_dict} mapping that tolerates duplicate keys.

    Using ``df.set_index("name_key").to_dict("index")`` raises
    ``ValueError: DataFrame index must be unique for orient='index'`` whenever
    two rows share a ``name_key`` — which routinely happens in Savant exports
    where the same hitter appears under multiple stints or where two players
    share a normalized name across teams. We instead iterate row-by-row and
    keep the *first* hit for each key so the lookup degrades gracefully
    instead of crashing the whole RBI Edge tab.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(df, pd.DataFrame) or df.empty or "name_key" not in df.columns:
        return out
    for rec in df.to_dict("records"):
        key = rec.get("name_key")
        if key is None or (isinstance(key, float) and pd.isna(key)):
            continue
        key_str = str(key)
        if key_str not in out:
            out[key_str] = rec
    return out


def _team_obp_map(batters_df: pd.DataFrame) -> Dict[str, float]:
    """Compute a {team_key: team_obp} map from the app's batters_df.

    Falls back to the league-average default in ``score_player`` for any team
    we cannot compute. Uses ``team_key`` if present (already-normalized) or
    falls back to ``Team``.
    """
    if not isinstance(batters_df, pd.DataFrame) or batters_df.empty:
        return {}
    if "OBP" not in batters_df.columns:
        return {}
    col_team = "team_key" if "team_key" in batters_df.columns else (
        "Team" if "Team" in batters_df.columns else None
    )
    if col_team is None:
        return {}
    try:
        s = pd.to_numeric(batters_df["OBP"], errors="coerce")
        df = batters_df.assign(_obp=s).dropna(subset=["_obp"])
        if df.empty:
            return {}
        return df.groupby(col_team)["_obp"].mean().to_dict()
    except Exception:
        return {}


def _team_runs_map(batters_df: pd.DataFrame) -> Dict[str, float]:
    """Approximate runs-per-game proxy from team-level offense.

    Uses (OBP * SLG) * 30 as a coarse linear proxy when no live team runs/game
    feed is wired up. We just want a value in the same 3.5–5.5 range that
    score_player expects; absolute accuracy is not required because the
    Context multiplier clips the result.
    """
    obp_map = _team_obp_map(batters_df)
    if not obp_map:
        return {}
    if "SLG" not in batters_df.columns:
        return obp_map  # tolerate, just return OBP-based ranking
    col_team = "team_key" if "team_key" in batters_df.columns else "Team"
    try:
        s = pd.to_numeric(batters_df["SLG"], errors="coerce")
        slg_map = (
            batters_df.assign(_slg=s)
            .dropna(subset=["_slg"])
            .groupby(col_team)["_slg"]
            .mean()
            .to_dict()
        )
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for team, obp in obp_map.items():
        slg = slg_map.get(team)
        if slg is None:
            continue
        # Coarse linear proxy → typical MLB team lands ~4.3-4.8 runs/game.
        # Tuned so that an OBP*SLG product of ~0.13 (league average) yields
        # ~4.5 runs. We clamp to a sane 3.5–5.5 band so a small or unusually
        # elite roster sample doesn't blow up the Model Est. game total.
        raw = float(obp) * float(slg) * 34.6
        out[team] = float(max(3.5, min(5.5, raw)))
    return out


def _model_est_total(home_runs: float, away_runs: float, park_factor_100: float,
                     home_sp_era: float, away_sp_era: float, temp_f: float) -> float:
    """Internal estimated game total. Used when Odds API key is absent/expired.

    Combines team form, opposing starter ERA, park run factor, and temperature
    into a single number in the 7.0–11.0 range. This is deliberately simple —
    we only need a *reasonable* default so the Opportunity sub-score isn't
    pinned at the league mean. Not meant to be a market-replacement total.
    """
    base = float(home_runs or 4.4) + float(away_runs or 4.4)
    # ERA pull: high ERA → more runs, ~+0.4 runs per ERA point above 4.0.
    sp_avg = (float(home_sp_era or 4.0) + float(away_sp_era or 4.0)) / 2.0
    base += (sp_avg - 4.0) * 0.45
    # Park: 100 is neutral; +5 → +0.3 runs.
    pf = float(park_factor_100 or 100.0)
    base *= 1.0 + ((pf - 100.0) / 100.0) * 0.6
    # Temperature: warmer → more runs, ~+0.15 runs per 10F above 65.
    base += max(0.0, (float(temp_f or 72.0) - 65.0)) * 0.015
    return float(max(6.5, min(12.5, base)))


def _build_slate_from_app(
    schedule_df: pd.DataFrame,
    batters_df: pd.DataFrame,
    pitchers_df: pd.DataFrame,
    build_game_context_fn,
    clean_name_fn,
    norm_team_fn,
    totals_map: Dict[str, float] | None = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """Preferred path: use the host app's already-cached schedule/lineup/weather.

    Returns ``(df, notices)``. Each row's ``lineup_status`` is set to
    ``"Confirmed"`` or ``"Projected"``. Sides flagged ``"Not Posted"`` are
    silently skipped so a single sparse team never blanks the whole page.
    """
    notices: List[str] = []
    if schedule_df is None or len(schedule_df) == 0:
        return pd.DataFrame(), ["Slate schedule is empty for today."]

    # Duplicate-key-safe lookup tables for batters and pitchers. ``name_key``
    # is *not* guaranteed unique in the host app's batters_df/pitchers_df
    # (multiple stints, identical normalized names), so we cannot use
    # ``set_index(...).to_dict("index")`` here — it raises
    # "DataFrame index must be unique for orient='index'".
    batters_idx = _index_by_name_key(batters_df)
    pitchers_idx = _index_by_name_key(pitchers_df)

    # Team-level offense proxies (OBP and runs/game) computed from the app's
    # batters_df. Falls back to score_player defaults for missing teams.
    team_obp = _team_obp_map(batters_df)
    team_runs = _team_runs_map(batters_df)
    totals_map = totals_map or {}

    n_confirmed_sides = 0
    n_projected_sides = 0
    n_not_posted_sides = 0
    rows: List[Dict[str, Any]] = []

    n_model_est_totals = 0

    for _, game in schedule_df.iterrows():
        # Per-game errors must NOT blank the whole page. Skip this game only
        # and continue rendering everyone else's projected/confirmed rows.
        try:
            ctx = build_game_context_fn(game)
        except Exception:
            ctx = None
        if not isinstance(ctx, dict):
            continue

        weather = ctx.get("weather") or {}
        try:
            temp = float(weather.get("temp_f") or weather.get("temperature") or 72.0)
        except (TypeError, ValueError):
            temp = 72.0

        # Resolve game total: prefer market total when injected, otherwise
        # compute an internal model estimate from team form + opposing SP +
        # park + temperature. We deliberately do NOT call the Odds API here —
        # the host app handles that and passes a totals_map in if available.
        home_full = str(game.get("home_team", "") or "")
        away_full = str(game.get("away_team", "") or "")
        home_abbr_raw = str(game.get("home_abbr", "") or "")
        away_abbr_raw = str(game.get("away_abbr", "") or "")
        market_total = None
        for key in (home_full, away_full, home_abbr_raw, away_abbr_raw):
            if key and key in totals_map:
                try:
                    market_total = float(totals_map[key])
                    break
                except (TypeError, ValueError):
                    pass

        if market_total is not None:
            game_total = market_total
            total_source = "Market"
        else:
            home_team_key = norm_team_fn(home_abbr_raw) if norm_team_fn else home_abbr_raw
            away_team_key = norm_team_fn(away_abbr_raw) if norm_team_fn else away_abbr_raw
            home_runs_pg = team_runs.get(home_team_key, 4.4)
            away_runs_pg = team_runs.get(away_team_key, 4.4)
            home_sp_key = clean_name_fn(game.get("home_probable", "")) if clean_name_fn else ""
            away_sp_key = clean_name_fn(game.get("away_probable", "")) if clean_name_fn else ""
            home_sp_row = pitchers_idx.get(home_sp_key) if home_sp_key else None
            away_sp_row = pitchers_idx.get(away_sp_key) if away_sp_key else None
            try:
                home_era = float(home_sp_row.get("ERA")) if home_sp_row else 4.0
            except (TypeError, ValueError):
                home_era = 4.0
            try:
                away_era = float(away_sp_row.get("ERA")) if away_sp_row else 4.0
            except (TypeError, ValueError):
                away_era = 4.0
            game_total = _model_est_total(
                home_runs=home_runs_pg, away_runs=away_runs_pg,
                park_factor_100=float(game.get("park_factor", 100) or 100),
                home_sp_era=home_era, away_sp_era=away_era, temp_f=temp,
            )
            total_source = "Model Est."
            n_model_est_totals += 1

        for side in ("away", "home"):
            status = ctx.get(f"{side}_status") or ""
            if status == "Not Posted":
                n_not_posted_sides += 1
                continue
            if status == "Confirmed":
                n_confirmed_sides += 1
            elif status == "Projected":
                n_projected_sides += 1
            else:
                # Unknown status — skip this side but keep rendering the rest
                # of today's slate.
                continue

            lineup = ctx.get(f"{side}_lineup")
            if not isinstance(lineup, pd.DataFrame) or lineup.empty:
                # Sparse data for this side only — skip silently, do not blank
                # the whole page.
                continue

            team_abbr = norm_team_fn(game.get(f"{side}_abbr", "")) or ""
            opp_side = "home" if side == "away" else "away"
            opp_abbr = norm_team_fn(game.get(f"{opp_side}_abbr", "")) or ""
            opp_pitcher = game.get(f"{opp_side}_probable", "TBD")
            game_label = f"{game.get('away_abbr', '')} @ {game.get('home_abbr', '')}"
            # team_runs_l7 / team_obp_l14 from app-native batters_df aggregates
            t_obp = team_obp.get(team_abbr)
            t_runs = team_runs.get(team_abbr)

            p_row = None
            if opp_pitcher and opp_pitcher != "TBD":
                p_key = clean_name_fn(opp_pitcher) if clean_name_fn else str(opp_pitcher).lower()
                p_row = pitchers_idx.get(p_key)

            for _, hitter in lineup.iterrows():
                name = hitter.get("player_name") or hitter.get("name") or ""
                if not name:
                    continue
                spot_val = hitter.get("lineup_spot")
                try:
                    slot = int(float(spot_val)) if spot_val is not None and not pd.isna(spot_val) else 5
                except (TypeError, ValueError):
                    slot = 5

                name_key = clean_name_fn(name) if clean_name_fn else str(name).lower()
                b_row = batters_idx.get(name_key)
                bat_side = hitter.get("bat_side") or (b_row.get("bat_side") if b_row else "")
                pitch_hand = hitter.get("opposing_pitch_hand") or ""

                row = _row_from_app_batter(
                    name=name, team=team_abbr, lineup_spot=slot,
                    b_row=b_row, p_row=p_row, game_row=game,
                    weather_temp=temp, bat_side=bat_side, pitch_hand=pitch_hand,
                )
                row["opp"] = opp_abbr
                row["game"] = game_label
                row["matchup"] = game_label
                row["lineup_status"] = "Confirmed" if status == "Confirmed" else "Projected"
                row["lineup_stable"] = status == "Confirmed"
                row["game_total"] = float(game_total)
                row["total_source"] = total_source
                if t_obp is not None:
                    row["team_obp_l14"] = float(t_obp)
                if t_runs is not None:
                    row["team_runs_l7"] = float(t_runs)
                rows.append(row)

    if rows:
        if n_confirmed_sides and n_projected_sides:
            notices.append(
                f"Using **{n_confirmed_sides} confirmed** + **{n_projected_sides} projected** lineups "
                f"({n_not_posted_sides} side(s) skipped — not posted)."
            )
        elif n_projected_sides:
            notices.append(
                f"No confirmed lineups posted yet — using **{n_projected_sides} projected lineup(s)** "
                "derived from each team's most-used 9 over recent games. "
                "Confirmed lineups typically post 2–4 hours before first pitch."
            )
        elif n_confirmed_sides:
            notices.append(f"All **{n_confirmed_sides}** lineups confirmed.")
        if n_model_est_totals and not totals_map:
            notices.append(
                f"Game totals computed by **Model Est.** (team form · opposing SP · park · weather) "
                f"for {n_model_est_totals} game(s)."
            )
        return pd.DataFrame(rows), notices

    return pd.DataFrame(), notices


def _build_live_slate(date_iso: str, season: int) -> Tuple[pd.DataFrame, List[str]]:
    """Standalone fallback (no app helpers): direct statsapi/pybaseball pulls.

    Used only if ``render_rbi_model_page`` is called without injected
    dependencies. Returns confirmed-lineup rows only — no projected fallback
    in this mode since we don't have access to the app's recent-games cache.
    """
    notices: List[str] = []
    games = _fetch_schedule(date_iso)
    if not games:
        notices.append("Live MLB schedule unavailable for today.")
        return pd.DataFrame(), notices

    game_pks = tuple(int(g["game_id"]) for g in games if g.get("game_id"))
    lineups = _fetch_lineups(game_pks)
    if not lineups:
        notices.append(
            "Lineups not yet confirmed. Confirmed lineups typically post 2–4 "
            "hours before first pitch."
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
        market = totals_map.get(g.get("home_name", "")) or totals_map.get(g.get("away_name", ""))
        if market is not None:
            total = float(market)
            total_source = "Market"
        else:
            total = _model_est_total(
                home_runs=4.4, away_runs=4.4, park_factor_100=100.0,
                home_sp_era=4.0, away_sp_era=4.0, temp_f=72.0,
            )
            total_source = "Model Est."

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
                    "lineup_status": "Confirmed",
                    "total_source": total_source,
                })

    if not rows:
        notices.append("No confirmed lineups parsed from today's slate.")
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
    if "lineup_status" not in out.columns:
        out["lineup_status"] = "Confirmed"
    out["score"] = out.apply(lambda r: score_player(r.to_dict()), axis=1)
    out["label"] = out["score"].apply(score_to_label)
    out["prob"] = out["score"].apply(score_to_prob)
    out["prob_mid"] = out["score"].apply(_prob_midpoint)
    flags = []
    for _, r in out.iterrows():
        f = []
        status = str(r.get("lineup_status", "Confirmed"))
        if status == "Projected":
            f.append("📋 Projected")
        else:
            f.append("✅ Confirmed")
        if r.get("platoon_advantage"):
            f.append("★ Platoon")
        if float(r.get("park_run_factor", 1.0)) >= 1.05:
            f.append("🏟 Hitter park")
        if float(r.get("game_total", 8.5)) >= 9.0:
            f.append("📈 Total ≥9")
        if str(r.get("total_source", "")) == "Model Est.":
            f.append("📐 Model Est. Total")
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


# ---------------------------------------------------------------------------
# Mobile player-card stack — matches the universal Slate-Pitchers visual
# language used elsewhere in app.py (black/gold/gold brand, dark gradient cards,
# colored stat chips, no horizontal scrolling). Kept inline here so the module
# stays self-contained.
# ---------------------------------------------------------------------------

_RBI_MOBILE_CSS = (
    "<style>"
    ".rbi-desktop { display:block; }"
    ".rbi-mobile  { display:none; }"
    "@media (max-width: 640px) {"
    "  .rbi-desktop { display:none !important; }"
    "  .rbi-mobile  { display:block !important; }"
    "  div[data-testid='stHorizontalBlock'] { flex-wrap: wrap !important; }"
    "  div[data-testid='stHorizontalBlock'] > div { "
    "    min-width: 0 !important; width: 100% !important; "
    "    flex: 1 1 100% !important; }"
    "}"
    ".rbi-grid { display:grid; grid-template-columns: 1fr; gap: 12px; "
    "  margin: 8px 0 14px 0; }"
    "@media (min-width: 480px) and (max-width: 640px) {"
    "  .rbi-grid { grid-template-columns: repeat(2, 1fr); }"
    "}"
    ".rbi-card { background: linear-gradient(160deg, #101820 0%, #101820 50%, #101820 100%); "
    "  border:1px solid rgba(255,182,18,.35); border-radius:16px; padding:13px 14px; "
    "  color:#e9e6f5; "
    "  box-shadow: 0 6px 18px rgba(0,0,0,.40), 0 0 0 1px rgba(255,182,18,.12); "
    "  display:flex; flex-direction:column; gap:8px; min-width:0; "
    "  position: relative; overflow: hidden; "
    "  transition: border-color .2s, box-shadow .2s; }"
    # subtle top gradient accent on cards
    ".rbi-card::before { content:''; position:absolute; top:0; left:0; right:0; height:1px; "
    "  background: linear-gradient(90deg, transparent 20%, rgba(250,204,21,.20) 50%, transparent 80%); }"
    ".rbi-head { display:flex; align-items:flex-start; gap:8px; min-width:0; position: relative; z-index:1; }"
    ".rbi-rank { font-variant-numeric: tabular-nums; font-weight:900; "
    "  font-size:.76rem; color:#fcd34d; "
    "  background: linear-gradient(135deg, #111111, #000000); "
    "  border:1px solid rgba(255,182,18,.55); padding:3px 9px; border-radius:8px; "
    "  flex:0 0 auto; line-height:1.3; "
    "  box-shadow: 0 2px 6px rgba(0,0,0,.40); }"
    ".rbi-id { display:flex; flex-direction:column; min-width:0; flex:1 1 auto; }"
    ".rbi-name { font-weight:800; font-size:1.0rem; line-height:1.15; "
    "  color:#f4f0ff; word-break:break-word; }"
    ".rbi-sub { font-size:.73rem; color:#9590b8; margin-top:2px; "
    "  word-break:break-word; font-weight:600; }"
    ".rbi-score { font-variant-numeric: tabular-nums; font-weight:900; "
    "  font-size:1.08rem; color:#facc15; text-align:right; flex:0 0 auto; "
    "  padding-left:6px; line-height:1.05; "
    "  text-shadow: 0 0 10px rgba(250,204,21,.30); }"
    ".rbi-score small { display:block; font-size:.58rem; color:#9590b8; "
    "  font-weight:700; letter-spacing:.08em; text-transform:uppercase; "
    "  margin-top:2px; }"
    ".rbi-tiers { display:flex; flex-wrap:wrap; gap:5px; position: relative; z-index:1; }"
    ".rbi-tier { display:inline-block; padding: 2px 9px; border-radius:999px; "
    "  font-size:.64rem; font-weight:800; letter-spacing:.04em; "
    "  border:1px solid transparent; text-transform:uppercase; }"
    ".rbi-tier.elite  { background: rgba(16,185,129,.16); color:#6ee7b7; "
    "  border-color: rgba(110,231,183,.35); }"
    ".rbi-tier.strong { background: rgba(132,204,22,.14); color:#bef264; "
    "  border-color: rgba(190,242,100,.35); }"
    ".rbi-tier.ok     { background: rgba(250,204,21,.12); color:#fde68a; "
    "  border-color: rgba(253,224,71,.35); }"
    ".rbi-tier.soft   { background: rgba(249,115,22,.14); color:#fdba74; "
    "  border-color: rgba(253,186,116,.35); }"
    ".rbi-tier.poor   { background: rgba(239,68,68,.16); color:#fca5a5; "
    "  border-color: rgba(252,165,165,.35); }"
    ".rbi-tier.gold   { background: rgba(252,211,77,.14); color:#fde68a; "
    "  border-color: rgba(253,224,71,.40); }"
    ".rbi-tier.info   { background: rgba(255,182,18,.18); color:#f5f5f5; "
    "  border-color: rgba(255,255,255,.35); }"
    ".rbi-grid2 { display:grid; grid-template-columns: 1fr 1fr; gap: 6px; "
    "  position: relative; z-index:1; }"
    ".rbi-chip { background: rgba(255,255,255,.04); "
    "  border:1px solid rgba(255,182,18,.25); "
    "  border-radius:10px; padding:6px 9px; min-width:0; "
    "  transition: border-color .18s; }"
    ".rbi-chip-label { font-size:.60rem; color:#8a87b0; "
    "  font-weight:700; letter-spacing:.05em; text-transform:uppercase; "
    "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
    ".rbi-chip-val { font-size:.96rem; font-weight:800; color:#f0eeff; "
    "  font-variant-numeric: tabular-nums; margin-top:2px; "
    "  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }"
    ".rbi-chip-val.na { color:#5a5778; }"
    ".rbi-foot { font-size:.71rem; color:#9590b8; display:flex; "
    "  flex-wrap:wrap; gap:6px 10px; position: relative; z-index:1; "
    "  border-top: 1px solid rgba(255,182,18,.18); padding-top: 6px; margin-top: 2px; }"
    ".rbi-foot b { color:#d4d0f0; }"
    ".rbi-empty { padding:16px 18px; color:#9590b8; "
    "  background: rgba(13,9,40,.80); "
    "  border:1px dashed rgba(255,182,18,.35); border-radius:16px; text-align:center; }"
    "</style>"
)


def _rbi_tier_from_label(label: str) -> Tuple[str, str]:
    s = str(label or "")
    if "Strong" in s: return ("elite",  "Strong Edge")
    if "Moderate" in s: return ("strong", "Moderate Edge")
    if "Marginal" in s: return ("ok",    "Marginal")
    if "Fade" in s:     return ("poor",  "Fade")
    return ("ok", s or "—")


def _rbi_chip(label: str, val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)) or str(val).strip() == "":
        return (
            '<div class="rbi-chip">'
            f'<div class="rbi-chip-label">{label}</div>'
            f'<div class="rbi-chip-val na">—</div></div>'
        )
    return (
        '<div class="rbi-chip">'
        f'<div class="rbi-chip-label">{label}</div>'
        f'<div class="rbi-chip-val">{val}</div></div>'
    )


def _rbi_card_html(*, rank: int | None, name: str, sub: str,
                   score: float | None, score_label: str = "RBI Edge",
                   tiers: List[Tuple[str, str]] | None = None,
                   chips: List[Tuple[str, Any]] | None = None,
                   foot_bits: List[str] | None = None) -> str:
    rank_html = f'<span class="rbi-rank">#{rank}</span>' if rank is not None else ""
    score_html = ""
    if score is not None:
        try:
            score_html = (
                f'<div class="rbi-score">{float(score):.2f}'
                f'<small>{score_label}</small></div>'
            )
        except Exception:
            score_html = ""
    tier_html = ""
    if tiers:
        tier_html = (
            '<div class="rbi-tiers">' +
            "".join(f'<span class="rbi-tier {cls}">{lab}</span>'
                    for cls, lab in tiers) +
            '</div>'
        )
    chips_html = ""
    if chips:
        chips_html = (
            '<div class="rbi-grid2">' +
            "".join(_rbi_chip(lbl, val) for lbl, val in chips) +
            '</div>'
        )
    foot_html = ""
    if foot_bits:
        foot_html = (
            '<div class="rbi-foot">' + " · ".join(foot_bits) + "</div>"
        )
    return (
        '<div class="rbi-card">'
        '<div class="rbi-head">'
        f'{rank_html}'
        f'<div class="rbi-id"><div class="rbi-name">{name}</div>'
        f'<div class="rbi-sub">{sub}</div></div>'
        f'{score_html}'
        '</div>'
        f'{tier_html}'
        f'{chips_html}'
        f'{foot_html}'
        '</div>'
    )


def _render_leaderboard(scored: pd.DataFrame) -> None:
    st.markdown("### 🏆 Leaderboard — Today's RBI Edge Targets")

    if scored is None or scored.empty or "score" not in scored.columns:
        st.info("No hitters available to score yet — see the status banner above for details.")
        return

    games = sorted(scored["game"].unique().tolist())
    has_projected = bool((scored.get("lineup_status") == "Projected").any()) if "lineup_status" in scored.columns else False
    with st.sidebar:
        st.markdown("#### RBI Edge filters")
        min_score = st.slider("Min RBI Edge Score", 0.20, 0.95, 0.50, 0.01, key="rbi_edge_min_score")
        sel_games = st.multiselect("Game filter", games, default=games, key="rbi_edge_games")
        only_platoon = st.checkbox("Platoon advantage only", value=False, key="rbi_edge_platoon")
        only_hitter_park = st.checkbox("Hitter park only (≥1.05)", value=False, key="rbi_edge_park")
        only_confirmed = False
        if has_projected:
            only_confirmed = st.checkbox(
                "Confirmed lineups only", value=False, key="rbi_edge_confirmed_only",
                help="Hide rows from projected (not-yet-posted) lineups.",
            )

    f = scored[scored["score"] >= float(min_score)]
    if sel_games:
        f = f[f["game"].isin(sel_games)]
    if only_platoon:
        f = f[f["platoon_advantage"] == True]  # noqa: E712
    if only_hitter_park:
        f = f[f["park_run_factor"].astype(float) >= 1.05]
    if has_projected and only_confirmed:
        f = f[f["lineup_status"] == "Confirmed"]

    if f.empty:
        # Fallback so the page never goes blank when slate data exists: drop
        # the score floor and show the top-15 by score, preserving any
        # game/platoon/park filters the user picked.
        fallback = scored.copy()
        if sel_games:
            fallback = fallback[fallback["game"].isin(sel_games)]
        if only_platoon:
            fallback = fallback[fallback["platoon_advantage"] == True]  # noqa: E712
        if only_hitter_park:
            fallback = fallback[fallback["park_run_factor"].astype(float) >= 1.05]
        if has_projected and only_confirmed:
            fallback = fallback[fallback["lineup_status"] == "Confirmed"]
        if fallback.empty:
            st.info("No hitters match the current filters. Try lowering the score threshold.")
            return
        st.caption(
            f"No hitters above score {float(min_score):.2f} — showing the top "
            f"{min(15, len(fallback))} candidates by RBI Edge instead."
        )
        f = fallback.sort_values("score", ascending=False).head(15)

    cols = ["player", "team", "lineup_slot", "matchup", "lineup_status",
            "score", "label", "prob", "ops", "flags"]
    cols = [c for c in cols if c in f.columns]
    show = f[cols].copy()
    rename_map = {
        "player": "Player", "team": "Team", "lineup_slot": "Slot", "matchup": "Matchup",
        "lineup_status": "Lineup", "score": "RBI Edge", "label": "Tier",
        "prob": "Est. Prob", "ops": "OPS", "flags": "Key Flags",
    }
    show.columns = [rename_map.get(c, c) for c in show.columns]
    show["RBI Edge"] = show["RBI Edge"].astype(float).round(2)
    if "OPS" in show.columns:
        # Format OPS with the canonical 3-decimal layout the rest of the app
        # uses; blank cells stay blank rather than rendering 0.000.
        show["OPS"] = show["OPS"].apply(
            lambda v: f"{float(v):.3f}" if v is not None and not pd.isna(v) else ""
        )

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
    st.markdown('<div class="rbi-desktop">', unsafe_allow_html=True)
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # ---- Mobile player-card stack (no horizontal scroll) -----------------
    f_sorted = f.sort_values("score", ascending=False).reset_index(drop=True)
    cards: List[str] = []
    for i, row in f_sorted.iterrows():
        tier_cls, tier_lab = _rbi_tier_from_label(row.get("label", ""))
        tiers: List[Tuple[str, str]] = [(tier_cls, tier_lab)]
        status = str(row.get("lineup_status", "Confirmed"))
        if status == "Projected":
            tiers.append(("info", "Projected"))
        else:
            tiers.append(("gold", "Confirmed"))
        if row.get("platoon_advantage"):
            tiers.append(("gold", "Platoon"))

        def _fmt(val, kind="num"):
            try:
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return None
            except Exception:
                pass
            try:
                f_v = float(val)
            except Exception:
                return str(val)
            if kind == "rate3":
                return f"{f_v:.3f}".lstrip("0") if 0 <= f_v < 1 else f"{f_v:.3f}"
            if kind == "pct":
                return f"{f_v*100:.1f}%" if f_v <= 1.5 else f"{f_v:.1f}%"
            if kind == "int":
                return f"{int(round(f_v))}"
            return f"{f_v:.2f}"

        chips: List[Tuple[str, Any]] = [
            ("Slot", _fmt(row.get("lineup_slot"), "int")),
            ("Est. Prob", row.get("prob") or "—"),
            # OPS sits between Est. Prob and the Statcast metrics so the
            # universal bat-quality signal is visible on every RBI Edge card.
            ("OPS", _fmt(row.get("ops"), "rate3")),
            ("xwOBA L15", _fmt(row.get("xwoba_l15"), "rate3")),
            ("xSLG", _fmt(row.get("xslg"), "rate3")),
            ("Barrel%", _fmt(row.get("barrel_pct"), "pct")),
            ("HardHit%", _fmt(row.get("hard_hit_pct"), "pct")),
            ("Team OBP L14", _fmt(row.get("team_obp_l14"), "rate3")),
            ("Total", _fmt(row.get("game_total"))),
        ]
        foot_bits: List[str] = []
        matchup = row.get("matchup")
        if matchup:
            foot_bits.append(f"<b>{matchup}</b>")
        flags = str(row.get("flags") or "").strip()
        if flags:
            foot_bits.append(flags)
        cards.append(_rbi_card_html(
            rank=i + 1,
            name=str(row.get("player", "") or "—"),
            sub=f'{row.get("team","")}'.strip(" ·"),
            score=float(row.get("score", 0.0) or 0.0),
            score_label="RBI Edge",
            tiers=tiers,
            chips=chips,
            foot_bits=foot_bits,
        ))
    st.markdown(
        _RBI_MOBILE_CSS +
        '<div class="rbi-mobile"><div class="rbi-grid">'
        + "".join(cards) +
        '</div></div>',
        unsafe_allow_html=True,
    )


def _render_parlays(scored: pd.DataFrame, n_legs: int) -> None:
    threshold = 0.55 if n_legs == 2 else 0.50
    # Minimum pool to consider building combos. Want enough cross-game variety
    # to find n_legs hitters from distinct games.
    pool_target = max(n_legs * 3, 6)
    if scored is None or scored.empty or "score" not in scored.columns:
        st.info(f"No scored hitters available — cannot build {n_legs}-leg combos yet.")
        return

    fallback_note = ""
    pool = scored[scored["score"] >= threshold].copy()
    if len(pool) < pool_target:
        # Threshold left too few candidates — top up with the highest-scored
        # remaining hitters so we always surface combos when the slate has
        # enough rows from distinct games.
        topup = scored.sort_values("score", ascending=False).head(pool_target)
        pool = pd.concat([pool, topup]).drop_duplicates(subset=["player", "team", "game"])
        if not pool.empty:
            fallback_note = (
                f"Pool extended below the {threshold:.2f} score floor — using the "
                f"top {len(pool)} candidates so cross-game combos remain available."
            )

    # Need at least n_legs hitters from distinct games to build any combo.
    if pool["game"].nunique() < n_legs:
        st.info(
            f"Need hitters from at least {n_legs} different games to build a "
            f"{n_legs}-leg parlay. Today's slate has only "
            f"{pool['game'].nunique()} game(s) with scored hitters."
        )
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
    if fallback_note:
        st.caption(fallback_note)
    # Build Rank as a string column so we can safely append the ⭐ badge to the
    # top three without triggering pandas' "setting str into numeric column"
    # TypeError that occurs when Rank is left as int64.
    ranks = [f"{i + 1} ⭐" if i < 3 else str(i + 1) for i in range(len(df))]
    df.insert(0, "Rank", ranks)
    df_view = df.drop(columns=["_prob"])

    st.markdown('<div class="rbi-desktop">', unsafe_allow_html=True)
    st.dataframe(df_view, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # ---- Mobile cards: one card per parlay row ---------------------------
    cards: List[str] = []
    leg_keys = [f"Player {chr(65 + i)}" for i in range(n_legs)]
    for i, r in df_view.iterrows():
        player_lines = [str(r.get(k, "")) for k in leg_keys if r.get(k)]
        first_name = player_lines[0] if player_lines else f"{n_legs}-leg parlay"
        sub = " · ".join(player_lines[1:]) if len(player_lines) > 1 else f"{n_legs}-leg combo"
        try:
            combined = float(r.get("Combined Score", 0.0))
        except Exception:
            combined = 0.0
        tiers: List[Tuple[str, str]] = []
        rank_str = str(r.get("Rank", ""))
        if "⭐" in rank_str:
            tiers.append(("gold", "Best Bet ⭐"))
        tiers.append(("info", f"{n_legs}-leg"))
        chips: List[Tuple[str, Any]] = [
            ("Est. Probability", r.get("Est. Probability", "—")),
            ("Implied Odds", r.get("Implied Odds", "—")),
        ]
        # Show every leg explicitly in foot for clarity.
        foot_bits = [f"<b>Leg {idx+1}:</b> {p}" for idx, p in enumerate(player_lines)]
        cards.append(_rbi_card_html(
            rank=(i + 1),
            name=first_name,
            sub=sub,
            score=combined,
            score_label="Combined",
            tiers=tiers,
            chips=chips,
            foot_bits=foot_bits,
        ))
    st.markdown(
        _RBI_MOBILE_CSS +
        '<div class="rbi-mobile"><div class="rbi-grid">'
        + "".join(cards) +
        '</div></div>',
        unsafe_allow_html=True,
    )
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
        "team_obp_l14": (0.320, "↑"),
        "sp_whip": (1.30, "↓"),
        "sp_bb9": (3.0, "↑"),
        "game_total": (8.5, "↑"),
        "bullpen_era_l10": (4.0, "↓"),
        "xwoba_l15": (0.320, "↑"),
        "xslg": (0.420, "↑"),
        "slg": (0.420, "↑"),
        "barrel_pct": (8.0, "↑"),
        "hard_hit_pct": (38.0, "↑"),
        "k_pct": (22.0, "↓"),
        "iso_l15": (0.160, "↑"),
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

    feat_df = pd.DataFrame(rows_out)
    st.markdown('<div class="rbi-desktop">', unsafe_allow_html=True)
    st.dataframe(feat_df, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # Mobile: render the same feature table as a compact stacked card so it
    # never overflows the viewport horizontally.
    feat_rows_html = []
    for _, fr in feat_df.iterrows():
        signal = str(fr.get("Signal", ""))
        tone_cls = "elite" if signal.startswith("✅") else "soft"
        feat_rows_html.append(
            '<div class="rbi-card" style="padding:8px 12px;">'
            '<div class="rbi-head" style="align-items:center;">'
            f'<div class="rbi-id"><div class="rbi-name" style="font-size:.92rem;">{fr.get("Feature","")}</div>'
            f'<div class="rbi-sub">Threshold {fr.get("Threshold","")}</div></div>'
            f'<div class="rbi-score" style="font-size:.92rem;">{fr.get("Value","—")}'
            f'<small style="text-transform:none;letter-spacing:0;">'
            f'<span class="rbi-tier {tone_cls}" style="margin-top:4px;">{signal}</span>'
            '</small></div>'
            '</div></div>'
        )
    st.markdown(
        _RBI_MOBILE_CSS +
        '<div class="rbi-mobile"><div class="rbi-grid">'
        + "".join(feat_rows_html) +
        '</div></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def render_rbi_model_page(
    schedule_df: pd.DataFrame | None = None,
    batters_df: pd.DataFrame | None = None,
    pitchers_df: pd.DataFrame | None = None,
    build_game_context_fn=None,
    clean_name_fn=None,
    norm_team_fn=None,
) -> None:
    """Render the RBI Edge Model page inside the main Streamlit app.

    When called with the host app's pre-loaded ``schedule_df`` and helpers
    (``build_game_context_fn``, ``clean_name_fn``, ``norm_team_fn``), the
    cascade is **confirmed → projected**. Without those helpers, falls back
    to direct ``statsapi``/``pybaseball`` pulls (confirmed only). When no
    rows can be built, a polished empty state is shown — never fake data.
    """
    st.markdown(
        """
<style>
@keyframes rbiPageScan {
    0%   { transform: translateX(-100%); opacity:0; }
    20%  { opacity:1; } 80% { opacity:1; }
    100% { transform: translateX(250%); opacity:0; }
}
.rbi-page-header {
    padding: 18px 22px; border-radius: 18px; margin-bottom: 14px;
    background: linear-gradient(125deg, #1a0808 0%, #3b0c0c 35%, #7f1d1d 65%, #b91c1c 100%);
    border: 1px solid rgba(251,191,36,.45);
    box-shadow: 0 8px 28px rgba(127,29,29,.40), 0 0 0 1px rgba(239,68,68,.12);
    position: relative; overflow: hidden;
}
.rbi-page-header::before {
    content:''; position:absolute; inset:0; pointer-events:none;
    background-image: radial-gradient(circle, rgba(251,191,36,.035) 1px, transparent 1px);
    background-size: 16px 16px;
}
.rbi-page-header::after {
    content:''; position:absolute; top:0; bottom:0; width:35%; pointer-events:none;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,.04), transparent);
    animation: rbiPageScan 6s ease-in-out infinite 1s;
}
.rbi-page-eyebrow {
    font-size: .68rem; font-weight: 800; letter-spacing: .18em;
    text-transform: uppercase; color: #fbbf24; margin-bottom: 6px;
    display: flex; align-items: center; gap: 7px;
    position: relative; z-index: 1;
}
.rbi-page-eyebrow::before {
    content: ''; width: 16px; height: 2px;
    background: #fbbf24; border-radius: 1px; display: inline-block;
}
.rbi-page-title {
    font-size: 1.55rem; font-weight: 900; color: #fde68a;
    letter-spacing: .01em; line-height: 1.1; margin: 0 0 8px 0;
    text-shadow: 0 0 20px rgba(251,191,36,.35), 0 2px 6px rgba(0,0,0,.55);
    position: relative; z-index: 1;
}
.rbi-page-sub {
    font-size: .88rem; color: #fecaca; line-height: 1.55; font-weight: 500;
    position: relative; z-index: 1;
}
.rbi-page-sub b { color: #fde68a; font-weight: 800; }
</style>
<div class="rbi-page-header">
  <div class="rbi-page-eyebrow">RBI Edge Model · Daily Prop Intelligence</div>
  <div class="rbi-page-title">⚾ Daily 2+ RBI Prop Targets</div>
  <div class="rbi-page-sub">
    Top targets scored by the RBI Edge Model — blending
    <b>Opportunity</b> (lineup slot, team OBP, opposing SP/bullpen, total)
    with <b>Skill</b> (xwOBA, xSLG gap, Barrel%, HardHit%, K%, ISO, RISP AVG) and a
    <b>Context multiplier</b> (platoon, park, temperature, team form, lineup stability).
    Tabs below: leaderboard, 2-leg parlays, 3-leg long shots, and a player deep dive.
  </div>
</div>
""",
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

    # --- Build slate: confirmed → projected (no demo/fake fallback) ---
    today = _dt.date.today()
    date_iso = today.strftime("%Y-%m-%d")
    season = today.year

    notices: List[str] = []
    live_df = pd.DataFrame()
    used_app_path = False

    # Try to pull market totals once. Missing key → empty map (silent); the
    # slate builder will compute Model Est. totals from app-native context.
    totals_map, totals_notice = _fetch_game_totals(date_iso)

    with st.spinner("Building today's RBI Edge slate…"):
        # Preferred path: host app injected schedule + helpers. This covers
        # BOTH confirmed and projected lineups via build_game_context, using
        # the same data sources as the Matchup/Heatmap pages. We treat this
        # as the *primary* path and do not fall through to a standalone pull
        # just because a non-critical enrichment raised — the duplicate-index
        # guard inside _build_slate_from_app now makes that path stable.
        if schedule_df is not None and build_game_context_fn is not None:
            try:
                live_df, app_notices = _build_slate_from_app(
                    schedule_df=schedule_df,
                    batters_df=batters_df if batters_df is not None else pd.DataFrame(),
                    pitchers_df=pitchers_df if pitchers_df is not None else pd.DataFrame(),
                    build_game_context_fn=build_game_context_fn,
                    clean_name_fn=clean_name_fn or (lambda s: str(s).lower()),
                    norm_team_fn=norm_team_fn or (lambda s: str(s)),
                    totals_map=totals_map,
                )
                notices.extend(app_notices)
                used_app_path = True
            except Exception as exc:
                # The app path is robust now (duplicate-safe lookups, per-game
                # try/except), so reaching here means a genuinely unexpected
                # error. Surface a small caption, not a scary warning, and
                # fall back to the standalone confirmed-only pull below.
                notices.append(f"Internal: slate builder hit an unexpected error ({exc}).")
                live_df = pd.DataFrame()

        # Standalone fallback only when the host app is not wired up at all.
        # We never fall back just because the app path produced zero rows —
        # zero rows is a real "no lineups yet" signal we render as an empty
        # state below, not a reason to re-pull the same data via statsapi.
        if not used_app_path and live_df.empty:
            standalone_df, sa_notices = _build_live_slate(date_iso, season)
            notices.extend(sa_notices)
            live_df = standalone_df

    if totals_notice:
        notices.append(totals_notice)

    scored = _score_slate(live_df)

    # --- Surface lineup-status banner (always visible, always accurate) ---
    if scored.empty:
        # Polished empty state — matches the style of other generators
        # ("No lineups posted yet…") — never synthesized rows.
        if schedule_df is None or len(schedule_df) == 0:
            st.warning(
                "🗓️ **No games on the slate.** Pick a different date or check back later — "
                "the RBI Edge Model needs an upcoming/in-progress MLB game to score hitters."
            )
        else:
            st.info(
                "📋 **Lineups not ready yet.** Confirmed lineups typically post 2–4 hours "
                "before first pitch, and projected lineups need a few recent completed games "
                "from each team. Use 🔄 Refresh data after lineups drop."
            )
    elif "lineup_status" in scored.columns:
        n_conf = int((scored["lineup_status"] == "Confirmed").sum())
        n_proj = int((scored["lineup_status"] == "Projected").sum())
        if n_conf and not n_proj:
            st.success(f"✅ **Confirmed lineups** — scoring {n_conf} hitters across today's slate.")
        elif n_proj and not n_conf:
            st.warning(
                f"📋 **Projected lineups** — confirmed lineups have not posted yet. "
                f"Scoring {n_proj} hitters using each team's most-used 9 from recent games. "
                "Refresh after confirmed lineups drop (typically 2–4 hours before first pitch)."
            )
        elif n_conf and n_proj:
            st.info(
                f"✅ {n_conf} confirmed · 📋 {n_proj} projected — mixed slate. "
                "Projected rows are flagged in the Key Flags column."
            )

    for n in notices:
        st.caption(n)

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
            "**Tier labels**: 🔥 Strong Edge ≥ 0.72 · ✅ Moderate Edge ≥ 0.58 · "
            "⚠️ Marginal ≥ 0.45 · ❌ Fade < 0.45"
        )
