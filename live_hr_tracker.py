"""Live HR Tracker — Apps & Generators module.

Drop-in module rendered by ``app.py`` when the user selects the
``🏟️ Live HR Tracker`` view. Everything is namespaced under ``.lhrt-*``
so the styling cannot leak into other Streamlit pages.

Public surface used by app.py:

- ``render_live_hr_tracker()`` — full page renderer (call from the view block).
- ``build_hr_card(player_data)`` — reusable HTML card builder. Safe to feed
  into ``st.components.v1.html(build_hr_card(player_data), height=200)``
  from a custom polling loop.
- ``poll_live_hr_events(...)`` — pluggable polling hook. The default
  implementation returns ``[]`` so the user can wire a real MLB feed
  (StatsAPI GUMBO ``/api/v1.1/game/{pk}/feed/live`` is the recommended
  source). When demo mode is on, the simulator produces fake events.

The module purposefully has zero hard runtime dependencies beyond
``streamlit`` and the stdlib so it works regardless of which optional
data feeds are configured in the host app.
"""

from __future__ import annotations

import html
import json
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

try:  # Python 3.9+ stdlib
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover - fallback to manual offsets
    ZoneInfo = None  # type: ignore

import streamlit as st

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - requests is in requirements.txt
    requests = None  # type: ignore

# Optional safe auto-rerun helper. Falls back to a JS meta-refresh component
# if the package is missing, so the page still auto-updates without freezing.
try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore
except Exception:  # pragma: no cover - optional dep
    st_autorefresh = None  # type: ignore


# ---------------------------------------------------------------------------
# Team color palette (compact fallback — used only if the host app doesn't
# already expose one). Values are (primary, secondary) hex pairs hand-picked
# to mirror each franchise's wordmark.
# ---------------------------------------------------------------------------
TEAM_COLORS: dict[str, tuple[str, str]] = {
    "ARI": ("#A71930", "#E3D4AD"), "ATL": ("#CE1141", "#13274F"),
    "BAL": ("#DF4601", "#000000"), "BOS": ("#BD3039", "#0C2340"),
    "CHC": ("#0E3386", "#CC3433"), "CWS": ("#27251F", "#C4CED4"),
    "CIN": ("#C6011F", "#000000"), "CLE": ("#00385D", "#E50022"),
    "COL": ("#33006F", "#C4CED4"), "DET": ("#0C2340", "#FA4616"),
    "HOU": ("#002D62", "#EB6E1F"), "KC":  ("#004687", "#BD9B60"),
    "LAA": ("#BA0021", "#003263"), "LAD": ("#005A9C", "#EF3E42"),
    "MIA": ("#00A3E0", "#EF3340"), "MIL": ("#12284B", "#FFC52F"),
    "MIN": ("#002B5C", "#D31145"), "NYM": ("#002D72", "#FF5910"),
    "NYY": ("#0C2340", "#C4CED4"), "OAK": ("#003831", "#EFB21E"),
    "ATH": ("#003831", "#EFB21E"),  # 2025 brand for Oakland → Athletics
    "PHI": ("#E81828", "#002D72"), "PIT": ("#FDB827", "#27251F"),
    "SD":  ("#2F241D", "#FFC425"), "SF":  ("#FD5A1E", "#27251F"),
    "SEA": ("#0C2C56", "#005C5C"), "STL": ("#C41E3A", "#0C2340"),
    "TB":  ("#092C5C", "#8FBCE6"), "TEX": ("#003278", "#C0111F"),
    "TOR": ("#134A8E", "#1D2D5C"), "WSH": ("#AB0003", "#14225A"),
}
_DEFAULT_TEAM = ("#1f2937", "#fcd34d")


def _team_colors(team_abbr: str | None) -> tuple[str, str]:
    if not team_abbr:
        return _DEFAULT_TEAM
    return TEAM_COLORS.get(str(team_abbr).upper().strip(), _DEFAULT_TEAM)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _esc(value: Any) -> str:
    """HTML-escape any value, returning empty string for None/NaN."""
    if value is None:
        return ""
    try:
        # pandas NaN / numpy NaN are floats whose != themselves
        if isinstance(value, float) and value != value:
            return ""
    except Exception:
        pass
    return html.escape(str(value), quote=True)


def _tier_color(metric: str, raw: float | None) -> str:
    """Color-code a stat value: gold elite, fire-orange great, green good."""
    if raw is None:
        return "#cbd5e1"
    thresholds: dict[str, tuple[float, float, float]] = {
        # (good, great, elite) — values >= elite get gold, etc.
        "season_hr":  (15, 25, 35),
        "ops":        (.750, .850, .950),
        "iso":        (.180, .230, .280),
        "barrel_pct": (8.0, 12.0, 16.0),
    }
    t = thresholds.get(metric)
    if not t:
        return "#e2e8f0"
    good, great, elite = t
    if raw >= elite:
        return "#fcd34d"  # gold
    if raw >= great:
        return "#fb923c"  # fire orange
    if raw >= good:
        return "#34d399"  # green good
    return "#cbd5e1"      # neutral


def _hr_type(rbi: int, on_base: int | None = None) -> str:
    """Categorize HR by RBI: 1=Solo, 2/3=Two/Three-Run, 4=Grand Slam."""
    try:
        r = int(rbi)
    except Exception:
        r = 1
    if r >= 4:
        return "Grand Slam"
    if r == 3:
        return "Three-Run HR"
    if r == 2:
        return "Two-Run HR"
    return "Solo HR"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class HRPlayer:
    name: str = ""
    team: str = ""
    jersey: str = ""
    avatar_url: str = ""
    player_id: int | str | None = None  # MLB person id — used to derive headshot URL
    season_hr: int | None = None
    ops: float | None = None
    iso: float | None = None
    barrel_pct: float | None = None
    # Event-specific fields
    rbi: int = 1
    exit_velo: float | None = None
    distance: int | None = None
    matchup: str = ""           # e.g. "off RHP Snell · NYY vs BOS"
    timestamp: str | None = None  # ISO-8601 or pre-formatted clock string
    event_id: str = ""          # unique id for de-dup

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HRPlayer":
        keys = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in keys})


# MLB official headshot CDN — current/cuts/120/{player_id} renders a clean
# 120px headshot for any active player. Used when we have a player_id but no
# explicit avatar_url. The URL is purely numeric → safe to interpolate.
_HEADSHOT_URL_TEMPLATE = (
    "https://midfield.mlbstatic.com/v1/people/{pid}/spots/120"
)


def _safe_avatar_url(avatar_url: Any, player_id: Any) -> str:
    """Return a safe http(s) URL for the avatar img, or '' to force fallback.

    - Trims whitespace.
    - Rejects empty / non-string / scheme-less / javascript:/data: URLs.
    - Falls back to the MLB headshot CDN when ``player_id`` is a positive int.
    """
    candidate = ""
    if avatar_url is not None:
        try:
            candidate = str(avatar_url).strip()
        except Exception:
            candidate = ""
    if candidate:
        low = candidate.lower()
        if low.startswith(("http://", "https://")) and " " not in candidate:
            return candidate
        # anything else (javascript:, data:, file:, relative, garbage) → drop
    # Derive from player_id if we have one.
    try:
        pid_int = int(str(player_id).strip()) if player_id not in (None, "") else 0
    except Exception:
        pid_int = 0
    if pid_int > 0:
        return _HEADSHOT_URL_TEMPLATE.format(pid=pid_int)
    return ""


# ---------------------------------------------------------------------------
# Card renderer
# ---------------------------------------------------------------------------
def build_hr_card(player_data: Any, *, dim: bool = False) -> str:
    """Build the HTML for a single HR card.

    ``player_data`` may be an :class:`HRPlayer`, a plain ``dict``, or any
    object with the same attribute names. Unknown / missing fields render
    as ``—``. All values are HTML-escaped before being injected.

    Pass ``dim=True`` to render the post-3s "settled" look (used for older
    cards in the feed so the freshest one always pops).
    """
    if isinstance(player_data, HRPlayer):
        p = player_data
    elif isinstance(player_data, dict):
        p = HRPlayer.from_dict(player_data)
    else:
        # Generic object — pull attributes defensively
        p = HRPlayer(
            **{f.name: getattr(player_data, f.name, None)
               for f in HRPlayer.__dataclass_fields__.values()  # type: ignore[attr-defined]
               if hasattr(player_data, f.name)}
        )

    primary, secondary = _team_colors(p.team)
    hr_type = _hr_type(p.rbi)
    # Stat blocks
    def stat_block(label: str, value: Any, metric: str, fmt: str = "{:.0f}") -> str:
        raw = None
        try:
            raw = float(value) if value is not None and value != "" else None
        except Exception:
            raw = None
        display = "—" if raw is None else fmt.format(raw)
        color = _tier_color(metric, raw)
        return (
            f'<div class="lhrt-stat">'
            f'<div class="lhrt-stat-label">{_esc(label)}</div>'
            f'<div class="lhrt-stat-value" style="color:{color};">{_esc(display)}</div>'
            f'</div>'
        )

    stats_html = (
        stat_block("Season HR", p.season_hr, "season_hr", "{:.0f}")
        + stat_block("OPS",     p.ops,        "ops",        "{:.3f}")
        + stat_block("ISO",     p.iso,        "iso",        "{:.3f}")
        + stat_block("Barrel%", p.barrel_pct, "barrel_pct", "{:.1f}%")
    )

    # Footer strip
    def foot(label: str, value: Any, suffix: str = "") -> str:
        if value is None or value == "":
            value = "—"
            suffix = ""
        return (
            f'<span class="lhrt-foot-item">'
            f'<span class="lhrt-foot-label">{_esc(label)}</span>'
            f'<span class="lhrt-foot-value">{_esc(value)}{_esc(suffix)}</span>'
            f'</span>'
        )

    ts_display = p.timestamp or ""
    if ts_display:
        # Try to render relative time if it's parseable as ISO.
        try:
            t = datetime.fromisoformat(str(ts_display).replace("Z", "+00:00"))
            ts_display = t.astimezone().strftime("%I:%M:%S %p").lstrip("0")
        except Exception:
            ts_display = str(p.timestamp)

    safe_url = _safe_avatar_url(p.avatar_url, p.player_id)
    initials = "".join(part[:1] for part in (p.name or "").split()[:2]).upper() or "?"
    # Avatar markup strategy:
    # - If we have a safe URL: render <img>. The .lhrt-avatar div does NOT
    #   get the fallback class up front, so initials are hidden behind the
    #   loaded headshot. If the image fails to load the inline onerror
    #   removes itself and toggles the fallback class on the parent so
    #   the CSS-only initials show. The handler references `this` only and
    #   uses no string literals, so quoting is bulletproof.
    # - If we have no URL: render the fallback directly. No <img> tag at all.
    if safe_url:
        avatar_classes = "lhrt-avatar"
        avatar_inner = (
            f'<img src="{_esc(safe_url)}" alt="" loading="lazy" '
            f'referrerpolicy="no-referrer" '
            f'onerror="this.onerror=null;this.remove();'
            f'this.parentNode&amp;&amp;this.parentNode.classList.add(&quot;lhrt-avatar-fallback&quot;);">'
        )
    else:
        avatar_classes = "lhrt-avatar lhrt-avatar-fallback"
        avatar_inner = ""

    card_classes = "lhrt-card" + (" lhrt-card-dim" if dim else " lhrt-card-fresh")
    grand_slam_badge = ""
    if hr_type == "Grand Slam":
        grand_slam_badge = '<div class="lhrt-gs-badge">⚡ GRAND SLAM ⚡</div>'

    return f"""
<div class="{card_classes}" style="--team-primary:{primary};--team-secondary:{secondary};">
  {grand_slam_badge}
  <div class="lhrt-card-head">
    <div class="{avatar_classes}" data-initials="{_esc(initials)}">
      {avatar_inner}
      <span class="lhrt-jersey">#{_esc(p.jersey or '—')}</span>
    </div>
    <div class="lhrt-id">
      <div class="lhrt-name">{_esc(p.name or 'Unknown Hitter')}</div>
      <div class="lhrt-team-tag">{_esc(p.team or '—')} · {_esc(hr_type)}</div>
    </div>
  </div>
  <div class="lhrt-stats">{stats_html}</div>
  <div class="lhrt-foot">
    {foot('EV',       f'{p.exit_velo:.1f}'  if isinstance(p.exit_velo, (int, float)) else p.exit_velo, ' mph')}
    {foot('Dist',     p.distance, ' ft')}
    {foot('RBI',      p.rbi)}
    {foot('Matchup',  p.matchup)}
    {foot('Time',     ts_display)}
  </div>
</div>
""".strip()


# ---------------------------------------------------------------------------
# Page-level CSS / scaffold (scoped to .lhrt-*)
# ---------------------------------------------------------------------------
_PAGE_CSS = """
<style>
.lhrt-wrap { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  color: #f8fafc; background: linear-gradient(180deg, #0b1020 0%, #14062e 60%, #1a0f3d 100%);
  border-radius: 18px; padding: 14px 14px 18px; border: 2px solid #312e81;
  box-shadow: 0 10px 40px rgba(20, 5, 50, .5); position: relative; overflow: hidden; }

@keyframes lhrt-banner-pulse {
  0%, 100% { box-shadow: 0 0 30px rgba(251, 146, 60, .55), inset 0 0 25px rgba(252, 211, 77, .25); }
  50%      { box-shadow: 0 0 55px rgba(251, 146, 60, .95), inset 0 0 45px rgba(252, 211, 77, .55); }
}
@keyframes lhrt-banner-flicker {
  0%,100% { filter: brightness(1.0) saturate(1.05); }
  20%     { filter: brightness(1.12) saturate(1.15); }
  60%     { filter: brightness(.95) saturate(1.0); }
}
.lhrt-banner { display:flex; align-items:center; justify-content:center;
  gap:10px; padding: 14px 18px; margin: 0 0 12px 0; border-radius: 14px;
  background: linear-gradient(110deg, #7c2d12 0%, #ea580c 35%, #facc15 70%, #ea580c 100%);
  color: #1c1917; font-weight: 900; font-size: 1.4rem; letter-spacing: .04em;
  text-transform: uppercase; text-shadow: 0 1px 0 rgba(255,255,255,.45);
  border: 2px solid #fcd34d;
  animation: lhrt-banner-pulse 1.8s ease-in-out infinite, lhrt-banner-flicker 2.4s ease-in-out infinite; }
.lhrt-banner-quiet { background: linear-gradient(110deg, #1e293b, #312e81);
  color: #fcd34d; animation: none; border-color: #4338ca; font-size: 1.05rem; }

.lhrt-ticker-row { background: rgba(15, 23, 42, .85);
  border: 1px solid #4338ca; border-radius: 999px; padding: 6px 0;
  overflow: hidden; margin: 0 0 10px 0; position: relative; }
.lhrt-ticker { display:inline-block; white-space: nowrap; padding-left: 100%;
  animation: lhrt-ticker-scroll 35s linear infinite; font-weight: 700;
  color: #fcd34d; font-size: .92rem; letter-spacing: .03em; }
.lhrt-ticker span { margin-right: 28px; }
.lhrt-ticker .lhrt-ticker-sep { color: #f97316; margin: 0 18px; }
@keyframes lhrt-ticker-scroll {
  from { transform: translate3d(0,0,0); }
  to   { transform: translate3d(-100%,0,0); }
}

.lhrt-counters { display:grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
  margin-bottom: 12px; }
.lhrt-counter { background: linear-gradient(180deg, #1e1b4b 0%, #312e81 100%);
  border: 1px solid #4338ca; border-radius: 14px; padding: 10px 12px; text-align:center;
  position: relative; overflow: hidden; }
.lhrt-counter-label { font-size: .72rem; letter-spacing: .12em; color: #c7d2fe;
  text-transform: uppercase; font-weight: 800; }
.lhrt-counter-value { font-size: 2.2rem; font-weight: 900; line-height: 1;
  color: #fcd34d; font-variant-numeric: tabular-nums;
  text-shadow: 0 0 18px rgba(252, 211, 77, .45);
  animation: lhrt-num-flip .6s ease-out; }
@keyframes lhrt-num-flip {
  0%   { transform: translateY(-14px) rotateX(80deg); opacity: 0; }
  60%  { transform: translateY(2px)   rotateX(-12deg); opacity: 1; }
  100% { transform: translateY(0)     rotateX(0); opacity: 1; }
}

.lhrt-feed { display:flex; flex-direction:column; gap: 10px; }

@keyframes lhrt-burst {
  0%   { transform: scale(.85) translateY(8px); opacity: 0;
         box-shadow: 0 0 0px 0 rgba(251, 146, 60, .9); }
  60%  { transform: scale(1.02) translateY(-2px); opacity: 1;
         box-shadow: 0 0 60px 14px rgba(251, 146, 60, .85); }
  100% { transform: scale(1) translateY(0); opacity: 1;
         box-shadow: 0 0 30px 6px rgba(251, 146, 60, .55); }
}
@keyframes lhrt-ring-pulse {
  0%, 100% { box-shadow: 0 0 18px 2px rgba(251, 146, 60, .35),
                         inset 0 0 18px rgba(252, 211, 77, .25); }
  50%      { box-shadow: 0 0 40px 6px rgba(251, 146, 60, .8),
                         inset 0 0 28px rgba(252, 211, 77, .55); }
}
.lhrt-card { position: relative; padding: 14px; border-radius: 16px;
  background: linear-gradient(155deg, rgba(15,23,42,.95) 0%, rgba(30,27,75,.95) 100%);
  border: 2px solid var(--team-primary, #312e81);
  color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
.lhrt-card-fresh { animation: lhrt-burst .9s ease-out;
  animation-fill-mode: both; }
.lhrt-card-fresh::before { content:""; position:absolute; inset:-2px; border-radius:18px;
  pointer-events:none; animation: lhrt-ring-pulse 1.4s ease-in-out infinite; }
.lhrt-card-dim { filter: saturate(.85) brightness(.92); opacity: .92;
  transition: filter .6s ease, opacity .6s ease; }
.lhrt-gs-badge { position:absolute; top: -10px; right: 14px;
  background: linear-gradient(110deg, #facc15, #ef4444); color:#1c1917;
  font-weight:900; font-size:.72rem; letter-spacing: .12em; padding: 4px 10px;
  border-radius: 999px; border: 1px solid #fcd34d;
  text-transform: uppercase; box-shadow: 0 0 14px rgba(252,211,77,.7); }

.lhrt-card-head { display:flex; align-items:center; gap: 12px; margin-bottom: 10px; }
.lhrt-avatar { position: relative; width: 58px; height: 58px; border-radius: 50%;
  background: linear-gradient(135deg, var(--team-primary), var(--team-secondary));
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
  border: 3px solid #fcd34d;
  box-shadow: 0 0 16px rgba(252, 211, 77, .6), inset 0 0 8px rgba(0,0,0,.35);
  overflow: visible; }
.lhrt-avatar img { width: 100%; height: 100%; border-radius:50%; object-fit: cover; }
.lhrt-avatar-fallback::after { content: attr(data-initials);
  position: absolute; inset: 0; display:flex; align-items:center; justify-content:center;
  color: #f8fafc; font-weight:900; font-size: 1.2rem; letter-spacing: .04em;
  text-shadow: 0 1px 2px rgba(0,0,0,.6); }
.lhrt-avatar img { position: relative; z-index: 1; }
.lhrt-jersey { position:absolute; bottom: -4px; right: -6px;
  background: #0f172a; color:#fcd34d; font-size:.68rem; font-weight:900;
  padding: 2px 6px; border-radius: 999px; border: 1px solid #fcd34d;
  letter-spacing: .04em; z-index: 2; }
.lhrt-id { flex: 1; min-width: 0; }
.lhrt-name { font-weight: 900; font-size: 1.15rem; line-height: 1.15;
  color: #f8fafc; text-shadow: 0 1px 1px rgba(0,0,0,.5); }
.lhrt-team-tag { display:inline-block; margin-top: 4px;
  background: var(--team-primary); color: var(--team-secondary);
  font-weight: 800; font-size: .72rem; letter-spacing: .08em;
  padding: 3px 9px; border-radius: 999px; text-transform: uppercase;
  border: 1px solid rgba(255,255,255,.18); }

.lhrt-stats { display:grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
  margin: 6px 0 8px 0; }
.lhrt-stat { background: rgba(15, 23, 42, .55); border: 1px solid #1e293b;
  border-radius: 10px; padding: 8px 6px; text-align:center; }
.lhrt-stat-label { font-size: .62rem; letter-spacing: .12em; font-weight: 800;
  text-transform: uppercase; color: #94a3b8; }
.lhrt-stat-value { font-size: 1.15rem; font-weight: 900; line-height: 1.05;
  font-variant-numeric: tabular-nums; margin-top: 2px; }

.lhrt-foot { display:flex; flex-wrap:wrap; gap: 6px 12px;
  padding-top: 8px; border-top: 1px dashed rgba(148, 163, 184, .25);
  font-size: .82rem; color: #cbd5e1; }
.lhrt-foot-item { display:inline-flex; gap: 5px; align-items: baseline;
  font-variant-numeric: tabular-nums; }
.lhrt-foot-label { color: #94a3b8; font-weight:700; letter-spacing: .04em;
  text-transform: uppercase; font-size: .68rem; }
.lhrt-foot-value { color: #f1f5f9; font-weight: 700; }

.lhrt-empty { padding: 22px 16px; text-align:center; color: #94a3b8;
  font-style: italic; border: 1px dashed #312e81; border-radius: 12px;
  background: rgba(15, 23, 42, .55); }

/* Confetti canvas pinned over the card area */
.lhrt-confetti-canvas { position: fixed; pointer-events: none; inset: 0;
  z-index: 9999; }

@media (max-width: 640px) {
  .lhrt-stats { grid-template-columns: repeat(2, 1fr); }
  .lhrt-banner { font-size: 1.1rem; padding: 12px 14px; }
  .lhrt-counter-value { font-size: 1.7rem; }
}
</style>
"""


# ---------------------------------------------------------------------------
# Polling hook (public seam for wiring real data later)
# ---------------------------------------------------------------------------
def poll_live_hr_events(
    fetcher: Callable[[], Iterable[dict[str, Any]]] | None = None,
    *,
    seen_ids: set[str] | None = None,
) -> list[HRPlayer]:
    """Pull a batch of new HR events.

    The ``fetcher`` argument is the user-supplied bridge to a real feed
    (e.g. MLB StatsAPI ``/api/v1.1/game/{pk}/feed/live``). It must return
    an iterable of dicts shaped like :class:`HRPlayer`. Each event must
    carry a stable ``event_id`` so we can de-dup across polls.

    Returns ``[]`` when no fetcher is wired — the caller decides what to
    do (e.g. fall back to demo mode).
    """
    if fetcher is None:
        return []
    seen_ids = seen_ids if seen_ids is not None else set()
    out: list[HRPlayer] = []
    try:
        for raw in fetcher() or []:
            event_id = str(raw.get("event_id") or "").strip()
            if event_id and event_id in seen_ids:
                continue
            if event_id:
                seen_ids.add(event_id)
            out.append(HRPlayer.from_dict(raw))
    except Exception as e:
        # Never let a feed hiccup crash the page.
        st.warning(f"Live HR feed error: {e}")
    return out


# ---------------------------------------------------------------------------
# Demo simulator — gives the user something to look at out of the box.
# ---------------------------------------------------------------------------
_DEMO_BATTERS = [
    # name, team, jersey, season_hr, ops, iso, barrel_pct, mlbam_id
    ("Aaron Judge",       "NYY", "99", 42, .998, .305, 19.8, 592450),
    ("Shohei Ohtani",     "LAD", "17", 38, .987, .298, 17.4, 660271),
    ("Juan Soto",         "NYY", "22", 31, .955, .267, 15.2, 665742),
    ("Mookie Betts",      "LAD", "50", 24, .891, .234, 13.8, 605141),
    ("Bryce Harper",      "PHI", "3",  28, .912, .248, 14.6, 547180),
    ("Ronald Acuña Jr.",  "ATL", "13", 26, .934, .241, 14.9, 660670),
    ("Pete Alonso",       "NYM", "20", 33, .854, .258, 16.1, 624413),
    ("Yordan Alvarez",    "HOU", "44", 30, .945, .276, 17.8, 670541),
    ("Kyle Tucker",       "HOU", "30", 25, .889, .238, 13.4, 663656),
    ("Vladimir Guerrero", "TOR", "27", 27, .872, .231, 14.1, 665489),
    ("Rafael Devers",     "BOS", "11", 29, .881, .249, 13.9, 646240),
    ("Matt Olson",        "ATL", "28", 31, .867, .254, 15.3, 621566),
    ("Corey Seager",      "TEX", "5",  26, .898, .256, 13.2, 608369),
    ("Bobby Witt Jr.",    "KC",  "7",  22, .914, .242, 11.8, 677951),
    ("Gunnar Henderson",  "BAL", "2",  28, .883, .247, 13.5, 683002),
]
_DEMO_PITCHERS = [
    "Gerrit Cole", "Tarik Skubal", "Zack Wheeler", "Logan Webb",
    "Pablo López", "Spencer Strider", "Corbin Burnes", "Aaron Nola",
    "Blake Snell", "Sonny Gray", "Framber Valdez", "Yoshinobu Yamamoto",
]


def _simulate_event(seq: int) -> HRPlayer:
    """Build a single fake HR event."""
    name, team, jersey, hr, ops, iso, brl, pid = random.choice(_DEMO_BATTERS)
    pitcher = random.choice(_DEMO_PITCHERS)
    rbi = random.choices([1, 2, 3, 4], weights=[55, 25, 15, 5], k=1)[0]
    return HRPlayer(
        name=name, team=team, jersey=jersey,
        player_id=pid,
        season_hr=hr, ops=ops, iso=iso, barrel_pct=brl,
        rbi=rbi,
        exit_velo=round(random.uniform(98.0, 117.0), 1),
        distance=int(random.uniform(360, 480)),
        matchup=f"off {pitcher}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_id=f"demo-{int(time.time()*1000)}-{seq}",
    )


# ---------------------------------------------------------------------------
# Real MLB feed — StatsAPI schedule + per-game live feed.
# ---------------------------------------------------------------------------
# Maps the StatsAPI team full names to the 2/3-letter abbreviations used by
# TEAM_COLORS above. StatsAPI itself usually provides `teams.away.team.abbreviation`
# on the schedule payload, so we only fall back to this map if that field is
# missing or unusual.
_TEAM_NAME_TO_ABBR: dict[str, str] = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Athletics": "ATH",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}
# StatsAPI sometimes emits divergent abbreviations (TBR/CHW/SDP/SFG/WSN/KCR).
# Normalize them to the keys we use in TEAM_COLORS.
_ABBR_ALIASES: dict[str, str] = {
    "TBR": "TB", "TBD": "TB",
    "CHW": "CWS", "WSN": "WSH", "WAS": "WSH",
    "SDP": "SD",  "SFG": "SF",  "KCR": "KC",
}

_STATSAPI_BASE = "https://statsapi.mlb.com"
_ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
_ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary"
_HTTP_TIMEOUT = 12  # seconds
_HTTP_MAX_RETRIES = 2  # additional attempts after the first failure

# Set of lowercase tokens that identify a HR event in StatsAPI / ESPN payloads.
_HR_EVENT_TOKENS = {
    "home_run", "home run", "homer", "homers", "homered",
    "inside-the-park home run", "inside the park home run",
    "grand slam",
}


def _is_hr_event(event_type: str | None, event: str | None,
                 description: str | None = None) -> bool:
    """Return True if any of the StatsAPI play fields identify the play as a HR.

    Checks eventType, event, and (as a last resort) the play description so we
    catch rare casing/punctuation variants in the live feed without depending
    on a single field. Matching is whole-token / substring against a fixed
    allow-list to avoid false positives like ""home runner"" hypotheticals.
    """
    for raw in (event_type, event, description):
        if not raw:
            continue
        low = str(raw).lower()
        for tok in _HR_EVENT_TOKENS:
            if tok in low:
                return True
    return False


def _normalize_abbr(raw: str | None, full_name: str | None = None) -> str:
    """Return a normalized abbreviation suitable for TEAM_COLORS lookup."""
    if raw:
        ab = str(raw).strip().upper()
        return _ABBR_ALIASES.get(ab, ab)
    if full_name:
        return _TEAM_NAME_TO_ABBR.get(str(full_name).strip(), str(full_name)[:3].upper())
    return ""


# MLB's "baseball date" for US viewers tracks the local-stadium calendar day.
# A Streamlit Cloud server runs in UTC, so `datetime.now()` rolls over to the
# next day at 00:00 UTC — which is 7:00 PM CDT / 8:00 PM EDT. That means a
# user watching live games at 8 PM Central sees the *next* day's schedule
# (preview only) and the tracker looks "broken." We anchor the tracking date
# to America/Chicago so the calendar day matches what the user expects for
# the night's slate. ET would also work, but Central is the most-conservative
# midpoint of US baseball viewing and matches the reporting user's timezone.
_BASEBALL_TZ_NAME = "America/Chicago"


def _baseball_tz() -> timezone | Any:
    """Return a tzinfo for America/Chicago. Falls back to a fixed -05:00 (CDT)
    offset if the system has no zoneinfo db (e.g. minimal docker image)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(_BASEBALL_TZ_NAME)
        except Exception:
            pass
    # Best-effort fallback. CDT (-05:00) is correct for the May→Nov MLB season;
    # CST (-06:00) is fine for off-season early-spring training pages.
    now_utc_month = datetime.now(timezone.utc).month
    # Rough DST window: mid-Mar through early-Nov in the US.
    if 3 <= now_utc_month <= 11:
        return timezone(timedelta(hours=-5))
    return timezone(timedelta(hours=-6))


def _baseball_now() -> datetime:
    """Current wall-clock time in MLB's reference timezone (America/Chicago)."""
    return datetime.now(timezone.utc).astimezone(_baseball_tz())


def _today_iso() -> str:
    """Today's MLB calendar date in YYYY-MM-DD, anchored to America/Chicago.

    Using Central time keeps the "today" boundary aligned with US night-game
    viewing — UTC midnight strikes mid-evening Central, which previously made
    the tracker flip to tomorrow's slate while live games were still playing.
    """
    return _baseball_now().strftime("%Y-%m-%d")


def _baseball_dates_to_scan(now: datetime | None = None) -> list[str]:
    """Pick the date(s) the tracker should query.

    Always returns the Central-time "today." If the UTC date currently
    disagrees with the Central date (which happens every evening Central
    between roughly 7 PM and midnight), we also include the UTC date as a
    secondary scan target so we still pick up any late-night games whose
    schedule entry sits on the next calendar day in StatsAPI's UTC view.

    The list preserves order: Central date first, adjacent date second.
    Callers dedupe events by their stable `event_id`.
    """
    tz_ct = _baseball_tz()
    n = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ct_today = n.astimezone(tz_ct).strftime("%Y-%m-%d")
    utc_today = n.strftime("%Y-%m-%d")
    dates = [ct_today]
    if utc_today != ct_today:
        dates.append(utc_today)
    return dates


@dataclass
class FeedStatus:
    """Lightweight, mutable snapshot of the most recent poll. Kept in
    session_state so the status panel can render after each rerun."""
    last_checked: float = 0.0     # epoch seconds
    games_scanned: int = 0
    games_live: int = 0
    games_final: int = 0
    games_preview: int = 0
    hrs_today: int = 0
    last_error: str = ""
    schedule_date: str = ""               # primary (Central-time) date
    schedule_dates: list[str] = field(default_factory=list)  # all scanned
    timezone_label: str = _BASEBALL_TZ_NAME
    source: str = "MLB StatsAPI"  # which feed served the most recent batch
    fallback_used: bool = False
    per_game_errors: int = 0


class MLBLiveHRFeed:
    """Pulls today's MLB games and yields new home-run events.

    Usage:
        feed = MLBLiveHRFeed()
        for ev in feed.fetch_new_events(seen_ids):
            ...

    The instance is intentionally cheap to construct; we cache the schedule
    for ~60s and the per-game ``feed/live`` payloads only on the call stack —
    Streamlit reruns will rebuild the instance, which is fine because the
    de-dup happens through ``seen_ids`` in session_state.
    """

    def __init__(self, *, date_iso: str | None = None,
                 dates_iso: list[str] | None = None,
                 status: FeedStatus | None = None) -> None:
        # ``dates_iso`` wins when both are supplied — used by the renderer to
        # scan Central date + (when they differ) the UTC date. When neither
        # is supplied we auto-pick the right window for the current moment.
        if dates_iso:
            self.dates_iso: list[str] = list(dict.fromkeys(dates_iso))
        elif date_iso:
            self.dates_iso = [date_iso]
        else:
            self.dates_iso = _baseball_dates_to_scan()
        # Keep the legacy single-date attribute for backwards compatibility
        # with any external callers / tests.
        self.date_iso = self.dates_iso[0]
        self.status = status or FeedStatus()
        self.status.schedule_date = self.date_iso
        self.status.schedule_dates = list(self.dates_iso)
        self.status.timezone_label = _BASEBALL_TZ_NAME

    # ---- HTTP ----
    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if requests is None:
            raise RuntimeError("requests is not installed")
        last_exc: Exception | None = None
        for attempt in range(_HTTP_MAX_RETRIES + 1):
            try:
                r = requests.get(
                    url, params=params or {}, timeout=_HTTP_TIMEOUT,
                    headers={"User-Agent": "live-hr-tracker/1.0"},
                )
                r.raise_for_status()
                return r.json() or {}
            except Exception as e:
                last_exc = e
                if attempt < _HTTP_MAX_RETRIES:
                    time.sleep(0.4 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    # ---- Schedule ----
    def fetch_schedule(self) -> list[dict[str, Any]]:
        """Return today's games (each entry is a StatsAPI ``game`` dict).

        Scans every date in ``self.dates_iso`` and dedupes by ``gamePk`` so a
        late-night game that appears on both the Central and UTC calendar
        rows is counted once.
        """
        all_games: list[dict[str, Any]] = []
        seen_pks: set[int] = set()
        for d_iso in self.dates_iso:
            try:
                data = self._get_json(
                    f"{_STATSAPI_BASE}/api/v1/schedule",
                    {"sportId": 1, "date": d_iso, "hydrate": "team,linescore"},
                )
            except Exception as e:
                # Record the first error but keep trying other dates so a
                # single bad date doesn't blank the slate.
                if not self.status.last_error:
                    self.status.last_error = f"schedule {d_iso}: {e}"
                continue
            for d in data.get("dates", []) or []:
                for g in d.get("games", []) or []:
                    try:
                        pk = int(g.get("gamePk"))
                    except Exception:
                        continue
                    if pk in seen_pks:
                        continue
                    seen_pks.add(pk)
                    all_games.append(g)
        return all_games

    # ---- Live feed ----
    def fetch_game_feed(self, game_pk: int) -> dict[str, Any]:
        return self._get_json(f"{_STATSAPI_BASE}/api/v1.1/game/{game_pk}/feed/live")

    # ---- Play → HR dict ----
    @staticmethod
    def _extract_hit_data(play: dict[str, Any]) -> tuple[float | None, int | None]:
        """Walk playEvents (last to first) and grab the first hitData payload."""
        ev_list = play.get("playEvents") or []
        for ev_p in reversed(ev_list):
            hd = ev_p.get("hitData") or {}
            if hd:
                ls = hd.get("launchSpeed")
                td = hd.get("totalDistance")
                try:
                    ls = float(ls) if ls is not None else None
                except Exception:
                    ls = None
                try:
                    td = int(round(float(td))) if td is not None else None
                except Exception:
                    td = None
                return ls, td
        return None, None

    @staticmethod
    def _make_event_id(game_pk: int, play: dict[str, Any]) -> str:
        """Stable id: gamePk + atBatIndex + endTime fallback."""
        about = play.get("about") or {}
        idx = play.get("atBatIndex")
        end = about.get("endTime") or ""
        if idx is not None:
            return f"{game_pk}-ab{idx}"
        return f"{game_pk}-{end}"

    def _build_event(self, game: dict[str, Any], game_pk: int,
                     play: dict[str, Any],
                     away_abbr: str, home_abbr: str) -> dict[str, Any] | None:
        result = play.get("result") or {}
        if not _is_hr_event(
            result.get("eventType"), result.get("event"), result.get("description"),
        ):
            return None
        matchup = play.get("matchup") or {}
        about = play.get("about") or {}
        batter = matchup.get("batter") or {}
        pitcher = matchup.get("pitcher") or {}
        half = (about.get("halfInning") or "").lower()
        inning = about.get("inning")
        batter_team_abbr = away_abbr if half == "top" else home_abbr
        opp_abbr = home_abbr if half == "top" else away_abbr
        try:
            rbi = int(result.get("rbi") or 1)
        except Exception:
            rbi = 1
        launch_speed, distance = self._extract_hit_data(play)
        # Compose matchup string: "off Snell · NYY vs BOS · T5"
        half_short = "T" if half == "top" else "B" if half == "bottom" else ""
        inning_tag = f"{half_short}{inning}" if inning else ""
        pitcher_name = pitcher.get("fullName") or ""
        matchup_parts = []
        if pitcher_name:
            matchup_parts.append(f"off {pitcher_name}")
        matchup_parts.append(f"{batter_team_abbr} vs {opp_abbr}")
        if inning_tag:
            matchup_parts.append(inning_tag)
        batter_id = batter.get("id")
        return {
            "event_id": self._make_event_id(game_pk, play),
            "name": batter.get("fullName") or "Unknown",
            "team": batter_team_abbr,
            "jersey": str(batter.get("primaryNumber") or "") or "",
            "player_id": batter_id,
            "rbi": rbi,
            "exit_velo": launch_speed,
            "distance": distance,
            "matchup": " · ".join(matchup_parts),
            "timestamp": about.get("endTime") or datetime.now(timezone.utc).isoformat(),
            # season stats are populated separately by the enrichment hook
            "season_hr": None, "ops": None, "iso": None, "barrel_pct": None,
            # carry the batter id for later enrichment (also exposed as player_id above)
            "_batter_id": batter_id,
        }

    # ---- Public ----
    def fetch_new_events(self, seen_ids: set[str]) -> list[dict[str, Any]]:
        """Pull all completed HR plays for today's slate; skip any whose
        event_id is already in ``seen_ids``.

        Uses MLB StatsAPI as the primary source. If the schedule call itself
        fails (network, outage, rate-limit) we fall back to ESPN's public
        scoreboard so the page keeps serving today's HRs.
        """
        out: list[dict[str, Any]] = []
        self.status.last_checked = time.time()
        self.status.per_game_errors = 0
        # fetch_schedule never throws — it records the first error in status
        # and returns whatever it could collect.
        prior_err = self.status.last_error
        self.status.last_error = ""
        games = self.fetch_schedule()
        if not games:
            # Total schedule miss across all dates → try ESPN fallback so the
            # page still serves something. We feed ESPN the primary Central
            # date; ESPN's `dates` param accepts a single YYYYMMDD.
            if not self.status.last_error:
                self.status.last_error = prior_err  # keep previous if newly empty
            espn_out: list[dict[str, Any]] = []
            for d_iso in self.dates_iso:
                espn_out.extend(
                    _fetch_espn_hr_events(d_iso, seen_ids, self.status)
                )
            if espn_out:
                self.status.source = "ESPN MLB (fallback)"
                self.status.fallback_used = True
            return espn_out
        self.status.source = "MLB StatsAPI"
        self.status.fallback_used = False
        self.status.games_scanned = len(games)
        live = final_n = preview_n = 0
        first_game_err: str = ""
        for g in games:
            gpk = g.get("gamePk")
            if not gpk:
                continue
            state = ((g.get("status") or {}).get("abstractGameState") or "").lower()
            if state == "live":
                live += 1
            elif state == "final":
                final_n += 1
            elif state == "preview":
                preview_n += 1
            # Skip pre-game games — no plays yet.
            if state == "preview":
                continue
            teams = g.get("teams") or {}
            away_team = (teams.get("away") or {}).get("team") or {}
            home_team = (teams.get("home") or {}).get("team") or {}
            away_abbr = _normalize_abbr(
                away_team.get("abbreviation"), away_team.get("name")
            )
            home_abbr = _normalize_abbr(
                home_team.get("abbreviation"), home_team.get("name")
            )
            try:
                feed = self.fetch_game_feed(int(gpk))
            except Exception as e:
                # one game failing should not poison the slate — keep the
                # first error so the user can see something went wrong
                # without overwriting it on every subsequent game.
                self.status.per_game_errors += 1
                if not first_game_err:
                    first_game_err = f"game {gpk} feed failed: {e}"
                continue
            plays = ((feed.get("liveData") or {}).get("plays") or {}).get("allPlays") or []
            for play in plays:
                ev = self._build_event(g, int(gpk), play, away_abbr, home_abbr)
                if not ev:
                    continue
                if ev["event_id"] in seen_ids:
                    continue
                out.append(ev)
        self.status.games_live = live
        self.status.games_final = final_n
        self.status.games_preview = preview_n
        if first_game_err and not self.status.last_error:
            self.status.last_error = first_game_err
        # Note: hrs_today is incremented by the renderer once the events are
        # ingested — we don't double-count here.
        return out


# ---------------------------------------------------------------------------
# Optional season-stat enrichment — pulls a hitter's season HR/OPS from
# StatsAPI /people/{id}/stats. Cached aggressively because these numbers are
# constant within a single rerun cycle. ISO is computed from SLG-AVG; the
# Savant-only Barrel% is left blank — wiring that here would require a
# second leaderboard call on every event, which we deliberately defer to a
# bulk pre-load in app.py if desired.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def _fetch_season_stats(player_id: int, season: int) -> dict[str, Any]:
    if requests is None or not player_id:
        return {}
    try:
        r = requests.get(
            f"{_STATSAPI_BASE}/api/v1/people/{int(player_id)}/stats",
            params={"stats": "season", "group": "hitting", "season": season},
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "live-hr-tracker/1.0"},
        )
        r.raise_for_status()
        data = r.json() or {}
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for s in data.get("stats", []) or []:
        for sp in s.get("splits", []) or []:
            stat = sp.get("stat") or {}
            try:
                out["season_hr"] = int(stat.get("homeRuns")) if stat.get("homeRuns") is not None else None
            except Exception:
                pass
            try:
                out["ops"] = float(stat.get("ops")) if stat.get("ops") is not None else None
            except Exception:
                pass
            try:
                slg = float(stat.get("slg")) if stat.get("slg") is not None else None
                avg = float(stat.get("avg")) if stat.get("avg") is not None else None
                if slg is not None and avg is not None:
                    out["iso"] = round(slg - avg, 3)
            except Exception:
                pass
            return out
    return out


def _enrich_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Mutate ``ev`` in place with season HR/OPS/ISO if available."""
    pid = ev.pop("_batter_id", None)
    if not pid:
        return ev
    season = datetime.now().year
    stats = _fetch_season_stats(int(pid), int(season))
    for k in ("season_hr", "ops", "iso"):
        if stats.get(k) is not None and ev.get(k) is None:
            ev[k] = stats[k]
    return ev


def _fetch_espn_hr_events(date_iso: str, seen_ids: set[str],
                          status: FeedStatus) -> list[dict[str, Any]]:
    """Public-API fallback when MLB StatsAPI is unavailable.

    Pulls today's slate from ESPN's MLB scoreboard, then per-event summary,
    and extracts plays whose ``type.text == "Home Run"``. Returns events in
    the same dict-shape as the StatsAPI path so callers do not branch.
    """
    if requests is None:
        return []
    out: list[dict[str, Any]] = []
    # ESPN expects YYYYMMDD with no dashes
    date_compact = date_iso.replace("-", "")
    try:
        r = requests.get(
            _ESPN_SCOREBOARD, params={"dates": date_compact},
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "live-hr-tracker/1.0"},
        )
        r.raise_for_status()
        sb = r.json() or {}
    except Exception as e:
        status.last_error = f"{status.last_error or ''} | ESPN scoreboard failed: {e}".strip(" |")
        return out
    events = sb.get("events", []) or []
    status.games_scanned = len(events)
    live = final_n = preview_n = 0
    for game in events:
        gid = game.get("id")
        if not gid:
            continue
        comp = ((game.get("competitions") or [{}])[0]) or {}
        state = (((game.get("status") or {}).get("type") or {}).get("state") or "").lower()
        if state == "in":
            live += 1
        elif state == "post":
            final_n += 1
        elif state == "pre":
            preview_n += 1
            continue
        # ESPN home/away team abbreviations
        comps = comp.get("competitors") or []
        away_abbr = home_abbr = ""
        for c in comps:
            ab = ((c.get("team") or {}).get("abbreviation") or "").upper()
            ab = _ABBR_ALIASES.get(ab, ab)
            if c.get("homeAway") == "home":
                home_abbr = ab
            else:
                away_abbr = ab
        try:
            sr = requests.get(
                _ESPN_SUMMARY, params={"event": gid},
                timeout=_HTTP_TIMEOUT,
                headers={"User-Agent": "live-hr-tracker/1.0"},
            )
            sr.raise_for_status()
            summary = sr.json() or {}
        except Exception:
            status.per_game_errors += 1
            continue
        plays = summary.get("plays") or []
        # ESPN emits one "Home Run" type play per HR + a follow-up "Play Result"
        # with the descriptive text/distance. We pair them by adjacent index.
        for i, p in enumerate(plays):
            ptype = ((p.get("type") or {}).get("text") or "").lower()
            if ptype != "home run":
                continue
            # Find the next "Play Result" for description.
            description = ""
            for j in range(i + 1, min(i + 4, len(plays))):
                pj = plays[j]
                if ((pj.get("type") or {}).get("text") or "").lower() == "play result":
                    description = pj.get("text") or ""
                    break
            participants = p.get("participants") or []
            batter_name = ""
            batter_id = None
            for part in participants:
                if (part.get("type") or "").lower() == "batter":
                    ath = part.get("athlete") or {}
                    batter_name = ath.get("displayName") or ath.get("shortName") or ""
                    batter_id = ath.get("id")
                    break
            # If batter not in participants, fall back to parsing "X homered" from description.
            if not batter_name and description:
                # crude split: "Smith homered to ..." → "Smith"
                first = description.split(" homered", 1)[0].split(" homers", 1)[0]
                batter_name = first.strip()
            # Period / inning
            period = p.get("period") or {}
            half = (p.get("periodType") or "").lower()
            half_short = "T" if "top" in half else ("B" if "bottom" in half else "")
            inning_num = period.get("number") or 0
            inning_tag = f"{half_short}{inning_num}" if inning_num else ""
            # Distance from description "(421 feet)"
            distance: int | None = None
            if "(" in description and "feet" in description:
                try:
                    distance = int(
                        description.rsplit("(", 1)[1].split(" feet", 1)[0].strip()
                    )
                except Exception:
                    distance = None
            batter_team = away_abbr if half_short == "T" else home_abbr
            opp_team = home_abbr if half_short == "T" else away_abbr
            event_id = f"espn-{gid}-{p.get('id') or i}"
            if event_id in seen_ids:
                continue
            matchup_parts = []
            if batter_team and opp_team:
                matchup_parts.append(f"{batter_team} vs {opp_team}")
            if inning_tag:
                matchup_parts.append(inning_tag)
            out.append({
                "event_id": event_id,
                "name": batter_name or "Unknown",
                "team": batter_team,
                "jersey": "",
                "player_id": batter_id,
                "rbi": 1,  # ESPN doesn't expose RBI per-play cleanly; default 1
                "exit_velo": None,
                "distance": distance,
                "matchup": " · ".join(matchup_parts) or batter_team,
                "timestamp": p.get("wallclock") or datetime.now(timezone.utc).isoformat(),
                "season_hr": None, "ops": None, "iso": None, "barrel_pct": None,
                "_batter_id": batter_id,
            })
    status.games_live = live
    status.games_final = final_n
    status.games_preview = preview_n
    return out


def make_mlb_fetcher(
    *, date_iso: str | None = None,
    status: FeedStatus | None = None,
    enrich: bool = True,
) -> Callable[[], list[dict[str, Any]]]:
    """Return a no-arg callable that yields *new* HR events on each call.

    The returned function relies on the host renderer's ``seen_ids`` set
    being threaded in through ``poll_live_hr_events``. To do this cleanly
    without changing the public signature, we stash the most recent seen_ids
    on the function object via ``setattr`` before each call (the renderer
    handles this — see ``render_live_hr_tracker``).
    """
    feed = MLBLiveHRFeed(date_iso=date_iso, status=status)

    def _fetcher() -> list[dict[str, Any]]:
        seen = getattr(_fetcher, "_seen_ids", set()) or set()
        events = feed.fetch_new_events(seen)
        if enrich:
            for ev in events:
                _enrich_event(ev)
        else:
            for ev in events:
                ev.pop("_batter_id", None)
        return events

    _fetcher._feed = feed  # type: ignore[attr-defined]
    return _fetcher


# ---------------------------------------------------------------------------
# Confetti / banner JS — wrapped in a tiny self-contained html component.
# ---------------------------------------------------------------------------
def _confetti_html(count: int) -> str:
    """Render a one-shot confetti burst keyed off ``count``.

    The component remounts whenever its key changes, which is how we
    trigger a fresh burst on each new HR.
    """
    safe_count = max(40, min(int(count), 400))
    payload = json.dumps({"count": safe_count})
    return f"""
<canvas class="lhrt-confetti-canvas" id="lhrt-confetti"></canvas>
<script>
(function() {{
  const cfg = {payload};
  const cvs = document.getElementById('lhrt-confetti');
  if (!cvs) return;
  const ctx = cvs.getContext('2d');
  function resize() {{
    cvs.width = window.innerWidth;
    cvs.height = window.innerHeight;
  }}
  resize();
  window.addEventListener('resize', resize);
  const colors = ['#fcd34d', '#fb923c', '#f97316', '#ef4444', '#ec4899',
                  '#a78bfa', '#34d399', '#60a5fa'];
  const parts = [];
  for (let i = 0; i < cfg.count; i++) {{
    parts.push({{
      x: cvs.width * (0.15 + Math.random() * 0.7),
      y: -20 - Math.random() * 80,
      vx: (Math.random() - 0.5) * 6,
      vy: 3 + Math.random() * 5,
      g:  0.12 + Math.random() * 0.08,
      r:  3 + Math.random() * 4,
      a:  Math.random() * Math.PI * 2,
      va: (Math.random() - 0.5) * 0.3,
      c:  colors[(Math.random() * colors.length) | 0],
      life: 90 + Math.random() * 60
    }});
  }}
  let frame = 0;
  function tick() {{
    frame++;
    ctx.clearRect(0, 0, cvs.width, cvs.height);
    let alive = 0;
    for (const p of parts) {{
      if (p.life <= 0) continue;
      alive++;
      p.vy += p.g; p.x += p.vx; p.y += p.vy; p.a += p.va; p.life--;
      ctx.save();
      ctx.translate(p.x, p.y); ctx.rotate(p.a);
      ctx.fillStyle = p.c;
      ctx.globalAlpha = Math.max(0, Math.min(1, p.life / 60));
      ctx.fillRect(-p.r, -p.r * 0.4, p.r * 2, p.r * 0.8);
      ctx.restore();
    }}
    if (alive > 0 && frame < 260) requestAnimationFrame(tick);
    else ctx.clearRect(0, 0, cvs.width, cvs.height);
  }}
  requestAnimationFrame(tick);
}})();
</script>
""".strip()


# ---------------------------------------------------------------------------
# Self-contained auto-refresh fallback
# ---------------------------------------------------------------------------
def _render_query_param_autorefresh(interval_ms: int) -> None:
    """Soft auto-refresh that does NOT reload the page.

    Strategy: a 0-height component schedules a single setTimeout. When it
    fires, it bumps a ``?_lhrt_tick=<ts>`` query parameter on the PARENT
    page using ``history.replaceState`` and then dispatches a
    ``popstate`` event. Streamlit listens for URL changes and triggers a
    soft rerun on the server, preserving session_state, scroll position,
    and any open expanders.

    If ``window.parent`` access is blocked (cross-origin iframe sandbox),
    we fall back to clicking the visible "Refresh now" button by data-key,
    which is also a soft rerun. Worst-case both fail and the user can hit
    the manual button — the page is never frozen.
    """
    safe_ms = max(2000, int(interval_ms))
    st.components.v1.html(
        f"""
<script>
(function() {{
  var INTERVAL = {safe_ms};
  if (window.__lhrtTickArmed) {{ return; }}
  window.__lhrtTickArmed = true;
  setTimeout(function() {{
    window.__lhrtTickArmed = false;
    try {{
      var w = window.parent || window;
      var url = new URL(w.location.href);
      url.searchParams.set('_lhrt_tick', String(Date.now()));
      w.history.replaceState({{}}, '', url.toString());
      // Streamlit listens for popstate to pick up URL changes.
      w.dispatchEvent(new PopStateEvent('popstate'));
    }} catch (e) {{
      try {{
        // Last-ditch fallback: click the visible Refresh button.
        var doc = (window.parent && window.parent.document) || document;
        var btns = doc.querySelectorAll('button');
        for (var i = 0; i < btns.length; i++) {{
          if ((btns[i].innerText || '').indexOf('Refresh now') !== -1) {{
            btns[i].click();
            break;
          }}
        }}
      }} catch (err) {{ /* ignore */ }}
    }}
  }}, INTERVAL);
}})();
</script>
""".strip(),
        height=0,
    )


# ---------------------------------------------------------------------------
# Top-level renderer
# ---------------------------------------------------------------------------
def _ensure_state() -> dict[str, Any]:
    s = st.session_state
    s.setdefault("lhrt_events", [])        # list of HRPlayer dicts (newest first)
    s.setdefault("lhrt_seen_ids", set())   # event_id dedup
    s.setdefault("lhrt_today", 0)
    s.setdefault("lhrt_solo", 0)
    s.setdefault("lhrt_multi", 0)
    s.setdefault("lhrt_confetti_seq", 0)   # bumped each time we want confetti
    s.setdefault("lhrt_confetti_count", 0)
    s.setdefault("lhrt_last_banner", "")
    s.setdefault("lhrt_demo_seq", 0)
    s.setdefault("lhrt_last_poll", 0.0)
    s.setdefault("lhrt_status", FeedStatus())
    s.setdefault("lhrt_backfilled", False)  # one-time history pull on first load
    return s


def _format_relative(epoch: float) -> str:
    if not epoch:
        return "never"
    delta = max(0, int(time.time() - epoch))
    if delta < 5:
        return "just now"
    if delta < 60:
        return f"{delta}s ago"
    m = delta // 60
    s = delta % 60
    if m < 60:
        return f"{m}m {s}s ago"
    h = m // 60
    return f"{h}h {m % 60}m ago"


def _ingest_events(events: list[HRPlayer]) -> HRPlayer | None:
    """Add events into session state. Returns the most recent one for banner."""
    if not events:
        return None
    s = st.session_state
    newest: HRPlayer | None = None
    for ev in events:
        # Mark all existing as not-fresh by simply not flagging anything;
        # cards in feed are rendered with the auto-dim rule based on index.
        s["lhrt_events"].insert(0, asdict(ev))
        if ev.event_id:
            s["lhrt_seen_ids"].add(ev.event_id)
        s["lhrt_today"] += 1
        if ev.rbi <= 1:
            s["lhrt_solo"] += 1
        else:
            s["lhrt_multi"] += 1
        newest = ev
    # Cap feed length to keep DOM small.
    s["lhrt_events"] = s["lhrt_events"][:50]
    return newest


def render_live_hr_tracker(
    *,
    fetcher: Callable[[], Iterable[dict[str, Any]]] | None = None,
) -> None:
    """Full-page renderer for the Live HR Tracker view.

    If ``fetcher`` is None we wire the built-in MLB StatsAPI fetcher
    automatically so the page is production-ready out of the box. A
    developer-only "Demo mode" toggle (off by default) still lets you
    inject synthetic events for visual QA.
    """
    s = _ensure_state()
    # The query-param autorefresh fallback sets ?_lhrt_tick=<ts>; strip it
    # immediately so the URL never accumulates stale ticks (and so the host
    # app's own ?view= / ?g= deep-link logic isn't confused by it).
    try:
        if "_lhrt_tick" in st.query_params:
            del st.query_params["_lhrt_tick"]
    except Exception:
        pass
    st.markdown(_PAGE_CSS, unsafe_allow_html=True)
    st.markdown('<div class="lhrt-wrap">', unsafe_allow_html=True)

    # Build (or reuse) the real MLB fetcher. We keep it on session_state so
    # the same FeedStatus instance survives across reruns.
    status: FeedStatus = s["lhrt_status"]
    if fetcher is None:
        if "lhrt_fetcher" not in s:
            s["lhrt_fetcher"] = make_mlb_fetcher(status=status)
        real_fetcher = s["lhrt_fetcher"]
    else:
        real_fetcher = fetcher

    # ---- Controls row ----
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1.2])
    with c1:
        auto_refresh = st.toggle(
            "🔄 Auto-refresh",
            value=True,
            help="Re-run the page every few seconds to pick up new HR events from the MLB live feed.",
            key="lhrt_auto_refresh",
        )
    with c2:
        interval = st.selectbox(
            "Poll every",
            [10, 15, 30, 60, 120],
            index=1,
            format_func=lambda x: f"{x}s",
            help="StatsAPI is courteous to poll once every 10-30s per game.",
            key="lhrt_poll_interval",
        )
    with c3:
        if st.button("🔄 Refresh now", use_container_width=True, key="lhrt_refresh_btn"):
            s["lhrt_last_poll"] = 0.0  # force a poll on this rerun
    with c4:
        if st.button("🧹 Reset feed", use_container_width=True, key="lhrt_reset_btn"):
            for k in ("lhrt_events", "lhrt_seen_ids", "lhrt_today",
                      "lhrt_solo", "lhrt_multi", "lhrt_last_banner",
                      "lhrt_confetti_seq", "lhrt_confetti_count",
                      "lhrt_backfilled"):
                if k in s:
                    del s[k]
            _ensure_state()
            st.rerun()

    # Dev/demo toggle is tucked under an expander, collapsed by default so it
    # never fires by accident. ``demo_mode`` is read after the expander closes
    # via session_state so the variable is always defined regardless of
    # whether the user has expanded the panel this rerun.
    with st.expander("🧪 Dev / demo controls", expanded=False):
        d1, d2 = st.columns([1, 1])
        with d1:
            st.toggle(
                "Demo mode (synthetic HR events)",
                value=False,
                help="Synthesize fake HRs every poll. Use only for visual QA.",
                key="lhrt_demo_toggle",
            )
        with d2:
            if st.button("💥 Fire test HR", use_container_width=True, key="lhrt_fire_btn"):
                s["lhrt_demo_seq"] += 1
                ev = _simulate_event(s["lhrt_demo_seq"])
                _ingest_events([ev])
                s["lhrt_confetti_seq"] += 1
                s["lhrt_confetti_count"] = 240 if ev.rbi >= 4 else 120
                s["lhrt_last_banner"] = (
                    f"🔥 {ev.name} — {_hr_type(ev.rbi)} ACTIVATED 🔥"
                )
    demo_mode = bool(s.get("lhrt_demo_toggle", False))

    # ---- Auto-refresh: must NEVER use time.sleep() + st.rerun() because that
    # blocks the script execution and freezes the page after the first run.
    # Must NEVER use window.parent.location.reload() either: that triggers a
    # full app reload which is slow on a heavy multi-page app and visually
    # looks like the page is "broken" (constant flash / loading spinner).
    #
    # We prefer streamlit-autorefresh (a tiny JS component that triggers a
    # SOFT Streamlit rerun via the component message bus — no page reload).
    # If that import failed (e.g. Streamlit Cloud hasn't reinstalled the
    # requirements yet), we fall back to a self-contained component that
    # bumps a `?lhrt_tick=` query-param on the parent page, which Streamlit
    # treats as a soft rerun and preserves session_state. Either way the
    # script thread is never blocked.
    if auto_refresh:
        _refresh_ms = max(2000, int(interval) * 1000)
        if st_autorefresh is not None:
            try:
                st_autorefresh(
                    interval=_refresh_ms,
                    key=f"lhrt_autorefresh_{int(interval)}",
                )
            except Exception:
                # Component failed at runtime — drop to the self-contained
                # fallback below so the page still ticks.
                _render_query_param_autorefresh(_refresh_ms)
        else:
            _render_query_param_autorefresh(_refresh_ms)

    # ---- Poll new events ----
    now = time.time()
    poll_due = (now - float(s.get("lhrt_last_poll") or 0.0)) >= float(interval)
    if poll_due or not s["lhrt_backfilled"]:
        first_load = not s["lhrt_backfilled"]
        s["lhrt_last_poll"] = now
        new_events: list[HRPlayer] = []
        try:
            if demo_mode:
                # Light random chance of a synthetic event per poll.
                if random.random() < 0.35:
                    s["lhrt_demo_seq"] += 1
                    new_events.append(_simulate_event(s["lhrt_demo_seq"]))
            else:
                # Thread the current seen_ids into the cached fetcher just before calling.
                if hasattr(real_fetcher, "__self__") is False:
                    try:
                        setattr(real_fetcher, "_seen_ids", s["lhrt_seen_ids"])
                    except Exception:
                        pass
                new_events = poll_live_hr_events(real_fetcher, seen_ids=s["lhrt_seen_ids"])
        except Exception as poll_err:
            # Belt + suspenders: poll_live_hr_events already swallows fetcher
            # errors, but if anything else trips (seen_ids attr, simulator,
            # etc.) we keep the page alive and surface a warning.
            status.last_error = f"Poll failed: {poll_err}"
            new_events = []

        if new_events:
            try:
                newest = _ingest_events(new_events)
            except Exception as ingest_err:
                status.last_error = f"Ingest failed: {ingest_err}"
                newest = None
            # On the very first load, populate the feed silently — no banner /
            # confetti — so the user sees today's HR history without 14 chained
            # "ACTIVATED" pulses. Subsequent polls fire the celebration.
            if newest is not None and not first_load:
                s["lhrt_confetti_seq"] += 1
                s["lhrt_confetti_count"] = 240 if newest.rbi >= 4 else 120
                s["lhrt_last_banner"] = (
                    f"🔥 {newest.name} — {_hr_type(newest.rbi)} ACTIVATED 🔥"
                )
        s["lhrt_backfilled"] = True

    # ---- Status / health panel ----
    status_bits = []
    if demo_mode:
        status_bits.append("🧪 Demo mode")
    else:
        src_icon = "📡" if not status.fallback_used else "🛟"
        status_bits.append(f"{src_icon} {status.source or 'MLB StatsAPI'}")
    # Date(s) and timezone — make it impossible to misread which calendar day
    # the tracker is on. If we're scanning more than one date (Central + UTC
    # overlap during evening hours) show both, separated by "+".
    scan_dates = list(status.schedule_dates) or [status.schedule_date or _today_iso()]
    date_label = " + ".join(scan_dates)
    tz_label = status.timezone_label or _BASEBALL_TZ_NAME
    status_bits.append(f"📅 {date_label} ({tz_label})")
    status_bits.append(
        f"🎮 {status.games_scanned} games "
        f"(🔴 {status.games_live} live · ✅ {status.games_final} final · "
        f"⏳ {status.games_preview} upcoming)"
    )
    status_bits.append(f"💥 {s['lhrt_today']} HR today")
    status_bits.append(f"🕒 Checked {_format_relative(status.last_checked)}")
    if auto_refresh:
        # ``lhrt_last_poll`` was just refreshed to ``now`` above when a poll
        # fired, so the displayed countdown is roughly the full interval —
        # which is what the user expects ("next refresh in 15s"). We clamp
        # to interval so a stale lhrt_last_poll can't show "next in -42s".
        _elapsed = max(0, int(time.time() - float(s.get("lhrt_last_poll") or time.time())))
        next_in = max(0, int(interval) - _elapsed)
        next_in = min(next_in, int(interval))
        status_bits.append(f"⏱️ Auto-refresh every {int(interval)}s (next in ~{next_in}s)")
    else:
        status_bits.append("⏸️ Auto-refresh OFF — use 🔄 Refresh now")
    if status.per_game_errors:
        status_bits.append(f"⚠️ {status.per_game_errors} game feed errors")
    status_html = (
        '<div style="display:flex; flex-wrap:wrap; gap:8px 14px; '
        'background:rgba(15,23,42,.55); border:1px solid #312e81; '
        'border-radius:10px; padding:8px 12px; margin:0 0 10px 0; '
        'font-size:.82rem; color:#cbd5e1;">'
        + " · ".join(f"<span>{_esc(x)}</span>" for x in status_bits)
        + "</div>"
    )
    st.markdown(status_html, unsafe_allow_html=True)
    if status.last_error and not demo_mode:
        st.warning(f"Feed warning: {status.last_error}")

    # ---- Banner ----
    banner = s.get("lhrt_last_banner") or ""
    if banner:
        st.markdown(
            f'<div class="lhrt-banner">{_esc(banner)}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="lhrt-banner lhrt-banner-quiet">⚾ Awaiting first homer of the slate…</div>',
            unsafe_allow_html=True,
        )

    # ---- Ticker ----
    if s["lhrt_events"]:
        ticker_items = []
        for ev in s["lhrt_events"][:25]:
            ts = ev.get("timestamp", "") or ""
            try:
                ts_short = datetime.fromisoformat(str(ts).replace("Z", "+00:00")) \
                    .astimezone().strftime("%I:%M %p").lstrip("0")
            except Exception:
                ts_short = ""
            ticker_items.append(
                f"<span>💥 {_esc(ev.get('name',''))} "
                f"({_esc(ev.get('team',''))}) · "
                f"{_esc(_hr_type(ev.get('rbi', 1)))} · "
                f"{_esc(ev.get('distance', '—'))} ft · "
                f"{_esc(ts_short)}</span>"
                f"<span class='lhrt-ticker-sep'>•</span>"
            )
        st.markdown(
            '<div class="lhrt-ticker-row"><div class="lhrt-ticker">'
            + "".join(ticker_items) +
            '</div></div>',
            unsafe_allow_html=True,
        )

    # ---- Counters ----
    counters_html = (
        '<div class="lhrt-counters">'
        f'<div class="lhrt-counter" key="t{s["lhrt_today"]}">'
        f'<div class="lhrt-counter-label">Today</div>'
        f'<div class="lhrt-counter-value">{int(s["lhrt_today"])}</div></div>'
        f'<div class="lhrt-counter">'
        f'<div class="lhrt-counter-label">Solo</div>'
        f'<div class="lhrt-counter-value">{int(s["lhrt_solo"])}</div></div>'
        f'<div class="lhrt-counter">'
        f'<div class="lhrt-counter-label">Multi-Run</div>'
        f'<div class="lhrt-counter-value">{int(s["lhrt_multi"])}</div></div>'
        '</div>'
    )
    st.markdown(counters_html, unsafe_allow_html=True)

    # ---- Confetti burst (re-mounted when seq changes) ----
    seq = int(s.get("lhrt_confetti_seq", 0))
    if seq > 0:
        st.components.v1.html(
            _confetti_html(int(s.get("lhrt_confetti_count", 120))),
            height=0,
        )

    # ---- Feed ----
    events = s["lhrt_events"]
    if not events:
        # Make the empty state informative: the user must be able to tell
        # the tracker is alive and polling, not broken. Show what we know.
        last_check = _format_relative(status.last_checked)
        if status.games_scanned == 0 and status.games_live == 0 and status.games_final == 0:
            empty_main = "No MLB games found for today."
            empty_sub = "Off-day, lockout, or the schedule API is unreachable. Use 🧪 Demo to preview."
        elif status.games_live > 0:
            empty_main = (
                f"⚾ Tracking {status.games_live} live game"
                f"{'s' if status.games_live != 1 else ''} — no HRs hit yet."
            )
            empty_sub = (
                f"Polling every {int(interval)}s · last checked {last_check} · "
                f"source: {status.source}"
            )
        elif status.games_preview > 0 and status.games_live == 0 and status.games_final == 0:
            empty_main = f"⏳ {status.games_preview} games scheduled — first pitch coming up."
            empty_sub = f"Tracker is armed and will catch the first HR of the slate. Checked {last_check}."
        elif status.games_final > 0 and status.games_live == 0:
            empty_main = f"✅ All {status.games_final} games final — no HRs detected today."
            empty_sub = "Try a historical date via the Dev panel, or wait for tomorrow's slate."
        else:
            empty_main = "No HRs in the feed yet."
            empty_sub = f"Polling every {int(interval)}s · last checked {last_check}."
        st.markdown(
            f'<div class="lhrt-empty">'
            f'<div style="font-weight:800;color:#fcd34d;margin-bottom:4px;">{_esc(empty_main)}</div>'
            f'<div style="font-size:.82rem;">{_esc(empty_sub)}</div>'
            f'<div style="font-size:.78rem;margin-top:6px;color:#94a3b8;">'
            f'Tip: open <b>🧪 Dev / demo controls</b> above and hit <b>💥 Fire test HR</b> '
            f'to confirm the tracker UI is working end-to-end.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        feed_html = ['<div class="lhrt-feed">']
        for i, ev in enumerate(events):
            # Newest (index 0) renders with the burst/pulse ring; subsequent
            # cards auto-dim so the freshest pops. The "3-second" feel is
            # baked into the burst animation duration (~.9s + 1.4s ring loop),
            # plus auto-dim on every card past index 0 each rerun.
            feed_html.append(build_hr_card(ev, dim=(i > 0)))
        feed_html.append('</div>')
        st.markdown("".join(feed_html), unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # ---- Polling hook docstring (visible to the user) ----
    with st.expander("⚙️ Data source & customization", expanded=False):
        st.markdown(
            """
**Live source.** This page polls the official MLB StatsAPI:

- `GET /api/v1/schedule?sportId=1&date=YYYY-MM-DD` for today's slate.
- `GET /api/v1.1/game/{gamePk}/feed/live` for each non-preview game.
- Plays where `result.eventType == "home_run"` are converted into cards.
- Season HR / OPS / ISO are enriched from `GET /api/v1/people/{id}/stats`
  (cached for 15 min). Barrel% is left blank — wire a Savant leaderboard
  call to populate it if needed.

**Pluggable polling hook.** You can also pass a custom `fetcher`:

```python
from live_hr_tracker import render_live_hr_tracker

def my_fetcher():
    # Return an iterable of dicts shaped like HRPlayer with a stable event_id.
    yield {"event_id": "g123-ab45", "name": "Aaron Judge", "team": "NYY",
           "rbi": 3, "exit_velo": 109.4, "distance": 442, ...}

render_live_hr_tracker(fetcher=my_fetcher)
```
            """
        )

