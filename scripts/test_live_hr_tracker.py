"""Smoke tests for live_hr_tracker.

Run from the repo root:
    python scripts/test_live_hr_tracker.py

These tests:
1. Verify the HR-event predicate accepts every wording variant the StatsAPI
   live feed has been observed to use (Home Run / home_run / homers /
   inside-the-park / Grand Slam) and rejects unrelated plays.
2. Hit the real MLB StatsAPI for a known historical date (2025-09-28) that
   has many HRs across the slate, run the parser end-to-end, and assert
   that we extracted multiple unique events with stable ids and intact
   batter/team/RBI/distance fields.
3. Touch today's date — should not crash and should report a valid status
   (zero or more HRs depending on the slate state).
4. Two-poll simulation: poll twice; the first call sees N events, the second
   call seeds an injected NEW play, and we assert that exactly one new event
   is yielded and de-dup prevents repeats on a third call.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow `import live_hr_tracker` when running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone
from typing import Any

import live_hr_tracker as lhr


def test_hr_event_predicate() -> None:
    assert lhr._is_hr_event("home_run", "Home Run", "Judge homers (10).")
    assert lhr._is_hr_event("home_run", None, None)
    assert lhr._is_hr_event(None, "Home Run", None)
    assert lhr._is_hr_event(None, None, "Smith homers (3) to deep center.")
    assert lhr._is_hr_event(None, "Grand Slam", None)
    assert lhr._is_hr_event(None, None, "inside-the-park home run by Rodriguez.")
    assert not lhr._is_hr_event("single", "Single", "Lined to right.")
    assert not lhr._is_hr_event("strikeout", "Strikeout", None)
    assert not lhr._is_hr_event(None, None, None)
    print("PASS: HR event predicate covers all variants")


def test_historical_slate() -> None:
    feed = lhr.MLBLiveHRFeed(date_iso="2025-09-28")
    seen: set[str] = set()
    events = feed.fetch_new_events(seen)
    assert len(events) >= 10, f"expected many HRs on 2025-09-28, got {len(events)}"
    ids = {e["event_id"] for e in events}
    assert len(ids) == len(events), "event_ids should be unique"
    sample = events[0]
    for key in ("event_id", "name", "team", "rbi", "matchup", "timestamp"):
        assert key in sample, f"missing key {key} in event"
    # At least one event should have a non-null distance from StatsAPI hitData
    assert any(e.get("distance") for e in events), "expected at least one hitData distance"
    print(f"PASS: historical slate yielded {len(events)} unique HR events "
          f"across {feed.status.games_scanned} games")


def test_today_does_not_crash() -> None:
    feed = lhr.MLBLiveHRFeed()
    seen: set[str] = set()
    events = feed.fetch_new_events(seen)
    print(f"PASS: today's slate ran cleanly — {len(events)} HRs, "
          f"{feed.status.games_scanned} games, source={feed.status.source}, "
          f"per_game_errors={feed.status.per_game_errors}")


def test_two_poll_dedup() -> None:
    """Simulate two polls; on the second poll a NEW HR appears. It must
    surface exactly once, never on a third poll."""
    seen: set[str] = set()

    def make_play(idx: int) -> dict[str, Any]:
        return {
            "atBatIndex": idx,
            "about": {"halfInning": "top", "inning": 5, "endTime": "2026-05-16T20:00:00Z"},
            "matchup": {
                "batter": {"id": 592450, "fullName": "Aaron Judge", "primaryNumber": "99"},
                "pitcher": {"id": 1, "fullName": "Tarik Skubal"},
            },
            "result": {"eventType": "home_run", "event": "Home Run",
                       "rbi": 2, "description": "Judge homers (10) to right."},
            "playEvents": [{"hitData": {"launchSpeed": 109.5, "totalDistance": 421}}],
        }

    feed = lhr.MLBLiveHRFeed(date_iso="2026-05-16")

    # Poll 1: zero plays in the simulated game
    plays_state: list[dict[str, Any]] = []

    def fake_get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if "/schedule" in url:
            return {"dates": [{"games": [{
                "gamePk": 999999,
                "status": {"abstractGameState": "Live"},
                "teams": {
                    "away": {"team": {"abbreviation": "NYY", "name": "New York Yankees"}},
                    "home": {"team": {"abbreviation": "DET", "name": "Detroit Tigers"}},
                },
            }]}]}
        if "/feed/live" in url:
            return {"liveData": {"plays": {"allPlays": list(plays_state)}}}
        return {}

    feed._get_json = fake_get_json  # type: ignore[assignment]
    out1 = feed.fetch_new_events(seen)
    assert out1 == [], f"expected 0 events on poll 1, got {out1}"
    for e in out1:
        seen.add(e["event_id"])

    # Poll 2: new HR appears
    plays_state.append(make_play(42))
    out2 = feed.fetch_new_events(seen)
    assert len(out2) == 1, f"expected exactly 1 NEW HR on poll 2, got {len(out2)}"
    assert out2[0]["name"] == "Aaron Judge"
    assert out2[0]["rbi"] == 2
    assert out2[0]["distance"] == 421
    assert out2[0]["team"] == "NYY"  # top of inning → away
    for e in out2:
        seen.add(e["event_id"])

    # Poll 3: same play still in payload — must NOT re-emit
    out3 = feed.fetch_new_events(seen)
    assert out3 == [], f"expected 0 events on poll 3 (dedup), got {out3}"
    print("PASS: two-poll dedup — new HR surfaced exactly once")


def test_ingest_dedup_session_state() -> None:
    """Independent of Streamlit context, _make_event_id must be stable."""
    play = {
        "atBatIndex": 7,
        "about": {"halfInning": "bottom", "inning": 9, "endTime": "T"},
        "result": {"eventType": "home_run"},
        "playEvents": [],
        "matchup": {"batter": {"id": 1, "fullName": "X"}, "pitcher": {"id": 2, "fullName": "Y"}},
    }
    a = lhr.MLBLiveHRFeed._make_event_id(12345, play)
    b = lhr.MLBLiveHRFeed._make_event_id(12345, play)
    assert a == b
    assert a == "12345-ab7"
    print("PASS: stable event id")


if __name__ == "__main__":
    test_hr_event_predicate()
    test_ingest_dedup_session_state()
    test_two_poll_dedup()
    test_historical_slate()
    test_today_does_not_crash()
    print("\nALL TESTS PASSED")
