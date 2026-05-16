"""MRBETS850 HOMERUN PICKS OF DAY — developer-curated daily homerun board.

Public users see a read-only list of MrBets850's hand-picked homerun plays of
the day, ranked 1-25, rendered as player cards populated from the same
Savant batters_df / TEAM_INFO data the rest of the app uses. The developer
unlocks an in-page editor with a session PIN (st.secrets / env var) and can
add, edit, reorder, clear, or delete picks. Picks persist to a JSON file at
the repo root so they survive Streamlit reruns and (when the deployment
storage is durable) redeploys.

Wired in app.py via the Apps & Generators pill row; see the
"🏆 MRBETS850 HOMERUN PICKS OF DAY" branch in _TOP_VIEW_OPTIONS.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Constants — keep file paths next to the other module-level data files
# (Data:savant_*.csv) so the app's existing deployment surface covers us.
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PICKS_FILENAME = "mrbets850_hr_picks.json"
PICKS_PATH = os.path.join(_THIS_DIR, PICKS_FILENAME)
ASSETS_DIR = os.path.join(_THIS_DIR, "assets")
LOGO_FILENAME = "mrbets850_logo.jpg"
# MLB Edge brand mark — same asset used by app.py's top brand bar so the
# HR picks header feels of-a-piece with the rest of the app.
BRAND_LOGO_FILENAME = "mlb_edge_logo.jpeg"

MAX_PICKS = 25

# Developer unlock — checked in this order:
#   1. st.secrets["MRBETS850_ADMIN_PIN"]
#   2. st.secrets["MLB_EDGE_ADMIN_PIN"]
#   3. os.environ["MRBETS850_ADMIN_PIN"]
#   4. os.environ["MLB_EDGE_ADMIN_PIN"]
# Never hardcoded. If none configured, the editor stays locked.
_ADMIN_PIN_KEYS = ("MRBETS850_ADMIN_PIN", "MLB_EDGE_ADMIN_PIN")


# ---------------------------------------------------------------------------
# Secret resolution + auth gating
# ---------------------------------------------------------------------------
def _resolve_admin_pin() -> str:
    """Pull the admin PIN from st.secrets first, then env. Empty string if
    nothing is configured — the editor renders a locked notice in that case."""
    for key in _ADMIN_PIN_KEYS:
        try:
            v = st.secrets.get(key) if hasattr(st.secrets, "get") else None
            if v:
                return str(v).strip()
        except Exception:
            pass
        try:
            v = st.secrets[key]  # type: ignore[index]
            if v:
                return str(v).strip()
        except Exception:
            pass
    for key in _ADMIN_PIN_KEYS:
        v = os.environ.get(key)
        if v:
            return str(v).strip()
    return ""


def _is_unlocked() -> bool:
    return bool(st.session_state.get("_mrbets850_admin_unlocked", False))


# ---------------------------------------------------------------------------
# Persistence — JSON file at repo root. Schema:
#   {
#     "last_updated": "2026-05-16T17:11:00",
#     "picks": [
#       {"rank": 1, "name": "Aaron Judge", "team": "NYY",
#        "player_id": 592450, "note": "...", "confidence": "🔥 Lock"},
#       ...
#     ]
#   }
# ---------------------------------------------------------------------------
def _empty_state() -> dict[str, Any]:
    return {"last_updated": None, "picks": []}


def load_picks() -> dict[str, Any]:
    """Read the picks file. Returns an empty state if missing/corrupt — never
    raises, so the page always renders."""
    if not os.path.exists(PICKS_PATH):
        return _empty_state()
    try:
        with open(PICKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_state()
        picks = data.get("picks", [])
        if not isinstance(picks, list):
            picks = []
        # Normalise + clamp to MAX_PICKS, re-rank 1..N in case of drift.
        clean: list[dict[str, Any]] = []
        for p in picks[:MAX_PICKS]:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", "")).strip()
            if not name:
                continue
            clean.append({
                "rank": int(p.get("rank") or 0) or (len(clean) + 1),
                "name": name,
                "team": str(p.get("team", "")).strip().upper(),
                "player_id": p.get("player_id"),
                "note": str(p.get("note", "")).strip(),
                "confidence": str(p.get("confidence", "")).strip(),
            })
        clean.sort(key=lambda r: r["rank"])
        for i, r in enumerate(clean, start=1):
            r["rank"] = i
        return {
            "last_updated": data.get("last_updated"),
            "picks": clean,
        }
    except Exception:
        return _empty_state()


def save_picks(state: dict[str, Any]) -> tuple[bool, str]:
    """Write picks to disk. Returns (ok, message)."""
    try:
        state = dict(state)
        state["last_updated"] = datetime.now().isoformat(timespec="seconds")
        with open(PICKS_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return True, "Saved."
    except Exception as e:
        return False, f"Save failed: {e}"


# ---------------------------------------------------------------------------
# Logo (data URI) — small enough to embed inline so the page renders without
# any external network calls.
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _image_data_uri(filename: str) -> str:
    path = os.path.join(ASSETS_DIR, filename)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return ""
    ext = os.path.splitext(filename)[1].lower().lstrip(".") or "jpeg"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _logo_data_uri() -> str:
    return _image_data_uri(LOGO_FILENAME)


def _brand_logo_data_uri() -> str:
    return _image_data_uri(BRAND_LOGO_FILENAME)


# ---------------------------------------------------------------------------
# Stats lookup against the app's existing batters_df. Returns a dict of the
# user's favorite power-combo stats with N/A fallbacks so missing data never
# crashes a card.
# ---------------------------------------------------------------------------
_POWER_STAT_LABELS: list[tuple[str, str, str]] = [
    # (display label, batters_df column, format spec)
    ("ISO",        "ISO",        "{:.3f}"),
    ("EV",         "EV",         "{:.1f} mph"),
    ("Barrel %",   "Barrel%",    "{:.1f}%"),
    ("Hard-Hit %", "HardHit%",   "{:.1f}%"),
    ("FB %",       "FB%",        "{:.1f}%"),
    ("Pull %",     "Pull%",      "{:.1f}%"),
    ("HR",         "HR",         "{:.0f}"),
    ("xwOBA",      "xwOBA",      "{:.3f}"),
]


def _na() -> str:
    return "N/A"


def _fmt(value: Any, fmt: str) -> str:
    try:
        if value is None:
            return _na()
        if isinstance(value, float) and pd.isna(value):
            return _na()
        return fmt.format(float(value))
    except Exception:
        return _na()


def _lookup_batter_row(batters_df: pd.DataFrame | None, name: str, team: str) -> pd.Series | None:
    """Find the batter row matching name (case-insensitive) and optionally team.
    Returns None if no plausible match — caller renders N/A everywhere."""
    if batters_df is None or batters_df.empty or "Name" not in batters_df.columns:
        return None
    nm = name.strip().lower()
    if not nm:
        return None
    rows = batters_df[batters_df["Name"].astype(str).str.lower() == nm]
    if rows.empty:
        # Fuzzy: "judge" matches "Aaron Judge". Keep it conservative — must
        # be a single match, otherwise we'd risk silently mis-attributing.
        contains = batters_df[
            batters_df["Name"].astype(str).str.lower().str.contains(nm, na=False)
        ]
        if len(contains) == 1:
            rows = contains
    if rows.empty:
        return None
    if team and "Team" in rows.columns:
        team_match = rows[rows["Team"].astype(str).str.upper() == team.upper()]
        if not team_match.empty:
            rows = team_match
    return rows.iloc[0]


def _compute_pull_air(row: pd.Series | None) -> str:
    """Pull Air% proxy = Pull% × FB% / 100, mirroring HR_METRIC_KEYS logic in
    app.py. Returns 'N/A' when either input is missing."""
    if row is None:
        return _na()
    try:
        pull = row.get("Pull%")
        fb = row.get("FB%")
        if pull is None or fb is None or pd.isna(pull) or pd.isna(fb):
            return _na()
        return f"{float(pull) * float(fb) / 100.0:.1f}%"
    except Exception:
        return _na()


def _build_stat_grid(row: pd.Series | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for label, col, fmt in _POWER_STAT_LABELS:
        if row is None:
            out.append((label, _na()))
            continue
        try:
            v = row.get(col) if col in row.index else None
        except Exception:
            v = None
        out.append((label, _fmt(v, fmt)))
    # Pull Air% is derived (matches app.py PullAir% definition).
    out.insert(5, ("Pull Air", _compute_pull_air(row)))
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _inject_css() -> None:
    # MLB Edge palette (matches app.py brand bar):
    #   deep-purple gradient #14062e → #2a0f5c → #4c1d95
    #   gold accent #facc15, light gold #fde68a, electric violet #7c3aed
    #
    # The header keeps the dark purple/gold look in BOTH themes — gold-on-
    # purple is the brand mark and reads cleanly against either Streamlit
    # background. The cards, stat grid, notes, empty-state, and editor
    # surfaces use CSS custom properties scoped to `.mrbets850-hr-wrap`
    # that flip via `@media (prefers-color-scheme: dark)`, so body text
    # never lands as low-contrast gold-on-white in light mode or
    # dark-purple-on-dark in dark mode.
    st.markdown(
        """
<style>
.mrbets850-hr-wrap {
    /* Light-theme defaults */
    --mrb-card-bg: linear-gradient(160deg, #ffffff 0%, #fdfaff 60%, #f5efff 100%);
    --mrb-card-border: rgba(124,58,237,0.35);
    --mrb-card-shadow: 0 4px 12px rgba(20,5,50,0.10);
    --mrb-text-strong: #1a0b3a;        /* deep-purple/near-black */
    --mrb-text-muted: #4c1d95;         /* MLB Edge violet for labels */
    --mrb-text-subtle: #6b7280;        /* slate-500 for N/A */
    --mrb-stat-bg: #f5f3ff;            /* violet-50 */
    --mrb-stat-border: rgba(124,58,237,0.18);
    --mrb-stat-value: #4c1d95;         /* violet-900 */
    --mrb-note-bg: rgba(124,58,237,0.06);
    --mrb-note-border: #facc15;
    --mrb-note-text: #3b0764;          /* dark violet, readable on lightviolet */
    --mrb-empty-bg: rgba(124,58,237,0.04);
    --mrb-empty-border: rgba(124,58,237,0.35);
    --mrb-empty-text: #4c1d95;
    --mrb-accent: #facc15;
    --mrb-accent-soft: #fde68a;
    --mrb-violet: #7c3aed;
    --mrb-editor-bg: linear-gradient(180deg, #faf8ff 0%, #f3eeff 100%);
    --mrb-editor-border: rgba(124,58,237,0.25);
    --mrb-editor-text: #1a0b3a;
    --mrb-editor-muted: #4c1d95;
    margin: 6px 0 14px 0;
}
@media (prefers-color-scheme: dark) {
    .mrbets850-hr-wrap {
        --mrb-card-bg: linear-gradient(160deg, #1a0b3a 0%, #221152 55%, #2a0f5c 100%);
        --mrb-card-border: rgba(250,204,21,0.55);
        --mrb-card-shadow: 0 6px 18px rgba(0,0,0,0.45);
        --mrb-text-strong: #fafafa;
        --mrb-text-muted: #fde68a;
        --mrb-text-subtle: #a1a1aa;
        --mrb-stat-bg: rgba(255,255,255,0.06);
        --mrb-stat-border: rgba(250,204,21,0.25);
        --mrb-stat-value: #facc15;
        --mrb-note-bg: rgba(250,204,21,0.10);
        --mrb-note-border: #facc15;
        --mrb-note-text: #fde68a;
        --mrb-empty-bg: rgba(250,204,21,0.06);
        --mrb-empty-border: rgba(250,204,21,0.55);
        --mrb-empty-text: #fde68a;
        --mrb-editor-bg: linear-gradient(180deg, #14062e 0%, #1f0c44 100%);
        --mrb-editor-border: rgba(250,204,21,0.35);
        --mrb-editor-text: #fafafa;
        --mrb-editor-muted: #fde68a;
    }
}

/* ---- Brand header (always dark purple + gold — readable on both themes) ---- */
.mrbets850-hr-wrap .mrbets850-hr-header {
    display: flex; align-items: center; gap: 14px;
    padding: 14px 18px; border-radius: 18px;
    background: linear-gradient(110deg, #14062e 0%, #2a0f5c 55%, #4c1d95 100%);
    border: 1.5px solid rgba(250,204,21,0.60);
    box-shadow: 0 12px 28px rgba(20,5,50,0.45), inset 0 1px 0 rgba(255,255,255,0.05);
    color: #fff;
}
.mrbets850-hr-wrap .mrbets850-hr-brand-logo {
    width: 64px; height: 64px; flex: 0 0 64px;
    border-radius: 14px; background: #1a0b3a; padding: 4px;
    object-fit: contain;
    border: 1px solid rgba(250,204,21,0.55);
    box-shadow: 0 4px 14px rgba(0,0,0,0.45);
}
.mrbets850-hr-wrap .mrbets850-hr-logo {
    width: 56px; height: 56px; border-radius: 12px;
    background: #1a0b3a; padding: 5px; object-fit: contain;
    border: 1.5px solid #facc15;
    box-shadow: 0 0 0 2px rgba(250,204,21,0.18);
}
.mrbets850-hr-wrap .mrbets850-hr-text { min-width: 0; }
.mrbets850-hr-wrap .mrbets850-hr-eyebrow {
    color: #fde68a; font-size: 0.72rem; letter-spacing: 0.18em;
    text-transform: uppercase; font-weight: 800;
}
.mrbets850-hr-wrap .mrbets850-hr-title {
    font-weight: 900; font-size: 1.35rem; color: #facc15;
    letter-spacing: 0.02em; line-height: 1.15;
    text-shadow: 0 2px 6px rgba(0,0,0,0.55);
}
.mrbets850-hr-wrap .mrbets850-hr-sub {
    color: #fde68a; font-size: 0.9rem; font-weight: 600; margin-top: 2px;
}
.mrbets850-hr-wrap .mrbets850-hr-meta {
    margin-left: auto; text-align: right;
    color: #fde68a; font-weight: 700; font-size: 0.85rem;
}
.mrbets850-hr-wrap .mrbets850-hr-meta .big {
    font-size: 1rem; color: #facc15; font-weight: 800;
}

/* ---- Cards ---- */
.mrbets850-hr-wrap .mrbets850-card-grid {
    display: grid; gap: 12px; margin-top: 14px;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
}
.mrbets850-hr-wrap .mrbets850-card {
    position: relative; padding: 14px 14px 12px 14px;
    border-radius: 14px;
    border: 1.5px solid var(--mrb-card-border);
    background: var(--mrb-card-bg);
    box-shadow: var(--mrb-card-shadow);
    color: var(--mrb-text-strong);
}
.mrbets850-hr-wrap .mrbets850-card .rank-badge {
    position: absolute; top: -10px; left: -10px;
    width: 36px; height: 36px; border-radius: 50%;
    background: linear-gradient(135deg, #facc15, #b45309);
    color: #14062e; font-weight: 900; font-size: 1rem;
    display: flex; align-items: center; justify-content: center;
    border: 2px solid #14062e;
    box-shadow: 0 2px 6px rgba(0,0,0,0.35);
}
.mrbets850-hr-wrap .mrbets850-card .head {
    display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
}
.mrbets850-hr-wrap .mrbets850-card .head img.headshot {
    width: 52px; height: 52px; border-radius: 50%;
    object-fit: cover; background: #1a0b3a;
    border: 2px solid var(--mrb-accent);
}
.mrbets850-hr-wrap .mrbets850-card .head .headshot.placeholder {
    display: flex; align-items: center; justify-content: center;
    color: var(--mrb-accent); font-weight: 900;
}
.mrbets850-hr-wrap .mrbets850-card .head .name {
    font-weight: 800; font-size: 1.02rem;
    color: var(--mrb-text-strong); line-height: 1.2;
}
.mrbets850-hr-wrap .mrbets850-card .head .team {
    font-size: 0.78rem; color: var(--mrb-text-muted);
    font-weight: 700; letter-spacing: 0.04em;
}
.mrbets850-hr-wrap .mrbets850-card .confidence {
    display: inline-block; margin-left: 6px;
    padding: 1px 8px; border-radius: 999px;
    font-size: 0.7rem; font-weight: 800;
    background: var(--mrb-accent); color: #14062e;
    border: 1px solid rgba(20,6,46,0.20);
}
.mrbets850-hr-wrap .mrbets850-card .stat-grid {
    display: grid; grid-template-columns: repeat(3, minmax(0,1fr));
    gap: 6px 8px; margin-top: 6px;
}
.mrbets850-hr-wrap .mrbets850-card .stat {
    background: var(--mrb-stat-bg);
    padding: 6px 8px; border-radius: 8px;
    border: 1px solid var(--mrb-stat-border);
}
.mrbets850-hr-wrap .mrbets850-card .stat .lbl {
    font-size: 0.65rem; color: var(--mrb-text-muted);
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
}
.mrbets850-hr-wrap .mrbets850-card .stat .val {
    font-size: 0.95rem; font-weight: 800; color: var(--mrb-stat-value);
}
.mrbets850-hr-wrap .mrbets850-card .stat .val.na {
    color: var(--mrb-text-subtle); font-weight: 700;
}
.mrbets850-hr-wrap .mrbets850-card .note {
    margin-top: 8px; padding: 8px 10px;
    background: var(--mrb-note-bg);
    border-left: 3px solid var(--mrb-note-border);
    border-radius: 8px;
    color: var(--mrb-note-text);
    font-size: 0.85rem; line-height: 1.35; font-weight: 600;
}
.mrbets850-hr-wrap .mrbets850-empty {
    padding: 18px; border-radius: 14px;
    border: 1.5px dashed var(--mrb-empty-border);
    background: var(--mrb-empty-bg);
    color: var(--mrb-empty-text);
    text-align: center; font-weight: 700;
}

/* ---- Editor surfaces (visible only when developer is unlocked) ---- */
.mrbets850-hr-wrap .mrbets850-editor-panel {
    margin-top: 16px; padding: 14px 16px;
    border-radius: 16px;
    background: var(--mrb-editor-bg);
    border: 1.5px solid var(--mrb-editor-border);
    color: var(--mrb-editor-text);
}
.mrbets850-hr-wrap .mrbets850-editor-title {
    color: var(--mrb-violet); font-weight: 900; font-size: 1.05rem;
    letter-spacing: 0.01em; margin: 0 0 4px 0;
}
@media (prefers-color-scheme: dark) {
    .mrbets850-hr-wrap .mrbets850-editor-title { color: #facc15; }
}
.mrbets850-hr-wrap .mrbets850-editor-sub {
    color: var(--mrb-editor-muted); font-weight: 600; font-size: 0.85rem;
}
.mrbets850-hr-wrap .mrbets850-row-team {
    color: var(--mrb-editor-muted); font-size: 0.85rem; font-weight: 700;
    letter-spacing: 0.04em;
}

@media (max-width: 640px) {
    .mrbets850-hr-wrap .mrbets850-hr-header {
        flex-wrap: wrap; padding: 12px; gap: 10px;
    }
    .mrbets850-hr-wrap .mrbets850-hr-brand-logo { width: 48px; height: 48px; flex-basis: 48px; }
    .mrbets850-hr-wrap .mrbets850-hr-logo { width: 44px; height: 44px; }
    .mrbets850-hr-wrap .mrbets850-hr-title { font-size: 1.12rem; }
    .mrbets850-hr-wrap .mrbets850-hr-meta {
        width: 100%; text-align: left;
        margin-left: 0; font-size: 0.78rem;
    }
    .mrbets850-hr-wrap .mrbets850-card-grid { grid-template-columns: 1fr; }
}
</style>
""",
        unsafe_allow_html=True,
    )


def _format_last_updated(iso: str | None) -> str:
    if not iso:
        return "Not posted yet"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%a %b %d, %Y · %I:%M %p").replace(" 0", " ")
    except Exception:
        return str(iso)


def _render_header(last_updated: str | None, count: int) -> None:
    brand_uri = _brand_logo_data_uri()
    logo_uri = _logo_data_uri()
    if brand_uri:
        brand_html = (
            f'<img class="mrbets850-hr-brand-logo" src="{brand_uri}" '
            'alt="MLB Edge" />'
        )
    else:
        brand_html = (
            '<div class="mrbets850-hr-brand-logo" '
            'style="display:flex;align-items:center;justify-content:center;'
            'font-size:1.8rem;color:#facc15;">⚾</div>'
        )
    if logo_uri:
        mrbets_html = (
            f'<img class="mrbets850-hr-logo" src="{logo_uri}" '
            'alt="MrBets850" />'
        )
    else:
        mrbets_html = (
            '<div class="mrbets850-hr-logo" '
            'style="display:flex;align-items:center;justify-content:center;'
            'font-size:1.6rem;color:#facc15;">👑</div>'
        )
    st.markdown(
        '<div class="mrbets850-hr-header">'
        + brand_html
        + mrbets_html
        + '<div class="mrbets850-hr-text">'
        '<div class="mrbets850-hr-eyebrow">MLB Edge · MrBets850</div>'
        '<div class="mrbets850-hr-title">'
        '👑 MRBETS850 HOMERUN PICKS OF DAY</div>'
        '<div class="mrbets850-hr-sub">'
        f'Daily hand-picked HR plays — Top {MAX_PICKS}, ranked by MrBets850</div>'
        '</div>'
        '<div class="mrbets850-hr-meta">'
        f'<div><span class="big">{count}/{MAX_PICKS}</span> picks</div>'
        f'<div>🕒 {_format_last_updated(last_updated)}</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _player_headshot_url(player_id: Any) -> str:
    try:
        pid = int(player_id)
    except (TypeError, ValueError):
        return ""
    if pid <= 0:
        return ""
    return (
        "https://img.mlbstatic.com/mlb-photos/image/upload/"
        "d_people:generic:headshot:67:current.png/"
        "w_180,q_auto:best/v1/people/" + str(pid) + "/headshot/67/current"
    )


def _render_card(pick: dict[str, Any], batters_df: pd.DataFrame | None) -> str:
    name = pick.get("name", "")
    team = pick.get("team", "")
    note = pick.get("note", "")
    confidence = pick.get("confidence", "")
    rank = pick.get("rank", "?")

    row = _lookup_batter_row(batters_df, name, team)
    # Prefer the manually keyed player_id (set when developer picks via the
    # name dropdown) so headshots resolve even if the Savant row lookup
    # misses on a fuzzy name.
    pid = pick.get("player_id")
    if (pid is None or pid == "") and row is not None and "player_id" in row.index:
        pid = row.get("player_id")
    headshot = _player_headshot_url(pid)
    if headshot:
        head_html = f'<img class="headshot" src="{headshot}" alt="{name}" />'
    else:
        head_html = '<div class="headshot placeholder">⚾</div>'

    stat_grid_html_parts: list[str] = []
    for lbl, val in _build_stat_grid(row):
        cls = "val na" if val == _na() else "val"
        stat_grid_html_parts.append(
            f'<div class="stat"><div class="lbl">{lbl}</div>'
            f'<div class="{cls}">{val}</div></div>'
        )

    note_html = (
        f'<div class="note">📝 {_html_escape(note)}</div>' if note else ""
    )
    confidence_html = (
        f'<span class="confidence">{_html_escape(confidence)}</span>'
        if confidence else ""
    )
    team_html = (
        f'<div class="team">{_html_escape(team)}{confidence_html}</div>'
        if team else f'<div class="team">{confidence_html}</div>'
    )

    return (
        '<div class="mrbets850-card">'
        f'<div class="rank-badge">#{rank}</div>'
        '<div class="head">'
        + head_html
        + '<div>'
        f'<div class="name">{_html_escape(name)}</div>'
        + team_html
        + '</div></div>'
        '<div class="stat-grid">'
        + "".join(stat_grid_html_parts)
        + '</div>'
        + note_html
        + '</div>'
    )


def _html_escape(s: Any) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_public_cards(picks: list[dict[str, Any]], batters_df: pd.DataFrame | None) -> None:
    if not picks:
        st.markdown(
            '<div class="mrbets850-empty">'
            "No homerun picks posted yet. Check back soon — MrBets850 posts the "
            "daily Top 25 once lineups settle."
            '</div>',
            unsafe_allow_html=True,
        )
        return
    cards = [_render_card(p, batters_df) for p in picks]
    st.markdown(
        '<div class="mrbets850-card-grid">' + "".join(cards) + '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Developer editor (gated by PIN)
# ---------------------------------------------------------------------------
def _render_unlock_form() -> None:
    """Inline PIN prompt. Lives in an expander so the public view stays clean."""
    expected = _resolve_admin_pin()
    with st.expander("🔐 Developer access (MrBets850 only)", expanded=False):
        if not expected:
            st.info(
                "Developer mode is disabled — no admin PIN is configured. "
                "Set `MRBETS850_ADMIN_PIN` (or `MLB_EDGE_ADMIN_PIN`) in "
                "Streamlit secrets or the deployment environment to enable "
                "the picks editor."
            )
            return
        pin = st.text_input(
            "Enter admin PIN", type="password", key="_mrbets850_pin_input",
            help="The PIN configured via st.secrets / env var.",
        )
        if st.button("Unlock editor", key="_mrbets850_unlock_btn"):
            if pin and pin.strip() == expected:
                st.session_state["_mrbets850_admin_unlocked"] = True
                st.success("Editor unlocked — scroll down to manage picks.")
                try:
                    st.rerun()
                except Exception:
                    pass
            else:
                st.error("Wrong PIN.")


def _batter_dropdown_options(batters_df: pd.DataFrame | None) -> list[str]:
    """Return 'Aaron Judge — NYY' style labels for the picker. Falls back to
    just names if Team column is missing."""
    if batters_df is None or batters_df.empty or "Name" not in batters_df.columns:
        return []
    df = batters_df[["Name"] + (["Team"] if "Team" in batters_df.columns else [])].copy()
    df = df.dropna(subset=["Name"])
    df["Name"] = df["Name"].astype(str).str.strip()
    df = df[df["Name"] != ""].drop_duplicates(subset=["Name"])
    if "Team" in df.columns:
        df["Team"] = df["Team"].astype(str).str.upper().fillna("")
        labels = df.apply(
            lambda r: f"{r['Name']} — {r['Team']}" if r['Team'] else r['Name'],
            axis=1,
        ).tolist()
    else:
        labels = df["Name"].tolist()
    labels.sort()
    return labels


def _parse_dropdown_label(label: str) -> tuple[str, str]:
    if " — " in label:
        n, t = label.split(" — ", 1)
        return n.strip(), t.strip().upper()
    return label.strip(), ""


def _resolve_player_id(batters_df: pd.DataFrame | None, name: str, team: str) -> Any:
    row = _lookup_batter_row(batters_df, name, team)
    if row is None or "player_id" not in row.index:
        return None
    pid = row.get("player_id")
    try:
        if pd.isna(pid):
            return None
    except Exception:
        pass
    try:
        return int(pid)
    except (TypeError, ValueError):
        return pid


def _render_editor(state: dict[str, Any], batters_df: pd.DataFrame | None) -> None:
    st.markdown(
        '<div class="mrbets850-editor-panel">'
        '<div class="mrbets850-editor-title">🛠️ Developer editor</div>'
        '<div class="mrbets850-editor-sub">'
        f'Add, edit, reorder, or clear today\'s Top {MAX_PICKS}. '
        'Changes persist to disk immediately.'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    picks: list[dict[str, Any]] = list(state.get("picks", []))

    options = _batter_dropdown_options(batters_df)
    existing_names = {(p.get("name", "").lower(), p.get("team", "").upper()) for p in picks}

    # ---- Add pick form ----
    with st.form("_mrbets850_add_pick", clear_on_submit=True):
        st.markdown("**➕ Add a new pick**")
        c1, c2 = st.columns([3, 1])
        with c1:
            if options:
                pick_label = st.selectbox(
                    "Player",
                    options=["— Select a batter —"] + options,
                    index=0,
                    key="_mrbets850_pick_label",
                )
            else:
                pick_label = st.text_input(
                    "Player (manual entry — Savant data not loaded)",
                    key="_mrbets850_pick_label_manual",
                )
        with c2:
            rank_target = st.number_input(
                "Rank",
                min_value=1,
                max_value=MAX_PICKS,
                value=min(len(picks) + 1, MAX_PICKS),
                step=1,
                key="_mrbets850_pick_rank",
            )
        c3, c4 = st.columns([2, 2])
        with c3:
            confidence = st.selectbox(
                "Confidence (optional)",
                options=["", "🔥 Lock", "💎 Strong", "🎯 Lean", "💸 Long Shot"],
                index=0,
                key="_mrbets850_pick_conf",
            )
        with c4:
            manual_team = st.text_input(
                "Team override (optional, e.g. NYY)",
                value="",
                key="_mrbets850_pick_team_override",
            ).strip().upper()
        note = st.text_area(
            "Notes (optional — short reason / matchup angle)",
            value="",
            key="_mrbets850_pick_note",
            max_chars=240,
        )
        submitted = st.form_submit_button("Add pick")
        if submitted:
            label_value = (pick_label or "").strip()
            if not label_value or label_value.startswith("— "):
                st.warning("Pick a player first.")
            elif len(picks) >= MAX_PICKS:
                st.warning(f"Already at the {MAX_PICKS}-pick maximum. Remove "
                           "one before adding another.")
            else:
                name, team = _parse_dropdown_label(label_value)
                if manual_team:
                    team = manual_team
                key = (name.lower(), team.upper())
                if key in existing_names:
                    st.warning(f"{name} ({team}) is already on the board.")
                else:
                    pid = _resolve_player_id(batters_df, name, team)
                    new_pick = {
                        "rank": int(rank_target),
                        "name": name,
                        "team": team,
                        "player_id": pid,
                        "note": note.strip(),
                        "confidence": confidence,
                    }
                    # Shift existing picks at >= rank_target down by 1, then
                    # insert. Re-rank 1..N at the end.
                    for p in picks:
                        if int(p.get("rank", 0)) >= int(rank_target):
                            p["rank"] = int(p.get("rank", 0)) + 1
                    picks.append(new_pick)
                    picks.sort(key=lambda r: r["rank"])
                    for i, r in enumerate(picks, start=1):
                        r["rank"] = i
                    picks = picks[:MAX_PICKS]
                    state["picks"] = picks
                    ok, msg = save_picks(state)
                    if ok:
                        st.success(f"Added {name} at rank {rank_target}.")
                        try:
                            st.rerun()
                        except Exception:
                            pass
                    else:
                        st.error(msg)

    # ---- Existing picks table ----
    if not picks:
        st.info("No picks yet — add one above.")
    else:
        st.markdown("**📋 Current picks** (edit rank / notes / confidence, then **Save changes**)")
        with st.form("_mrbets850_edit_picks"):
            edited: list[dict[str, Any]] = []
            for idx, p in enumerate(picks):
                with st.container():
                    cols = st.columns([1, 4, 2, 3, 1])
                    with cols[0]:
                        new_rank = st.number_input(
                            "Rank",
                            min_value=1, max_value=MAX_PICKS,
                            value=int(p.get("rank", idx + 1)),
                            key=f"_mrbets850_edit_rank_{idx}",
                            label_visibility="collapsed",
                        )
                    with cols[1]:
                        st.markdown(
                            f"**{_html_escape(p.get('name'))}** "
                            f"<span class='mrbets850-row-team'>"
                            f"{_html_escape(p.get('team') or '')}</span>",
                            unsafe_allow_html=True,
                        )
                    with cols[2]:
                        new_conf = st.selectbox(
                            "Confidence",
                            options=["", "🔥 Lock", "💎 Strong", "🎯 Lean", "💸 Long Shot"],
                            index=(
                                ["", "🔥 Lock", "💎 Strong", "🎯 Lean", "💸 Long Shot"]
                                .index(p.get("confidence") or "")
                                if (p.get("confidence") or "") in
                                ["", "🔥 Lock", "💎 Strong", "🎯 Lean", "💸 Long Shot"]
                                else 0
                            ),
                            key=f"_mrbets850_edit_conf_{idx}",
                            label_visibility="collapsed",
                        )
                    with cols[3]:
                        new_note = st.text_input(
                            "Note",
                            value=p.get("note", ""),
                            key=f"_mrbets850_edit_note_{idx}",
                            label_visibility="collapsed",
                            placeholder="note",
                        )
                    with cols[4]:
                        delete_me = st.checkbox(
                            "Del",
                            value=False,
                            key=f"_mrbets850_edit_del_{idx}",
                        )
                    if not delete_me:
                        edited.append({
                            "rank": int(new_rank),
                            "name": p.get("name", ""),
                            "team": p.get("team", ""),
                            "player_id": p.get("player_id"),
                            "note": new_note.strip(),
                            "confidence": new_conf,
                        })
            save_btn = st.form_submit_button("💾 Save changes")
            if save_btn:
                # Re-rank 1..N to absorb gaps and duplicates after deletes.
                edited.sort(key=lambda r: r["rank"])
                for i, r in enumerate(edited, start=1):
                    r["rank"] = i
                state["picks"] = edited[:MAX_PICKS]
                ok, msg = save_picks(state)
                if ok:
                    st.success("Saved.")
                    try:
                        st.rerun()
                    except Exception:
                        pass
                else:
                    st.error(msg)

    # ---- Danger zone ----
    with st.expander("⚠️ Danger zone", expanded=False):
        st.caption(
            "Clearing wipes today's board. Picks are persisted at "
            f"`{PICKS_FILENAME}` next to app.py."
        )
        confirm = st.checkbox(
            "I understand this will delete all picks",
            key="_mrbets850_clear_confirm",
        )
        if st.button("🧹 Clear all picks", disabled=not confirm,
                     key="_mrbets850_clear_btn"):
            state["picks"] = []
            ok, msg = save_picks(state)
            if ok:
                st.success("Cleared.")
                try:
                    st.rerun()
                except Exception:
                    pass
            else:
                st.error(msg)
        if st.button("🔒 Lock editor", key="_mrbets850_lock_btn"):
            st.session_state["_mrbets850_admin_unlocked"] = False
            try:
                st.rerun()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public entrypoint — called from app.py
# ---------------------------------------------------------------------------
def render_mrbets850_hr_picks(batters_df: pd.DataFrame | None = None) -> None:
    """Render the MRBETS850 HOMERUN PICKS OF DAY tab. Public users see the
    logo + ranked player cards; the developer sees an additional editor once
    the PIN is entered."""
    _inject_css()
    # Open a single wrapper so the CSS custom properties (--mrb-*) apply
    # to the header, public cards, AND the editor surface — keeping the
    # whole tab in a consistent MLB Edge theme and switching cleanly
    # between light/dark via prefers-color-scheme.
    st.markdown('<div class="mrbets850-hr-wrap">', unsafe_allow_html=True)
    state = load_picks()
    picks = state.get("picks", [])

    _render_header(state.get("last_updated"), count=len(picks))
    _render_public_cards(picks, batters_df)

    _render_unlock_form()
    if _is_unlocked():
        _render_editor(state, batters_df)
    st.markdown('</div>', unsafe_allow_html=True)
