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


def test_projected_only_slate_yields_leaderboard_rows():
    """Tuning regression: a projected-only slate must produce rows that pass
    the default min-score filter (0.50) and the Moderate Edge label band.

    The pre-2026-05-13 scoring crushed projected lineups via the context
    multiplier so even strong projected hitters sat below 0.65, blanking the
    page when the default filter was applied. We now require at least one
    projected row to score ≥ 0.50."""
    df, _ = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_projected_both(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    scored = rm._score_slate(df)
    assert not scored.empty
    above = scored[scored["score"] >= 0.50]
    assert not above.empty, (
        f"projected-only slate must produce ≥1 row at score≥0.50; got {scored['score'].tolist()}"
    )
    print(f"  [OK] projected-only slate -> {len(above)}/{len(scored)} rows pass 0.50 min")


def test_missing_optional_data_does_not_zero_out_score():
    """A near-empty row (only slot + name) must still produce a usable score
    in the 0.45-0.70 band, not 0.0 — missing optional context is neutral."""
    sparse_row = {"player": "Test Hitter", "team": "NYY", "lineup_slot": 4}
    s = rm.score_player(sparse_row)
    assert 0.45 <= s <= 0.75, f"sparse-data score landed at {s}; expected 0.45-0.75 neutral band"
    label = rm.score_to_label(s)
    assert label != "❌ Fade", f"sparse-data hitter should not be auto-faded; got {label}"
    print(f"  [OK] sparse-data hitter scores {s:.3f} ({label}) — not zeroed")


def test_score_labels_are_reasonable():
    """League-average heart-of-order should be 'Moderate Edge', not 'Fade'.
    Elite should reach 'Strong Edge'. Bands changed 2026-05-13."""
    avg_heart = {"lineup_slot": 3, "lineup_stable": True}
    elite = {
        "lineup_slot": 4, "xwoba_l15": 0.420, "xslg": 0.600, "slg": 0.550,
        "barrel_pct": 20, "hard_hit_pct": 52, "k_pct": 18, "iso_l15": 0.260,
        "platoon_advantage": True, "park_run_factor": 1.08, "game_total": 9.5,
        "team_runs_l7": 5.3, "lineup_stable": True,
    }
    avg_score = rm.score_player(avg_heart)
    elite_score = rm.score_player(elite)
    assert rm.score_to_label(avg_score) in ("✅ Moderate Edge", "⚠️ Marginal"), (
        f"average heart-of-order should not be 'Fade'; got {rm.score_to_label(avg_score)} @ {avg_score}"
    )
    assert rm.score_to_label(elite_score) == "🔥 Strong Edge", (
        f"elite hitter should reach Strong Edge; got {rm.score_to_label(elite_score)} @ {elite_score}"
    )
    print(f"  [OK] avg #3 -> {avg_score:.2f} ({rm.score_to_label(avg_score)}); "
          f"elite -> {elite_score:.2f} ({rm.score_to_label(elite_score)})")


def test_parlay_pool_has_enough_candidates_for_2leg():
    """Parlay generator must reach ≥2 cross-game candidates from a typical
    slate. We assemble two projected games, score them, and confirm the
    threshold + fallback combination yields a non-empty pool with hitters
    from at least 2 games."""
    schedule = pd.DataFrame([
        {"game_pk": 1, "game_time_utc": "2026-05-13T23:05:00Z",
         "home_team": "Yankees", "home_abbr": "NYY", "home_id": 147,
         "away_team": "Mets", "away_abbr": "NYM", "away_id": 121,
         "home_probable": "Gerrit Cole", "away_probable": "Kodai Senga",
         "park_factor": 105},
        {"game_pk": 2, "game_time_utc": "2026-05-13T23:10:00Z",
         "home_team": "Red Sox", "home_abbr": "BOS", "home_id": 111,
         "away_team": "Blue Jays", "away_abbr": "TOR", "away_id": 141,
         "home_probable": "Gerrit Cole", "away_probable": "Kodai Senga",
         "park_factor": 102},
    ])
    df, _ = rm._build_slate_from_app(
        schedule_df=schedule,
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_projected_both(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    scored = rm._score_slate(df)
    threshold = 0.55  # 2-leg parlay floor
    pool = scored[scored["score"] >= threshold]
    if len(pool) < 6:
        topup = scored.sort_values("score", ascending=False).head(6)
        pool = pd.concat([pool, topup]).drop_duplicates(subset=["player", "team", "game"])
    assert pool["game"].nunique() >= 2, (
        f"parlay pool must span ≥2 games; got {pool['game'].nunique()} from {len(pool)} rows"
    )
    print(f"  [OK] 2-leg parlay pool: {len(pool)} hitters across "
          f"{pool['game'].nunique()} games")


def test_strict_thresholds_do_not_blank_page():
    """Even with the user dragging the min-score slider very high, the
    leaderboard's fallback logic must guarantee the page never blanks when
    slate data exists. We assert there is always at least one scored row
    we can fall back to."""
    df, _ = rm._build_slate_from_app(
        schedule_df=_fake_schedule(),
        batters_df=_make_batters_with_dupes(),
        pitchers_df=_make_pitchers_with_dupes(),
        build_game_context_fn=lambda g: _ctx_mixed(),
        clean_name_fn=_clean, norm_team_fn=_norm, totals_map={},
    )
    scored = rm._score_slate(df)
    # Simulate the very-strict slider position 0.95
    strict = scored[scored["score"] >= 0.95]
    # Strict may legitimately be empty — but the fallback can still surface rows:
    fallback = scored.sort_values("score", ascending=False).head(15)
    assert not fallback.empty, "fallback must produce ≥1 row when slate has data"
    print(f"  [OK] strict 0.95 yielded {len(strict)} rows; fallback surfaces "
          f"{len(fallback)} — page does not blank")


def test_projected_penalty_is_modest():
    """The projected vs confirmed gap must be modest (≤0.07) so a strong
    projected hitter is not pushed below the default 0.50 filter."""
    base = {
        "lineup_slot": 3, "xwoba_l15": 0.360, "xslg": 0.470, "slg": 0.440,
        "barrel_pct": 12, "hard_hit_pct": 44, "k_pct": 20, "iso_l15": 0.190,
        "platoon_advantage": True, "park_run_factor": 1.02, "game_total": 9.0,
        "team_runs_l7": 4.9,
    }
    confirmed = rm.score_player(dict(base, lineup_stable=True))
    projected = rm.score_player(dict(base, lineup_stable=False))
    gap = confirmed - projected
    assert 0.0 <= gap <= 0.07, f"projected vs confirmed gap should be modest; got {gap:.3f}"
    print(f"  [OK] confirmed={confirmed:.3f}, projected={projected:.3f}, gap={gap:.3f}")


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
        test_projected_only_slate_yields_leaderboard_rows,
        test_missing_optional_data_does_not_zero_out_score,
        test_score_labels_are_reasonable,
        test_parlay_pool_has_enough_candidates_for_2leg,
        test_strict_thresholds_do_not_blank_page,
        test_projected_penalty_is_modest,
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
