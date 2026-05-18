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
    aggregate_window,
    build_bvp_rows,
    build_split_windows,
    classify_metric,
    classify_pitcher_tier,
    compute_hr_due_indicator,
    compute_pitcher_rating,
    compute_player_grade,
    fetch_batter_game_log,
    filter_log_for_split,
    format_game_log_rows,
    headshot_url,
    heatmap_style_for,
    short_opp_abbr,
    split_label_to_key,
    team_logo_url,
)


def _make_game(date_str, *, ab=4, h=1, hr=0, k=1, doubles=0, triples=0,
               opp="LAD", is_home=False):
    return {
        "date": date_str, "opponent": opp, "is_home": is_home, "result": "",
        "ab": ab, "h": h, "hr": hr, "rbi": hr, "bb": 0, "k": k,
        "pa": ab, "tb": h + doubles + 2 * triples + 3 * hr,
        "doubles": doubles, "triples": triples, "avg": None, "sb": 0,
        "sf": 0, "hbp": 0,
    }


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
        # Trimmed to two rows: "vs SP" (proxy from L10) and "vs Team" (TwoYear).
        self.assertEqual(len(rows), 2)
        labels = [r["label"] for r in rows]
        self.assertIn("vs SP", labels)
        self.assertIn("vs Team", labels)
        by_label = {r["label"]: r for r in rows}
        self.assertFalse(by_label["vs SP"]["actual"])
        self.assertTrue(by_label["vs Team"]["actual"])
        # The pre-PR-#60 verbose labels and duplicate 25-26 rows are gone.
        for r in rows:
            self.assertNotIn("Recent form", r["label"])
            self.assertNotIn("Extended form", r["label"])
            self.assertNotIn("2025-26", r["label"])
            self.assertNotIn("25-26", r["label"])
            self.assertNotIn("right-handed", r["label"])
            self.assertNotIn("left-handed", r["label"])
            self.assertNotIn("L3 SZN", r["label"])
            self.assertNotIn("vs S...", r["label"])
        # No duplicate label appears twice.
        self.assertEqual(len(labels), len(set(labels)))

    def test_no_duplicate_two_year_rows(self):
        # Regression guard: the old layout emitted two rows backed by the
        # same TwoYear split ("2025-26 vs <hand>" and "2025-26 all games"),
        # which the user flagged as redundant. The new layout collapses
        # them into a single "vs Team" row.
        splits = {
            "L10": {"PA": 40, "AVG": .278, "H%": 25.0, "SLG": .500,
                    "HR%": 5.0, "BB%": 10.0, "K%": 22.5},
            "TwoYear": {"PA": 400, "AVG": .264, "H%": 23.75, "SLG": .472,
                        "HR%": 4.5, "BB%": 8.75, "K%": 20.0},
        }
        rows = build_bvp_rows(batter_row=None, pitcher_row=None,
                              season_splits=splits, bat_side="R", pitch_hand="L")
        # Only one row should reflect the TwoYear aggregate (PA == 400).
        two_year_rows = [r for r in rows if r.get("PA") == 400]
        self.assertEqual(len(two_year_rows), 1)
        self.assertEqual(two_year_rows[0]["label"], "vs Team")


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

    def test_includes_strikeouts(self):
        # User-facing requirement: every row exposes a K column even when 0.
        rows = fetch_batter_game_log(
            12345, 2026, http_get=_fake_get(_payload_for(5, 0))
        )
        out = format_game_log_rows(rows, limit=5)
        for r in out:
            self.assertIn("k", r)
            self.assertIsInstance(r["k"], int)
            # The canned payload sets strikeOuts=1 per game.
            self.assertEqual(r["k"], 1)

    def test_exposes_opp_logo_url(self):
        # Each row carries the opponent abbreviation AND a public-CDN logo
        # URL so the UI can render the team logo without a separate lookup.
        rows = fetch_batter_game_log(
            12345, 2026, http_get=_fake_get(_payload_for(3, 0))
        )
        out = format_game_log_rows(rows, limit=3)
        for r in out:
            self.assertEqual(r["opp"], "LAD")
            self.assertIsNotNone(r["opp_logo"])
            self.assertIn("mlbstatic.com", r["opp_logo"])

    def test_empty_input(self):
        self.assertEqual(format_game_log_rows([]), [])


class TestTeamLogoUrl(unittest.TestCase):
    """Logo URL builder — pure, free public CDN, abbreviation -> SVG URL.

    The interactive Hits L10 chart and the modal's game log both lean on
    this helper to show a team logo next to each opponent. It must never
    raise on bad input, and must accept the common abbreviation aliases
    (CHW/CWS, KCR/KC, etc.) so old data doesn't blank the icon.
    """

    def test_returns_none_for_missing(self):
        self.assertIsNone(team_logo_url(None))
        self.assertIsNone(team_logo_url(""))
        self.assertIsNone(team_logo_url("   "))

    def test_returns_none_for_unknown(self):
        self.assertIsNone(team_logo_url("XYZ"))

    def test_returns_url_for_canonical_abbr(self):
        u = team_logo_url("LAD")
        self.assertIsNotNone(u)
        self.assertIn("mlbstatic.com/team-logos/", u)
        self.assertTrue(u.endswith(".svg"))

    def test_accepts_alias_abbrs(self):
        # OAK and ATH should both resolve to the same id (133).
        self.assertEqual(team_logo_url("OAK"), team_logo_url("ATH"))
        # KCR -> KC
        self.assertEqual(team_logo_url("KCR"), team_logo_url("KC"))


class TestShortOppAbbr(unittest.TestCase):
    """Mobile bar-chip fallback when a team logo fails or is missing.

    The chip must never wrap, so the helper trims long abbreviations and
    normalizes the common long aliases (WSN -> WSH, CHW -> CWS, etc.).
    Bad inputs return ``""`` so the caller can drop the chip cleanly.
    """

    def test_empty_inputs_return_empty(self):
        self.assertEqual(short_opp_abbr(None), "")
        self.assertEqual(short_opp_abbr(""), "")
        self.assertEqual(short_opp_abbr("   "), "")

    def test_canonical_passthrough(self):
        self.assertEqual(short_opp_abbr("LAD"), "LAD")
        self.assertEqual(short_opp_abbr("nyy"), "NYY")
        self.assertEqual(short_opp_abbr(" bos "), "BOS")

    def test_normalizes_long_aliases(self):
        self.assertEqual(short_opp_abbr("WSN"), "WSH")
        self.assertEqual(short_opp_abbr("CHW"), "CWS")
        self.assertEqual(short_opp_abbr("KCR"), "KC")
        self.assertEqual(short_opp_abbr("SDP"), "SD")
        self.assertEqual(short_opp_abbr("SFG"), "SF")
        self.assertEqual(short_opp_abbr("TBR"), "TB")
        self.assertEqual(short_opp_abbr("ATH"), "OAK")

    def test_trims_to_max_len(self):
        # Default cap is 3 — any longer string must be truncated so the
        # chip stays the width of a single logo on phone screens.
        self.assertEqual(short_opp_abbr("LONGNAME"), "LON")
        self.assertEqual(short_opp_abbr("LONGNAME", max_len=2), "LO")
        # max_len floor is 1, never 0/negative.
        self.assertEqual(short_opp_abbr("LAD", max_len=0), "L")
        self.assertEqual(short_opp_abbr("LAD", max_len=-1), "L")


class TestSplitLabelToKey(unittest.TestCase):
    def test_canonical_keys_passthrough(self):
        for k in ("L5", "L10", "L20", "Season", "TwoYear"):
            self.assertEqual(split_label_to_key(k), k)

    def test_year_resolves_to_season(self):
        self.assertEqual(split_label_to_key("2026"), "Season")
        self.assertEqual(split_label_to_key("2025"), "Season")

    def test_two_year_label_variants(self):
        self.assertEqual(split_label_to_key("’25-’26"), "TwoYear")
        self.assertEqual(split_label_to_key("25-26"), "TwoYear")

    def test_h2h_falls_back_to_l10(self):
        # Without true H2H data the modal renders L10 — keep the fallback
        # so a missing opponent never blanks the UI.
        self.assertEqual(split_label_to_key("H2H"), "L10")

    def test_unknown_label_safe_default(self):
        self.assertEqual(split_label_to_key("???"), "L10")
        self.assertEqual(split_label_to_key(None), "L10")


class TestFilterLogForSplit(unittest.TestCase):
    """Pinning the dynamic-window filter the modal uses to repaint stats
    when the user taps a chip. Without this, the modal stayed static."""

    def setUp(self):
        self.rows = fetch_batter_game_log(
            12345, 2026, http_get=_fake_get(_payload_for(12, 3))
        )
        self.end = date(2026, 4, 15)

    def test_l5_returns_last_five(self):
        out = filter_log_for_split(self.rows, "L5", 2026, self.end)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[-1]["date"], "2026-04-12")

    def test_l20_caps_at_available(self):
        out = filter_log_for_split(self.rows, "L20", 2026, self.end)
        self.assertEqual(len(out), 15)  # only 15 games on file

    def test_season_isolates_current_year(self):
        out = filter_log_for_split(self.rows, "Season", 2026, self.end)
        self.assertEqual(len(out), 12)
        for r in out:
            self.assertTrue(r["date"].startswith("2026"))

    def test_two_year_includes_prior(self):
        out = filter_log_for_split(self.rows, "TwoYear", 2026, self.end)
        self.assertEqual(len(out), 15)

    def test_h2h_with_matching_opponent(self):
        # All 2026 games are vs LAD — H2H against LAD should return only
        # those, not the prior-season BOS rows.
        out = filter_log_for_split(
            self.rows, "H2H", 2026, self.end, opp_team="LAD"
        )
        self.assertEqual(len(out), 12)
        for r in out:
            self.assertEqual(r["opponent"], "LAD")

    def test_h2h_no_match_falls_back_to_l10(self):
        # Opponent that never appeared -> fall back to L10 so the modal
        # never shows an empty body.
        out = filter_log_for_split(
            self.rows, "H2H", 2026, self.end, opp_team="NYY"
        )
        self.assertEqual(len(out), 10)

    def test_empty_log_returns_empty(self):
        self.assertEqual(filter_log_for_split([], "L10", 2026, self.end), [])


class TestAggregateWindowExposed(unittest.TestCase):
    """The dialog needs to aggregate an arbitrary slice (H2H subset) so
    aggregate_window is publicly exported. Verify it sums correctly."""

    def test_aggregate_basic(self):
        rows = [
            {"pa": 5, "ab": 4, "h": 2, "hr": 1, "bb": 1, "k": 1, "tb": 5,
             "hbp": 0, "sf": 0},
            {"pa": 4, "ab": 4, "h": 1, "hr": 0, "bb": 0, "k": 2, "tb": 1,
             "hbp": 0, "sf": 0},
        ]
        out = aggregate_window(rows)
        self.assertEqual(out["games"], 2)
        self.assertEqual(out["PA"], 9)
        self.assertEqual(out["AB"], 8)
        self.assertEqual(out["H"], 3)
        self.assertEqual(out["HR"], 1)
        self.assertAlmostEqual(out["AVG"], 3 / 8)

    def test_aggregate_empty(self):
        out = aggregate_window([])
        self.assertEqual(out["games"], 0)
        self.assertEqual(out["PA"], 0)
        self.assertIsNone(out["AVG"])


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


class TestComputePlayerGrade(unittest.TestCase):
    """Letter grade rendered next to the player's name in the modal header.

    A best, D worst. Each metric contributes a 0-100 sub-score; the composite
    is averaged across whatever inputs are present. With no inputs we get a
    neutral C with ``available=False`` so the UI can hide the badge.
    """

    def test_no_inputs_returns_neutral_unavailable_grade(self):
        out = compute_player_grade()
        self.assertEqual(out["grade"], "C")
        self.assertFalse(out["available"])
        self.assertEqual(out["css_class"], "pdc-grade-C")

    def test_top_of_slate_grades_a(self):
        out = compute_player_grade(
            matchup=170.0,
            pitcher_score=85,
            ops=0.950,
            iso=0.280,
            hr_pct=6.5,
            barrel_pct=12.0,
            xwoba=0.400,
        )
        self.assertEqual(out["grade"], "A")
        self.assertTrue(out["available"])
        self.assertGreaterEqual(out["score"], 72)
        self.assertEqual(out["css_class"], "pdc-grade-A")

    def test_above_average_grades_b(self):
        out = compute_player_grade(
            matchup=140.0,
            pitcher_score=65,
            ops=0.820,
            iso=0.200,
            hr_pct=4.5,
            barrel_pct=9.0,
            xwoba=0.350,
        )
        self.assertEqual(out["grade"], "B")
        self.assertGreaterEqual(out["score"], 58)
        self.assertLess(out["score"], 72)

    def test_league_ish_grades_c(self):
        out = compute_player_grade(
            matchup=120.0,
            pitcher_score=55,
            ops=0.730,
            iso=0.170,
            hr_pct=3.0,
            barrel_pct=7.0,
            xwoba=0.330,
        )
        self.assertEqual(out["grade"], "C")
        self.assertGreaterEqual(out["score"], 42)
        self.assertLess(out["score"], 58)

    def test_weak_matchup_grades_d(self):
        out = compute_player_grade(
            matchup=85.0,
            pitcher_score=22,
            ops=0.580,
            iso=0.090,
            hr_pct=1.0,
            barrel_pct=4.0,
            xwoba=0.280,
        )
        self.assertEqual(out["grade"], "D")
        self.assertLess(out["score"], 42)
        self.assertEqual(out["css_class"], "pdc-grade-D")

    def test_partial_inputs_still_grade(self):
        # Only matchup + pitcher available — should still produce a grade.
        out = compute_player_grade(matchup=160.0, pitcher_score=80)
        self.assertTrue(out["available"])
        self.assertIn(out["grade"], {"A", "B"})

    def test_returns_style_fields(self):
        out = compute_player_grade(matchup=150.0, pitcher_score=70, ops=0.850)
        self.assertTrue(out["background"].startswith("#"))
        self.assertTrue(out["color"].startswith("#"))
        self.assertTrue(out["css_class"].startswith("pdc-grade-"))

    def test_ignores_non_numeric_inputs(self):
        # Strings, None, NaN-like values must be tolerated.
        out = compute_player_grade(
            matchup=None, pitcher_score="n/a", ops=float("nan"), iso=0.200,
        )
        self.assertTrue(out["available"])
        # Only ISO contributed; should land in the middle bands, not crash.
        self.assertIn(out["grade"], {"A", "B", "C", "D"})


class TestComputeHrDueIndicator(unittest.TestCase):
    """Six-criterion HR Due composite. Each criterion can resolve as hit /
    miss / missing; the score denominator stays at 6 either way."""

    def test_no_inputs_returns_all_missing(self):
        out = compute_hr_due_indicator()
        self.assertEqual(out["score"], 0)
        self.assertEqual(out["total"], 6)
        self.assertEqual(len(out["criteria"]), 6)
        for c in out["criteria"]:
            self.assertEqual(c["state"], "missing")
            self.assertEqual(c["detail"], "Data unavailable")

    def test_recent_barrel_hit_from_recent_hr(self):
        # A HR in the last 3 games is treated as a barrel by the proxy.
        log = [
            _make_game("2026-05-10"),
            _make_game("2026-05-12"),
            _make_game("2026-05-15", hr=1),
        ]
        out = compute_hr_due_indicator(game_log=log)
        crit = next(c for c in out["criteria"] if c["key"] == "recent_barrel")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("1 barrel", crit["detail"])

    def test_recent_barrel_miss_with_no_xbh(self):
        log = [
            _make_game("2026-05-10"),
            _make_game("2026-05-12"),
            _make_game("2026-05-15"),
        ]
        out = compute_hr_due_indicator(game_log=log)
        crit = next(c for c in out["criteria"] if c["key"] == "recent_barrel")
        self.assertEqual(crit["state"], "miss")

    def test_season_barrel_elite_hit_above_threshold(self):
        out = compute_hr_due_indicator(season_barrel_pct=14.8)
        crit = next(c for c in out["criteria"] if c["key"] == "season_barrel")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("14.8%", crit["detail"])
        self.assertIn("MLB median 7.0%", crit["detail"])

    def test_season_barrel_miss_below_threshold(self):
        out = compute_hr_due_indicator(season_barrel_pct=6.0)
        crit = next(c for c in out["criteria"] if c["key"] == "season_barrel")
        self.assertEqual(crit["state"], "miss")

    def test_season_barrel_missing_when_none(self):
        out = compute_hr_due_indicator(season_barrel_pct=None)
        crit = next(c for c in out["criteria"] if c["key"] == "season_barrel")
        self.assertEqual(crit["state"], "missing")

    def test_drought_z_hit_when_above_median_gap(self):
        # Construct a log with HRs at predictable gaps and a long current
        # drought so Z is comfortably above 0.5.
        log = []
        # Two HR-then-quiet sequences set median gap ~ ab*5 each.
        for i in range(5):
            log.append(_make_game(f"2026-04-{10+i:02d}", ab=4, k=1))
        log.append(_make_game("2026-04-15", ab=4, k=0, hr=1))
        for i in range(5):
            log.append(_make_game(f"2026-04-{16+i:02d}", ab=4, k=1))
        log.append(_make_game("2026-04-21", ab=4, k=0, hr=1))
        # Long current drought (post last HR).
        for i in range(20):
            log.append(_make_game(f"2026-04-{22+i:02d}", ab=4, k=0))
        out = compute_hr_due_indicator(game_log=log)
        crit = next(c for c in out["criteria"] if c["key"] == "drought_z")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("BIPs since HR", crit["detail"])
        self.assertIn("Z:", crit["detail"])

    def test_drought_missing_when_no_hr_in_log(self):
        log = [_make_game("2026-05-01") for _ in range(5)]
        out = compute_hr_due_indicator(game_log=log)
        crit = next(c for c in out["criteria"] if c["key"] == "drought_z")
        self.assertEqual(crit["state"], "missing")

    def test_la_window_hit_in_range(self):
        out = compute_hr_due_indicator(season_la=20.9, recent_la=19.8)
        crit = next(c for c in out["criteria"] if c["key"] == "la_window")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("19.8°", crit["detail"])
        self.assertIn("20.9°", crit["detail"])

    def test_la_window_miss_outside_range(self):
        out = compute_hr_due_indicator(season_la=8.0)
        crit = next(c for c in out["criteria"] if c["key"] == "la_window")
        self.assertEqual(crit["state"], "miss")

    def test_la_window_missing_when_none(self):
        out = compute_hr_due_indicator()
        crit = next(c for c in out["criteria"] if c["key"] == "la_window")
        self.assertEqual(crit["state"], "missing")

    def test_pitcher_barrel_hit_above_league(self):
        out = compute_hr_due_indicator(opp_pitcher_row={"Barrel%": 9.8})
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "hit")
        self.assertEqual(crit["title"], "Barrel-Friendly Pitcher")
        self.assertIn("9.8", crit["detail"])
        self.assertIn("MLB avg 7.0%", crit["detail"])

    def test_pitcher_barrel_miss_below_league(self):
        out = compute_hr_due_indicator(opp_pitcher_row={"Barrel%": 5.4})
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "miss")
        self.assertIn("5.4", crit["detail"])

    def test_pitcher_barrel_alt_column_brl_bip(self):
        # Repo's slate sometimes carries Brl/BIP% instead of Barrel%.
        out = compute_hr_due_indicator(opp_pitcher_row={"Brl/BIP%": 8.5})
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("8.5", crit["detail"])

    def test_pitcher_barrel_raw_savant_column(self):
        # Raw upstream Savant feed name.
        out = compute_hr_due_indicator(
            opp_pitcher_row={"barrel_batted_rate": 12.0},
        )
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "hit")

    def test_pitcher_barrel_at_league_avg_is_miss(self):
        # Strict ">" rule: exactly average should not count as HR-friendly.
        out = compute_hr_due_indicator(opp_pitcher_row={"Barrel%": 7.0})
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "miss")

    def test_pitcher_fallback_hr9_when_no_barrel(self):
        # No barrel column present — must fall back to HR/9 and label it.
        out = compute_hr_due_indicator(opp_pitcher_row={"HR/9": 1.69})
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("Fallback", crit["detail"])
        self.assertIn("1.69", crit["detail"])

    def test_pitcher_fallback_hr9_derived_from_hr_and_ip(self):
        out = compute_hr_due_indicator(
            opp_pitcher_row={"HR": 20, "IP": 100.0},  # 1.80 HR/9
        )
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("Fallback", crit["detail"])

    def test_pitcher_missing_when_no_data_at_all(self):
        out = compute_hr_due_indicator(opp_pitcher_row={})
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "missing")

    def test_pitcher_barrel_preferred_over_hr9_when_both_present(self):
        # If both columns are present, primary metric wins and detail is barrel.
        out = compute_hr_due_indicator(
            opp_pitcher_row={"Barrel%": 9.8, "HR/9": 0.50},
        )
        crit = next(c for c in out["criteria"] if c["key"] == "pitcher_barrel")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("Barrel%", crit["detail"])
        self.assertNotIn("Fallback", crit["detail"])

    def test_park_factor_hit_above_neutral(self):
        out = compute_hr_due_indicator(park_factor=131)
        crit = next(c for c in out["criteria"] if c["key"] == "park_hr")
        self.assertEqual(crit["state"], "hit")
        self.assertIn("+31%", crit["detail"])

    def test_park_factor_miss_at_or_below_neutral(self):
        out = compute_hr_due_indicator(park_factor=95)
        crit = next(c for c in out["criteria"] if c["key"] == "park_hr")
        self.assertEqual(crit["state"], "miss")

    def test_label_due_when_score_high(self):
        # Hit every criterion. HR in last 3 games + elite barrel + drought
        # + LA window + bad pitcher + good park.
        log = []
        # gap 0 (multi-hr) + gap establishing median (5)
        log.append(_make_game("2026-04-01", hr=1))
        for i in range(5):
            log.append(_make_game(f"2026-04-{2+i:02d}", ab=4, k=1))
        log.append(_make_game("2026-04-07", hr=1))
        for i in range(20):
            log.append(_make_game(f"2026-04-{8+i:02d}", ab=4, k=0))
        # Then a recent HR to satisfy recent_barrel and a 30-game drought.
        log.append(_make_game("2026-05-01", hr=1))
        for i in range(25):
            log.append(_make_game(f"2026-05-{2+i:02d}", ab=4, k=0))
        # Note: the most recent HR is at 2026-05-01 but the drought runs ~100 BIPs.
        out = compute_hr_due_indicator(
            game_log=log,
            season_barrel_pct=14.8,
            season_la=20.9,
            opp_pitcher_row={"Barrel%": 9.8},
            park_factor=131,
        )
        self.assertGreaterEqual(out["score"], 5)
        self.assertIn(out["label"], {"Due \U0001f525", "Warm"})

    def test_score_is_count_of_hits(self):
        out = compute_hr_due_indicator(season_barrel_pct=14.8, park_factor=131)
        self.assertEqual(out["score"], 2)
        self.assertEqual(out["total"], 6)


if __name__ == "__main__":
    unittest.main()
