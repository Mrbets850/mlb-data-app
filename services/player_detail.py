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

    rows.append(_row_from("L3 SZN vs SP (proxy)", splits.get("L10"), actual=False))
    rows.append(_row_from("L3 SZN vs team (proxy)", splits.get("L20"), actual=False))

    hand_label = "RHP" if (pitch_hand or "").upper().startswith("R") else \
                 ("LHP" if (pitch_hand or "").upper().startswith("L") else "Pitcher hand")
    rows.append(_row_from(f"’25-’26 vs {hand_label}", splits.get("TwoYear"), actual=True))
    rows.append(_row_from("’25-’26 All Games", splits.get("TwoYear"), actual=True))
    return rows


# --- Game log formatting ----------------------------------------------------

def format_game_log_rows(game_log: list[dict], limit: int = 10) -> list[dict]:
    """Format the last ``limit`` games as detail-dialog rows.

    Returns rows ordered most-recent first, with keys:
      date_short ('Apr 14'), opp_label ('@LAD' or 'vs CHC'), score, ab, h,
      hr, tb, rbi.
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
            "score": r.get("result") or "",
            "ab": int(r.get("ab") or 0),
            "h": int(r.get("h") or 0),
            "hr": int(r.get("hr") or 0),
            "tb": int(r.get("tb") or 0),
            "rbi": int(r.get("rbi") or 0),
        })
    return out
