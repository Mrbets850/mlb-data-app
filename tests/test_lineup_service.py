"""Smoke tests for services.lineup_service.

These tests never hit the network: each provider receives an injected
``fetcher`` that returns canned payloads modelled on the real MLB StatsAPI
shape. Tests focus on the parser correctness and the orchestration TTL /
fallback behavior, since those are what the Streamlit UI relies on.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

# Make sure the repo root (one level above ``tests/``) is importable when
# pytest / unittest discovery is invoked from anywhere.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from services.lineup_service import (
    LINEUP_STATUS_CONFIRMED,
    LINEUP_STATUS_EXPECTED,
    LINEUP_STATUS_FINAL,
    LINEUP_STATUS_LIVE,
    LINEUP_STATUS_NOT_POSTED,
    LineupService,
    MLBStatsAPIProvider,
    SportsDataIOProvider,
    SportradarProvider,
)


# --- Canned payloads --------------------------------------------------------

# A minimal schedule payload with one pregame matchup and one postponed game.
SCHEDULE_PREGAME = {
    "dates": [
        {
            "games": [
                {
                    "gamePk": 700001,
                    "gameDate": "2026-05-17T23:05:00Z",
                    "officialDate": "2026-05-17",
                    "status": {"detailedState": "Scheduled",
                                "abstractGameState": "Preview"},
                    "venue": {"name": "Yankee Stadium"},
                    "teams": {
                        "away": {
                            "team": {"id": 117, "abbreviation": "HOU",
                                     "name": "Houston Astros"},
                            "probablePitcher": {"id": 666201,
                                                "fullName": "Framber Valdez"},
                        },
                        "home": {
                            "team": {"id": 147, "abbreviation": "NYY",
                                     "name": "New York Yankees"},
                            "probablePitcher": {"id": 519242,
                                                "fullName": "Gerrit Cole"},
                        },
                    },
                },
                {
                    "gamePk": 700002,
                    "gameDate": "2026-05-17T23:10:00Z",
                    "status": {"detailedState": "Postponed",
                                "abstractGameState": "Preview"},
                    "venue": {"name": "Citi Field"},
                    "teams": {
                        "away": {"team": {"id": 121, "abbreviation": "NYM"}},
                        "home": {"team": {"id": 144, "abbreviation": "ATL"}},
                    },
                },
            ]
        }
    ]
}


def _starter(pid: int, name: str, pos: str, order: int, *,
             bat_side: str = "R", pitch_hand: str = "") -> dict:
    """Build a single players[] entry resembling the MLB StatsAPI shape."""
    return {
        "person": {"id": pid, "fullName": name},
        "position": {"abbreviation": pos},
        "batSide": {"code": bat_side},
        "pitchHand": {"code": pitch_hand},
        "battingOrder": f"{order}00",
    }


def _make_team_box(starters: list[dict]) -> dict:
    players = {}
    for s in starters:
        players[f"ID{s['person']['id']}"] = s
    return {
        "players": players,
        "battingOrder": [s["person"]["id"] for s in starters[:9]],
    }


# Game 700001 — full confirmed lineup posted via the live feed boxscore block.
LIVE_FEED_CONFIRMED = {
    "gameData": {
        "game": {"pk": 700001},
        "datetime": {"dateTime": "2026-05-17T23:05:00Z",
                      "officialDate": "2026-05-17"},
        "status": {"detailedState": "Pre-Game",
                    "abstractGameState": "Preview"},
        "venue": {"name": "Yankee Stadium"},
        "teams": {
            "away": {"id": 117, "abbreviation": "HOU", "name": "Houston Astros"},
            "home": {"id": 147, "abbreviation": "NYY", "name": "New York Yankees"},
        },
        "probablePitchers": {
            "away": {"id": 666201, "fullName": "Framber Valdez"},
            "home": {"id": 519242, "fullName": "Gerrit Cole"},
        },
        "players": {
            "ID666201": {"pitchHand": {"code": "L"}},
            "ID519242": {"pitchHand": {"code": "R"}},
        },
    },
    "liveData": {
        "boxscore": {
            "teams": {
                "away": _make_team_box([
                    _starter(1, "Jose Altuve", "2B", 1, bat_side="R"),
                    _starter(2, "Jeremy Pena", "SS", 2),
                    _starter(3, "Yordan Alvarez", "DH", 3, bat_side="L"),
                    _starter(4, "Kyle Tucker", "RF", 4, bat_side="L"),
                    _starter(5, "Alex Bregman", "3B", 5),
                    _starter(6, "Jose Abreu", "1B", 6),
                    _starter(7, "Chas McCormick", "CF", 7),
                    _starter(8, "Yainer Diaz", "C", 8),
                    _starter(9, "Mauricio Dubon", "LF", 9),
                ]),
                "home": _make_team_box([
                    _starter(10, "Anthony Volpe", "SS", 1),
                    _starter(11, "Juan Soto", "RF", 2, bat_side="L"),
                    _starter(12, "Aaron Judge", "CF", 3),
                    _starter(13, "Giancarlo Stanton", "DH", 4),
                    _starter(14, "Anthony Rizzo", "1B", 5, bat_side="L"),
                    _starter(15, "Gleyber Torres", "2B", 6),
                    _starter(16, "Alex Verdugo", "LF", 7, bat_side="L"),
                    _starter(17, "Oswaldo Cabrera", "3B", 8, bat_side="S"),
                    _starter(18, "Jose Trevino", "C", 9),
                ]),
            }
        }
    },
}

# Live game in progress — same shape but abstractGameState=Live and one
# substitute pinch hitter swapped into slot 4 (battingOrder "401" — sub bit set).
LIVE_FEED_INPROGRESS = {
    "gameData": {
        "game": {"pk": 700003},
        "datetime": {"dateTime": "2026-05-17T19:05:00Z",
                      "officialDate": "2026-05-17"},
        "status": {"detailedState": "In Progress",
                    "abstractGameState": "Live"},
        "venue": {"name": "Wrigley Field"},
        "teams": {
            "away": {"id": 158, "abbreviation": "MIL"},
            "home": {"id": 112, "abbreviation": "CHC"},
        },
        "probablePitchers": {},
        "players": {},
    },
    "liveData": {
        "boxscore": {
            "teams": {
                "away": _make_team_box([
                    _starter(101, "Christian Yelich", "LF", 1, bat_side="L"),
                    _starter(102, "William Contreras", "C", 2),
                    _starter(103, "Willy Adames", "SS", 3),
                    # Pinch hitter at slot 4 — battingOrder "401"
                    {"person": {"id": 1041, "fullName": "Pinch Hitter Sub"},
                     "position": {"abbreviation": "DH"},
                     "batSide": {"code": "R"},
                     "pitchHand": {"code": ""},
                     "battingOrder": "401"},
                    _starter(105, "Rhys Hoskins", "1B", 5),
                    _starter(106, "Jackson Chourio", "RF", 6),
                    _starter(107, "Sal Frelick", "CF", 7),
                    _starter(108, "Joey Ortiz", "3B", 8),
                    _starter(109, "Brice Turang", "2B", 9),
                ]),
                "home": _make_team_box([
                    _starter(201, "Ian Happ", "LF", 1, bat_side="S"),
                    _starter(202, "Nico Hoerner", "2B", 2),
                    _starter(203, "Seiya Suzuki", "RF", 3),
                    _starter(204, "Cody Bellinger", "DH", 4, bat_side="L"),
                    _starter(205, "Christopher Morel", "3B", 5),
                    _starter(206, "Pete Crow-Armstrong", "CF", 6, bat_side="L"),
                    _starter(207, "Michael Busch", "1B", 7, bat_side="L"),
                    _starter(208, "Dansby Swanson", "SS", 8),
                    _starter(209, "Yan Gomes", "C", 9),
                ]),
            }
        }
    },
}


# --- Helpers ----------------------------------------------------------------

def make_fetcher(routes: dict[str, dict]):
    """Build a fetcher closure that returns the canned payload whose key is
    a substring of the requested URL. Raises if no route matches so missing
    fixtures fail loudly."""

    def _fetch(url, params, headers):
        for fragment, payload in routes.items():
            if fragment in url:
                return payload
        raise AssertionError(f"No canned route for URL: {url}")

    return _fetch


# --- Tests ------------------------------------------------------------------

class MLBStatsAPIProviderTests(unittest.TestCase):

    def test_schedule_skeleton_carries_probable_pitchers(self):
        prov = MLBStatsAPIProvider(fetcher=make_fetcher({
            "/schedule": SCHEDULE_PREGAME,
            # Game enrichment will fail-soft because no live feed routes provided.
            "/feed/live": {},
            "/boxscore": {},
        }))
        games = prov.fetch_daily("2026-05-17")
        self.assertEqual(len(games), 2)
        g = games[0]
        self.assertEqual(g.game_pk, 700001)
        self.assertEqual(g.away.team_abbr, "HOU")
        self.assertEqual(g.home.team_abbr, "NYY")
        self.assertEqual(g.away.probable_pitcher_name, "Framber Valdez")
        self.assertEqual(g.home.probable_pitcher_name, "Gerrit Cole")
        # Empty live feed leaves the lineup as not-posted, not crashed.
        self.assertEqual(g.lineup_status, LINEUP_STATUS_NOT_POSTED)

    def test_postponed_game_marked_postponed(self):
        prov = MLBStatsAPIProvider(fetcher=make_fetcher({
            "/schedule": SCHEDULE_PREGAME,
            "/feed/live": {},
            "/boxscore": {},
        }))
        games = prov.fetch_daily("2026-05-17")
        postponed = games[1]
        self.assertTrue(postponed.is_postponed)
        self.assertEqual(postponed.lineup_status, "postponed")

    def test_live_feed_confirmed_lineup_full_parse(self):
        prov = MLBStatsAPIProvider(fetcher=make_fetcher({
            "/feed/live": LIVE_FEED_CONFIRMED,
        }))
        gl = prov.fetch_game(700001)
        self.assertIsNotNone(gl)
        self.assertEqual(gl.away.team_abbr, "HOU")
        self.assertEqual(len(gl.away.starters), 9)
        self.assertEqual(len(gl.home.starters), 9)
        # Batting order is preserved and sorted 1..9.
        self.assertEqual([p.batting_order for p in gl.away.starters],
                         list(range(1, 10)))
        self.assertEqual(gl.away.starters[0].name, "Jose Altuve")
        self.assertEqual(gl.home.starters[2].name, "Aaron Judge")
        # Pitch hand should be picked up from gameData.players index.
        self.assertEqual(gl.away.probable_pitcher_hand, "L")
        self.assertEqual(gl.home.probable_pitcher_hand, "R")
        self.assertEqual(gl.lineup_status, LINEUP_STATUS_CONFIRMED)

    def test_live_game_marks_substitute_and_live_status(self):
        prov = MLBStatsAPIProvider(fetcher=make_fetcher({
            "/feed/live": LIVE_FEED_INPROGRESS,
        }))
        gl = prov.fetch_game(700003)
        self.assertEqual(gl.lineup_status, LINEUP_STATUS_LIVE)
        # The pinch hitter has batting_order==4 but is a substitute, so
        # they should NOT count as the slot-4 starter for downstream filters.
        away_sub_names = [p.name for p in gl.away.bench
                          if p.name == "Pinch Hitter Sub"]
        self.assertEqual(away_sub_names, ["Pinch Hitter Sub"])
        slot4_starters = [p for p in gl.away.starters if p.batting_order == 4]
        self.assertEqual(slot4_starters, [])

    def test_live_feed_with_final_status(self):
        feed = {
            **LIVE_FEED_CONFIRMED,
            "gameData": {
                **LIVE_FEED_CONFIRMED["gameData"],
                "status": {"detailedState": "Final",
                            "abstractGameState": "Final"},
            },
        }
        prov = MLBStatsAPIProvider(fetcher=make_fetcher({"/feed/live": feed}))
        gl = prov.fetch_game(700001)
        self.assertEqual(gl.lineup_status, LINEUP_STATUS_FINAL)


class LineupServiceTests(unittest.TestCase):

    def test_cache_hit_uses_short_ttl_for_unposted(self):
        clock = [1000.0]
        prov = MLBStatsAPIProvider(fetcher=make_fetcher({
            "/schedule": SCHEDULE_PREGAME,
            "/feed/live": {},
            "/boxscore": {},
        }))
        svc = LineupService(providers=[prov], clock=lambda: clock[0])
        first = svc.get_daily("2026-05-17")
        # 1 second later the cache should still serve.
        clock[0] += 1
        second = svc.get_daily("2026-05-17")
        self.assertIs(first, second)
        # Two minutes later the not_posted slate TTL (60s) has expired.
        clock[0] += 300
        third = svc.get_daily("2026-05-17")
        self.assertIsNot(first, third)

    def test_premium_provider_fallback_to_statsapi(self):
        # Sportradar configured but its fetcher raises — service must fall
        # through to the StatsAPI provider.
        def boom(url, params, headers):
            raise RuntimeError("sportradar 500")

        sr = SportradarProvider(api_key="fake-key", fetcher=boom)
        statsapi = MLBStatsAPIProvider(fetcher=make_fetcher({
            "/schedule": SCHEDULE_PREGAME,
            "/game/700001/feed/live": LIVE_FEED_CONFIRMED,
            # game 700002 has no live feed yet (postponed) — let it 404.
            "/feed/live": {},
            "/boxscore": {},
        }))
        svc = LineupService(providers=[sr, statsapi])
        slate = svc.get_daily("2026-05-17")
        self.assertGreaterEqual(len(slate), 1)
        confirmed = [g for g in slate if g.game_pk == 700001]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].provider, "mlb-statsapi")
        self.assertEqual(confirmed[0].lineup_status, LINEUP_STATUS_CONFIRMED)

    def test_unconfigured_premium_is_skipped(self):
        # No env var set → not configured → silently skipped.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPORTRADAR_MLB_API_KEY", None)
            os.environ.pop("SPORTSDATAIO_MLB_API_KEY", None)
            self.assertFalse(SportradarProvider().is_configured)
            self.assertFalse(SportsDataIOProvider().is_configured)


class FakeStatsAPI:
    """Tiny stand-in for the ``statsapi`` Python wrapper.

    Records every ``get(endpoint, params)`` call so tests can prove that the
    provider delegates to the wrapper instead of making direct HTTP requests.
    Each entry in ``responses`` maps an endpoint name to a payload.
    """

    def __init__(self, responses: dict[str, dict]):
        self._responses = dict(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, endpoint: str, params: dict):
        self.calls.append((endpoint, dict(params)))
        if endpoint in self._responses:
            return self._responses[endpoint]
        return {}


class StatsAPIWrapperTests(unittest.TestCase):
    """Confirm MLBStatsAPIProvider routes through the wrapper when present
    and never hits HTTP for the same calls."""

    def test_uses_wrapper_when_no_fetcher_injected(self):
        sapi = FakeStatsAPI({"schedule": SCHEDULE_PREGAME})

        # If the wrapper path is taken, this fetcher should never be called.
        def panic_fetcher(url, params, headers):
            raise AssertionError(f"Expected wrapper, got HTTP call to {url}")

        prov = MLBStatsAPIProvider(statsapi_module=sapi,
                                   use_statsapi_wrapper=True)
        # Replace the HTTP fallback so a wrapper miss would still be visible.
        prov._fetch = panic_fetcher  # type: ignore[attr-defined]
        self.assertTrue(prov.using_wrapper)

        games = prov.fetch_daily("2026-05-17")
        self.assertEqual(len(games), 2)
        self.assertEqual(games[0].game_pk, 700001)
        # The wrapper was called with the schedule endpoint + hydrate params.
        endpoints = [c[0] for c in sapi.calls]
        self.assertIn("schedule", endpoints)
        sched_params = next(p for ep, p in sapi.calls if ep == "schedule")
        self.assertEqual(sched_params.get("date"), "2026-05-17")
        self.assertEqual(sched_params.get("sportId"), 1)
        self.assertIn("probablePitcher", sched_params.get("hydrate", ""))

    def test_injected_fetcher_disables_wrapper(self):
        sapi = FakeStatsAPI({"schedule": SCHEDULE_PREGAME})
        prov = MLBStatsAPIProvider(
            fetcher=make_fetcher({"/schedule": SCHEDULE_PREGAME,
                                  "/feed/live": {},
                                  "/boxscore": {}}),
            statsapi_module=sapi,
        )
        # Tests inject a fetcher → wrapper must NOT be used or the offline
        # HTTP shim is meaningless.
        self.assertFalse(prov.using_wrapper)
        games = prov.fetch_daily("2026-05-17")
        self.assertEqual(len(games), 2)
        self.assertEqual(sapi.calls, [])  # wrapper untouched

    def test_wrapper_failure_falls_back_to_http(self):
        # Wrapper raises → provider should fall back to its HTTP fetcher.
        class BoomAPI:
            def __init__(self): self.calls = 0
            def get(self, endpoint, params):
                self.calls += 1
                raise RuntimeError("wrapper down")

        sapi = BoomAPI()
        prov = MLBStatsAPIProvider(statsapi_module=sapi,
                                   use_statsapi_wrapper=True)
        # Provide an HTTP fallback that returns the schedule fixture.
        def http(url, params, headers):
            if "/schedule" in url:
                return SCHEDULE_PREGAME
            return {}
        prov._fetch = http  # type: ignore[attr-defined]

        games = prov.fetch_daily("2026-05-17")
        self.assertEqual(len(games), 2)
        self.assertGreaterEqual(sapi.calls, 1)

    def test_wrapper_disabled_when_module_missing(self):
        # Simulate the package not being importable by forcing the module
        # reference to a sentinel ``False`` (any non-callable will do); the
        # provider should then refuse to route through the wrapper.
        prov = MLBStatsAPIProvider(use_statsapi_wrapper=True)
        prov._statsapi = None  # type: ignore[attr-defined]
        self.assertFalse(prov.using_wrapper)


if __name__ == "__main__":
    unittest.main()
