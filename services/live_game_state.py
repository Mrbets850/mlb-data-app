"""Live game state helper — current pitcher / batter / status for active games.

The Streamlit app's pitcher cards, matchup heat-maps, and generators historically
keyed off the *probable* pitcher from the schedule. Once a game goes live and the
probable starter is pulled, that view becomes stale and live-betting consumers
get bad matchups. This service is the single source of "what's actually
happening right now" so every consumer can route through one helper instead of
each tab re-implementing live overrides.

Free MLB StatsAPI only — no paid feeds, no extra deps.

Public surface
--------------
- ``get_live_game_state(game_pk)`` -> ``LiveGameState | None``
- ``get_live_pitcher(game_pk, side)`` -> ``LivePitcher | None``
- ``apply_live_pitcher_to_game_row(row)`` -> dict   # row with overrides applied
- ``LiveGameState`` / ``LivePitcher`` dataclasses

All helpers degrade gracefully — when the live feed is unavailable they return
``None``/the original input so callers fall back to the pregame probable
starter without crashing.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import requests

log = logging.getLogger(__name__)


# Default cache TTL — live game state should refresh roughly every 60s so a
# pitching change reflects in the UI on the next interaction without
# hammering MLB's free endpoint.
LIVE_TTL_SECONDS = 60
# Once a game is final the box no longer changes — cache much longer so we
# don't keep requesting completed games on every rerun.
FINAL_TTL_SECONDS = 3600
# Preview/pregame games change rarely — short cache so as soon as the game
# flips to live we pick up the actual pitcher.
PREVIEW_TTL_SECONDS = 120


_LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
_HEADERS = {"User-Agent": "themlbedge.com/live-game-state", "Accept": "application/json"}


@dataclass
class LivePitcher:
    """The pitcher currently on the mound for one side of a game.

    Mirrors the shape the app's existing probable-pitcher consumers already
    use (``id`` + ``name`` + ``hand``) so adapter code can drop it into the
    schedule row without changing downstream signatures.
    """

    player_id: int | None
    name: str
    hand: str = ""          # "L" / "R" — pitchHand.code from MLB
    is_starter: bool = True  # False once the starter has been pulled
    pitches_thrown: int | None = None


@dataclass
class LiveGameState:
    """Snapshot of a single game's live state.

    Only the fields downstream consumers actually need are surfaced. Adding
    a new field is cheap; removing one risks a silent break for a card that
    reads it, so we keep the surface small and explicit.
    """

    game_pk: int
    abstract_status: str = ""   # "Preview" / "Live" / "Final"
    detailed_status: str = ""   # e.g. "In Progress", "Manager Challenge"
    inning: int | None = None
    inning_half: str = ""       # "top" / "bottom"
    venue: str = ""
    away_pitcher: LivePitcher | None = None
    home_pitcher: LivePitcher | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_live(self) -> bool:
        return (self.abstract_status or "").lower() == "live"

    @property
    def is_final(self) -> bool:
        return (self.abstract_status or "").lower() == "final"

    @property
    def is_preview(self) -> bool:
        return (self.abstract_status or "").lower() in ("preview", "")

    def pitcher_for_side(self, side: str) -> LivePitcher | None:
        """Return the current pitcher facing batters from ``side``'s lineup.

        Side is the *batting* team — "away" batters face the *home* pitcher,
        and vice versa. Callers passing a defensive side ("the away team's
        pitcher") should use ``defensive_pitcher`` instead.
        """
        s = (side or "").lower()
        if s == "away":
            return self.home_pitcher
        if s == "home":
            return self.away_pitcher
        return None

    def defensive_pitcher(self, side: str) -> LivePitcher | None:
        """Return the pitcher on the mound for the defensive ``side``."""
        s = (side or "").lower()
        if s == "away":
            return self.away_pitcher
        if s == "home":
            return self.home_pitcher
        return None


# --- HTTP injection point ---------------------------------------------------

Fetcher = Callable[[str], dict[str, Any] | None]


def _default_fetch(url: str) -> dict[str, Any] | None:
    """HTTP GET → dict. Returns ``None`` (never raises) on any failure so
    callers can keep their pregame fallback path simple."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json() or {}
    except Exception as exc:
        log.debug("live_game_state fetch %s failed: %s", url, exc)
        return None


# --- TTL cache --------------------------------------------------------------

class _TTLCache:
    """Small thread-safe TTL cache. Streamlit's ``st.cache_data`` is the
    right tool inside the app, but this service is also used from helper
    scripts and unit tests where Streamlit isn't running — so we keep a
    plain in-process cache here and let app-level callers layer ``st.cache_data``
    on top for cross-rerun reuse."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at <= self._clock():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._data[key] = (self._clock() + max(1.0, float(ttl_seconds)), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# --- Parsing ---------------------------------------------------------------

def _safe_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _player_lookup(game_data: Mapping[str, Any], pid: int | None) -> Mapping[str, Any]:
    if not pid:
        return {}
    players = game_data.get("players") or {}
    return players.get(f"ID{pid}") or {}


def _hand_from_player(pdata: Mapping[str, Any]) -> str:
    pitch_hand = (pdata.get("pitchHand") or {}).get("code") or ""
    return str(pitch_hand).strip().upper()


def _current_pitcher_from_boxscore(box_side: Mapping[str, Any],
                                   players_idx: Mapping[str, Any] | None) -> LivePitcher | None:
    """Walk a per-team boxscore block and return the pitcher currently on the mound.

    StatsAPI publishes the active pitcher in ``boxscore.teams.{side}.pitchers``
    as a list of player ids — the *last* entry is the current pitcher. This
    matches the same ordering the official MLB box uses for the in-game
    pitching line.
    """
    if not box_side:
        return None
    pitcher_ids = box_side.get("pitchers") or []
    if not pitcher_ids:
        return None
    pid = _safe_int(pitcher_ids[-1])
    if not pid:
        return None
    player_block = (box_side.get("players") or {}).get(f"ID{pid}") or {}
    person = player_block.get("person") or {}
    name = person.get("fullName") or ""
    hand = ""
    if not name and players_idx:
        person = (players_idx.get(f"ID{pid}") or {}).get("person") or {}
        name = person.get("fullName") or ""
    if not hand and players_idx:
        hand = _hand_from_player(players_idx.get(f"ID{pid}") or {})
    if not hand:
        # The boxscore player block doesn't carry pitchHand reliably — only
        # the live-feed players index does. We surface a blank hand and let
        # the caller fall back to a separate handedness lookup if it cares.
        hand = ""
    stats = ((player_block.get("stats") or {}).get("pitching") or {})
    pitches = _safe_int(stats.get("numberOfPitches"))
    # Is this still the starter? Compare to the first pitcher in the list.
    starter_id = _safe_int((pitcher_ids or [None])[0])
    is_starter = (starter_id == pid) if starter_id else True
    return LivePitcher(
        player_id=pid, name=name, hand=hand,
        is_starter=is_starter, pitches_thrown=pitches,
    )


def parse_live_feed(feed: Mapping[str, Any] | None) -> LiveGameState | None:
    """Parse a StatsAPI ``/feed/live`` payload into a ``LiveGameState``.

    Returns ``None`` if the payload doesn't contain the expected blocks (a
    parse failure mid-flight is treated as a missing feed so callers reach
    their pregame fallback). Otherwise returns a populated state — the
    pitchers fields are still ``None`` if the live feed lacks them, which
    is the correct signal for "no live pitcher available, use probable".
    """
    if not feed or not isinstance(feed, Mapping):
        return None
    game_data = feed.get("gameData") or {}
    live_data = feed.get("liveData") or {}
    status = game_data.get("status") or {}
    linescore = live_data.get("linescore") or {}
    boxscore = live_data.get("boxscore") or {}
    pk = _safe_int((game_data.get("game") or {}).get("pk"))
    if pk is None:
        return None

    players_idx = game_data.get("players") or {}
    abstract = status.get("abstractGameState", "") or ""
    detailed = status.get("detailedState", "") or ""

    box_teams = boxscore.get("teams") or {}
    away_pitcher = _current_pitcher_from_boxscore(box_teams.get("away"), players_idx)
    home_pitcher = _current_pitcher_from_boxscore(box_teams.get("home"), players_idx)

    # Backfill handedness from the live-feed players index when the
    # boxscore block didn't carry it. This is the cheap path — the same
    # payload, just looking the player up by id.
    for p in (away_pitcher, home_pitcher):
        if p and not p.hand and p.player_id:
            p.hand = _hand_from_player(players_idx.get(f"ID{p.player_id}") or {})
        if p and not p.name and p.player_id:
            person = (players_idx.get(f"ID{p.player_id}") or {}).get("person") or {}
            p.name = person.get("fullName") or p.name

    inning = _safe_int(linescore.get("currentInning"))
    half = (linescore.get("inningHalf") or "").lower()
    venue = ((game_data.get("venue") or {}).get("name") or "")

    return LiveGameState(
        game_pk=pk,
        abstract_status=abstract,
        detailed_status=detailed,
        inning=inning,
        inning_half=half,
        venue=venue,
        away_pitcher=away_pitcher,
        home_pitcher=home_pitcher,
    )


def parse_boxscore_only(box: Mapping[str, Any] | None,
                        *, game_pk: int,
                        abstract_status: str = "",
                        detailed_status: str = "") -> LiveGameState | None:
    """Fallback parser for the lighter ``/boxscore`` endpoint.

    Used when the full live feed isn't available. The boxscore lacks the
    full ``gameData.players`` index, so pitcher handedness is left blank
    here — callers that need it can layer a separate handedness lookup.
    """
    if not box or not isinstance(box, Mapping):
        return None
    teams = box.get("teams") or {}
    away_pitcher = _current_pitcher_from_boxscore(teams.get("away"), None)
    home_pitcher = _current_pitcher_from_boxscore(teams.get("home"), None)
    return LiveGameState(
        game_pk=int(game_pk),
        abstract_status=abstract_status,
        detailed_status=detailed_status,
        away_pitcher=away_pitcher,
        home_pitcher=home_pitcher,
    )


# --- Service layer ---------------------------------------------------------

class LiveGameStateService:
    """Caches per-game live state with a TTL keyed off the game's status.

    The cache is process-local. In a Streamlit context the app wraps
    ``get_live_game_state`` in ``st.cache_data(ttl=60)`` so reruns get the
    same answer for 60s — this in-process cache is the second layer that
    keeps helper scripts and tests fast without standing up Streamlit.
    """

    def __init__(self,
                 *,
                 fetcher: Fetcher | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetcher or _default_fetch
        self._cache = _TTLCache(clock=clock)
        self._clock = clock

    def _ttl_for(self, state: LiveGameState | None) -> float:
        if state is None:
            return PREVIEW_TTL_SECONDS
        if state.is_final:
            return FINAL_TTL_SECONDS
        if state.is_live:
            return LIVE_TTL_SECONDS
        return PREVIEW_TTL_SECONDS

    def get_state(self, game_pk: int, *, force: bool = False) -> LiveGameState | None:
        pk = _safe_int(game_pk)
        if not pk:
            return None
        if not force:
            cached = self._cache.get(pk)
            if cached is not None:
                return cached
        feed = self._fetch(_LIVE_FEED_URL.format(game_pk=pk))
        state = parse_live_feed(feed)
        if state is None:
            # Fall back to the lighter boxscore endpoint — useful when the
            # live feed is intermittently 503'ing or when we're spot-checking
            # a final game whose feed has been archived.
            box = self._fetch(_BOXSCORE_URL.format(game_pk=pk))
            state = parse_boxscore_only(box, game_pk=pk)
        if state is not None:
            self._cache.set(pk, state, self._ttl_for(state))
        return state

    def clear(self) -> None:
        self._cache.clear()


# Module-level singleton — mirrors the lineup_service pattern so callers can
# import a function rather than juggling a service instance.

_singleton: LiveGameStateService | None = None
_singleton_lock = threading.Lock()


def get_service() -> LiveGameStateService:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = LiveGameStateService()
    return _singleton


def get_live_game_state(game_pk: int | None, *, force: bool = False) -> LiveGameState | None:
    """Return the live state for ``game_pk`` or ``None`` if unavailable."""
    if not game_pk:
        return None
    return get_service().get_state(int(game_pk), force=force)


def get_live_pitcher(game_pk: int | None, side: str) -> LivePitcher | None:
    """Return the *defensive* pitcher for ``side`` ("away"/"home") or None.

    "Defensive" because every existing consumer in the app keys pitchers by
    the team that's pitching, not the team that's batting. Use
    ``LiveGameState.pitcher_for_side`` if you want the pitcher a given
    *batting* lineup is facing.
    """
    state = get_live_game_state(game_pk)
    if state is None:
        return None
    return state.defensive_pitcher(side)


# --- Adapter helpers --------------------------------------------------------

def apply_live_pitcher_to_game_row(row: Mapping[str, Any] | Any,
                                   *,
                                   state: LiveGameState | None = None,
                                   ) -> dict[str, Any]:
    """Return a shallow copy of ``row`` with live pitcher overrides applied.

    The Streamlit app's schedule rows carry ``away_probable`` /
    ``home_probable`` / ``*_probable_id`` keys built from the pregame
    schedule hydrate. Once a game is live we want every downstream consumer
    (matchup table, heat map, generators, pitcher cards) to see the *current*
    pitcher in those same slots without forking every signature.

    Behavior:
      - Pregame or final: returns a dict copy of ``row`` unchanged.
      - Live with a current pitcher available: overrides the per-side
        ``*_probable`` / ``*_probable_id`` fields, and adds:
          - ``{side}_pitcher_source`` = "live" | "probable"
          - ``{side}_pitcher_hand`` (when known)
          - ``{side}_pitcher_is_starter`` (False once the starter is pulled)
      - Live with no current pitcher (e.g. between innings, feed lag):
        leaves the probable in place but tags ``*_pitcher_source = "probable"``
        so the UI knows the live override hasn't kicked in yet.
    """
    # Normalize to a mutable dict. Pandas Series support .to_dict().
    if hasattr(row, "to_dict"):
        out: dict[str, Any] = dict(row.to_dict())  # type: ignore[attr-defined]
    elif isinstance(row, Mapping):
        out = dict(row)
    else:
        # Unknown shape — bail out cleanly rather than raise.
        return dict(row) if isinstance(row, dict) else {"_row": row}

    game_pk = _safe_int(out.get("game_pk"))
    if state is None and game_pk:
        state = get_live_game_state(game_pk)

    # Default tagging — even pregame rows get a source label so consumers
    # can render a freshness chip uniformly.
    out.setdefault("away_pitcher_source", "probable")
    out.setdefault("home_pitcher_source", "probable")
    out.setdefault("away_pitcher_changed", False)
    out.setdefault("home_pitcher_changed", False)

    if state is None or not state.is_live:
        return out

    for side in ("away", "home"):
        live_p = state.defensive_pitcher(side)
        if not live_p or not live_p.player_id:
            # Game is live but the feed didn't return a current pitcher for
            # this side yet. Keep the probable so the card still renders.
            continue
        # Preserve the original probable/starter values so the UI can render
        # a "PITCHING CHANGE DETECTED" badge with the original starter's
        # name even after we've overwritten the *_probable* fields below.
        probable_id = _safe_int(out.get(f"{side}_probable_id"))
        probable_name = out.get(f"{side}_probable") or ""
        out[f"{side}_original_probable"] = probable_name
        out[f"{side}_original_probable_id"] = probable_id
        # Always override once we have a real current pitcher id — even if
        # it happens to equal the probable, this normalizes the source tag
        # and surfaces handedness from the live feed (more reliable than
        # the schedule hydrate, which sometimes omits it).
        out[f"{side}_probable"] = live_p.name or probable_name
        out[f"{side}_probable_id"] = live_p.player_id
        out[f"{side}_pitcher_source"] = (
            "live" if (probable_id != live_p.player_id) else "live-same"
        )
        out[f"{side}_pitcher_hand"] = live_p.hand or ""
        out[f"{side}_pitcher_is_starter"] = bool(live_p.is_starter)
        out[f"{side}_pitcher_pitches"] = live_p.pitches_thrown
        out[f"{side}_pitcher_changed"] = _is_pitcher_change(
            probable_id, probable_name, live_p,
        )

    out["_live_state_status"] = state.abstract_status
    out["_live_state_inning"] = state.inning
    out["_live_state_inning_half"] = state.inning_half
    out["_live_pitcher_change_count"] = (
        int(bool(out.get("away_pitcher_changed")))
        + int(bool(out.get("home_pitcher_changed")))
    )
    return out


def _normalize_name(s: Any) -> str:
    """Lowercase + collapse whitespace + strip punctuation for name compares.

    Used as a fallback when one side of the compare lacks a stable id (e.g.
    the schedule's probable_id is blank but a name was hydrated). Conservative
    on purpose — when normalization is empty we report "unknown" so callers
    don't trigger a false positive on missing data.
    """
    if not s:
        return ""
    out = []
    for ch in str(s).lower():
        if ch.isalnum() or ch.isspace():
            out.append(ch)
    return " ".join("".join(out).split())


def _is_pitcher_change(probable_id: int | None,
                       probable_name: str,
                       live_p: "LivePitcher") -> bool:
    """Return True iff the live pitcher demonstrably differs from the
    pregame probable starter.

    Decision rules, in order:
      1. If both sides have a stable player id → compare ids only.
      2. If exactly one side has an id → can't compare reliably; fall back
         to normalized name compare ONLY if both names are present.
      3. If neither id nor a usable name is available on the probable side
         → return False (we have no original to compare against, so we
         can't claim a change happened).

    The bias is toward False on ambiguity: a missing badge is better than
    a false alarm during the live-betting flow.
    """
    live_id = _safe_int(getattr(live_p, "player_id", None))
    if probable_id and live_id:
        return probable_id != live_id
    pn = _normalize_name(probable_name)
    ln = _normalize_name(getattr(live_p, "name", ""))
    if pn and ln:
        return pn != ln
    return False


def freshness_label(row: Mapping[str, Any], side: str) -> str:
    """Return a short human-readable label describing the pitcher source for
    one side of ``row`` (after ``apply_live_pitcher_to_game_row``).

    Examples:
      - "Live · pitching change"        — current pitcher differs from probable
      - "Live · current pitcher"        — game in progress, override active
      - "Live · starter still in"       — game live but starter hasn't been pulled
      - "Probable"                      — pregame
    """
    src = row.get(f"{side}_pitcher_source") or "probable"
    if src in ("live", "live-same"):
        if row.get(f"{side}_pitcher_changed"):
            return "Live · pitching change"
        starter = row.get(f"{side}_pitcher_is_starter")
        if starter is False:
            return "Live · current pitcher"
        return "Live · starter on mound"
    return "Probable"
