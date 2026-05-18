"""Tests for services.slate_rollover.

All tests inject a fake ``fetch_schedule`` callable so they never hit
the network. ``now_utc`` is also passed in explicitly, so the rollover
window can be exercised at any wall-clock time without monkey-patching.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from services.slate_rollover import (
    DEFAULT_GRACE_MINUTES,
    compute_default_slate_date,
    is_slate_complete,
    latest_completion_time,
    next_slate_with_games,
)


# --- Helpers ---------------------------------------------------------------

def _game(
    *,
    detailed: str = "Scheduled",
    abstract: str = "Preview",
    game_date: str = "2026-05-18T23:05:00Z",
    end_time: str | None = None,
) -> dict:
    """Build a minimal raw StatsAPI game dict for the rollover tests."""
    g: dict = {
        "gameDate": game_date,
        "status": {"detailedState": detailed, "abstractGameState": abstract},
    }
    if end_time is not None:
        g["gameInfo"] = {"gameEndDateTime": end_time}
    return g


def _final(end_time: str, *, game_date: str = "2026-05-18T23:05:00Z") -> dict:
    return _game(
        detailed="Final",
        abstract="Final",
        game_date=game_date,
        end_time=end_time,
    )


def _live(game_date: str = "2026-05-18T23:05:00Z") -> dict:
    return _game(detailed="In Progress", abstract="Live", game_date=game_date)


def _scheduled(game_date: str = "2026-05-18T23:05:00Z") -> dict:
    return _game(detailed="Scheduled", abstract="Preview", game_date=game_date)


def _postponed(game_date: str = "2026-05-18T23:05:00Z") -> dict:
    return _game(detailed="Postponed", abstract="Preview", game_date=game_date)


def _make_fetcher(schedule: dict[date, list[dict]]):
    """Return a fetcher that looks up canned game lists by date."""

    def fetch(d: date) -> list[dict]:
        return list(schedule.get(d, []))

    return fetch


# --- is_slate_complete -----------------------------------------------------

class IsSlateCompleteTests(unittest.TestCase):
    def test_empty_slate_is_not_complete(self):
        self.assertFalse(is_slate_complete([]))

    def test_any_live_game_blocks_completion(self):
        games = [_final("2026-05-19T02:30:00Z"), _live()]
        self.assertFalse(is_slate_complete(games))

    def test_any_scheduled_game_blocks_completion(self):
        games = [_final("2026-05-19T02:30:00Z"), _scheduled()]
        self.assertFalse(is_slate_complete(games))

    def test_all_final_is_complete(self):
        games = [_final("2026-05-19T02:00:00Z"), _final("2026-05-19T02:45:00Z")]
        self.assertTrue(is_slate_complete(games))

    def test_postponed_counts_as_terminal(self):
        # All games postponed -> slate is "complete" (we shouldn't sit
        # on it forever).
        games = [_postponed(), _postponed()]
        self.assertTrue(is_slate_complete(games))

    def test_mixed_final_and_postponed_is_complete(self):
        games = [_final("2026-05-19T02:30:00Z"), _postponed()]
        self.assertTrue(is_slate_complete(games))


# --- latest_completion_time ------------------------------------------------

class LatestCompletionTimeTests(unittest.TestCase):
    def test_picks_latest_end_time(self):
        games = [
            _final("2026-05-19T02:00:00Z"),
            _final("2026-05-19T03:15:00Z"),
            _final("2026-05-19T02:45:00Z"),
        ]
        t = latest_completion_time(games)
        self.assertEqual(
            t, datetime(2026, 5, 19, 3, 15, tzinfo=timezone.utc)
        )

    def test_falls_back_to_start_when_only_postponed(self):
        games = [
            _postponed(game_date="2026-05-18T23:05:00Z"),
            _postponed(game_date="2026-05-18T20:10:00Z"),
        ]
        t = latest_completion_time(games)
        self.assertEqual(
            t, datetime(2026, 5, 18, 23, 5, tzinfo=timezone.utc)
        )

    def test_prefers_completed_end_over_postponed_start(self):
        # If a slate has one final game and one postponed, anchor on
        # the final's end time, not the (potentially later) postponed
        # start time.
        games = [
            _final(
                "2026-05-19T02:30:00Z",
                game_date="2026-05-18T23:05:00Z",
            ),
            _postponed(game_date="2026-05-19T23:00:00Z"),
        ]
        t = latest_completion_time(games)
        self.assertEqual(
            t, datetime(2026, 5, 19, 2, 30, tzinfo=timezone.utc)
        )

    def test_no_games_returns_none(self):
        self.assertIsNone(latest_completion_time([]))


# --- next_slate_with_games -------------------------------------------------

class NextSlateWithGamesTests(unittest.TestCase):
    def test_returns_next_day_if_it_has_games(self):
        fetch = _make_fetcher({date(2026, 5, 19): [_scheduled()]})
        self.assertEqual(
            next_slate_with_games(date(2026, 5, 18), fetch),
            date(2026, 5, 19),
        )

    def test_skips_empty_days(self):
        fetch = _make_fetcher(
            {
                date(2026, 5, 19): [],
                date(2026, 5, 20): [],
                date(2026, 5, 21): [_scheduled()],
            }
        )
        self.assertEqual(
            next_slate_with_games(date(2026, 5, 18), fetch),
            date(2026, 5, 21),
        )

    def test_returns_none_when_nothing_in_window(self):
        fetch = _make_fetcher({})  # always empty
        self.assertIsNone(
            next_slate_with_games(
                date(2026, 5, 18), fetch, max_lookahead_days=3
            )
        )

    def test_fetcher_exceptions_treated_as_empty(self):
        def boom(_d):
            raise RuntimeError("network down")

        self.assertIsNone(
            next_slate_with_games(date(2026, 5, 18), boom, max_lookahead_days=2)
        )


# --- compute_default_slate_date --------------------------------------------

TODAY = date(2026, 5, 18)
TOMORROW = date(2026, 5, 19)


class ComputeDefaultSlateDateTests(unittest.TestCase):
    def test_stays_on_today_when_games_in_progress(self):
        fetch = _make_fetcher(
            {
                TODAY: [_live(), _final("2026-05-19T02:30:00Z")],
                TOMORROW: [_scheduled()],
            }
        )
        now = datetime(2026, 5, 19, 5, 0, tzinfo=timezone.utc)
        result = compute_default_slate_date(TODAY, now, fetch)
        self.assertEqual(result.slate_date, TODAY)
        self.assertFalse(result.rolled_over)
        self.assertEqual(result.reason, "slate_in_progress")

    def test_stays_on_today_when_all_final_but_grace_not_elapsed(self):
        # Last final ended 02:30 UTC; grace is 45 min so 03:14 UTC
        # still falls *inside* the grace window.
        fetch = _make_fetcher(
            {
                TODAY: [
                    _final("2026-05-19T02:00:00Z"),
                    _final("2026-05-19T02:30:00Z"),
                ],
                TOMORROW: [_scheduled()],
            }
        )
        now = datetime(2026, 5, 19, 3, 14, tzinfo=timezone.utc)
        result = compute_default_slate_date(TODAY, now, fetch)
        self.assertEqual(result.slate_date, TODAY)
        self.assertFalse(result.rolled_over)
        self.assertEqual(result.reason, "awaiting_grace")
        # Grace window readout should be the latest end + 45 min.
        self.assertEqual(
            result.grace_ready_at,
            datetime(2026, 5, 19, 3, 15, tzinfo=timezone.utc),
        )

    def test_rolls_forward_after_grace_elapsed(self):
        # Last final 02:30 UTC, grace ends 03:15 UTC, now is 03:16 UTC.
        fetch = _make_fetcher(
            {
                TODAY: [
                    _final("2026-05-19T02:00:00Z"),
                    _final("2026-05-19T02:30:00Z"),
                ],
                TOMORROW: [_scheduled()],
            }
        )
        now = datetime(2026, 5, 19, 3, 16, tzinfo=timezone.utc)
        result = compute_default_slate_date(TODAY, now, fetch)
        self.assertEqual(result.slate_date, TOMORROW)
        self.assertTrue(result.rolled_over)
        self.assertEqual(result.reason, "rolled_forward")

    def test_rolls_forward_skipping_empty_days(self):
        day_after = TOMORROW + timedelta(days=1)
        fetch = _make_fetcher(
            {
                TODAY: [_final("2026-05-19T02:30:00Z")],
                TOMORROW: [],  # off-day
                day_after: [_scheduled()],
            }
        )
        # Long past the grace window.
        now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        result = compute_default_slate_date(TODAY, now, fetch)
        self.assertEqual(result.slate_date, day_after)
        self.assertTrue(result.rolled_over)
        self.assertEqual(result.reason, "rolled_forward")

    def test_rolls_when_today_has_no_games_at_all(self):
        # Off-day on TODAY. Should hop straight to TOMORROW without a
        # grace timer at all.
        fetch = _make_fetcher(
            {
                TODAY: [],
                TOMORROW: [_scheduled()],
            }
        )
        now = datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc)
        result = compute_default_slate_date(TODAY, now, fetch)
        self.assertEqual(result.slate_date, TOMORROW)
        self.assertTrue(result.rolled_over)
        self.assertEqual(result.reason, "no_games_today")

    def test_postponed_only_slate_rolls_after_grace(self):
        # Whole slate postponed. Anchor falls back to the latest
        # scheduled start; once 45 min past that, roll forward.
        fetch = _make_fetcher(
            {
                TODAY: [
                    _postponed(game_date="2026-05-18T23:05:00Z"),
                    _postponed(game_date="2026-05-18T20:10:00Z"),
                ],
                TOMORROW: [_scheduled()],
            }
        )
        # 23:05 UTC + 45 min = 23:50 UTC. Pick a time well after that.
        now = datetime(2026, 5, 19, 0, 30, tzinfo=timezone.utc)
        result = compute_default_slate_date(TODAY, now, fetch)
        self.assertEqual(result.slate_date, TOMORROW)
        self.assertTrue(result.rolled_over)

    def test_stays_put_when_lookahead_finds_nothing(self):
        fetch = _make_fetcher({TODAY: [_final("2026-05-19T02:30:00Z")]})
        now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        result = compute_default_slate_date(
            TODAY, now, fetch, max_lookahead_days=3
        )
        # Nothing to roll to -> stay on TODAY.
        self.assertEqual(result.slate_date, TODAY)
        self.assertFalse(result.rolled_over)

    def test_custom_grace_minutes_respected(self):
        fetch = _make_fetcher(
            {
                TODAY: [_final("2026-05-19T02:30:00Z")],
                TOMORROW: [_scheduled()],
            }
        )
        # 10 min grace; at 02:41 UTC we're past it.
        now = datetime(2026, 5, 19, 2, 41, tzinfo=timezone.utc)
        result = compute_default_slate_date(
            TODAY, now, fetch, grace_minutes=10
        )
        self.assertEqual(result.slate_date, TOMORROW)
        self.assertTrue(result.rolled_over)

    def test_default_grace_is_45_minutes(self):
        self.assertEqual(DEFAULT_GRACE_MINUTES, 45)

    def test_naive_now_treated_as_utc(self):
        fetch = _make_fetcher(
            {
                TODAY: [_final("2026-05-19T02:30:00Z")],
                TOMORROW: [_scheduled()],
            }
        )
        # Pass a naive datetime — module should treat it as UTC and
        # roll forward.
        now = datetime(2026, 5, 19, 4, 0)
        result = compute_default_slate_date(TODAY, now, fetch)
        self.assertEqual(result.slate_date, TOMORROW)


if __name__ == "__main__":
    unittest.main()
