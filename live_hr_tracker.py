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
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import streamlit as st


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

    avatar_inner = (
        f'<img src="{_esc(p.avatar_url)}" alt="" '
        f'onerror="this.style.display=\'none\';this.parentNode.classList.add(\'lhrt-avatar-fallback\')">'
        if p.avatar_url else ""
    )
    initials = "".join(part[:1] for part in (p.name or "").split()[:2]).upper() or "?"

    card_classes = "lhrt-card" + (" lhrt-card-dim" if dim else " lhrt-card-fresh")
    grand_slam_badge = ""
    if hr_type == "Grand Slam":
        grand_slam_badge = '<div class="lhrt-gs-badge">⚡ GRAND SLAM ⚡</div>'

    return f"""
<div class="{card_classes}" style="--team-primary:{primary};--team-secondary:{secondary};">
  {grand_slam_badge}
  <div class="lhrt-card-head">
    <div class="lhrt-avatar lhrt-avatar-fallback" data-initials="{_esc(initials)}">
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
    # name, team, jersey, season_hr, ops, iso, barrel_pct
    ("Aaron Judge",       "NYY", "99", 42, .998, .305, 19.8),
    ("Shohei Ohtani",     "LAD", "17", 38, .987, .298, 17.4),
    ("Juan Soto",         "NYY", "22", 31, .955, .267, 15.2),
    ("Mookie Betts",      "LAD", "50", 24, .891, .234, 13.8),
    ("Bryce Harper",      "PHI", "3",  28, .912, .248, 14.6),
    ("Ronald Acuña Jr.",  "ATL", "13", 26, .934, .241, 14.9),
    ("Pete Alonso",       "NYM", "20", 33, .854, .258, 16.1),
    ("Yordan Alvarez",    "HOU", "44", 30, .945, .276, 17.8),
    ("Kyle Tucker",       "HOU", "30", 25, .889, .238, 13.4),
    ("Vladimir Guerrero", "TOR", "27", 27, .872, .231, 14.1),
    ("Rafael Devers",     "BOS", "11", 29, .881, .249, 13.9),
    ("Matt Olson",        "ATL", "28", 31, .867, .254, 15.3),
    ("Corey Seager",      "TEX", "5",  26, .898, .256, 13.2),
    ("Bobby Witt Jr.",    "KC",  "7",  22, .914, .242, 11.8),
    ("Gunnar Henderson",  "BAL", "2",  28, .883, .247, 13.5),
]
_DEMO_PITCHERS = [
    "Gerrit Cole", "Tarik Skubal", "Zack Wheeler", "Logan Webb",
    "Pablo López", "Spencer Strider", "Corbin Burnes", "Aaron Nola",
    "Blake Snell", "Sonny Gray", "Framber Valdez", "Yoshinobu Yamamoto",
]


def _simulate_event(seq: int) -> HRPlayer:
    """Build a single fake HR event."""
    name, team, jersey, hr, ops, iso, brl = random.choice(_DEMO_BATTERS)
    pitcher = random.choice(_DEMO_PITCHERS)
    rbi = random.choices([1, 2, 3, 4], weights=[55, 25, 15, 5], k=1)[0]
    return HRPlayer(
        name=name, team=team, jersey=jersey,
        season_hr=hr, ops=ops, iso=iso, barrel_pct=brl,
        rbi=rbi,
        exit_velo=round(random.uniform(98.0, 117.0), 1),
        distance=int(random.uniform(360, 480)),
        matchup=f"off {pitcher}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_id=f"demo-{int(time.time()*1000)}-{seq}",
    )


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
    return s


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

    Pass ``fetcher`` to wire a real MLB feed. If absent, the page exposes
    a "Demo mode" toggle that synthesizes events for visual QA.
    """
    s = _ensure_state()
    st.markdown('<div class="lhrt-wrap">', unsafe_allow_html=True)
    st.markdown(_PAGE_CSS, unsafe_allow_html=True)

    # ---- Controls row ----
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1.2])
    with c1:
        demo_mode = st.toggle(
            "🧪 Demo mode",
            value=fetcher is None,
            help="Generate fake HR events for visual QA. Turn off once a real feed is wired.",
            key="lhrt_demo_toggle",
        )
    with c2:
        auto_refresh = st.toggle(
            "🔄 Auto-refresh",
            value=True,
            help="Re-run the page every few seconds to pick up new events.",
            key="lhrt_auto_refresh",
        )
    with c3:
        interval = st.selectbox(
            "Poll every",
            [3, 5, 10, 15, 30],
            index=1,
            format_func=lambda x: f"{x}s",
            key="lhrt_poll_interval",
        )
    with c4:
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("💥 Fire HR", use_container_width=True, key="lhrt_fire_btn"):
                s["lhrt_demo_seq"] += 1
                ev = _simulate_event(s["lhrt_demo_seq"])
                _ingest_events([ev])
                s["lhrt_confetti_seq"] += 1
                s["lhrt_confetti_count"] = 240 if ev.rbi >= 4 else 120
                s["lhrt_last_banner"] = (
                    f"🔥 {ev.name} — {_hr_type(ev.rbi)} ACTIVATED 🔥"
                )
        with col_b:
            if st.button("🧹 Reset", use_container_width=True, key="lhrt_reset_btn"):
                for k in ("lhrt_events", "lhrt_seen_ids", "lhrt_today",
                          "lhrt_solo", "lhrt_multi", "lhrt_last_banner",
                          "lhrt_confetti_seq", "lhrt_confetti_count"):
                    if k in s:
                        del s[k]
                _ensure_state()
                st.rerun()

    # ---- Poll new events ----
    now = time.time()
    if auto_refresh and (now - s["lhrt_last_poll"]) >= float(interval):
        s["lhrt_last_poll"] = now
        new_events: list[HRPlayer] = []
        if demo_mode:
            # Light random chance of an event per poll, so it feels live.
            if random.random() < 0.35:
                s["lhrt_demo_seq"] += 1
                new_events.append(_simulate_event(s["lhrt_demo_seq"]))
        else:
            new_events = poll_live_hr_events(fetcher, seen_ids=s["lhrt_seen_ids"])

        if new_events:
            newest = _ingest_events(new_events)
            if newest is not None:
                s["lhrt_confetti_seq"] += 1
                s["lhrt_confetti_count"] = 240 if newest.rbi >= 4 else 120
                s["lhrt_last_banner"] = (
                    f"🔥 {newest.name} — {_hr_type(newest.rbi)} ACTIVATED 🔥"
                )

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
        st.markdown(
            '<div class="lhrt-empty">No HRs yet — toggle Demo mode or hit '
            '<b>💥 Fire HR</b> to preview the experience.</div>',
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
    with st.expander("⚙️ How to wire a real MLB feed", expanded=False):
        st.markdown(
            """
**Pluggable polling hook.** This page is built around a simple seam:

```python
from live_hr_tracker import render_live_hr_tracker, build_hr_card, HRPlayer

def my_fetcher():
    # Hit StatsAPI /api/v1.1/game/{pk}/feed/live for each in-progress game,
    # filter `liveData.plays.allPlays` for `result.eventType == 'home_run'`
    # and yield dicts shaped like HRPlayer (name, team, jersey, rbi, …).
    for play in fetch_new_hr_plays():
        yield {
            "event_id": play["atBatIndex_gamepk"],
            "name":     play["matchup"]["batter"]["fullName"],
            "team":     play["batter_team_abbr"],
            "jersey":   play["matchup"]["batter"].get("primaryNumber", ""),
            "season_hr": play.get("season_hr"),
            "ops":       play.get("ops"),
            "iso":       play.get("iso"),
            "barrel_pct": play.get("barrel_pct"),
            "rbi":       play["result"]["rbi"],
            "exit_velo": play.get("hitData", {}).get("launchSpeed"),
            "distance":  play.get("hitData", {}).get("totalDistance"),
            "matchup":   f'off {play["matchup"]["pitcher"]["fullName"]}',
            "timestamp": play["about"]["endTime"],
        }

render_live_hr_tracker(fetcher=my_fetcher)
```

For a one-shot custom render path you can also call `build_hr_card`
directly:

```python
if new_hr_event:
    st.components.v1.html(build_hr_card(player_data), height=200)
```
            """
        )

    # ---- Schedule the next rerun for auto-refresh ----
    if auto_refresh:
        time.sleep(float(interval))
        st.rerun()
