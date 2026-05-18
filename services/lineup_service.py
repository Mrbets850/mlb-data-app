"""Real-time MLB lineup / roster / probable-pitcher service.

The Streamlit app uses this module to pull confirmed and projected lineups
without depending on any single vendor. The primary, free provider is the
official MLB StatsAPI; optional premium providers (Sportradar, SportsDataIO)
activate automatically when their API keys are present in the environment.

Design goals
------------
- Provider-agnostic: callers receive a ``GameLineups`` dataclass and never
  touch a raw vendor payload.
- Freshness-aware: every result carries the provider name, the lineup status
  (``confirmed`` / ``expected`` / ``not_posted`` / ``live`` / ``final``) and
  a ``last_updated`` timestamp the UI can surface.
- Streamlit-Cloud safe: no secrets in code, optional dependencies degrade
  silently, network errors never raise out to the caller.
- Cheap to test: the HTTP layer is injectable so tests can drive the parser
  with canned payloads instead of hitting the network.

Public surface
--------------
- ``get_game_lineups(game_pk, ...)`` -> ``GameLineups``
- ``get_daily_lineups(date, ...)`` -> ``list[GameLineups]``
- ``LineupService`` for explicit provider control (used by tests).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone
from typing import Any, Callable, Iterable

import requests

log = logging.getLogger(__name__)

# Optional dependency: ``MLB-StatsAPI`` (https://github.com/toddrob99/MLB-StatsAPI).
# This is the free Python wrapper around the same statsapi.mlb.com endpoints
# we already call directly — importing it lets us delegate URL construction,
# query-string assembly, and minor schema quirks to a maintained library. We
# import lazily and guard against ImportError so the service degrades to the
# direct-HTTP path if the package isn't installed in a given environment
# (Streamlit Cloud builds occasionally exclude optional deps).
try:  # pragma: no cover — import wiring only
    import statsapi as _statsapi  # type: ignore
except Exception:  # pragma: no cover
    _statsapi = None


def statsapi_available() -> bool:
    """True when the ``statsapi`` wrapper is importable.

    Exposed for tests and for the app's diagnostics panel — UI code can show
    which path the lineup service is using without poking module internals.
    """
    return _statsapi is not None

# --- Public types -----------------------------------------------------------

LINEUP_STATUS_CONFIRMED = "confirmed"   # MLB has published the batting order
LINEUP_STATUS_EXPECTED  = "expected"    # Provider supplied a projected lineup
LINEUP_STATUS_NOT_POSTED = "not_posted" # No lineup yet (pre-game, TBD)
LINEUP_STATUS_LIVE      = "live"        # Game in progress, lineup reflects current state
LINEUP_STATUS_FINAL     = "final"       # Game complete, lineup is historical record
LINEUP_STATUS_POSTPONED = "postponed"   # Game won't be played as scheduled


@dataclass
class LineupPlayer:
    player_id: int | None
    name: str
    position: str = ""
    bat_side: str = ""        # "L" / "R" / "S"
    pitch_hand: str = ""      # "L" / "R"
    batting_order: int | None = None  # 1..9 for starters; None for bench
    is_starter: bool = False
    is_substitute: bool = False  # entered the game off the bench (live games)


@dataclass
class TeamLineup:
    team_id: int | None
    team_abbr: str
    team_name: str = ""
    starters: list[LineupPlayer] = field(default_factory=list)
    bench: list[LineupPlayer] = field(default_factory=list)
    probable_pitcher_id: int | None = None
    probable_pitcher_name: str = ""
    probable_pitcher_hand: str = ""
    status: str = LINEUP_STATUS_NOT_POSTED  # one of LINEUP_STATUS_*


@dataclass
class GameLineups:
    game_pk: int
    game_date: str            # ISO 8601 date (YYYY-MM-DD) in local game-day terms
    game_time_utc: str        # ISO 8601 UTC timestamp from the schedule feed
    status: str               # raw detailedState from the provider
    abstract_status: str      # Preview / Live / Final
    away: TeamLineup
    home: TeamLineup
    venue: str = ""
    provider: str = ""        # which provider populated this record
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_postponed: bool = False
    notes: str = ""           # provider-specific note (e.g. doubleheader game 1)

    @property
    def lineup_status(self) -> str:
        """Aggregate lineup status across both teams. ``confirmed`` only if
        both sides are confirmed, else the most-progressed status either side
        has reached."""
        priority = [
            LINEUP_STATUS_POSTPONED,
            LINEUP_STATUS_FINAL,
            LINEUP_STATUS_LIVE,
            LINEUP_STATUS_CONFIRMED,
            LINEUP_STATUS_EXPECTED,
            LINEUP_STATUS_NOT_POSTED,
        ]
        a, h = self.away.status, self.home.status
        if a == h:
            return a
        if a == LINEUP_STATUS_CONFIRMED and h == LINEUP_STATUS_CONFIRMED:
            return LINEUP_STATUS_CONFIRMED
        # If either side is confirmed but the other isn't, report partial.
        for s in priority:
            if a == s or h == s:
                # confirmed-on-one-side is downgraded to expected for UI honesty
                if s == LINEUP_STATUS_CONFIRMED:
                    return LINEUP_STATUS_EXPECTED
                return s
        return LINEUP_STATUS_NOT_POSTED


# --- HTTP layer (injectable) ------------------------------------------------

Fetcher = Callable[[str, dict[str, Any] | None, dict[str, str] | None], dict[str, Any]]


def _default_http_fetcher(url: str,
                          params: dict[str, Any] | None,
                          headers: dict[str, str] | None) -> dict[str, Any]:
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


# --- Provider interface -----------------------------------------------------

class LineupProvider:
    """Protocol every provider implements. Methods return ``None`` when the
    provider cannot answer for that input — callers chain providers and stop
    at the first non-None response."""

    name: str = "unknown"

    def fetch_daily(self, date_iso: str) -> list[GameLineups] | None:
        raise NotImplementedError

    def fetch_game(self, game_pk: int) -> GameLineups | None:
        raise NotImplementedError

    @property
    def is_configured(self) -> bool:
        return True


# --- MLB StatsAPI provider --------------------------------------------------

class MLBStatsAPIProvider(LineupProvider):
    """Free, official source. Always available — no credentials needed."""

    name = "mlb-statsapi"
    SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
    LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"

    def __init__(self, fetcher: Fetcher | None = None,
                 user_agent: str = "themlbedge.com/lineup-service",
                 *,
                 use_statsapi_wrapper: bool | None = None,
                 statsapi_module: Any | None = None) -> None:
        # Custom fetcher always wins — tests inject one to drive parsers
        # offline. When the caller doesn't supply one, we prefer the free
        # ``statsapi`` Python wrapper if it's importable (or explicitly
        # injected via ``statsapi_module``), and fall back to plain
        # ``requests.get`` otherwise. The wrapper hits the same public
        # statsapi.mlb.com endpoints so behavior is unchanged — we just
        # reuse a maintained query-string + URL builder.
        self._injected_fetcher = fetcher is not None
        self._fetch = fetcher or _default_http_fetcher
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._statsapi = statsapi_module if statsapi_module is not None else _statsapi
        if use_statsapi_wrapper is None:
            self._use_wrapper = (self._statsapi is not None and not self._injected_fetcher)
        else:
            # Honour explicit opt-in/out, but never wrap an injected fetcher
            # (that would defeat the test-only HTTP shim).
            self._use_wrapper = bool(use_statsapi_wrapper) and not self._injected_fetcher

    @property
    def using_wrapper(self) -> bool:
        """Whether this provider is delegating to the ``statsapi`` package."""
        return self._use_wrapper and self._statsapi is not None

    def _wrapper_get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Call ``statsapi.get(endpoint, params)`` defensively.

        Returns ``None`` if the wrapper isn't usable or the call fails so
        the caller can fall through to direct HTTP. Errors are logged at
        debug — they're expected in offline test environments and on
        Streamlit Cloud cold starts where the package may not be present.
        """
        sapi = self._statsapi
        if not (self._use_wrapper and sapi is not None):
            return None
        try:
            return sapi.get(endpoint, params) or {}
        except Exception as exc:
            log.debug("statsapi.get(%s) failed: %s", endpoint, exc)
            return None

    # ---- Public methods ----

    def fetch_daily(self, date_iso: str) -> list[GameLineups] | None:
        # Prefer the ``statsapi`` wrapper when present — same endpoint, but
        # we lean on the package to keep its query-string in sync if MLB
        # ever changes parameter names. Fall through to direct HTTP on any
        # error so a flaky wrapper install never blocks the slate.
        sched: dict[str, Any] | None = self._wrapper_get(
            "schedule",
            {"sportId": 1, "date": date_iso,
             "hydrate": "probablePitcher,team,venue,lineups"},
        )
        if sched is None:
            try:
                sched = self._fetch(
                    self.SCHEDULE_URL,
                    {"sportId": 1, "date": date_iso,
                     "hydrate": "probablePitcher,team,venue,lineups"},
                    self._headers,
                )
            except Exception as exc:
                log.warning("MLB schedule fetch failed for %s: %s", date_iso, exc)
                return None
        out: list[GameLineups] = []
        for d in sched.get("dates", []) or []:
            for game in d.get("games", []) or []:
                gpk = game.get("gamePk")
                if not gpk:
                    continue
                # Build the schedule-only skeleton first so we always have
                # probable pitchers even when no boxscore exists yet.
                skel = self._parse_schedule_game(game)
                # Try to enrich with the boxscore (confirmed batting order
                # when MLB has posted it). For preview-state games this often
                # returns an empty batting order — that's fine, we keep the
                # skeleton.
                try:
                    enriched = self.fetch_game(gpk, _skeleton=skel)
                    if enriched is not None:
                        out.append(enriched)
                        continue
                except Exception as exc:
                    log.debug("Game enrichment failed for %s: %s", gpk, exc)
                out.append(skel)
        return out

    def fetch_game(self, game_pk: int,
                   _skeleton: GameLineups | None = None) -> GameLineups | None:
        # Live feed is the richest source; boxscore is a smaller fallback.
        feed = self._wrapper_get("game", {"gamePk": int(game_pk)})
        if not feed:
            try:
                feed = self._fetch(
                    self.LIVE_FEED_URL.format(game_pk=game_pk),
                    None, self._headers,
                )
            except Exception as exc:
                log.debug("Live feed fetch failed for %s: %s", game_pk, exc)
                feed = None

        if feed:
            parsed = self._parse_live_feed(feed, fallback_skel=_skeleton)
            if parsed is not None and (parsed.away.starters or parsed.home.starters
                                       or parsed.lineup_status != LINEUP_STATUS_NOT_POSTED):
                return parsed

        box = self._wrapper_get("game_boxscore", {"gamePk": int(game_pk)})
        if not box:
            try:
                box = self._fetch(
                    self.BOXSCORE_URL.format(game_pk=game_pk),
                    None, self._headers,
                )
            except Exception as exc:
                log.debug("Boxscore fetch failed for %s: %s", game_pk, exc)
                box = None

        if box:
            return self._parse_boxscore_only(box, _skeleton)

        return _skeleton

    # ---- Schedule skeleton ----

    def _parse_schedule_game(self, game: dict[str, Any]) -> GameLineups:
        gpk = int(game.get("gamePk") or 0)
        away_team = (game.get("teams") or {}).get("away") or {}
        home_team = (game.get("teams") or {}).get("home") or {}
        status = (game.get("status") or {})
        is_pp = (status.get("detailedState", "") or "").lower() in (
            "postponed", "cancelled", "canceled", "suspended"
        )

        def _team_skel(t: dict) -> TeamLineup:
            team = (t.get("team") or {})
            pp = (t.get("probablePitcher") or {})
            return TeamLineup(
                team_id=team.get("id"),
                team_abbr=(team.get("abbreviation") or "").upper(),
                team_name=team.get("name", "") or "",
                probable_pitcher_id=pp.get("id"),
                probable_pitcher_name=pp.get("fullName", "") or "",
                status=(LINEUP_STATUS_POSTPONED if is_pp else LINEUP_STATUS_NOT_POSTED),
            )

        return GameLineups(
            game_pk=gpk,
            game_date=(game.get("officialDate") or game.get("gameDate", "")[:10]),
            game_time_utc=game.get("gameDate", "") or "",
            status=status.get("detailedState", "") or "",
            abstract_status=status.get("abstractGameState", "") or "",
            venue=((game.get("venue") or {}).get("name") or ""),
            away=_team_skel(away_team),
            home=_team_skel(home_team),
            provider=self.name,
            is_postponed=is_pp,
        )

    # ---- Live feed parser ----

    def _parse_live_feed(self, feed: dict[str, Any],
                         fallback_skel: GameLineups | None) -> GameLineups | None:
        game_data = feed.get("gameData") or {}
        live_data = feed.get("liveData") or {}
        status = game_data.get("status") or {}
        teams = game_data.get("teams") or {}
        datetime_block = game_data.get("datetime") or {}
        venue = (game_data.get("venue") or {}).get("name", "") or ""
        probable = game_data.get("probablePitchers") or {}

        def _team(side: str) -> dict:
            return teams.get(side) or {}

        # Players index lives on gameData.players (keyed by "ID605141")
        players_idx: dict[str, Any] = game_data.get("players") or {}

        def _player_lookup(pid: int | None) -> dict[str, Any]:
            if not pid:
                return {}
            return players_idx.get(f"ID{pid}") or {}

        abstract = (status.get("abstractGameState") or "").lower()
        detailed = status.get("detailedState", "") or ""
        is_pp = detailed.lower() in (
            "postponed", "cancelled", "canceled", "suspended"
        )

        # Boxscore lives at liveData.boxscore in the live feed — the same
        # structure /boxscore returns. Reuse our boxscore parser for it.
        boxscore = live_data.get("boxscore") or {}

        def _build_side(side: str) -> TeamLineup:
            t = _team(side)
            box_side = (boxscore.get("teams") or {}).get(side) or {}
            probable_p = probable.get(side) or {}
            pp_id = probable_p.get("id")
            pp_name = probable_p.get("fullName", "") or ""
            pp_hand = ""
            if pp_id:
                pdata = _player_lookup(pp_id)
                pp_hand = ((pdata.get("pitchHand") or {}).get("code") or "") or ""
            tl = TeamLineup(
                team_id=t.get("id"),
                team_abbr=(t.get("abbreviation") or "").upper(),
                team_name=t.get("name", "") or t.get("teamName", "") or "",
                probable_pitcher_id=pp_id,
                probable_pitcher_name=pp_name,
                probable_pitcher_hand=pp_hand,
            )
            _populate_team_from_box(tl, box_side, abstract=abstract,
                                    is_postponed=is_pp)
            return tl

        gl = GameLineups(
            game_pk=int(game_data.get("game", {}).get("pk") or
                        (fallback_skel.game_pk if fallback_skel else 0)),
            game_date=(datetime_block.get("officialDate") or
                       datetime_block.get("originalDate") or
                       (fallback_skel.game_date if fallback_skel else "")),
            game_time_utc=(datetime_block.get("dateTime") or
                           (fallback_skel.game_time_utc if fallback_skel else "")),
            status=detailed,
            abstract_status=status.get("abstractGameState", "") or "",
            venue=venue or (fallback_skel.venue if fallback_skel else ""),
            away=_build_side("away"),
            home=_build_side("home"),
            provider=self.name,
            is_postponed=is_pp,
        )
        return gl

    # ---- Boxscore-only parser ----

    def _parse_boxscore_only(self, box: dict[str, Any],
                             skel: GameLineups | None) -> GameLineups:
        # Boxscore on its own doesn't carry schedule metadata — we layer it
        # on top of the schedule skeleton.
        out = skel or GameLineups(
            game_pk=0, game_date="", game_time_utc="", status="",
            abstract_status="", away=TeamLineup(None, ""), home=TeamLineup(None, ""),
            provider=self.name,
        )
        for side in ("away", "home"):
            tl = getattr(out, side)
            box_side = (box.get("teams") or {}).get(side) or {}
            _populate_team_from_box(tl, box_side,
                                    abstract=out.abstract_status.lower(),
                                    is_postponed=out.is_postponed)
        out.provider = self.name
        return out


def _populate_team_from_box(tl: TeamLineup, box_side: dict[str, Any], *,
                            abstract: str, is_postponed: bool) -> None:
    """Mutate ``tl`` in place using the boxscore's per-team payload."""
    players = (box_side or {}).get("players") or {}
    starters: list[LineupPlayer] = []
    bench: list[LineupPlayer] = []

    # Boxscore-published batting order list (when MLB has posted it). Each
    # entry is a player_id as int.
    batting_ids = box_side.get("battingOrder") or []
    batting_order_lookup = {int(pid): idx + 1 for idx, pid in enumerate(batting_ids)}

    for key, pdata in players.items():
        person = pdata.get("person") or {}
        pid = person.get("id")
        if not pid:
            continue
        name = person.get("fullName", "") or ""
        pos = (pdata.get("position") or {}).get("abbreviation", "") or ""
        bat_side = (pdata.get("batSide") or {}).get("code", "") or ""
        pitch_hand = (pdata.get("pitchHand") or {}).get("code", "") or ""
        bo_raw = str(pdata.get("battingOrder") or "").strip()
        # battingOrder is e.g. "100" for leadoff, "200" for #2, ...,
        # "101" / "102" indicate substitutes who entered the #1 slot.
        bo_int = None
        is_sub = False
        if bo_raw and bo_raw[:1].isdigit():
            try:
                primary = int(bo_raw[0])
                if 1 <= primary <= 9:
                    bo_int = primary
                sub_digits = bo_raw[-2:] if len(bo_raw) >= 3 else "00"
                is_sub = sub_digits != "00"
            except ValueError:
                bo_int = None
        elif pid in batting_order_lookup:
            bo_int = batting_order_lookup[pid]

        lp = LineupPlayer(
            player_id=pid,
            name=name,
            position=pos,
            bat_side=bat_side,
            pitch_hand=pitch_hand,
            batting_order=bo_int,
            is_starter=(bo_int is not None and not is_sub and pos != "P"),
            is_substitute=is_sub,
        )
        if lp.is_starter:
            starters.append(lp)
        else:
            bench.append(lp)

    starters.sort(key=lambda p: (p.batting_order or 99, p.name))
    tl.starters = starters
    tl.bench = bench

    if is_postponed:
        tl.status = LINEUP_STATUS_POSTPONED
    elif len(starters) >= 9:
        # MLB has posted a full 9-spot batting order.
        if abstract == "final":
            tl.status = LINEUP_STATUS_FINAL
        elif abstract == "live":
            tl.status = LINEUP_STATUS_LIVE
        else:
            tl.status = LINEUP_STATUS_CONFIRMED
    else:
        tl.status = LINEUP_STATUS_NOT_POSTED


# --- Premium provider stubs -------------------------------------------------
#
# These are wired but inert unless credentials are supplied. We keep the
# adapter surface small and provider-specific schemas internal so the rest
# of the app never needs to know which one is active.

class SportradarProvider(LineupProvider):
    """Sportradar MLB Game Summary / Game Extended Summary feed.

    Discovers game IDs via the daily schedule feed, then pulls the Game
    Summary for each game. Activated when ``SPORTRADAR_MLB_API_KEY`` is
    present. Falls back to MLB StatsAPI if any request fails.
    """

    name = "sportradar"
    BASE = "https://api.sportradar.com/mlb/trial/v7/en"  # trial tier — adjust per plan

    def __init__(self, api_key: str | None = None,
                 fetcher: Fetcher | None = None) -> None:
        self._api_key = api_key or os.environ.get("SPORTRADAR_MLB_API_KEY", "")
        self._fetch = fetcher or _default_http_fetcher

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def fetch_daily(self, date_iso: str) -> list[GameLineups] | None:
        if not self.is_configured:
            return None
        # Schedule endpoint: /games/{year}/{month}/{day}/schedule.json?api_key=...
        try:
            y, m, d = date_iso.split("-")
            url = f"{self.BASE}/games/{y}/{m}/{d}/schedule.json"
            sched = self._fetch(url, {"api_key": self._api_key}, None)
        except Exception as exc:
            log.warning("Sportradar schedule failed: %s", exc)
            return None
        out: list[GameLineups] = []
        for g in sched.get("games", []) or []:
            sr_id = g.get("id")
            if not sr_id:
                continue
            try:
                summary = self._fetch(
                    f"{self.BASE}/games/{sr_id}/summary.json",
                    {"api_key": self._api_key}, None,
                )
                gl = self._parse_summary(summary)
                if gl is not None:
                    out.append(gl)
            except Exception as exc:
                log.debug("Sportradar summary %s failed: %s", sr_id, exc)
        return out or None

    def fetch_game(self, game_pk: int) -> GameLineups | None:
        # Sportradar uses its own GUIDs, not MLB gamePks. Mapping requires the
        # schedule feed first. Callers should prefer fetch_daily() — fetch_game
        # is left intentionally unimplemented here.
        return None

    def _parse_summary(self, summary: dict[str, Any]) -> GameLineups | None:
        # Minimal parser — kept small and defensive. Real-world Sportradar
        # payloads carry every starter under ``home.lineup`` / ``away.lineup``
        # with a ``order`` integer. We translate that into our shared schema.
        game = summary.get("game") or summary
        if not game:
            return None

        def _team(side: str) -> TeamLineup:
            t = game.get(side) or {}
            starters: list[LineupPlayer] = []
            bench: list[LineupPlayer] = []
            for entry in (t.get("lineup") or []):
                lp = LineupPlayer(
                    player_id=None,  # Sportradar IDs are GUIDs, not MLB ints
                    name=entry.get("preferred_name", "") + " " + entry.get("last_name", ""),
                    position=entry.get("position", "") or "",
                    bat_side="",
                    batting_order=entry.get("order"),
                    is_starter=True,
                )
                starters.append(lp)
            for entry in (t.get("roster") or []):
                bench.append(LineupPlayer(
                    player_id=None,
                    name=entry.get("full_name", "") or "",
                    position=entry.get("position", "") or "",
                ))
            pp = t.get("probable_pitcher") or {}
            tl = TeamLineup(
                team_id=None,
                team_abbr=(t.get("abbr") or "").upper(),
                team_name=t.get("name", "") or "",
                starters=starters,
                bench=bench,
                probable_pitcher_id=None,
                probable_pitcher_name=pp.get("full_name", "") or "",
                probable_pitcher_hand=pp.get("throw_hand", "") or "",
            )
            tl.status = LINEUP_STATUS_CONFIRMED if len(starters) >= 9 else LINEUP_STATUS_NOT_POSTED
            return tl

        gpk = 0
        ids = game.get("reference") or {}
        try:
            gpk = int(ids.get("mlb_game_id") or 0)
        except (TypeError, ValueError):
            gpk = 0
        status = (game.get("status") or "").lower()
        return GameLineups(
            game_pk=gpk,
            game_date=game.get("scheduled", "")[:10],
            game_time_utc=game.get("scheduled", "") or "",
            status=game.get("status", "") or "",
            abstract_status=("live" if status == "inprogress"
                             else "final" if status == "closed"
                             else "preview"),
            venue=(game.get("venue") or {}).get("name", "") or "",
            away=_team("away"),
            home=_team("home"),
            provider=self.name,
        )


class SportsDataIOProvider(LineupProvider):
    """SportsDataIO MLB starting-lineups endpoint.

    Activated when ``SPORTSDATAIO_MLB_API_KEY`` is set. The key is sent via
    the ``Ocp-Apim-Subscription-Key`` header.
    """

    name = "sportsdataio"
    BASE = "https://api.sportsdata.io/v3/mlb"

    def __init__(self, api_key: str | None = None,
                 fetcher: Fetcher | None = None) -> None:
        self._api_key = api_key or os.environ.get("SPORTSDATAIO_MLB_API_KEY", "")
        self._fetch = fetcher or _default_http_fetcher

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {"Ocp-Apim-Subscription-Key": self._api_key,
                "Accept": "application/json"}

    def fetch_daily(self, date_iso: str) -> list[GameLineups] | None:
        if not self.is_configured:
            return None
        try:
            # SportsDataIO formats dates as YYYY-MMM-DD (e.g. 2026-MAY-17).
            y, m, d = date_iso.split("-")
            months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
            sd_date = f"{y}-{months[int(m) - 1]}-{d}"
            url = f"{self.BASE}/stats/json/StartingLineupsByDate/{sd_date}"
            data = self._fetch(url, None, self._headers())
        except Exception as exc:
            log.warning("SportsDataIO StartingLineups failed: %s", exc)
            return None
        return self._parse_starting_lineups(data)

    def fetch_game(self, game_pk: int) -> GameLineups | None:
        return None

    def _parse_starting_lineups(self, data: list[dict[str, Any]] | dict[str, Any]
                                ) -> list[GameLineups] | None:
        # SportsDataIO returns a list of per-game records, each carrying both
        # teams' batting orders under HomeTeamLineup / AwayTeamLineup arrays.
        rows = data if isinstance(data, list) else (data.get("Lineups") or [])
        out: list[GameLineups] = []
        for row in rows:
            try:
                gpk = int(row.get("GameID") or 0)
            except (TypeError, ValueError):
                gpk = 0

            def _side(prefix: str) -> TeamLineup:
                starters: list[LineupPlayer] = []
                lineup_arr = row.get(f"{prefix}TeamLineup") or []
                for entry in lineup_arr:
                    starters.append(LineupPlayer(
                        player_id=entry.get("PlayerID"),
                        name=entry.get("Name", "") or "",
                        position=entry.get("Position", "") or "",
                        bat_side=entry.get("BatHand", "") or "",
                        batting_order=entry.get("BattingOrder"),
                        is_starter=True,
                    ))
                pp_name = row.get(f"{prefix}TeamStartingPitcherName", "") or ""
                tl = TeamLineup(
                    team_id=None,
                    team_abbr=(row.get(f"{prefix}Team") or "").upper(),
                    starters=starters,
                    probable_pitcher_id=row.get(f"{prefix}TeamStartingPitcherID"),
                    probable_pitcher_name=pp_name,
                )
                tl.status = LINEUP_STATUS_CONFIRMED if len(starters) >= 9 else LINEUP_STATUS_EXPECTED
                return tl

            out.append(GameLineups(
                game_pk=gpk,
                game_date=(row.get("Day") or "")[:10],
                game_time_utc=row.get("DateTime", "") or "",
                status=row.get("Status", "") or "",
                abstract_status="preview",
                venue=row.get("StadiumName", "") or "",
                away=_side("Away"),
                home=_side("Home"),
                provider=self.name,
            ))
        return out or None


# --- Orchestration ----------------------------------------------------------

class LineupService:
    """Chains providers in priority order and caches results with a TTL that
    reacts to lineup status: short pre-game so we pick up the moment MLB
    publishes the order, longer once the slate is final."""

    # TTL in seconds, keyed by lineup status.
    TTL_BY_STATUS = {
        LINEUP_STATUS_NOT_POSTED: 60,
        LINEUP_STATUS_EXPECTED:   90,
        LINEUP_STATUS_CONFIRMED:  120,
        LINEUP_STATUS_LIVE:       45,
        LINEUP_STATUS_FINAL:      3600,
        LINEUP_STATUS_POSTPONED:  900,
    }

    def __init__(self, providers: Iterable[LineupProvider] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if providers is None:
            providers = default_providers()
        self._providers: list[LineupProvider] = [p for p in providers if p is not None]
        self._clock = clock
        self._cache_daily: dict[str, tuple[float, list[GameLineups]]] = {}
        self._cache_game: dict[int, tuple[float, GameLineups]] = {}

    # ---- Daily ----

    def get_daily(self, date_iso: str, *, force: bool = False
                  ) -> list[GameLineups]:
        now = self._clock()
        cached = self._cache_daily.get(date_iso)
        if cached and not force:
            expires_at, payload = cached
            if expires_at > now:
                return payload
        result: list[GameLineups] | None = None
        for prov in self._providers:
            if not prov.is_configured:
                continue
            try:
                got = prov.fetch_daily(date_iso)
            except Exception as exc:
                log.warning("Provider %s.fetch_daily raised: %s", prov.name, exc)
                got = None
            if got:
                result = got
                break
        if result is None:
            result = []
        # TTL is computed off the worst status across the slate.
        ttl = self._slate_ttl(result)
        self._cache_daily[date_iso] = (now + ttl, result)
        return result

    def get_game(self, game_pk: int, *, force: bool = False
                 ) -> GameLineups | None:
        now = self._clock()
        cached = self._cache_game.get(game_pk)
        if cached and not force:
            expires_at, payload = cached
            if expires_at > now:
                return payload
        result: GameLineups | None = None
        for prov in self._providers:
            if not prov.is_configured:
                continue
            try:
                got = prov.fetch_game(game_pk)
            except Exception as exc:
                log.warning("Provider %s.fetch_game raised: %s", prov.name, exc)
                got = None
            if got is not None:
                result = got
                break
        if result is None:
            return None
        ttl = self.TTL_BY_STATUS.get(result.lineup_status, 120)
        self._cache_game[game_pk] = (now + ttl, result)
        return result

    def _slate_ttl(self, slate: list[GameLineups]) -> int:
        if not slate:
            return 60
        # Pick the shortest TTL across the slate so the cache always refreshes
        # at the cadence of the freshest-changing game.
        return min(
            self.TTL_BY_STATUS.get(g.lineup_status, 120) for g in slate
        )


def default_providers() -> list[LineupProvider]:
    """Provider order: premium first if keys present, MLB StatsAPI last."""
    providers: list[LineupProvider] = []
    sportradar = SportradarProvider()
    if sportradar.is_configured:
        providers.append(sportradar)
    sportsdataio = SportsDataIOProvider()
    if sportsdataio.is_configured:
        providers.append(sportsdataio)
    providers.append(MLBStatsAPIProvider())
    return providers


# --- Module-level convenience -----------------------------------------------

_singleton: LineupService | None = None


def get_service() -> LineupService:
    global _singleton
    if _singleton is None:
        _singleton = LineupService()
    return _singleton


def get_daily_lineups(date_iso: str | _date | datetime) -> list[GameLineups]:
    if isinstance(date_iso, datetime):
        date_iso = date_iso.date().isoformat()
    elif isinstance(date_iso, _date):
        date_iso = date_iso.isoformat()
    return get_service().get_daily(date_iso)


def get_game_lineups(game_pk: int) -> GameLineups | None:
    return get_service().get_game(int(game_pk))


# --- Adapter helpers for the existing Streamlit app -------------------------

def lineup_to_dict_rows(team: TeamLineup) -> list[dict[str, Any]]:
    """Translate a ``TeamLineup`` into the row-dict format the app's
    existing ``build_matchup_table`` / ``build_rolling_table`` helpers expect.
    Keeps column names consistent with ``roster_df_from_box``."""
    rows: list[dict[str, Any]] = []
    for p in team.starters:
        rows.append({
            "player_name": p.name,
            "team": team.team_abbr,
            "position": p.position,
            "bat_side": p.bat_side,
            "pitch_hand": p.pitch_hand,
            "player_id": p.player_id,
            "lineup_spot": float(p.batting_order) if p.batting_order else None,
            "is_substitute": p.is_substitute,
        })
    return rows


def format_freshness(gl: GameLineups) -> str:
    """One-line freshness chip text for the UI: provider + status + age."""
    age = datetime.now(timezone.utc) - gl.last_updated
    secs = max(0, int(age.total_seconds()))
    if secs < 60:
        age_str = f"{secs}s ago"
    elif secs < 3600:
        age_str = f"{secs // 60}m ago"
    else:
        age_str = f"{secs // 3600}h ago"
    return f"{gl.provider} · {gl.lineup_status} · {age_str}"
