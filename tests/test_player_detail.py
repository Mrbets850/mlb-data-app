"""Tests for services.player_detail pure helpers.

Network is stubbed via an injected ``http_get`` so the suite runs offline.
The focus is on the aggregation + scoring math the dialog renders — formatting
helpers in app.py wrap these results untouched.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from services.player_detail import (
    build_bvp_rows,
    build_split_windows,
    classify_metric,
    classify_pitcher_tier,
    compute_pitcher_rating,
    fetch_batter_game_log,
    format_game_log_rows,
    headshot_url,
    heatmap_style_for,
)


class TestHeadshotUrl(unittest.TestCase):
    """The headshot helper must be a pure URL builder — no I/O, safe on bad
    inputs (None / 0 / strings / NaN-like). The detail dialog calls it once
    per render and the browser fetches the image."""

    def test_returns_none_for_missing_id(self):
        self.assertIsNone(headshot_url(None))
        self.assertIsNone(headshot_url(0))
        self.assertIsNone(headshot_url(""))

    def test_returns_none_for_unparseable_id(self):
        self.assertIsNone(headshot_url("not-a-number"))
        self.assertIsNone(headshot_url(-12))

    def test_builds_mlb_cdn_url(self):
        url = headshot_url(660271)  # Shohei Ohtani's MLBAM id, public.
        self.assertIsNotNone(url)
        self.assertIn("img.mlbstatic.com", url)
        self.assertIn("/people/660271/headshot/", url)

    def test_accepts_string_numeric_id(self):
        url = headshot_url("592450")
        self.assertIsNotNone(url)
        self.assertIn("/people/592450/", url)


# Canned MLB StatsAPI gameLog payload (15 games across two seasons).
def _payload_for(season_2026_games=12, season_2025_games=3):
    splits = []
    # 2025 games (older) — these should land in TwoYear but not Season.
    for i in range(season_2025_games):
        d = f"2025-09-{20 + i:02d}"
        splits.append({
            "date": d,
            "opponent": {"abbreviation": "BOS"},
            "isHome": (i % 2 == 0),
            "game": {"teams": {"home": {"score": 5}, "away": {"score": 3}}},
            "stat": {
                "atBats": 4, "hits": 1, "baseOnBalls": 0, "hitByPitch": 0,
                "sacFlies": 0, "strikeOuts": 1, "plateAppearances": 4,
                "homeRuns": 0, "rbi": 0, "doubles": 0, "triples": 0,
                "stolenBases": 0,
            },
        })
    # 2026 games (newer).
    for i in range(season_2026_games):
        d = f"2026-04-{1 + i:02d}"
        splits.append({
            "date": d,
            "opponent": {"abbreviation": "LAD"},
            "isHome": (i % 2 == 1),
            "game": {"teams": {"home": {"score": 4}, "away": {"score": 2}}},
            "stat": {
                # Mix of HRs to test HR%/TB.
                "atBats": 4, "hits": 2 if i % 3 == 0 else 1,
                "baseOnBalls": 1 if i % 4 == 0 else 0,
                "hitByPitch": 0, "sacFlies": 0,
                "strikeOuts": 1, "plateAppearances": 5,
                "homeRuns": 1 if i % 5 == 0 else 0,
                "rbi": 2 if i % 5 == 0 else 0, "doubles": 0, "triples": 0,
                "stolenBases": 0,
            },
        })
    return {"stats": [{"splits": splits}]}


def _fake_get(payload):
    def _g(url, *, params=None, timeout=10):
        return payload
    return _g


class TestFetchBatterGameLog(unittest.TestCase):
    def test_parses_splits_into_rows(self):
        rows = fetch_batter_game_log(
            12345, 2026, http_get=_fake_get(_payload_for(12, 3))
        )
        self.assertEqual(len(rows), 15)
        # Sorted oldest -> newest.
        self.assertEqual(rows[0]["date"], "2025-09-20")
        self.assertEqual(rows[-1]["date"], "2026-04-12")
        # TB derived from hits/HR (HR-row: 2H including 1HR -> 1 + 4 = 5? actually
        # h=2 hr=1 doubles=0 triples=0 -> tb = 2 + 0 + 0 + 3 = 5).
        hr_row = next(r for r in rows if r["hr"] == 1)
        self.assertEqual(hr_row["tb"], hr_row["h"] + 3 * hr_row["hr"])
        # Opponent + home/away preserved.
        self.assertEqual(rows[-1]["opponent"], "LAD")

    def test_empty_inputs(self):
        self.assertEqual(fetch_batter_game_log(None, 2026), [])
        self.assertEqual(fetch_batter_game_log(0, 2026), [])
        self.assertEqual(fetch_batter_game_log(12345, 2026, http_get=_fake_get({})), [])

    def test_network_failure_returns_empty(self):
        def _boom(url, **kw):
            raise RuntimeError("network down")
        self.assertEqual(
            fetch_batter_game_log(12345, 2026, http_get=_boom), []
        )


class TestBuildSplitWindows(unittest.TestCase):
    def setUp(self):
        self.rows = fetch_batter_game_log(
            12345, 2026, http_get=_fake_get(_payload_for(12, 3))
        )

    def test_window_sizes(self):
        out = build_split_windows(self.rows, 2026, date(2026, 4, 15))
        self.assertEqual(out["L5"]["games"], 5)
        self.assertEqual(out["L10"]["games"], 10)
        self.assertEqual(out["L20"]["games"], 15)  # capped by total games
        self.assertEqual(out["Season"]["games"], 12)
        self.assertEqual(out["TwoYear"]["games"], 15)

    def test_avg_and_hr_pct_computed(self):
        out = build_split_windows(self.rows, 2026, date(2026, 4, 15))
        season = out["Season"]
        self.assertIsNotNone(season["AVG"])
        self.assertGreater(season["AVG"], 0.0)
        self.assertIsNotNone(season["HR%"])
        # PA = 5 * 12 = 60.
        self.assertEqual(season["PA"], 60)


class TestPitcherRating(unittest.TestCase):
    def test_no_pitcher_row_marks_unavailable(self):
        out = compute_pitcher_rating(None)
        self.assertFalse(out["available"])
        self.assertEqual(out["score"], 50)

    def test_juicy_pitcher_scores_high(self):
        out = compute_pitcher_rating({
            "xSLG": 0.520, "Barrel%": 12.0, "HardHit%": 45.0,
            "HR": 25, "K%": 18.0, "ERA": 5.50, "WHIP": 1.50,
        })
        self.assertTrue(out["available"])
        self.assertGreater(out["score"], 70)
        self.assertEqual(out["tier"], "Juicy")
        self.assertTrue(out["bullets"])

    def test_elite_pitcher_scores_low(self):
        out = compute_pitcher_rating({
            "xSLG": 0.300, "Barrel%": 4.0, "HardHit%": 30.0,
            "HR": 5, "K%": 32.0, "ERA": 2.20, "WHIP": 0.95,
        })
        self.assertLess(out["score"], 35)
        self.assertEqual(out["tier"], "Elite")

    def test_missing_fields_use_defaults(self):
        out = compute_pitcher_rating({})
        self.assertTrue(out["available"])
        # All defaults are league-ish -> should land near 50.
        self.assertGreaterEqual(out["score"], 35)
        self.assertLessEqual(out["score"], 65)


class TestBuildBvpRows(unittest.TestCase):
    def test_proxies_flagged(self):
        splits = {
            "L10": {"games": 10, "PA": 40, "AB": 36, "H": 10, "HR": 2,
                    "BB": 4, "K": 9, "TB": 18, "AVG": .278, "OBP": .350,
                    "SLG": .500, "OPS": .850,
                    "H%": 25.0, "HR%": 5.0, "BB%": 10.0, "K%": 22.5},
            "L20": {"games": 20, "PA": 80, "AB": 70, "H": 18, "HR": 3,
                    "BB": 8, "K": 18, "TB": 30, "AVG": .257, "OBP": .325,
                    "SLG": .429, "OPS": .754,
                    "H%": 22.5, "HR%": 3.75, "BB%": 10.0, "K%": 22.5},
            "TwoYear": {"games": 100, "PA": 400, "AB": 360, "H": 95, "HR": 18,
                        "BB": 35, "K": 80, "TB": 170, "AVG": .264, "OBP": .325,
                        "SLG": .472, "OPS": .797,
                        "H%": 23.75, "HR%": 4.5, "BB%": 8.75, "K%": 20.0},
        }
        rows = build_bvp_rows(batter_row=None, pitcher_row=None,
                              season_splits=splits, bat_side="R", pitch_hand="R")
        self.assertEqual(len(rows), 4)
        # L3 SZN rows are flagged as proxies; '25-'26 rows are actual.
        proxy_rows = [r for r in rows if "L3 SZN" in r["label"]]
        actual_rows = [r for r in rows if "L3 SZN" not in r["label"]]
        for r in proxy_rows:
            self.assertFalse(r["actual"])
        for r in actual_rows:
            self.assertTrue(r["actual"])
        # Right-hander pitcher -> RHP label.
        self.assertTrue(any("RHP" in r["label"] for r in rows))


class TestFormatGameLogRows(unittest.TestCase):
    def test_most_recent_first(self):
        rows = fetch_batter_game_log(
            12345, 2026, http_get=_fake_get(_payload_for(12, 3))
        )
        out = format_game_log_rows(rows, limit=5)
        self.assertEqual(len(out), 5)
        # Most recent first.
        self.assertEqual(out[0]["date_short"], "Apr 12")
        # Home/away label set.
        for r in out:
            self.assertTrue(r["opp_label"].startswith("@") or r["opp_label"].startswith("vs "))

    def test_empty_input(self):
        self.assertEqual(format_game_log_rows([]), [])


class TestClassifyMetric(unittest.TestCase):
    """Heat-map band classification — pinned thresholds and orientation.

    The interactive detail modal relies on these to paint metric cells
    green/yellow/red. If thresholds shift the UI shifts with them, so we
    pin a representative case per metric family.
    """

    def test_unknown_metric_is_neutral(self):
        self.assertEqual(classify_metric("not-a-metric", 50), "neutral")

    def test_missing_value_is_neutral(self):
        self.assertEqual(classify_metric("OPS", None), "neutral")
        self.assertEqual(classify_metric("OPS", float("nan")), "neutral")
        self.assertEqual(classify_metric("OPS", "abc"), "neutral")

    def test_higher_is_better_bands(self):
        # OPS thresholds: 0.820 good, 0.720 okay, below = bad
        self.assertEqual(classify_metric("OPS", 0.900), "good")
        self.assertEqual(classify_metric("OPS", 0.820), "good")  # boundary
        self.assertEqual(classify_metric("OPS", 0.750), "okay")
        self.assertEqual(classify_metric("OPS", 0.600), "bad")

    def test_k_pct_is_reversed(self):
        # K% — LOWER is better. <=18 good, <=24 okay, >24 bad
        self.assertEqual(classify_metric("K%", 15.0), "good")
        self.assertEqual(classify_metric("K%", 20.0), "okay")
        self.assertEqual(classify_metric("K%", 30.0), "bad")

    def test_hr_pct_thresholds(self):
        self.assertEqual(classify_metric("HR%", 5.0), "good")
        self.assertEqual(classify_metric("HR%", 3.0), "okay")
        self.assertEqual(classify_metric("HR%", 1.5), "bad")

    def test_matchup_score_high_is_good(self):
        self.assertEqual(classify_metric("Matchup", 150.0), "good")
        self.assertEqual(classify_metric("Matchup", 120.0), "okay")
        self.assertEqual(classify_metric("Matchup", 90.0), "bad")


class TestHeatmapStyleFor(unittest.TestCase):
    def test_returns_class_and_colors_for_good(self):
        s = heatmap_style_for("OPS", 1.000)
        self.assertEqual(s["band"], "good")
        self.assertEqual(s["css_class"], "pdc-hm-good")
        self.assertTrue(s["background"])
        self.assertTrue(s["color"])

    def test_neutral_has_empty_class(self):
        s = heatmap_style_for("OPS", None)
        self.assertEqual(s["band"], "neutral")
        self.assertEqual(s["css_class"], "")
        self.assertEqual(s["background"], "")
        self.assertEqual(s["color"], "")

    def test_bad_uses_red_background(self):
        s = heatmap_style_for("OPS", 0.500)
        self.assertEqual(s["band"], "bad")
        # Don't pin exact hex (avoids brittle theme tests) — just check it
        # picked the bad palette.
        self.assertTrue(s["background"].startswith("#"))
        self.assertTrue(s["color"].startswith("#"))


class TestClassifyPitcherTier(unittest.TestCase):
    def test_juicy_is_good_for_hitter(self):
        self.assertEqual(classify_pitcher_tier("Juicy"), "good")
        self.assertEqual(classify_pitcher_tier("Risky"), "good")

    def test_average_is_okay(self):
        self.assertEqual(classify_pitcher_tier("Average"), "okay")

    def test_elite_is_bad_for_hitter(self):
        self.assertEqual(classify_pitcher_tier("Elite"), "bad")
        self.assertEqual(classify_pitcher_tier("Above-Avg"), "bad")

    def test_missing_or_unknown_is_neutral(self):
        self.assertEqual(classify_pitcher_tier(None), "neutral")
        self.assertEqual(classify_pitcher_tier(""), "neutral")
        self.assertEqual(classify_pitcher_tier("WhoKnows"), "neutral")


if __name__ == "__main__":
    unittest.main()
