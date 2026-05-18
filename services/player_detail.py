"""Player detail card data + pure helpers.

The matchup tab renders a per-player detail dialog (dark-themed mobile card)
when a hitter tile is tapped. This module hosts the data-fetch + computation
that powers that dialog so the helpers can be unit-tested without standing up
Streamlit.

Public surface
--------------
- ``fetch_batter_game_log(player_id, season)`` -> ``list[dict]``
- ``build_split_windows(game_log, season, end_date)`` -> ``dict``
- ``compute_pitcher_rating(pitcher_row)`` -> ``dict``
- ``build_bvp_rows(...)`` -> ``list[dict]``
- ``format_game_log_rows(game_log, opponent_map=None, limit=10)`` -> ``list[dict]``
- ``headshot_url(player_id)`` -> ``str | None``
- ``team_logo_url(team_abbr)`` -> ``str | None``
- ``short_opp_abbr(team_abbr, max_len=3)`` -> ``str``
- ``filter_log_for_split(game_log, split, season, end_date, opp_team=None)`` -> ``list[dict]``

All helpers degrade gracefully — missing inputs return empty/None values
rather than raising so the dialog can show "—" cells where data is absent.
"""

from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Any, Iterable

import requests

_BASE = "https://statsapi.mlb.com/api/v1"
_HEADERS = {"User-Agent": "mlb-edge-app/1.0 (player_detail)"}

# Public MLB headshot CDN. No auth required, no per-request rate concerns
# for typical slate volumes — the browser fetches images directly, the app
# only emits URLs. Width is fixed at 120 so we don't pull oversized images
# on mobile.
_HEADSHOT_TMPL = (
    "https://img.mlbstatic.com/mlb-photos/image/upload/"
    "w_120,q_auto:best/v1/people/{pid}/headshot/67/current"
)


# Public MLB team-logo CDN, served as SVG. Same no-auth/free constraint
# as headshots — the browser fetches and caches. Keyed by numeric team id.
_TEAM_LOGO_TMPL = "https://www.mlbstatic.com/team-logos/{tid}.svg"

# Abbreviation -> MLBAM team id. Kept inside the service so the helper is
# self-contained for tests and for any caller that doesn't have access to
# app.py's TEAM_INFO dict.
_TEAM_ABBR_TO_ID: dict[str, int] = {
    "ARI": 109, "ATL": 144, "BAL": 110, "BOS": 111, "CHC": 112, "CWS": 145,
    "CIN": 113, "CLE": 114, "COL": 115, "DET": 116, "HOU": 117, "KC": 118,
    "LAA": 108, "LAD": 119, "MIA": 146, "MIL": 158, "MIN": 142, "NYM": 121,
    "NYY": 147, "ATH": 133, "OAK": 133, "PHI": 143, "PIT": 134, "SD": 135,
    "SF": 137, "SEA": 136, "STL": 138, "TB": 139, "TEX": 140, "TOR": 141,
    "WSH": 120, "WSN": 120, "CHW": 145, "KCR": 118, "SDP": 135, "SFG": 137,
    "TBR": 139,
}


def team_logo_url(team_abbr: str | None) -> str | None:
    """Return the public MLB team-logo SVG URL for an abbreviation, or None.

    Pure URL builder — no I/O. The browser fetches the SVG directly from the
    public CDN; callers fall back to the bare abbreviation if the image
    fails to load.
    """
    if not team_abbr:
        return None
    abbr = str(team_abbr).strip().upper()
    if not abbr:
        return None
    tid = _TEAM_ABBR_TO_ID.get(abbr)
    if not tid:
        return None
    return _TEAM_LOGO_TMPL.format(tid=tid)


def short_opp_abbr(team_abbr: str | None, max_len: int = 3) -> str:
    """Return a compact uppercased team abbreviation for chip display.

    Mobile bar chips have very little horizontal room (one logo wide). When
    we have to fall back to text (unknown team or broken image), the chip
    must still fit cleanly — so trim to at most ``max_len`` chars and
    normalize known long aliases (e.g. ``WSN`` -> ``WSH``).
    """
    if not team_abbr:
        return ""
    s = str(team_abbr).strip().upper()
    if not s:
        return ""
    aliases = {"WSN": "WSH", "CHW": "CWS", "KCR": "KC", "SDP": "SD",
               "SFG": "SF", "TBR": "TB", "ATH": "OAK"}
    s = aliases.get(s, s)
    return s[: max(1, int(max_len))]


def headshot_url(player_id: int | None) -> str | None:
    """Return the public MLB headshot URL for a player MLBAM id, or None.

    Pure URL builder — does no network I/O. Caller renders an <img> tag
    and the browser handles fetching/caching; on 404 the caller's fallback
    avatar takes over via CSS.
    """
    if not player_id:
        return None
    try:
        pid = int(player_id)
    except Exception:
        return None
    if pid <= 0:
        return None
    return _HEADSHOT_TMPL.format(pid=pid)


# --- Game log fetch ---------------------------------------------------------

def fetch_batter_game_log(player_id: int | None, season: int,
                          *, http_get: Any = None) -> list[dict]:
    """Pull a batter's per-game hitting log for one season as a list of dicts.

    Returns rows ordered oldest -> newest with keys:
      date (ISO str), opponent (team abbr), is_home (bool), result (str like
      'W 5-3'), ab, h, hr, rbi, bb, k, pa, tb, doubles, triples, avg, sb, sf,
      hbp.

    Network failures and empty payloads return ``[]`` — never raises.
    """
    if not player_id:
        return []
    try:
        pid = int(player_id)
    except Exception:
        return []
    getter = http_get or _default_get
    try:
        payload = getter(
            f"{_BASE}/people/{pid}/stats",
            params={"stats": "gameLog", "group": "hitting",
                    "season": int(season), "sportId": 1},
            timeout=10,
        )
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    rows: list[dict] = []
    for s in (payload.get("stats") or []):
        for sp in (s.get("splits") or []):
            stat = sp.get("stat") or {}
            opp = (sp.get("opponent") or {}).get("abbreviation") \
                or (sp.get("opponent") or {}).get("teamCode") \
                or (sp.get("opponent") or {}).get("name") \
                or ""
            is_home = bool(sp.get("isHome", False))
            d = sp.get("date") or (sp.get("game") or {}).get("date") or ""
            game = sp.get("game") or {}
            result = _format_game_result(game)
            def _i(k):
                try: return int(stat.get(k) or 0)
                except Exception: return 0
            ab  = _i("atBats")
            h   = _i("hits")
            bb  = _i("baseOnBalls")
            hbp = _i("hitByPitch")
            sf  = _i("sacFlies")
            k   = _i("strikeOuts")
            pa  = _i("plateAppearances") or (ab + bb + hbp + sf)
            hr  = _i("homeRuns")
            rbi = _i("rbi")
            doubles = _i("doubles")
            triples = _i("triples")
            sb = _i("stolenBases")
            tb = h + doubles + 2 * triples + 3 * hr  # singles + 2B + 3B + HR
            avg = (h / ab) if ab > 0 else None
            rows.append({
                "date": d,
                "opponent": opp,
                "is_home": is_home,
                "result": result,
                "ab": ab, "h": h, "hr": hr, "rbi": rbi, "bb": bb, "k": k,
                "pa": pa, "tb": tb, "doubles": doubles, "triples": triples,
                "avg": avg, "sb": sb, "sf": sf, "hbp": hbp,
            })
    rows.sort(key=lambda r: r["date"] or "")
    return rows


def _default_get(url: str, *, params=None, timeout: int = 10):
    r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json() or {}


def _format_game_result(game: dict) -> str:
    """Return a short 'W 5-3' / 'L 1-2' string from a game payload, or ''.

    Tolerant of missing fields — returns '' rather than raising.
    """
    if not isinstance(game, dict):
        return ""
    home = game.get("teams", {}).get("home", {}) if isinstance(game.get("teams"), dict) else {}
    away = game.get("teams", {}).get("away", {}) if isinstance(game.get("teams"), dict) else {}
    hs = home.get("score") if isinstance(home, dict) else None
    as_ = away.get("score") if isinstance(away, dict) else None
    if hs is None or as_ is None:
        return ""
    try:
        hs = int(hs); as_ = int(as_)
    except Exception:
        return ""
    return f"{max(hs, as_)}-{min(hs, as_)}"


# --- Splits aggregation -----------------------------------------------------

def aggregate_window(rows: Iterable[dict]) -> dict:
    """Public alias of :func:`_agg_window` for callers outside this module."""
    return _agg_window(rows)


def _agg_window(rows: Iterable[dict]) -> dict:
    """Aggregate a window of game-log rows into split totals + rates.

    Returns dict with PA, AB, H, HR, BB, K, TB, AVG, OBP, SLG, OPS, H_pct,
    HR_pct, BB_pct. Missing data -> None.
    """
    rows = list(rows)
    pa = sum(int(r.get("pa") or 0) for r in rows)
    ab = sum(int(r.get("ab") or 0) for r in rows)
    h  = sum(int(r.get("h") or 0) for r in rows)
    hr = sum(int(r.get("hr") or 0) for r in rows)
    bb = sum(int(r.get("bb") or 0) for r in rows)
    k  = sum(int(r.get("k") or 0) for r in rows)
    hbp = sum(int(r.get("hbp") or 0) for r in rows)
    sf  = sum(int(r.get("sf") or 0) for r in rows)
    tb = sum(int(r.get("tb") or 0) for r in rows)

    avg = (h / ab) if ab > 0 else None
    obp_den = ab + bb + hbp + sf
    obp = ((h + bb + hbp) / obp_den) if obp_den > 0 else None
    slg = (tb / ab) if ab > 0 else None
    ops = (obp + slg) if (obp is not None and slg is not None) else None
    h_pct = (100.0 * h / pa) if pa > 0 else None
    hr_pct = (100.0 * hr / pa) if pa > 0 else None
    bb_pct = (100.0 * bb / pa) if pa > 0 else None
    k_pct = (100.0 * k / pa) if pa > 0 else None

    return {
        "games": len(rows),
        "PA": pa, "AB": ab, "H": h, "HR": hr, "BB": bb, "K": k, "TB": tb,
        "AVG": avg, "OBP": obp, "SLG": slg, "OPS": ops,
        "H%": h_pct, "HR%": hr_pct, "BB%": bb_pct, "K%": k_pct,
    }


def build_split_windows(game_log: list[dict], season: int,
                        end_date: _date | None = None) -> dict:
    """Build the split-chip windows the detail dialog displays.

    Keys returned: ``L5``, ``L10``, ``L20``, ``Season`` (current season), and
    ``TwoYear`` ('25-'26 style). Each value is an :func:`_agg_window` dict.
    Empty windows return zero-row aggregates rather than ``None``.
    """
    if end_date is None:
        end_date = _date.today()

    def _on_or_before(rows, d):
        out = []
        for r in rows:
            rd = r.get("date") or ""
            if not rd:
                continue
            try:
                rdate = _date.fromisoformat(rd[:10])
            except Exception:
                continue
            if rdate <= d:
                out.append(r)
        return out

    filtered = _on_or_before(game_log, end_date)
    cur_season = [r for r in filtered if (r.get("date") or "")[:4] == str(season)]
    two_year = [r for r in filtered if (r.get("date") or "")[:4] in (str(season), str(season - 1))]

    return {
        "L5":  _agg_window(filtered[-5:]),
        "L10": _agg_window(filtered[-10:]),
        "L20": _agg_window(filtered[-20:]),
        "Season": _agg_window(cur_season),
        "TwoYear": _agg_window(two_year),
    }


# --- Opposing pitcher rating ------------------------------------------------

def compute_pitcher_rating(pitcher_row: dict | None) -> dict:
    """Score the opposing pitcher's HR vulnerability on a 0-100 scale.

    Inputs are taken from the Savant-style pitcher row used elsewhere in the
    app (xSLG, Barrel%, HardHit%, HR, K%, ERA, WHIP). Missing fields fall back
    to league-average proxies. Returns dict with:

      score: int 0-100 (higher == more vulnerable to power)
      tier: 'Elite' / 'Above-Avg' / 'Average' / 'Risky' / 'Juicy'
      bullets: list of short strings describing the top vulnerabilities
      available: bool — False when we had no pitcher row to score at all
    """
    if pitcher_row is None:
        return {"score": 50, "tier": "Average", "bullets": [], "available": False}

    def _f(key, default):
        v = pitcher_row.get(key)
        try:
            if v is None:
                return default
            f = float(v)
            if f != f:  # NaN
                return default
            return f
        except Exception:
            return default

    xslg    = _f("xSLG", 0.410)
    barrel  = _f("Barrel%", 8.0)
    hardhit = _f("HardHit%", 38.0)
    hr      = _f("HR", 0.0)
    kpct    = _f("K%", 22.0)
    era     = _f("ERA", 4.20)
    whip    = _f("WHIP", 1.30)

    # Vulnerability score: higher = juicier matchup for the hitter.
    vuln = 50.0
    vuln += (xslg - 0.380) * 140
    vuln += (barrel - 7.0) * 2.5
    vuln += (hardhit - 36.0) * 0.5
    vuln += hr * 0.4
    vuln += (era - 4.00) * 4.0
    vuln += (whip - 1.25) * 12.0
    vuln -= (kpct - 22.0) * 0.6

    score = int(max(0, min(100, round(vuln))))
    if score >= 75:
        tier = "Juicy"
    elif score >= 60:
        tier = "Risky"
    elif score >= 45:
        tier = "Average"
    elif score >= 30:
        tier = "Above-Avg"
    else:
        tier = "Elite"

    bullets = []
    if xslg >= 0.440:
        bullets.append(f"xSLG allowed {xslg:.3f}")
    if barrel >= 9.5:
        bullets.append(f"{barrel:.1f}% barrels against")
    if hardhit >= 42.0:
        bullets.append(f"{hardhit:.1f}% hard-hit allowed")
    if hr >= 15:
        bullets.append(f"{int(hr)} HR allowed")
    if era >= 4.50:
        bullets.append(f"ERA {era:.2f}")
    if kpct <= 20.0:
        bullets.append(f"K% {kpct:.1f}% (low)")
    if not bullets:
        bullets.append("Average-ish across HR-relevant inputs")
    return {"score": score, "tier": tier, "bullets": bullets[:4], "available": True}


# --- Player grade (A-D) -----------------------------------------------------
#
# A composite letter grade shown next to the player's name in the detail
# header. Driven by inputs we already have on the slate row + matchup:
#   - Matchup score (the tile metric — power-weighted composite, ~50-200 scale)
#   - Pitcher vulnerability score (0-100; higher = juicier for the hitter)
#   - Recent power proxies: OPS, ISO, HR%, Barrel%
# Each signal contributes a sub-score on the same 0-100 scale; missing inputs
# are simply skipped (the average is taken over present signals). Conservative
# by design — when we have no real signal at all, we return "C" rather than
# faking an opinion.
#
# Cut points were chosen so a typical slate produces a believable spread:
#   A >= 78, B >= 62, C >= 45, else D.

_GRADE_BAND_STYLES: dict[str, tuple[str, str]] = {
    # grade -> (background, text)
    "A": ("#15803d", "#ecfdf5"),  # green
    "B": ("#0369a1", "#e0f2fe"),  # blue
    "C": ("#ca8a04", "#0f172a"),  # amber/yellow
    "D": ("#b91c1c", "#fef2f2"),  # red
}


def _scale(value: float | None, low: float, high: float) -> float | None:
    """Linearly scale ``value`` from [low, high] into [0, 100], clamped.

    Returns None when ``value`` is missing so the caller can skip it from
    the average instead of dragging the composite toward 0.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    if high == low:
        return 50.0
    pct = 100.0 * (f - low) / (high - low)
    return max(0.0, min(100.0, pct))


def compute_player_grade(*, matchup: Any = None, pitcher_score: Any = None,
                         ops: Any = None, iso: Any = None,
                         hr_pct: Any = None, barrel_pct: Any = None,
                         xwoba: Any = None) -> dict:
    """Return a letter grade A-D for one batter on the current slate.

    All inputs are optional — the composite averages whatever signals are
    available. With zero signals we return a neutral "C" so the badge still
    renders, but ``available`` is False so the caller can suppress it.

    Returns ``{grade, score, available, background, color, css_class}``.

    Thresholds (composite, 0-100):
      A >= 72  · top of slate, strong matchup + strong recent form
      B >= 58  · above average
      C >= 42  · league-ish
      D <  42  · weak matchup, weak form, or both
    """
    components: list[float] = []

    # Each scale is anchored so a league-average input maps to ~50, an elite
    # input toward 100, and a weak input toward 0. Anchors are calibrated
    # against typical MLB slate distributions so composites cluster sensibly.
    s_match = _scale(matchup, low=80.0, high=180.0)
    if s_match is not None:
        components.append(s_match)
    s_pitch = _scale(pitcher_score, low=20.0, high=85.0)
    if s_pitch is not None:
        components.append(s_pitch)
    s_ops = _scale(ops, low=0.560, high=0.880)
    if s_ops is not None:
        components.append(s_ops)
    s_iso = _scale(iso, low=0.090, high=0.250)
    if s_iso is not None:
        components.append(s_iso)
    s_hr = _scale(hr_pct, low=1.0, high=6.5)
    if s_hr is not None:
        components.append(s_hr)
    s_brl = _scale(barrel_pct, low=4.0, high=12.0)
    if s_brl is not None:
        components.append(s_brl)
    s_xwoba = _scale(xwoba, low=0.280, high=0.380)
    if s_xwoba is not None:
        components.append(s_xwoba)

    if not components:
        bg, fg = _GRADE_BAND_STYLES["C"]
        return {"grade": "C", "score": 50, "available": False,
                "background": bg, "color": fg, "css_class": "pdc-grade-C"}

    score = sum(components) / len(components)
    if score >= 72:
        grade = "A"
    elif score >= 58:
        grade = "B"
    elif score >= 42:
        grade = "C"
    else:
        grade = "D"
    bg, fg = _GRADE_BAND_STYLES[grade]
    return {
        "grade": grade,
        "score": int(round(score)),
        "available": True,
        "background": bg,
        "color": fg,
        "css_class": f"pdc-grade-{grade}",
    }


# --- Batter vs Pitcher matchup table ---------------------------------------

def build_bvp_rows(*, batter_row: dict | None, pitcher_row: dict | None,
                   season_splits: dict | None,
                   bat_side: str = "", pitch_hand: str = "") -> list[dict]:
    """Compose the BvP matchup table rows shown in the detail dialog.

    Without true head-to-head splits (MLB StatsAPI exposes them but is
    expensive to query per slate), we derive proxies from data we already
    have. Every row carries an ``actual`` flag so the UI can mark proxies.

    Returns list of dicts with keys: ``label``, ``PA``, ``H%``, ``SLG``,
    ``HR%``, ``BB%``, ``actual``.
    """
    rows: list[dict] = []
    splits = season_splits or {}

    def _row_from(label: str, agg: dict | None, actual: bool = True) -> dict:
        if not agg or not agg.get("PA"):
            return {"label": label, "PA": 0, "H%": None, "SLG": None,
                    "HR%": None, "BB%": None, "actual": actual}
        return {
            "label": label,
            "PA": int(agg.get("PA") or 0),
            "H%": agg.get("H%"),
            "SLG": agg.get("SLG"),
            "HR%": agg.get("HR%"),
            "BB%": agg.get("BB%"),
            "actual": actual,
        }

    # Two concise rows: `vs SP` is the per-start proxy (recent form is the
    # closest signal we have without a true head-to-head query), and
    # `vs Team` is the season-level look against the opposing team's
    # handedness pool (bullpen + non-SP exposure).
    rows.append(_row_from("vs SP", splits.get("L10"), actual=False))
    rows.append(_row_from("vs Team", splits.get("TwoYear"), actual=True))
    return rows


# --- Game log formatting ----------------------------------------------------

# --- Heat-map classification for the detail dialog --------------------------
#
# The interactive detail modal surfaces percentages, rates, and rating values.
# To match the look of the original heat-map cards, every metric cell gets a
# semantic band (good / okay / bad / neutral) plus the CSS color triplet used
# to paint the cell. Pure functions — no Streamlit, no formatting — so the
# tests can pin thresholds and orientation without standing up the dialog.
#
# Thresholds align with baseball-betting intuition:
#   - hit rate, OBP, SLG, OPS, AVG: higher is better
#   - HR%, ISO, Barrel%, HR/FB%, xwOBA, hard-hit%: higher is better
#   - BB%: higher is better (walks help OPS, but only mildly)
#   - K%: lower is better (REVERSED orientation)
#   - pitcher rating score (Vulnerability): higher == juicier for the hitter
#
# Edge inputs (None, NaN, non-numeric) return ``neutral`` so the cell stays
# styled-but-readable rather than collapsing the layout.

# Each spec: (good_at_or_above, okay_at_or_above, reverse).
# - reverse=False: >= good is "good", >= okay is "okay", below okay is "bad"
# - reverse=True : <= good is "good", <= okay is "okay", above okay is "bad"
_METRIC_THRESHOLDS: dict[str, tuple[float, float, bool]] = {
    # Hitting rates (percent units: 0–100)
    "H%":   (28.0, 22.0, False),
    "HR%":  (4.5,  2.5,  False),
    "BB%":  (10.0, 7.0,  False),
    "K%":   (18.0, 24.0, True),
    # Slash-line stats (decimal units, e.g. .500 SLG)
    "AVG":  (0.280, 0.240, False),
    "OBP":  (0.350, 0.310, False),
    "SLG":  (0.470, 0.400, False),
    "OPS":  (0.820, 0.720, False),
    "ISO":  (0.200, 0.150, False),
    "xwOBA": (0.360, 0.320, False),
    # Statcast-ish quality metrics (percent units)
    "Brl/BIP%": (10.0, 7.0, False),
    "Barrel%":  (10.0, 7.0, False),
    "HR/FB%":   (16.0, 11.0, False),
    "HH%":      (42.0, 36.0, False),
    "HardHit%": (42.0, 36.0, False),
    "SweetSpot%": (36.0, 32.0, False),
    "PullAir%": (22.0, 15.0, False),
    # Matchup / rating score (0–100 vulnerability scale — higher == better for hitter)
    "PitcherScore": (70.0, 50.0, False),
    "Matchup":      (140.0, 115.0, False),
}

# Visual palette — kept in sync with the dark-card theme. Backgrounds carry
# enough saturation to read as semantic without drowning the foreground text.
# The text color is chosen for luminance contrast against each background.
_BAND_STYLES: dict[str, tuple[str, str]] = {
    # band -> (background CSS color, text CSS color)
    "good":    ("#15803d", "#ecfdf5"),  # dark green / mint
    "okay":    ("#ca8a04", "#0f172a"),  # amber / near-black (yellow needs dark text)
    "bad":     ("#b91c1c", "#fef2f2"),  # dark red / soft white
    "neutral": ("",        ""),         # no override — inherits parent
}


def _coerce_float(value: Any) -> float | None:
    """Return ``value`` as a float, or None if missing/NaN/non-numeric.

    Tolerates pandas NaN without importing pandas.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def classify_metric(metric: str, value: Any) -> str:
    """Return one of ``'good' | 'okay' | 'bad' | 'neutral'`` for a metric value.

    Unknown metrics and missing values return ``'neutral'`` so callers don't
    paint a cell they shouldn't. Metric orientation (higher better vs lower
    better) is encoded in :data:`_METRIC_THRESHOLDS`.
    """
    f = _coerce_float(value)
    if f is None:
        return "neutral"
    spec = _METRIC_THRESHOLDS.get(metric)
    if spec is None:
        return "neutral"
    good_at, okay_at, reverse = spec
    if reverse:
        if f <= good_at:
            return "good"
        if f <= okay_at:
            return "okay"
        return "bad"
    if f >= good_at:
        return "good"
    if f >= okay_at:
        return "okay"
    return "bad"


def heatmap_style_for(metric: str, value: Any) -> dict[str, str]:
    """Return CSS-ready styling for a metric cell.

    Keys:
      band       : 'good' | 'okay' | 'bad' | 'neutral'
      background : CSS color or '' (neutral)
      color      : CSS text color or '' (neutral)
      css_class  : 'pdc-hm-good' / 'pdc-hm-okay' / 'pdc-hm-bad' / '' (neutral)

    Designed so the renderer can either inject inline ``style="..."`` for
    one-off use or attach a class for shared CSS rules.
    """
    band = classify_metric(metric, value)
    bg, fg = _BAND_STYLES.get(band, ("", ""))
    css_class = "" if band == "neutral" else f"pdc-hm-{band}"
    return {
        "band": band,
        "background": bg,
        "color": fg,
        "css_class": css_class,
    }


def classify_pitcher_tier(tier: str | None) -> str:
    """Map a pitcher rating tier label to a heat-map band.

    The rating is *vulnerability* — "Juicy" means the matchup is good FOR
    THE HITTER, so it gets the ``good`` band on the modal even though a
    juicy pitcher is bad for the pitcher.
    """
    t = (tier or "").strip().lower()
    if t in ("juicy", "risky"):
        return "good"
    if t in ("average",):
        return "okay"
    if t in ("above-avg", "above avg", "elite"):
        return "bad"
    return "neutral"


def format_game_log_rows(game_log: list[dict], limit: int = 10) -> list[dict]:
    """Format the last ``limit`` games as detail-dialog rows.

    Returns rows ordered most-recent first, with keys:
      date_short ('Apr 14'), opp_label ('@LAD' or 'vs CHC'), score, ab, h,
      hr, tb, rbi, k (strikeouts), opp ('LAD'), opp_logo (URL or None).
    """
    if not game_log:
        return []
    out: list[dict] = []
    for r in game_log[-limit:][::-1]:
        d = r.get("date") or ""
        try:
            dd = _date.fromisoformat(d[:10])
            date_short = dd.strftime("%b %d")
        except Exception:
            date_short = d[:10]
        opp = r.get("opponent") or ""
        opp_label = (f"@{opp}" if not r.get("is_home") else f"vs {opp}") if opp else ""
        out.append({
            "date_short": date_short,
            "opp_label": opp_label,
            "opp": opp,
            "opp_logo": team_logo_url(opp),
            "score": r.get("result") or "",
            "ab": int(r.get("ab") or 0),
            "h": int(r.get("h") or 0),
            "hr": int(r.get("hr") or 0),
            "tb": int(r.get("tb") or 0),
            "rbi": int(r.get("rbi") or 0),
            "k": int(r.get("k") or 0),
        })
    return out


# --- Split-window filtering for interactive modal ---------------------------

# Map UI split labels to the keys used by :func:`build_split_windows` so the
# dialog can drive a single source of truth and the UI just passes the label
# the user tapped (e.g. "L10", "2026", "’25-’26").
_SPLIT_LABEL_TO_KEY: dict[str, str] = {
    "L5": "L5",
    "L10": "L10",
    "L20": "L20",
    "Season": "Season",
    "TwoYear": "TwoYear",
    # Common UI variants — the modal renders "2026" for current season and
    # "’25-’26" for the two-year window. H2H falls back to L10 because true
    # head-to-head data is not pulled per slate (proxy, like build_bvp_rows).
    "H2H": "L10",
}


def split_label_to_key(label: str | None) -> str:
    """Normalize a UI split chip label to a ``build_split_windows`` key.

    The dialog accepts variants like "2026" or "’25-’26"; this helper resolves
    them to the canonical aggregate key. Unknown labels fall back to ``L10``
    so a typo never produces an empty modal.
    """
    if not label:
        return "L10"
    raw = str(label).strip()
    if raw in _SPLIT_LABEL_TO_KEY:
        return _SPLIT_LABEL_TO_KEY[raw]
    # The current-season chip is rendered as the season year ("2026"); the
    # two-year chip uses curly quotes ("’25-’26"). Detect both shapes.
    if raw.isdigit() and len(raw) == 4:
        return "Season"
    if ("25" in raw and "26" in raw) or "-" in raw or "–" in raw:
        return "TwoYear"
    return "L10"


def filter_log_for_split(game_log: list[dict], split: str,
                         season: int, end_date: _date | None = None,
                         *, opp_team: str | None = None) -> list[dict]:
    """Return the subset of ``game_log`` rows that belongs to ``split``.

    - ``L5`` / ``L10`` / ``L20``: last N games on or before ``end_date``.
    - ``Season`` / "2026": current-season rows only.
    - ``TwoYear`` / "’25-’26": current + prior season rows.
    - ``H2H``: rows whose opponent matches ``opp_team`` if supplied,
      otherwise falls back to the L10 window so the modal never goes blank.

    Returned rows preserve the underlying chronological order
    (oldest -> newest) so downstream consumers can take ``[-N:]`` slices.
    """
    if not game_log:
        return []
    if end_date is None:
        end_date = _date.today()

    def _on_or_before(rows):
        out = []
        for r in rows:
            rd = r.get("date") or ""
            if not rd:
                continue
            try:
                if _date.fromisoformat(rd[:10]) <= end_date:
                    out.append(r)
            except Exception:
                continue
        return out

    filtered = _on_or_before(game_log)
    key = split_label_to_key(split)

    if key == "L5":
        return filtered[-5:]
    if key == "L10":
        if split == "H2H" and opp_team:
            opp_norm = str(opp_team).strip().upper()
            h2h = [r for r in filtered if str(r.get("opponent") or "").upper() == opp_norm]
            return h2h or filtered[-10:]
        return filtered[-10:]
    if key == "L20":
        return filtered[-20:]
    if key == "Season":
        return [r for r in filtered if (r.get("date") or "")[:4] == str(season)]
    if key == "TwoYear":
        return [r for r in filtered if (r.get("date") or "")[:4]
                in (str(season), str(season - 1))]
    return filtered[-10:]
