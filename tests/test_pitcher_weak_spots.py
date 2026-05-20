"""Tests for the Pitcher Weak Spots service module.

These tests verify the slot-based scoring logic, the projected → confirmed
lineup binding behavior, and the card-level aggregates that drive the
tab's filters/sort. The service has no I/O — every test runs against
in-memory ``PitcherProfile`` and ``LineupBatter`` instances.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import pitcher_weak_spots as pws


def _make_profile(**kw):
    prof = pws.PitcherProfile()
    for k, v in kw.items():
        setattr(prof, k, v)
    return prof


def test_score_slot_returns_in_range_and_has_zone():
    prof = _make_profile(hand="R", xslg=0.420, k_pct=22.0)
    b = pws.LineupBatter(slot=1, name="Test Hitter", bat_side="L", is_projected=False)
    s = pws.score_slot(prof, b)
    assert 0.0 <= s["score"] <= 100.0
    assert s["zone"] in (pws.ZONE_PRIMARY, pws.ZONE_SECONDARY, pws.ZONE_NEUTRAL)
    assert isinstance(s["reasons"], list) and s["reasons"]
    assert isinstance(s["tags"], list)
    assert 0.0 <= s["confidence"] <= 100.0


def test_lefty_attack_zone_against_vulnerable_lhp():
    # Pitcher with explicit hand split showing he's worse vs LHB.
    prof = _make_profile(
        hand="L", xslg=0.470, barrel=10.0, hard_hit=42.0, iso_allowed=0.190,
        woba_vs_l=0.360, woba_vs_r=0.300, had_hand_split=True,
    )
    lhb = pws.LineupBatter(slot=3, name="Lefty Bomber", bat_side="L", is_projected=False)
    rhb = pws.LineupBatter(slot=3, name="Righty Slap", bat_side="R", is_projected=False)
    s_l = pws.score_slot(prof, lhb)
    s_r = pws.score_slot(prof, rhb)
    assert s_l["score"] > s_r["score"], "LHB should score higher vs LHP-bad-vs-L"


def test_lineup_from_rows_fills_missing_slots():
    rows = [
        {"player_name": "A", "lineup_spot": 1, "bat_side": "R"},
        {"player_name": "B", "lineup_spot": 4, "bat_side": "L"},
    ]
    lineup = pws.lineup_from_rows(rows, is_projected=True)
    assert len(lineup) == 9
    assert lineup[0].name == "A"
    assert lineup[3].name == "B"
    assert lineup[1].name.startswith("Projected #")
    assert all(b.is_projected for b in lineup)


def test_bind_confirmed_lineup_preserves_slot_scores():
    prof = _make_profile(xslg=0.450, hard_hit=42.0, barrel=10.0)
    projected = [pws.make_projected_batter(s) for s in range(1, 10)]
    card = pws.assemble_card(
        game_pk=1, game_time_label="7:10 PM",
        away_abbr="AAA", home_abbr="BBB",
        pitcher_name="Test SP", pitcher_hand="R",
        pitcher_team_abbr="AAA", opponent_abbr="BBB",
        pitcher_profile=prof, lineup=projected,
        lineup_status=pws.LINEUP_STATUS_EXPECTED,
    )
    original_scores = [s["score"] for s in card.slot_scores]
    confirmed = [
        pws.LineupBatter(slot=i, name=f"Real Hitter {i}", bat_side="L",
                         is_projected=False)
        for i in range(1, 10)
    ]
    pws.bind_confirmed_lineup(card, confirmed)
    assert card.is_lineup_confirmed
    assert card.lineup_status == pws.LINEUP_STATUS_CONFIRMED
    assert [b.name for b in card.batters] == [f"Real Hitter {i}" for i in range(1, 10)]
    assert all(not b.is_projected for b in card.batters)
    assert [s["score"] for s in card.slot_scores] == original_scores


def test_card_aggregates_top4_targetable_and_overall():
    # A juicy pitcher with a real hand split — should produce several
    # attack-zone slots and a non-zero overall score.
    prof = _make_profile(
        hand="R", xslg=0.480, barrel=11.0, hard_hit=43.0, iso_allowed=0.200,
        woba_vs_l=0.350, woba_vs_r=0.330, had_hand_split=True,
    )
    lineup = pws.lineup_from_rows(
        [
            {"player_name": "L1", "lineup_spot": 1, "bat_side": "L"},
            {"player_name": "R2", "lineup_spot": 2, "bat_side": "R"},
            {"player_name": "L3", "lineup_spot": 3, "bat_side": "L"},
            {"player_name": "L4", "lineup_spot": 4, "bat_side": "L"},
            {"player_name": "R5", "lineup_spot": 5, "bat_side": "R"},
            {"player_name": "L6", "lineup_spot": 6, "bat_side": "L"},
            {"player_name": "R7", "lineup_spot": 7, "bat_side": "R"},
            {"player_name": "R8", "lineup_spot": 8, "bat_side": "R"},
            {"player_name": "R9", "lineup_spot": 9, "bat_side": "R"},
        ],
        is_projected=False,
    )
    card = pws.assemble_card(
        game_pk=2, game_time_label="6:35 PM",
        away_abbr="LAD", home_abbr="SD",
        pitcher_name="Stinker", pitcher_hand="R",
        pitcher_team_abbr="SD", opponent_abbr="LAD",
        pitcher_profile=prof, lineup=lineup,
        lineup_status=pws.LINEUP_STATUS_CONFIRMED,
    )
    assert card.overall_score > 50.0
    assert card.is_lineup_confirmed
    assert card.top4_targetable >= 0
    assert card.confidence >= 50.0
    # card_to_slot_rows should return 9 rows in slot order
    rows = pws.card_to_slot_rows(card)
    assert [r["slot"] for r in rows] == list(range(1, 10))


def test_confidence_is_lower_when_projected_and_no_splits():
    prof = _make_profile()  # all defaults, no hand split
    lineup = [pws.make_projected_batter(s) for s in range(1, 10)]
    card = pws.assemble_card(
        game_pk=3, game_time_label="1:05 PM",
        away_abbr="NYY", home_abbr="BOS",
        pitcher_name="Anon SP", pitcher_hand="R",
        pitcher_team_abbr="BOS", opponent_abbr="NYY",
        pitcher_profile=prof, lineup=lineup,
        lineup_status=pws.LINEUP_STATUS_NOT_POSTED,
    )
    assert card.confidence < 60.0  # fallback-heavy → low confidence


def test_safe_float_handles_garbage_inputs():
    assert pws.safe_float(None, 1.5) == 1.5
    assert pws.safe_float("abc", 2.0) == 2.0
    assert pws.safe_float("3.5", 0.0) == 3.5
    assert pws.safe_float(7, 0.0) == 7.0


# --- Lineup status / label tests -------------------------------------------
# These cover the polish PR: live and final games must not be misreported as
# "Lineup pending", and the confirmed-style green outline applies to any
# locked-in lineup (confirmed / live / final).


def test_lineup_status_label_for_live_and_final_with_lineup():
    assert pws.lineup_status_label(pws.LINEUP_STATUS_LIVE, has_lineup=True) == "Live"
    assert pws.lineup_status_label(pws.LINEUP_STATUS_FINAL, has_lineup=True) == "Final"
    assert pws.lineup_status_label(pws.LINEUP_STATUS_CONFIRMED) == "Lineup confirmed"
    assert pws.lineup_status_label(pws.LINEUP_STATUS_EXPECTED) == "Projected lineup"
    assert pws.lineup_status_label(pws.LINEUP_STATUS_POSTPONED) == "Postponed"
    assert pws.lineup_status_label(pws.LINEUP_STATUS_NOT_POSTED) == "Lineup pending"


def test_lineup_status_label_falls_back_when_lineup_unavailable():
    # If the boxscore didn't carry a batting order, live/final games should
    # admit so rather than claiming a confirmed lineup or showing "pending".
    assert pws.lineup_status_label(pws.LINEUP_STATUS_LIVE, has_lineup=False) == "Live - lineup unavailable"
    assert pws.lineup_status_label(pws.LINEUP_STATUS_FINAL, has_lineup=False) == "Final - lineup unavailable"


def test_card_with_live_status_does_not_show_pending():
    prof = _make_profile()
    lineup = pws.lineup_from_rows(
        [{"player_name": f"Hitter {i}", "lineup_spot": i, "bat_side": "R"}
         for i in range(1, 10)],
        is_projected=False,
    )
    card = pws.assemble_card(
        game_pk=42, game_time_label="7:10 PM",
        away_abbr="NYY", home_abbr="BOS",
        pitcher_name="Live SP", pitcher_hand="R",
        pitcher_team_abbr="BOS", opponent_abbr="NYY",
        pitcher_profile=prof, lineup=lineup,
        lineup_status=pws.LINEUP_STATUS_LIVE,
    )
    assert card.lineup_status == pws.LINEUP_STATUS_LIVE
    assert card.lineup_status_label == "Live"
    assert card.is_lineup_confirmed  # locked-in for UI styling


def test_card_with_final_status_when_lineup_missing_says_unavailable():
    # All slots are anonymous "Projected #N" -> has_named_lineup is False.
    prof = _make_profile()
    lineup = [pws.make_projected_batter(s) for s in range(1, 10)]
    card = pws.assemble_card(
        game_pk=43, game_time_label="1:05 PM",
        away_abbr="LAA", home_abbr="OAK",
        pitcher_name="Done SP", pitcher_hand="R",
        pitcher_team_abbr="OAK", opponent_abbr="LAA",
        pitcher_profile=prof, lineup=lineup,
        lineup_status=pws.LINEUP_STATUS_FINAL,
    )
    assert card.lineup_status_label == "Final - lineup unavailable"
    assert card.is_lineup_confirmed  # still treated as locked-in for styling


def test_card_with_expected_status_stays_projected():
    prof = _make_profile()
    lineup = pws.lineup_from_rows(
        [{"player_name": f"Hitter {i}", "lineup_spot": i, "bat_side": "R"}
         for i in range(1, 10)],
        is_projected=True,
    )
    card = pws.assemble_card(
        game_pk=44, game_time_label="6:05 PM",
        away_abbr="HOU", home_abbr="TEX",
        pitcher_name="Pregame SP", pitcher_hand="R",
        pitcher_team_abbr="TEX", opponent_abbr="HOU",
        pitcher_profile=prof, lineup=lineup,
        lineup_status=pws.LINEUP_STATUS_EXPECTED,
    )
    assert card.lineup_status_label == "Projected lineup"
    assert not card.is_lineup_confirmed
