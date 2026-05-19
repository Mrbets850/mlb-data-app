"""Tests for ``services.live_game_state``.

These exercise the helper that powers the app's live-betting refresh: making
sure live current-pitcher data overrides the pregame probable for active
games, that pregame and final games fall through cleanly, and that the TTL
cache behaves the way the Streamlit cache layer assumes it does.
"""

from __future__ import annotations

import time
from unittest import mock

import pytest

from services.live_game_state import (
    LIVE_TTL_SECONDS,
    LiveGameState,
    LivePitcher,
    LiveGameStateService,
    apply_live_pitcher_to_game_row,
    freshness_label,
    parse_boxscore_only,
    parse_live_feed,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_feed(*, status: str = "Live",
               detailed_status: str = "In Progress",
               away_pitcher_ids: list[int] | None = None,
               home_pitcher_ids: list[int] | None = None,
               inning: int = 6,
               half: str = "Top",
               player_names: dict[int, str] | None = None,
               player_hands: dict[int, str] | None = None) -> dict:
    """Synthesize a StatsAPI /feed/live payload that matches the shape the
    parser keys off. Kept minimal — only the fields the parser reads."""
    away_pitcher_ids = away_pitcher_ids or []
    home_pitcher_ids = home_pitcher_ids or []
    player_names = player_names or {}
    player_hands = player_hands or {}

    def _player_block(pid: int) -> dict:
        return {
            "person": {"id": pid, "fullName": player_names.get(pid, f"Pitcher {pid}")},
            "pitchHand": {"code": player_hands.get(pid, "R")},
            "stats": {"pitching": {"numberOfPitches": 42}},
        }

    players_idx = {f"ID{pid}": _player_block(pid)
                   for pid in (set(away_pitcher_ids) | set(home_pitcher_ids))}

    def _box_team(pitcher_ids: list[int]) -> dict:
        return {
            "pitchers": pitcher_ids,
            "players": {f"ID{pid}": {
                "person": {"id": pid, "fullName": player_names.get(pid, f"Pitcher {pid}")},
                "stats": {"pitching": {"numberOfPitches": 42}},
            } for pid in pitcher_ids},
        }

    return {
        "gameData": {
            "game": {"pk": 777001},
            "status": {"abstractGameState": status, "detailedState": detailed_status},
            "venue": {"name": "Test Park"},
            "players": players_idx,
        },
        "liveData": {
            "linescore": {"currentInning": inning, "inningHalf": half},
            "boxscore": {
                "teams": {
                    "away": _box_team(away_pitcher_ids),
                    "home": _box_team(home_pitcher_ids),
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# parse_live_feed
# ---------------------------------------------------------------------------

def test_parse_live_feed_returns_none_on_empty():
    assert parse_live_feed(None) is None
    assert parse_live_feed({}) is None


def test_parse_live_feed_extracts_current_pitcher_after_change():
    # away side: starter 1001 was pulled, reliever 1002 came in.
    feed = _make_feed(
        status="Live",
        away_pitcher_ids=[1001, 1002],
        home_pitcher_ids=[2001],
        player_names={1001: "Old Starter", 1002: "Reliever", 2001: "Home SP"},
        player_hands={1001: "R", 1002: "L", 2001: "R"},
    )
    state = parse_live_feed(feed)
    assert state is not None
    assert state.is_live
    assert state.away_pitcher is not None
    assert state.away_pitcher.player_id == 1002
    assert state.away_pitcher.name == "Reliever"
    assert state.away_pitcher.hand == "L"
    assert state.away_pitcher.is_starter is False  # starter was pulled
    assert state.home_pitcher.player_id == 2001
    assert state.home_pitcher.is_starter is True
    assert state.inning == 6
    assert state.inning_half == "top"
    assert state.venue == "Test Park"


def test_parse_live_feed_handles_preview_game_without_pitcher():
    feed = _make_feed(status="Preview", detailed_status="Scheduled",
                      away_pitcher_ids=[], home_pitcher_ids=[])
    state = parse_live_feed(feed)
    assert state is not None
    assert state.is_preview
    assert state.away_pitcher is None
    assert state.home_pitcher is None


def test_parse_boxscore_only_fallback():
    box = {
        "teams": {
            "away": {"pitchers": [555], "players": {
                "ID555": {"person": {"id": 555, "fullName": "Box Pitcher"},
                          "stats": {"pitching": {"numberOfPitches": 17}}}
            }},
            "home": {"pitchers": [], "players": {}},
        },
    }
    state = parse_boxscore_only(box, game_pk=999, abstract_status="Live",
                                detailed_status="In Progress")
    assert state is not None
    assert state.game_pk == 999
    assert state.away_pitcher.player_id == 555
    assert state.away_pitcher.name == "Box Pitcher"
    # boxscore-only path can't supply pitchHand — leave it blank
    assert state.away_pitcher.hand == ""
    assert state.home_pitcher is None


# ---------------------------------------------------------------------------
# apply_live_pitcher_to_game_row
# ---------------------------------------------------------------------------

def _base_row() -> dict:
    return {
        "game_pk": 777001,
        "away_abbr": "NYY", "home_abbr": "BOS",
        "away_probable": "Old Starter", "away_probable_id": 1001,
        "home_probable": "Home SP",     "home_probable_id": 2001,
    }


def test_overlay_pregame_keeps_probable_and_tags_source():
    pregame = LiveGameState(game_pk=777001, abstract_status="Preview")
    out = apply_live_pitcher_to_game_row(_base_row(), state=pregame)
    assert out["away_probable_id"] == 1001
    assert out["away_pitcher_source"] == "probable"
    assert out["home_pitcher_source"] == "probable"


def test_overlay_live_replaces_pitcher_after_change():
    live = LiveGameState(
        game_pk=777001,
        abstract_status="Live",
        detailed_status="In Progress",
        inning=6,
        inning_half="bottom",
        away_pitcher=LivePitcher(player_id=1002, name="Reliever", hand="L",
                                 is_starter=False, pitches_thrown=12),
        home_pitcher=LivePitcher(player_id=2001, name="Home SP", hand="R",
                                 is_starter=True, pitches_thrown=85),
    )
    out = apply_live_pitcher_to_game_row(_base_row(), state=live)
    # Away side flipped to the reliever
    assert out["away_probable"] == "Reliever"
    assert out["away_probable_id"] == 1002
    assert out["away_pitcher_source"] == "live"
    assert out["away_pitcher_hand"] == "L"
    assert out["away_pitcher_is_starter"] is False
    # Home starter still in — same id, tagged live-same
    assert out["home_probable_id"] == 2001
    assert out["home_pitcher_source"] == "live-same"
    assert out["home_pitcher_is_starter"] is True
    assert out["_live_state_status"] == "Live"
    assert out["_live_state_inning"] == 6


def test_overlay_live_without_current_pitcher_falls_back_to_probable():
    # Game is live but the live feed hasn't reported a current pitcher yet
    # (between innings, momentary feed gap). The pregame probable must
    # remain so downstream cards never go blank.
    live_no_p = LiveGameState(game_pk=777001, abstract_status="Live")
    out = apply_live_pitcher_to_game_row(_base_row(), state=live_no_p)
    assert out["away_probable_id"] == 1001
    assert out["home_probable_id"] == 2001
    # The source stays "probable" — the consumer can render "Probable"
    # rather than misleading the user with a "Live" chip.
    assert out["away_pitcher_source"] == "probable"
    assert out["home_pitcher_source"] == "probable"


def test_overlay_handles_pandas_series_input():
    pd = pytest.importorskip("pandas")
    row = pd.Series(_base_row())
    live = LiveGameState(
        game_pk=777001, abstract_status="Live",
        away_pitcher=LivePitcher(player_id=9999, name="New Guy", hand="R",
                                 is_starter=False),
        home_pitcher=None,
    )
    out = apply_live_pitcher_to_game_row(row, state=live)
    assert isinstance(out, dict)
    assert out["away_probable_id"] == 9999
    assert out["away_pitcher_source"] == "live"


def test_freshness_label_variants():
    row_pre = {"away_pitcher_source": "probable"}
    row_live_starter = {"away_pitcher_source": "live-same",
                        "away_pitcher_is_starter": True}
    row_live_reliever = {"away_pitcher_source": "live",
                         "away_pitcher_is_starter": False}
    row_changed = {"away_pitcher_source": "live",
                   "away_pitcher_is_starter": False,
                   "away_pitcher_changed": True}
    assert freshness_label(row_pre, "away") == "Probable"
    assert "starter" in freshness_label(row_live_starter, "away").lower()
    assert "current pitcher" in freshness_label(row_live_reliever, "away").lower()
    assert "pitching change" in freshness_label(row_changed, "away").lower()


# ---------------------------------------------------------------------------
# Pitching change detection
# ---------------------------------------------------------------------------

def test_overlay_pregame_does_not_flag_change():
    pregame = LiveGameState(game_pk=777001, abstract_status="Preview")
    out = apply_live_pitcher_to_game_row(_base_row(), state=pregame)
    assert out["away_pitcher_changed"] is False
    assert out["home_pitcher_changed"] is False


def test_overlay_live_same_pitcher_does_not_flag_change():
    live = LiveGameState(
        game_pk=777001, abstract_status="Live",
        away_pitcher=LivePitcher(player_id=1001, name="Old Starter", hand="R",
                                 is_starter=True, pitches_thrown=42),
        home_pitcher=LivePitcher(player_id=2001, name="Home SP", hand="R",
                                 is_starter=True, pitches_thrown=55),
    )
    out = apply_live_pitcher_to_game_row(_base_row(), state=live)
    assert out["away_pitcher_changed"] is False
    assert out["home_pitcher_changed"] is False
    assert out["_live_pitcher_change_count"] == 0


def test_overlay_flags_change_when_pitcher_id_differs():
    live = LiveGameState(
        game_pk=777001, abstract_status="Live",
        away_pitcher=LivePitcher(player_id=9999, name="Reliever", hand="L",
                                 is_starter=False),
        home_pitcher=LivePitcher(player_id=2001, name="Home SP", hand="R",
                                 is_starter=True),
    )
    out = apply_live_pitcher_to_game_row(_base_row(), state=live)
    assert out["away_pitcher_changed"] is True
    assert out["away_original_probable"] == "Old Starter"
    assert out["away_original_probable_id"] == 1001
    assert out["home_pitcher_changed"] is False
    assert out["_live_pitcher_change_count"] == 1


def test_overlay_flags_change_by_name_when_id_missing():
    # Schedule probable had a name but no id (rare hydrate edge case). When
    # only one side has an id we still need to detect a change via names.
    row = _base_row()
    row["away_probable_id"] = None
    live = LiveGameState(
        game_pk=777001, abstract_status="Live",
        away_pitcher=LivePitcher(player_id=9999, name="Brand New Reliever",
                                 hand="L", is_starter=False),
        home_pitcher=None,
    )
    out = apply_live_pitcher_to_game_row(row, state=live)
    assert out["away_pitcher_changed"] is True


def test_overlay_no_false_positive_when_probable_missing():
    # Probable was never hydrated (id + name both blank). We have nothing to
    # compare against — the badge must NOT trigger.
    row = _base_row()
    row["away_probable_id"] = None
    row["away_probable"] = ""
    live = LiveGameState(
        game_pk=777001, abstract_status="Live",
        away_pitcher=LivePitcher(player_id=9999, name="Reliever", hand="R",
                                 is_starter=False),
        home_pitcher=None,
    )
    out = apply_live_pitcher_to_game_row(row, state=live)
    assert out["away_pitcher_changed"] is False


def test_overlay_no_false_positive_on_name_variants():
    # Same starter, same id — slight name difference (e.g. "John A. Doe" vs
    # "John Doe") must NOT flag a change because the id is authoritative.
    row = _base_row()
    row["away_probable"] = "John A. Doe"
    row["away_probable_id"] = 1001
    live = LiveGameState(
        game_pk=777001, abstract_status="Live",
        away_pitcher=LivePitcher(player_id=1001, name="John Doe", hand="R",
                                 is_starter=True),
        home_pitcher=None,
    )
    out = apply_live_pitcher_to_game_row(row, state=live)
    assert out["away_pitcher_changed"] is False


def test_overlay_change_count_includes_both_sides():
    live = LiveGameState(
        game_pk=777001, abstract_status="Live",
        away_pitcher=LivePitcher(player_id=9999, name="Away Reliever",
                                 hand="L", is_starter=False),
        home_pitcher=LivePitcher(player_id=8888, name="Home Reliever",
                                 hand="R", is_starter=False),
    )
    out = apply_live_pitcher_to_game_row(_base_row(), state=live)
    assert out["away_pitcher_changed"] is True
    assert out["home_pitcher_changed"] is True
    assert out["_live_pitcher_change_count"] == 2


# ---------------------------------------------------------------------------
# LiveGameStateService — TTL cache
# ---------------------------------------------------------------------------

def test_service_caches_for_ttl_then_refetches_on_expiry():
    clock_value = {"t": 100.0}
    def fake_clock():
        return clock_value["t"]

    call_log: list[str] = []

    def fake_fetch(url: str):
        call_log.append(url)
        return _make_feed(
            status="Live",
            away_pitcher_ids=[1001], home_pitcher_ids=[2001],
        )

    svc = LiveGameStateService(fetcher=fake_fetch, clock=fake_clock)

    state1 = svc.get_state(777001)
    assert state1 is not None
    assert state1.is_live
    n1 = len(call_log)

    # Same gamePk within TTL → no new HTTP call
    clock_value["t"] += LIVE_TTL_SECONDS - 1
    state2 = svc.get_state(777001)
    assert state2 is not None
    assert len(call_log) == n1  # cached

    # Past TTL → refetch
    clock_value["t"] += 5
    svc.get_state(777001)
    assert len(call_log) == n1 + 1


def test_service_force_refresh_bypasses_cache():
    def fake_fetch(url: str):
        return _make_feed(status="Live", away_pitcher_ids=[1001])

    svc = LiveGameStateService(fetcher=fake_fetch)
    with mock.patch.object(svc, "_fetch", wraps=fake_fetch) as wrapped:
        svc.get_state(777001)
        svc.get_state(777001)
        svc.get_state(777001, force=True)
        # First call + force-refresh = 2 fetches. Live feed is the first
        # endpoint tried; box fallback only runs when parse_live_feed
        # returns None, which it shouldn't for our synthetic payload.
        urls = [c.args[0] for c in wrapped.call_args_list]
        assert sum("/feed/live" in u for u in urls) == 2


def test_service_returns_none_on_persistent_fetch_failure():
    def always_none(url: str):
        return None

    svc = LiveGameStateService(fetcher=always_none)
    assert svc.get_state(123456) is None


def test_overlay_with_invalid_row_does_not_raise():
    # Helper must never raise on partial inputs — the calling code path is
    # in a per-game loop and one bad row should never break the slate.
    out = apply_live_pitcher_to_game_row({}, state=None)
    assert isinstance(out, dict)
    assert out["away_pitcher_source"] == "probable"


# ---------------------------------------------------------------------------
# score + box-score extraction (Live ticker + Final box on game cards)
# ---------------------------------------------------------------------------

def _feed_with_linescore(**ls_overrides) -> dict:
    """Build a minimal feed payload with a populated linescore block — used
    to drive the new score/innings/diamond extraction without standing up
    the whole pitching shape."""
    ls = {
        "currentInning": 5,
        "inningHalf": "Bottom",
        "balls": 2, "strikes": 1, "outs": 1,
        "offense": {"first": {"id": 1}, "second": None, "third": {"id": 2}},
        "teams": {
            "away": {"runs": 3, "hits": 5, "errors": 1},
            "home": {"runs": 2, "hits": 4, "errors": 0},
        },
        "innings": [
            {"num": 1, "away": {"runs": 1}, "home": {"runs": 0}},
            {"num": 2, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 3, "away": {"runs": 2}, "home": {"runs": 0}},
            {"num": 4, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 5, "away": {"runs": 0}, "home": {"runs": None}},
        ],
    }
    ls.update(ls_overrides)
    return {
        "gameData": {
            "game": {"pk": 555},
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
            "venue": {"name": "Test Park"},
            "players": {},
        },
        "liveData": {"linescore": ls, "boxscore": {"teams": {"away": {}, "home": {}}}},
    }


def test_parse_live_feed_extracts_rhe_and_innings():
    state = parse_live_feed(_feed_with_linescore())
    assert state is not None
    assert state.away_score == 3 and state.home_score == 2
    assert state.away_hits == 5 and state.home_hits == 4
    assert state.away_errors == 1 and state.home_errors == 0
    # Inning-by-inning runs preserved in order. Bottom of 5 hasn't been
    # played yet → home value is None.
    assert state.innings == [(1, 0), (0, 1), (2, 0), (0, 1), (0, None)]


def test_parse_live_feed_extracts_diamond_and_count():
    state = parse_live_feed(_feed_with_linescore())
    assert state is not None
    assert state.balls == 2 and state.strikes == 1 and state.outs == 1
    assert state.on_first is True
    assert state.on_second is False
    assert state.on_third is True


def test_parse_live_feed_missing_linescore_keeps_score_none():
    # A pregame feed should leave all score fields as None so callers can
    # render the original pregame UI rather than zeros.
    state = parse_live_feed(_feed_with_linescore(teams={}, innings=[]))
    assert state is not None
    assert state.away_score is None and state.home_score is None
    assert state.away_hits is None and state.home_errors is None
    assert state.innings == []


def test_parse_boxscore_only_extracts_rhe():
    # Final-game fallback path: the /boxscore endpoint carries R/H/E inside
    # teamStats. Without it the card would render "Final" with empty cells.
    box = {
        "teams": {
            "away": {
                "pitchers": [],
                "teamStats": {
                    "batting": {"runs": 7, "hits": 11},
                    "fielding": {"errors": 0},
                },
            },
            "home": {
                "pitchers": [],
                "teamStats": {
                    "batting": {"runs": 4, "hits": 9},
                    "fielding": {"errors": 2},
                },
            },
        },
    }
    state = parse_boxscore_only(box, game_pk=42, abstract_status="Final",
                                detailed_status="Final")
    assert state is not None
    assert state.away_score == 7 and state.home_score == 4
    assert state.away_hits == 11 and state.home_hits == 9
    assert state.away_errors == 0 and state.home_errors == 2
