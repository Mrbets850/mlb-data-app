"""Smoke checks for ``rbi_model`` — runs without Streamlit secrets or network.

Validates the regressions from the live-app incident on 2026-05-13:

  - ``DataFrame index must be unique for orient='index'`` no longer raises
    when ``batters_df``/``pitchers_df`` contain duplicate ``name_key`` rows.
  - The app-injected slate builder produces real rows for projected-only
    slates (the matchup-page case 2-4 hours before first pitch).
  - Mixed confirmed + projected slates produce both kinds of rows tagged
    correctly and parlay generation can consume them.
  - Missing Odds API key uses ``Model Est.`` totals, not the old hardcoded
    8.5 default, and totals land in a sane 6.5-12.5 band.
  - The package never references a "demo" / "fake" code path.

Run with: ``python scripts/smoke_rbi_model.py``.
"""
from __future__ import annotations

import os
import sys
import types

# Stub Streamlit so we can import rbi_model without a running server.
# Only the surface used at import-time is needed; rendering helpers are not
# exercised here.
_stub_st = types.ModuleType("streamlit")

class _Secrets(dict):
    def get(self, k, default=None): return super().get(k, default)

_stub_st.secrets = _Secrets()

class _CacheData:
    def __call__(self, *a, **k):
        def deco(fn): return fn
        return deco
    def clear(self): pass

_stub_st.cache_data = _CacheData()
sys.modules["streamlit"] = _stub_st

# Make ``import rbi_model`` resolve from repo root regardless of cwd.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pandas as pd  # noqa: E402

import rbi_model as rm  # noqa: E402


def _make_batters_with_dupes() -> pd.DataFrame:
    """Two rows for one player (e.g. multi-stint) + one normal row."""
    return pd.DataFrame([
        {"Name": "Aaron Judge", "Team": "NYY", "name_key": "aaron judge", "team_key": "NYY",
         "xwOBA": 0.430, "xSLG": 0.620, "SLG": 0.580, "Barrel%": 22.0, "HardHit%": 55.0,
         "K%": 24.0, "ISO": 0.280, "OBP": 0.420, "bat_side": "R"},
        {"Name": "Aaron Judge", "Team": "NYY", "name_key": "aaron judge", "team_key": "NYY",
         "xwOBA": 0.420, "xSLG": 0.600, "SLG": 0.560, "Barrel%": 20.0, "HardHit%": 53.0,
         "K%": 25.0, "ISO": 0.260, "OBP": 0.410, "bat_side": "R"},
        {"Name": "Juan Soto", "Team": "NYM", "name_key": "juan soto", "team_key": "NYM",
         "xwOBA": 0.410, "xSLG": 0.560, "SLG": 0.530, "Barrel%": 18.0, "HardHit%": 50.0,
         "K%": 18.0, "ISO": 0.250, "OBP": 0.430, "bat_side": "L"},
    ])


def _make_pitchers_with_dupes() -> pd.DataFrame:
    return pd.DataFrame([
        {"Name": "Gerrit Cole", "Team": "NYY", "name_key": "gerrit cole",
         "WHIP": 1.05, "ERA": 2.95, "BB/9": 2.0},
        {"Name": "Gerrit Cole", "Team": "NYY", "name_key": "gerrit cole",
         "WHIP": 1.10, "ERA": 3.05, "BB/9": 2.1},
        {"Name": "Kodai Senga", "Team": "NYM", "name_key": "kodai senga",
         "WHIP": 1.20, "ERA": 3.40, "BB/9": 3.2},
    ])


def _fake_schedule() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_pk": 1, "game_time_utc": "2026-05-13T23:05:00Z",
         "home_team": "Yankees", "home_abbr": "NYY", "home_id": 147,
         "away_team": "Mets", "away_abbr": "NYM", "away_id": 121,
         "home_probable": "Gerrit Cole", "away_probable": "Kodai Senga",
         "park_factor": 105},
    ])


def _ctx_projected_both():
    return {
        "weather": {"temp_f": 78.0},
        "away_lineup": pd.DataFrame([
            {"player_name": "Juan Soto", "lineup_spot": 2, "bat_side": "L",
             "opposing_pitch_hand": "R"},
        ]),
        "home_lineup": pd.DataFrame([
            {"player_name": "Aaron Judge", "lineup_spot": 3, "bat_side": "R",
             "opposing_pitch_hand": "R"},
        ]),
        "away_status": "Projected",
        "home_status": "Projected",
    }


def _ctx_mixed():
    return {
        "weather": {"temp_f": 78.0},
        "away_lineup": pd.DataFrame([
            {"player_name": "Juan Soto", "lineup_spot": 2, "bat_side": "L",
             "opposing_pitch_hand": "R"},
        ]),
        "home_lineup": pd.DataFrame([
            {"player_name": "Aaron Judge", "lineup_spot": 3, "bat_side": "R",
             "opposing_pitch_hand": "R"},
        ]),
        "away_status": "Confirmed",
        "home_status": "Projected",
    }


def _ctx_one_sparse():
    """One side has lineup, the other side is not posted — page must still render."""
    return {
        "weather": {"temp_f": 78.0},
        "away_lineup": pd.DataFrame(),
        "home_lineup": pd.DataFrame([
            {"player_name": "Aaron Judge", "lineup_spot": 3, "bat_side": "R",
             "opposing_pitch_hand": "R"},
        ]),
        "away_status": "Not Posted",
        "home_status": "Projected",
    }


def _clean(s): return str(s or "").strip().lower()
def _norm(s): return str(s or "").upper()


def test_duplicate_index_does_not_raise():
    """Regression: ``DataFrame index must be unique for orient='index'``."""
    bat = _make_batters_with_dupes()
    pit = _make_pitchers_with_dupes()
    # Direct check — the bug was here.
    bidx = rm._index_by_name_key(bat)
    pidx = rm._index_by_name_key(pit)
    assert "aaron judge" in bidx and "juan soto" in bidx, bidx
    assert "gerrit cole" in pidx and "kodai senga" in pidx, pidx
    # First-row-wins dedup.
    assert bidx["aaron judge"]["xwOBA"] == 0.430
    print("  [OK] duplicate name_key does not raise; first-row dedup wins")


def test_projected_only_slate_produces_rows():
    df, notices = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_projected_both(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    assert not df.empty, "projected-only slate must produce rows"
    assert set(df["lineup_status"]) == {"Projected"}
    assert "Model Est. Total" in " ".join(notices) or any("Model Est." in n for n in notices), notices
    print(f"  [OK] projected-only slate -> {len(df)} rows, notices: {notices}")


def test_mixed_confirmed_and_projected_slate():
    df, _ = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_mixed(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    assert not df.empty
    statuses = set(df["lineup_status"])
    assert "Confirmed" in statuses and "Projected" in statuses, statuses
    print(f"  [OK] mixed slate -> {len(df)} rows, statuses={statuses}")


def test_sparse_side_does_not_blank_page():
    df, _ = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_one_sparse(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    assert not df.empty, "one-sparse-side slate must still render the other side"
    assert (df["team"] == "NYY").all()
    print(f"  [OK] sparse-side slate -> {len(df)} rows (other side rendered)")


def test_parlay_generation_uses_projected_rows():
    """Parlay generator scores both Confirmed and Projected rows."""
    # Build a mixed slate, then score it, then ensure pool contains both kinds.
    df, _ = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_mixed(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    scored = rm._score_slate(df)
    assert "score" in scored.columns
    # The parlay path filters by score threshold but never by lineup_status —
    # confirm both statuses survive into the scored frame the parlay reads.
    statuses = set(scored["lineup_status"])
    assert {"Confirmed", "Projected"}.issubset(statuses), statuses
    print(f"  [OK] parlay-eligible scored frame keeps both statuses: {statuses}")


def test_no_odds_key_uses_model_est_total():
    df, _ = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_projected_both(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    assert (df["total_source"] == "Model Est.").all()
    totals = df["game_total"].astype(float).unique().tolist()
    for t in totals:
        assert 6.5 <= t <= 12.5, f"Model Est. total {t} out of expected band"
    # ...and the total must NOT just be 8.5 — the model must use context.
    assert any(abs(t - 8.5) > 0.01 for t in totals), totals
    print(f"  [OK] Model Est. totals computed from context: {totals}")


def test_market_total_overrides_model_est():
    df, _ = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_projected_both(),
        clean_name_fn=_clean, norm_team_fn=_norm,
        totals_map={"NYY": 9.5, "NYM": 9.5, "Yankees": 9.5, "Mets": 9.5},
    )
    assert (df["total_source"] == "Market").all()
    assert (df["game_total"].astype(float) == 9.5).all()
    print("  [OK] Market totals_map overrides Model Est.")


def test_no_demo_references_in_module():
    src = open(rm.__file__, "r", encoding="utf-8").read().lower()
    for token in ("demo player", "fake player", "is_demo", "demo_mode",
                  "demo mode", "build_demo", "_demo_slate"):
        assert token not in src, f"Found forbidden token {token!r} in rbi_model.py"
    print("  [OK] no demo/fake code paths in rbi_model.py")


def main() -> int:
    tests = [
        test_duplicate_index_does_not_raise,
        test_projected_only_slate_produces_rows,
        test_mixed_confirmed_and_projected_slate,
        test_sparse_side_does_not_blank_page,
        test_parlay_generation_uses_projected_rows,
        test_no_odds_key_uses_model_est_total,
        test_market_total_overrides_model_est,
        test_no_demo_references_in_module,
    ]
    failed = 0
    for t in tests:
        print(f"- {t.__name__}")
        try:
            t()
        except AssertionError as e:
            print(f"  [FAIL] {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
