"""Automatic slate-date rollover.

The Streamlit app defaults its date picker to "today in Central Time". For
late-night users that creates a window — between the last final pitch and
midnight CT — where the picker still points at a finished slate even though
all the action has moved on to tomorrow's games. This module computes a
*smarter* default:

    * While any game on the current slate is still pre-game or live, keep
      the default on the current CT date.
    * Once every game on the current slate is in a terminal state
      (Final / Postponed / Cancelled / Suspended / Forfeit), wait
      ``grace_minutes`` (default 45) past the latest completion time and
      then roll forward to the next CT date that has MLB games scheduled.
    * Skip empty days — e.g. if tomorrow has no games, advance to the next
      date that does, up to a small lookahead window.

The user's manual date selection always wins; the app calls this only to
seed the picker on first render, not to override an explicit choice.

All functions are pure aside from the injected ``fetch_schedule`` callable,
which makes the behavior straightforward to unit-test with canned schedule
payloads (see ``tests/test_slate_rollover.py``).

Data source: the free MLB StatsAPI ``/api/v1/schedule`` endpoint — no
credentials, no scraping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

import requests

MLB_TZ = ZoneInfo("America/Chicago")

# Default grace period after the last final game before we roll the
# default date forward. 45 minutes covers post-game wrap-up (final box
# scores, lineup-card updates, etc.) without lingering on a dead slate.
DEFAULT_GRACE_MINUTES = 45

# How many days ahead of the current slate we'll scan for the next date
# that has at least one MLB game. The All-Star break is the longest gap
# in a normal season; 14 days easily covers it.
DEFAULT_LOOKAHEAD_DAYS = 14

# Status tokens (lowercased substrings of MLB StatsAPI ``detailedState``)
# that mean a game is *not* going to produce any more action today.
# ``abstractGameState == "Final"`` also satisfies this, but we keep a
# substring list so that postponed / cancelled / suspended / forfeit
# games — which never reach Final — don't block rollover indefinitely.
_TERMINAL_TOKENS = (
    "final",
    "completed",
    "game over",
    "postponed",
    "cancelled",
    "canceled",
    "suspended",
    "forfeit",
)

# Statuses that mean the game has been played to completion (vs. wiped
# off the slate). Used to decide which timestamp to use as the "last
# final" anchor for the grace period.
_COMPLETED_TOKENS = ("final", "completed", "game over")


# ---------------------------------------------------------------------------
# Schedule fetching
# ---------------------------------------------------------------------------

ScheduleFetcher = Callable[[date], List[dict]]
"""Callable that takes a date and returns a list of raw StatsAPI game
dicts (the elements of ``data["dates"][i]["games"]``). Tests inject
canned payloads; production uses ``default_schedule_fetcher``."""


def default_schedule_fetcher(
    target_date: date,
    *,
    session: Optional[requests.Session] = None,
    timeout: float = 15.0,
) -> List[dict]:
    """Fetch raw game dicts for ``target_date`` from MLB StatsAPI.

    Returns ``[]`` on network/parse errors so that rollover logic can
    degrade gracefully — a failed lookup should never crash the app's
    date picker.
    """
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date": target_date.strftime("%Y-%m-%d"),
        # ``linescore`` exposes per-game progress fields; we don't need
        # them for rollover (status + gameEndDateTime are enough) but
        # leaving the hydrate set minimal keeps the response small.
    }
    headers = {"User-Agent": "Mozilla/5.0 (mlb-edge slate-rollover)"}
    try:
        if session is not None:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
        else:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    games: List[dict] = []
    for d in data.get("dates", []) or []:
        for g in d.get("games", []) or []:
            games.append(g)
    return games


# ---------------------------------------------------------------------------
# Per-game helpers
# ---------------------------------------------------------------------------

def _status_text(game: dict) -> str:
    return str((game.get("status") or {}).get("detailedState") or "").strip().lower()


def _abstract_state(game: dict) -> str:
    return str((game.get("status") or {}).get("abstractGameState") or "").strip().lower()


def _is_terminal(game: dict) -> bool:
    """True if the game has reached a state that will produce no more
    action today (final, completed, postponed, cancelled, suspended,
    forfeit). Live and pre-game games return False.
    """
    if _abstract_state(game) == "final":
        return True
    status = _status_text(game)
    if not status:
        return False
    return any(tok in status for tok in _TERMINAL_TOKENS)


def _is_completed(game: dict) -> bool:
    """True only for games that were played to completion (Final / Game
    Over / Completed Early). Postponed/cancelled games are *terminal*
    but not *completed* — we don't use their end time as the rollover
    anchor because they were never played."""
    if _abstract_state(game) == "final":
        return True
    status = _status_text(game)
    return any(tok in status for tok in _COMPLETED_TOKENS)


def _parse_utc(value) -> Optional[datetime]:
    """Parse a StatsAPI timestamp into a UTC ``datetime``. Returns
    ``None`` for missing/invalid values. Handles the ``Z`` suffix that
    StatsAPI uses."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    txt = value.strip()
    if not txt:
        return None
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _game_end_time(game: dict) -> Optional[datetime]:
    """Best-effort timestamp for "when did this game finish?".

    Prefers the explicit ``gameInfo.gameEndDateTime`` published once the
    box score is final; falls back to ``gameDate`` (scheduled first
    pitch) so that a postponed game or a finalized game missing the
    end-time field still anchors the grace period to *something*
    sensible rather than blocking rollover.
    """
    info = game.get("gameInfo") or {}
    end_dt = _parse_utc(info.get("gameEndDateTime"))
    if end_dt is not None:
        return end_dt
    return _parse_utc(game.get("gameDate"))


# ---------------------------------------------------------------------------
# Slate-level helpers
# ---------------------------------------------------------------------------

def is_slate_complete(games: Sequence[dict]) -> bool:
    """True if every game in ``games`` is in a terminal state.

    An empty slate is *not* complete — the caller should treat "no
    games on this date" separately (and typically skip to the next
    date with games).
    """
    if not games:
        return False
    return all(_is_terminal(g) for g in games)


def latest_completion_time(games: Sequence[dict]) -> Optional[datetime]:
    """Return the latest "end time" across all terminal games, or
    ``None`` if no game in the slate has a usable timestamp.

    Used as the anchor for the post-slate grace period. Prefers actual
    end times of completed games; falls back to scheduled start times
    for postponed/cancelled games so a slate consisting entirely of
    weather-outs still rolls over.
    """
    completed_ends: List[datetime] = []
    fallback_starts: List[datetime] = []
    for g in games:
        if not _is_terminal(g):
            continue
        if _is_completed(g):
            t = _game_end_time(g)
            if t is not None:
                completed_ends.append(t)
                continue
        # Postponed / cancelled / suspended: use scheduled start so the
        # grace period has *some* anchor.
        t = _parse_utc(g.get("gameDate"))
        if t is not None:
            fallback_starts.append(t)
    if completed_ends:
        return max(completed_ends)
    if fallback_starts:
        return max(fallback_starts)
    return None


# ---------------------------------------------------------------------------
# Roll-forward
# ---------------------------------------------------------------------------

def next_slate_with_games(
    start_date: date,
    fetch_schedule: ScheduleFetcher,
    *,
    max_lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> Optional[date]:
    """Find the next date *strictly after* ``start_date`` that has at
    least one scheduled MLB game.

    Skips empty days (off-days, All-Star break). Returns ``None`` if no
    games are found within ``max_lookahead_days``.
    """
    for offset in range(1, max_lookahead_days + 1):
        candidate = start_date + timedelta(days=offset)
        try:
            games = fetch_schedule(candidate)
        except Exception:
            games = []
        if games:
            return candidate
    return None


@dataclass(frozen=True)
class RolloverDecision:
    """Result of :func:`compute_default_slate_date`.

    Attributes
    ----------
    slate_date:
        The date the picker should default to.
    rolled_over:
        ``True`` if we advanced past the current CT date because the
        current slate is complete + grace period elapsed.
    reason:
        Short machine-readable reason code, useful for a small UI note
        or debug logging. One of ``"current"`` (today is the right
        default), ``"slate_in_progress"`` (today has unfinished games),
        ``"awaiting_grace"`` (today is final but grace hasn't elapsed),
        ``"rolled_forward"`` (default advanced to a future date), or
        ``"no_games_today"`` (today has no games at all, so we rolled).
    grace_ready_at:
        UTC datetime at which the grace period will elapse, when known.
        ``None`` when we haven't computed one (e.g. slate not yet
        complete or no completion times available).
    """

    slate_date: date
    rolled_over: bool
    reason: str
    grace_ready_at: Optional[datetime] = None


def compute_default_slate_date(
    today: date,
    now_utc: datetime,
    fetch_schedule: ScheduleFetcher,
    *,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
    max_lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> RolloverDecision:
    """Decide which date the slate picker should default to.

    Parameters
    ----------
    today:
        The current date in Central Time (``today_ct()`` in the app).
    now_utc:
        The current wall-clock time as a timezone-aware UTC datetime.
        Passed in (rather than read from the clock) so tests can pin it
        without monkey-patching.
    fetch_schedule:
        Callable that returns the list of raw StatsAPI game dicts for
        a given date. Production callers should pass
        :func:`default_schedule_fetcher`; tests inject canned data.
    grace_minutes:
        Minutes to wait past the latest completion time before rolling
        forward. Defaults to :data:`DEFAULT_GRACE_MINUTES` (45).
    max_lookahead_days:
        How many days ahead to search for the next slate with games.

    Returns
    -------
    RolloverDecision
        See above. ``slate_date`` is the value to seed the picker with.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    try:
        today_games = fetch_schedule(today)
    except Exception:
        today_games = []

    # No games at all on today's CT date — roll straight forward.
    if not today_games:
        nxt = next_slate_with_games(
            today, fetch_schedule, max_lookahead_days=max_lookahead_days
        )
        if nxt is not None:
            return RolloverDecision(
                slate_date=nxt,
                rolled_over=True,
                reason="no_games_today",
            )
        return RolloverDecision(slate_date=today, rolled_over=False, reason="current")

    if not is_slate_complete(today_games):
        return RolloverDecision(
            slate_date=today, rolled_over=False, reason="slate_in_progress"
        )

    # Slate is complete. Check the grace timer.
    anchor = latest_completion_time(today_games)
    if anchor is None:
        # We know every game is terminal but we can't pin a timestamp
        # (no end times, no start times). Be conservative and stay on
        # today rather than roll prematurely — the user can still hit
        # the picker manually.
        return RolloverDecision(
            slate_date=today,
            rolled_over=False,
            reason="awaiting_grace",
        )

    grace_ready = anchor + timedelta(minutes=grace_minutes)
    if now_utc < grace_ready:
        return RolloverDecision(
            slate_date=today,
            rolled_over=False,
            reason="awaiting_grace",
            grace_ready_at=grace_ready,
        )

    nxt = next_slate_with_games(
        today, fetch_schedule, max_lookahead_days=max_lookahead_days
    )
    if nxt is None:
        # Nothing in the lookahead window — stay put rather than push
        # the picker to a blank future date.
        return RolloverDecision(
            slate_date=today,
            rolled_over=False,
            reason="awaiting_grace",
            grace_ready_at=grace_ready,
        )
    return RolloverDecision(
        slate_date=nxt,
        rolled_over=True,
        reason="rolled_forward",
        grace_ready_at=grace_ready,
    )


def now_utc() -> datetime:
    """Convenience: current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)
