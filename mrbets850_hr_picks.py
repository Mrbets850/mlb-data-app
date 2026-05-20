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
import re
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # py<3.9 — Streamlit Cloud runs 3.10+, but stay safe.
    ZoneInfo = None  # type: ignore[assignment]

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

# Remote persistence (GitHub Contents API). All keys optional — when no
# token is present we silently fall back to local JSON only.
_GH_TOKEN_KEYS = ("MRBETS850_GITHUB_TOKEN", "GITHUB_TOKEN")
_GH_REPO_KEYS = ("MRBETS850_PICKS_REPO",)
_GH_PATH_KEYS = ("MRBETS850_PICKS_PATH",)
_GH_BRANCH_KEYS = ("MRBETS850_PICKS_BRANCH",)
_DEFAULT_REPO = "Mrbets850/mlb-data-app"
_DEFAULT_REMOTE_PATH = "data/mrbets850_hr_picks.json"
_DEFAULT_BRANCH = "main"


# ---------------------------------------------------------------------------
# Secret resolution + auth gating
# ---------------------------------------------------------------------------
def _resolve_secret(keys: tuple[str, ...]) -> str:
    """Pull a secret from st.secrets first, then env. Empty string if
    nothing is configured."""
    for key in keys:
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
    for key in keys:
        v = os.environ.get(key)
        if v:
            return str(v).strip()
    return ""


def _resolve_admin_pin() -> str:
    """Admin PIN — see _ADMIN_PIN_KEYS for lookup order."""
    return _resolve_secret(_ADMIN_PIN_KEYS)


def _is_unlocked() -> bool:
    return bool(st.session_state.get("_mrbets850_admin_unlocked", False))


# ---------------------------------------------------------------------------
# Central-time slate date helper. The live HR tracker uses America/Chicago to
# define "today's slate" because UTC flips mid-evening Central. We mirror that
# convention so HR-hit activation lines up with the same slate the tracker
# shows. Overridable via ``MRBETS850_TODAY_OVERRIDE`` (YYYY-MM-DD) for dev.
# ---------------------------------------------------------------------------
_CENTRAL_TZ_NAME = "America/Chicago"


def _today_central_date(now: datetime | None = None) -> date:
    """Return today's date in America/Chicago. Honours the
    ``MRBETS850_TODAY_OVERRIDE`` env var (or st.secrets key) when set, for
    deterministic local testing."""
    override = _resolve_secret(("MRBETS850_TODAY_OVERRIDE",))
    if override:
        try:
            return datetime.strptime(override.strip(), "%Y-%m-%d").date()
        except Exception:
            pass
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    if ZoneInfo is not None:
        try:
            return now.replace(tzinfo=timezone.utc).astimezone(
                ZoneInfo(_CENTRAL_TZ_NAME)
            ).date()
        except Exception:
            pass
    # Coarse fallback when zoneinfo is missing (e.g. minimal docker image):
    # apply a fixed -5h CDT offset for May–Oct, -6h CST otherwise. The MLB
    # season runs mostly in DST so this is correct almost all the time.
    from datetime import timedelta
    offset = -5 if 3 <= now.month <= 10 else -6
    return (now + timedelta(hours=offset)).date()


# ---------------------------------------------------------------------------
# Remote (GitHub Contents API) persistence backend. Stdlib-only so we don't
# add a dependency. Returns rich status dicts the UI can surface.
# ---------------------------------------------------------------------------
def _remote_config() -> dict[str, str]:
    return {
        "token": _resolve_secret(_GH_TOKEN_KEYS),
        "repo": _resolve_secret(_GH_REPO_KEYS) or _DEFAULT_REPO,
        "path": _resolve_secret(_GH_PATH_KEYS) or _DEFAULT_REMOTE_PATH,
        "branch": _resolve_secret(_GH_BRANCH_KEYS) or _DEFAULT_BRANCH,
    }


def _remote_enabled(cfg: dict[str, str] | None = None) -> bool:
    cfg = cfg or _remote_config()
    return bool(cfg.get("token") and cfg.get("repo") and cfg.get("path"))


def _gh_request(url: str, *, token: str, method: str = "GET",
                payload: dict[str, Any] | None = None,
                timeout: float = 10.0) -> tuple[int, dict[str, Any] | None]:
    """Minimal GitHub Contents API call. Returns (status_code, json|None)."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "mrbets850-hr-picks/1.0")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            return resp.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
            j = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            j = None
        return e.code, j
    except Exception as e:  # network/timeout/decode failure
        return 0, {"_exception": str(e)}


def _gh_contents_url(repo: str, path: str) -> str:
    # Repo / path are user-configured; build the URL with urllib quoting to
    # avoid breaking on spaces or subdirectories.
    from urllib.parse import quote
    return f"https://api.github.com/repos/{repo}/contents/{quote(path)}"


def _normalize_state(data: Any) -> dict[str, Any]:
    """Validate + normalize an arbitrary blob into the picks schema. Caps to
    MAX_PICKS and re-ranks 1..N. Never raises."""
    if not isinstance(data, dict):
        return _empty_state()
    picks_in = data.get("picks", [])
    if not isinstance(picks_in, list):
        picks_in = []
    clean: list[dict[str, Any]] = []
    for p in picks_in[:MAX_PICKS]:
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
    return {"last_updated": data.get("last_updated"), "picks": clean}


def fetch_remote_picks(cfg: dict[str, str] | None = None
                       ) -> tuple[dict[str, Any] | None, str | None, str]:
    """Fetch picks JSON from GitHub. Returns (state|None, sha|None, message).
    ``state`` is None when the file does not exist or the call failed."""
    cfg = cfg or _remote_config()
    if not _remote_enabled(cfg):
        return None, None, "remote disabled"
    url = _gh_contents_url(cfg["repo"], cfg["path"])
    code, body = _gh_request(
        f"{url}?ref={cfg['branch']}", token=cfg["token"], method="GET",
    )
    if code == 404:
        return None, None, "remote file not found"
    if code != 200 or not isinstance(body, dict):
        return None, None, f"remote fetch failed (HTTP {code})"
    raw_b64 = body.get("content") or ""
    sha = body.get("sha")
    try:
        decoded = base64.b64decode(raw_b64).decode("utf-8")
        data = json.loads(decoded) if decoded.strip() else {}
    except Exception as e:
        return None, sha, f"remote JSON parse failed: {e}"
    return _normalize_state(data), sha, "ok"


def push_remote_picks(state: dict[str, Any],
                      *, sha: str | None,
                      cfg: dict[str, str] | None = None,
                      commit_message: str | None = None,
                      ) -> tuple[bool, str | None, str]:
    """Push picks JSON to GitHub. Returns (ok, new_sha, message)."""
    cfg = cfg or _remote_config()
    if not _remote_enabled(cfg):
        return False, None, "remote disabled — no token configured"
    state = _normalize_state(state)
    state["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_str = json.dumps(state, indent=2, ensure_ascii=False)
    encoded = base64.b64encode(content_str.encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {
        "message": commit_message or
        f"chore(mrbets850): update HR picks ({len(state['picks'])} picks)",
        "content": encoded,
        "branch": cfg["branch"],
    }
    if sha:
        payload["sha"] = sha
    url = _gh_contents_url(cfg["repo"], cfg["path"])
    code, body = _gh_request(url, token=cfg["token"], method="PUT",
                             payload=payload)
    if code in (200, 201) and isinstance(body, dict):
        new_sha = ((body.get("content") or {}) if isinstance(body.get("content"), dict)
                   else {}).get("sha")
        return True, new_sha, "ok"
    msg = ""
    if isinstance(body, dict):
        msg = body.get("message") or body.get("_exception") or ""
    return False, None, f"remote save failed (HTTP {code}) {msg}".strip()


def _safe_remote_overwrite_allowed(remote_state: dict[str, Any] | None,
                                   new_state: dict[str, Any]) -> bool:
    """Guard rail: refuse to push an EMPTY board on top of a non-empty remote
    unless the caller has explicitly opted in via session state. Prevents a
    fresh-deploy local cache (which starts empty) from wiping good data."""
    if remote_state is None:
        return True
    remote_picks = remote_state.get("picks") or []
    new_picks = new_state.get("picks") or []
    if remote_picks and not new_picks:
        return bool(st.session_state.get("_mrbets850_allow_remote_clear", False))
    return True


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


def _read_local_picks_file() -> dict[str, Any]:
    """Read + normalize the local cache file. Never raises."""
    if not os.path.exists(PICKS_PATH):
        return _empty_state()
    try:
        with open(PICKS_PATH, "r", encoding="utf-8") as f:
            return _normalize_state(json.load(f))
    except Exception:
        return _empty_state()


def _write_local_picks_file(state: dict[str, Any]) -> tuple[bool, str]:
    try:
        with open(PICKS_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return True, "local cache updated"
    except Exception as e:
        return False, f"local save failed: {e}"


def load_picks() -> dict[str, Any]:
    """Load picks. Prefers remote (GitHub) when configured; falls back to
    local JSON otherwise. Never raises so the page always renders."""
    cfg = _remote_config()
    if _remote_enabled(cfg):
        remote_state, sha, msg = fetch_remote_picks(cfg)
        if remote_state is not None and remote_state.get("picks"):
            # Refresh local cache so a future offline render still works.
            _write_local_picks_file(remote_state)
            st.session_state["_mrbets850_remote_sha"] = sha
            st.session_state["_mrbets850_remote_status"] = (
                f"connected · {len(remote_state['picks'])} picks · {msg}"
            )
            return remote_state
        # Remote configured but empty/missing — fall back to local, and let
        # the editor offer a migration when admin is unlocked.
        st.session_state["_mrbets850_remote_sha"] = sha
        st.session_state["_mrbets850_remote_status"] = (
            f"connected · remote empty/missing ({msg})"
        )
        return _read_local_picks_file()
    st.session_state["_mrbets850_remote_status"] = (
        "local fallback only — no GitHub token configured"
    )
    return _read_local_picks_file()


def save_picks(state: dict[str, Any]) -> tuple[bool, str]:
    """Persist picks. Writes remote (GitHub) when configured AND the editor
    is unlocked (so a public view can never trigger a remote write), and
    always refreshes the local cache. Returns (ok, message)."""
    state = _normalize_state(state)
    state["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cfg = _remote_config()
    remote_msg = ""
    remote_ok = True  # only flips to False on an actual failed remote push

    if _remote_enabled(cfg) and _is_unlocked():
        # Re-check remote so the no-overwrite guard sees the latest state and
        # we have a fresh sha. (GitHub PUT requires the previous sha when the
        # file already exists.)
        remote_state, sha, _ = fetch_remote_picks(cfg)
        if not _safe_remote_overwrite_allowed(remote_state, state):
            remote_ok = False
            remote_msg = (
                "remote save BLOCKED — refusing to overwrite a non-empty "
                "remote board with an empty list. Tick 'Allow remote clear' "
                "in Danger zone to override."
            )
        else:
            ok, new_sha, msg = push_remote_picks(state, sha=sha, cfg=cfg)
            if ok:
                st.session_state["_mrbets850_remote_sha"] = new_sha or sha
                remote_msg = "Saved to GitHub (permanent)."
            else:
                remote_ok = False
                remote_msg = msg

    ok_local, local_msg = _write_local_picks_file(state)
    if not ok_local and not remote_ok:
        return False, f"{remote_msg or 'save failed'}; {local_msg}"
    if remote_msg:
        return remote_ok, f"{remote_msg}" + (f"; {local_msg}" if ok_local else "")
    return ok_local, local_msg if ok_local else f"Save failed: {local_msg}"


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
    # Labels kept short — they render inside compact stat chips in the
    # square tile layout, so anything wider than ~7 characters wraps and
    # blows the card height up. "Barrel%"/"HardHit%"/"FB%"/"Pull%" match
    # the Savant column names directly so they read at a glance.
    # OPS leads the grid as the universal bat-quality signal so every
    # picks card surfaces "is this bat producing?" alongside HR power.
    ("OPS",     "OPS",      "{:.3f}"),
    ("ISO",     "ISO",      "{:.3f}"),
    ("EV",      "EV",       "{:.1f}"),
    ("Barrel%", "Barrel%",  "{:.1f}%"),
    ("HardHit", "HardHit%", "{:.1f}%"),
    ("FB%",     "FB%",      "{:.1f}%"),
    ("Pull%",   "Pull%",    "{:.1f}%"),
    ("HR",      "HR",       "{:.0f}"),
    ("xwOBA",   "xwOBA",    "{:.3f}"),
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
        # OPS: backfill from OBP + SLG when the canonical column is empty so
        # cards never collapse to N/A for batters whose feed only ships the
        # components. standardize_columns already fills most cases; this is
        # a final safety net for picks invoked outside the main app flow.
        if col == "OPS":
            try:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    obp = row.get("OBP") if "OBP" in row.index else None
                    slg = row.get("SLG") if "SLG" in row.index else None
                    if obp is not None and slg is not None:
                        obp_f = float(obp); slg_f = float(slg)
                        if not pd.isna(obp_f) and not pd.isna(slg_f):
                            v = obp_f + slg_f
            except Exception:
                pass
        out.append((label, _fmt(v, fmt)))
    # Pull Air% is derived (matches app.py PullAir% definition). With OPS
    # prepended above, FB% now sits at index 5; insert PullAir right after
    # it so the original FB% → PullAir → Pull% ordering is preserved.
    fb_idx = next((i for i, (lbl, *_rest) in enumerate(out) if lbl == "FB%"), 5)
    out.insert(fb_idx + 1, ("PullAir", _compute_pull_air(row)))
    return out


# ---------------------------------------------------------------------------
# HR-hit activation — pull today's HR events from live_hr_tracker and match
# them against our picks. A pick is "cashed" when one of its identifying
# fields lines up with a HR event for the Central-time slate date.
# ---------------------------------------------------------------------------
def _normalize_name(s: Any) -> str:
    """Strip accents, lowercase, drop punctuation. ``José Ramírez`` →
    ``jose ramirez``. Used to make name matching robust to apostrophes,
    diacritics, and "Jr."/Sr. suffixes."""
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\.,'`]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_todays_hr_events_cached(date_iso: str) -> list[dict[str, Any]]:
    """Pull all of today's HR plays from live_hr_tracker. Cached for 60s so
    the picks page doesn't slam StatsAPI on every Streamlit rerun.

    ``date_iso`` exists purely as a cache key — we still ask the tracker to
    scan its own Central-date window, which already includes the UTC-adjacent
    date when needed. Passing the date in keeps the cache scoped to "today."
    """
    try:
        from live_hr_tracker import MLBLiveHRFeed
    except Exception:
        return []
    try:
        feed = MLBLiveHRFeed(date_iso=date_iso)
        return feed.fetch_new_events(set())
    except Exception:
        return []


def fetch_todays_hr_events(today: date | None = None) -> list[dict[str, Any]]:
    """Public-facing wrapper around the cached fetch. Accepts a date override
    so tests can pin the slate. Returns [] on any failure."""
    d = today or _today_central_date()
    try:
        return _fetch_todays_hr_events_cached(d.strftime("%Y-%m-%d"))
    except Exception:
        return []


def compute_hr_hits(picks: list[dict[str, Any]],
                    events: list[dict[str, Any]] | None,
                    ) -> dict[int, dict[str, Any]]:
    """For each pick (keyed by rank), return how many HRs they've hit today
    plus a representative event. Matching prefers MLB player_id, then falls
    back to normalized name + team.

    Returns: ``{rank: {"count": int, "events": [...], "first": event_dict}}``
    """
    if not picks or not events:
        return {}
    # Build lookup indices.
    by_id: dict[int, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_name_team: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ev in events:
        pid = ev.get("player_id") or ev.get("_batter_id")
        try:
            pid_int = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_int = None
        if pid_int:
            by_id.setdefault(pid_int, []).append(ev)
        nm = _normalize_name(ev.get("name"))
        if nm:
            by_name.setdefault(nm, []).append(ev)
            team = str(ev.get("team") or "").strip().upper()
            if team:
                by_name_team.setdefault((nm, team), []).append(ev)

    out: dict[int, dict[str, Any]] = {}
    for p in picks:
        try:
            pid = int(p.get("player_id")) if p.get("player_id") is not None else None
        except (TypeError, ValueError):
            pid = None
        matched: list[dict[str, Any]] = []
        if pid and pid in by_id:
            matched = by_id[pid]
        else:
            nm = _normalize_name(p.get("name"))
            team = str(p.get("team") or "").strip().upper()
            if nm and team and (nm, team) in by_name_team:
                matched = by_name_team[(nm, team)]
            elif nm and nm in by_name:
                matched = by_name[nm]
        if matched:
            try:
                rank_key = int(p.get("rank") or 0)
            except (TypeError, ValueError):
                rank_key = 0
            out[rank_key] = {
                "count": len(matched),
                "events": matched,
                "first": matched[0],
            }
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
    --mrb-card-border: rgba(124,58,237,0.30);
    --mrb-card-shadow: 0 4px 14px rgba(20,5,50,0.10);
    --mrb-text-strong: #1a0b3a;
    --mrb-text-muted: #4c1d95;
    --mrb-text-subtle: #6b7280;
    --mrb-stat-bg: #f5f3ff;
    --mrb-stat-border: rgba(124,58,237,0.16);
    --mrb-stat-value: #4c1d95;
    --mrb-note-bg: rgba(124,58,237,0.06);
    --mrb-note-border: #facc15;
    --mrb-note-text: #3b0764;
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
        --mrb-card-bg: linear-gradient(160deg, #0e0628 0%, #1a0b3a 50%, #221152 100%);
        --mrb-card-border: rgba(250,204,21,0.45);
        --mrb-card-shadow: 0 6px 22px rgba(0,0,0,0.50);
        --mrb-text-strong: #f4f0ff;
        --mrb-text-muted: #fde68a;
        --mrb-text-subtle: #a1a1aa;
        --mrb-stat-bg: rgba(255,255,255,0.06);
        --mrb-stat-border: rgba(250,204,21,0.22);
        --mrb-stat-value: #facc15;
        --mrb-note-bg: rgba(250,204,21,0.09);
        --mrb-note-border: #facc15;
        --mrb-note-text: #fde68a;
        --mrb-empty-bg: rgba(250,204,21,0.06);
        --mrb-empty-border: rgba(250,204,21,0.50);
        --mrb-empty-text: #fde68a;
        --mrb-editor-bg: linear-gradient(180deg, #14062e 0%, #1f0c44 100%);
        --mrb-editor-border: rgba(250,204,21,0.35);
        --mrb-editor-text: #fafafa;
        --mrb-editor-muted: #fde68a;
    }
}

/* ---- animations ---- */
@keyframes mrbHeaderScan {
    0%   { transform: translateX(-100%); opacity:0; }
    20%  { opacity:1; } 80% { opacity:1; }
    100% { transform: translateX(250%); opacity:0; }
}
@keyframes mrbHeaderGlow {
    0%, 100% { box-shadow: 0 12px 32px rgba(20,5,50,.50), 0 0 0 1px rgba(250,204,21,.25); }
    50%       { box-shadow: 0 16px 42px rgba(20,5,50,.65), 0 0 0 1px rgba(250,204,21,.50); }
}

/* ---- Brand header (always dark purple + gold) ---- */
.mrbets850-hr-wrap .mrbets850-hr-header {
    display: flex; align-items: center; gap: 14px;
    padding: 16px 20px; border-radius: 20px;
    background: linear-gradient(115deg, #080220 0%, #1e0b4a 40%, #2e1065 70%, #4c1d95 100%);
    border: 1px solid rgba(250,204,21,0.52);
    animation: mrbHeaderGlow 4s ease-in-out infinite;
    color: #fff; position: relative; overflow: hidden;
}
.mrbets850-hr-wrap .mrbets850-hr-header::before {
    content:''; position:absolute; inset:0; pointer-events:none;
    background-image: radial-gradient(circle, rgba(250,204,21,.04) 1px, transparent 1px);
    background-size: 16px 16px;
}
.mrbets850-hr-wrap .mrbets850-hr-header::after {
    content:''; position:absolute; top:0; bottom:0; width:35%;
    pointer-events:none;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,.05), transparent);
    animation: mrbHeaderScan 6s ease-in-out infinite 1.5s;
}
.mrbets850-hr-wrap .mrbets850-hr-brand-logo {
    width: 64px; height: 64px; flex: 0 0 64px;
    border-radius: 14px; background: #12073a; padding: 4px;
    object-fit: contain;
    border: 1px solid rgba(250,204,21,0.55);
    box-shadow: 0 4px 18px rgba(0,0,0,.50), 0 0 0 2px rgba(250,204,21,.10);
    position: relative; z-index: 1;
}
.mrbets850-hr-wrap .mrbets850-hr-logo {
    width: 56px; height: 56px; border-radius: 12px;
    background: #12073a; padding: 5px; object-fit: contain;
    border: 1px solid rgba(250,204,21,0.55);
    box-shadow: 0 0 0 2px rgba(250,204,21,0.15);
    position: relative; z-index: 1;
}
.mrbets850-hr-wrap .mrbets850-hr-text { min-width: 0; position: relative; z-index: 1; }
.mrbets850-hr-wrap .mrbets850-hr-eyebrow {
    color: #fde68a; font-size: 0.70rem; letter-spacing: 0.18em;
    text-transform: uppercase; font-weight: 800;
    display: flex; align-items: center; gap: 7px;
}
.mrbets850-hr-wrap .mrbets850-hr-eyebrow::before {
    content: '';
    width: 7px; height: 7px; border-radius: 50%; background: #22c55e;
    flex-shrink: 0; display: inline-block;
    animation: mrbLiveDot 1.8s ease-in-out infinite;
}
@keyframes mrbLiveDot {
    0%, 100% { opacity:1; box-shadow: 0 0 0 0 rgba(34,197,94,.6); }
    50%       { opacity:.7; box-shadow: 0 0 0 5px rgba(34,197,94,0); }
}
.mrbets850-hr-wrap .mrbets850-hr-title {
    font-weight: 900; font-size: 1.38rem; color: #facc15;
    letter-spacing: 0.02em; line-height: 1.12;
    text-shadow: 0 0 20px rgba(250,204,21,.35), 0 2px 6px rgba(0,0,0,.60);
}
.mrbets850-hr-wrap .mrbets850-hr-sub {
    color: #c4b5fd; font-size: 0.88rem; font-weight: 600; margin-top: 3px;
}
.mrbets850-hr-wrap .mrbets850-hr-meta {
    margin-left: auto; text-align: right;
    color: #fde68a; font-weight: 700; font-size: 0.85rem;
    position: relative; z-index: 1;
}
.mrbets850-hr-wrap .mrbets850-hr-meta .big {
    font-size: 1rem; color: #facc15; font-weight: 800;
}

/* ---- Cards (compact square tiles) ---- */
.mrbets850-hr-wrap .mrbets850-card-grid {
    display: grid; gap: 10px; margin-top: 14px;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
}
.mrbets850-hr-wrap .mrbets850-card {
    position: relative; padding: 11px;
    border-radius: 16px;
    border: 1px solid var(--mrb-card-border);
    background: var(--mrb-card-bg);
    box-shadow: var(--mrb-card-shadow);
    color: var(--mrb-text-strong);
    overflow: hidden;
    display: flex; flex-direction: column;
    transition: border-color .22s, box-shadow .22s, transform .22s;
}
/* subtle top accent line on each card */
.mrbets850-hr-wrap .mrbets850-card::before {
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent 15%, rgba(250,204,21,.25) 50%, transparent 85%);
    opacity: 0; transition: opacity .22s;
}
@media (prefers-color-scheme: dark) {
    .mrbets850-hr-wrap .mrbets850-card:hover {
        border-color: rgba(250,204,21,.65);
        box-shadow: 0 10px 28px rgba(0,0,0,.55), 0 0 0 1px rgba(250,204,21,.15),
                    0 0 16px rgba(250,204,21,.06);
        transform: translateY(-2px);
    }
    .mrbets850-hr-wrap .mrbets850-card:hover::before { opacity: 1; }
}
/* The header row keeps the rank pill, headshot, and name on one line. */
.mrbets850-hr-wrap .mrbets850-card .head {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 8px; min-width: 0;
}
.mrbets850-hr-wrap .mrbets850-card .rank-pill {
    flex: 0 0 auto;
    min-width: 26px; height: 26px; padding: 0 6px; border-radius: 999px;
    background: linear-gradient(135deg, #ffe042 0%, #facc15 50%, #f59e0b 100%);
    color: #14062e; font-weight: 900; font-size: 0.78rem;
    display: inline-flex; align-items: center; justify-content: center;
    border: 1px solid rgba(20,6,46,0.25);
    box-shadow: 0 2px 6px rgba(0,0,0,.30), 0 0 0 1px rgba(255,255,255,.15) inset;
    line-height: 1;
}
.mrbets850-hr-wrap .mrbets850-card .head img.headshot,
.mrbets850-hr-wrap .mrbets850-card .head .headshot {
    width: 36px; height: 36px; flex: 0 0 36px;
    border-radius: 50%;
    object-fit: cover; background: #1a0b3a;
    border: 2px solid var(--mrb-accent);
    box-shadow: 0 0 0 1px rgba(250,204,21,.12);
}
.mrbets850-hr-wrap .mrbets850-card .head .headshot.placeholder {
    display: flex; align-items: center; justify-content: center;
    color: var(--mrb-accent); font-weight: 900; font-size: 0.9rem;
}
.mrbets850-hr-wrap .mrbets850-card .head .id {
    min-width: 0; flex: 1 1 auto;
}
.mrbets850-hr-wrap .mrbets850-card .head .name {
    font-weight: 800; font-size: 0.9rem;
    color: var(--mrb-text-strong); line-height: 1.15;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.mrbets850-hr-wrap .mrbets850-card .head .team {
    font-size: 0.68rem; color: var(--mrb-text-muted);
    font-weight: 700; letter-spacing: 0.04em;
    display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
}
.mrbets850-hr-wrap .mrbets850-card .confidence {
    display: inline-block;
    padding: 1px 7px; border-radius: 999px;
    font-size: 0.62rem; font-weight: 800;
    background: var(--mrb-accent); color: #14062e;
    border: 1px solid rgba(20,6,46,0.20);
    line-height: 1.4;
}
.mrbets850-hr-wrap .mrbets850-card .stat-grid {
    display: grid; grid-template-columns: repeat(2, minmax(0,1fr));
    gap: 4px 6px; margin-top: 2px;
}
.mrbets850-hr-wrap .mrbets850-card .stat {
    background: var(--mrb-stat-bg);
    padding: 4px 7px; border-radius: 8px;
    border: 1px solid var(--mrb-stat-border);
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 4px; min-width: 0;
    transition: border-color .18s;
}
.mrbets850-hr-wrap .mrbets850-card:hover .stat {
    border-color: rgba(250,204,21,.30);
}
.mrbets850-hr-wrap .mrbets850-card .stat .lbl {
    font-size: 0.58rem; color: var(--mrb-text-muted);
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
    white-space: nowrap;
}
.mrbets850-hr-wrap .mrbets850-card .stat .val {
    font-size: 0.80rem; font-weight: 800; color: var(--mrb-stat-value);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-variant-numeric: tabular-nums;
}
.mrbets850-hr-wrap .mrbets850-card .stat .val.na {
    color: var(--mrb-text-subtle); font-weight: 700;
}
.mrbets850-hr-wrap .mrbets850-card .note {
    margin-top: 7px; padding: 5px 8px;
    background: var(--mrb-note-bg);
    border-left: 3px solid var(--mrb-note-border);
    border-radius: 7px;
    color: var(--mrb-note-text);
    font-size: 0.72rem; line-height: 1.3; font-weight: 600;
}
/* ---- Cashed-card state ---- */
.mrbets850-hr-wrap .mrbets850-card.cashed {
    background: linear-gradient(160deg, #032b16 0%, #054e2e 55%, #047857 100%);
    border-color: #22c55e;
    box-shadow: 0 0 0 1px rgba(34,197,94,.25),
                0 8px 28px rgba(5,150,105,.50);
    color: #ecfdf5;
}
.mrbets850-hr-wrap .mrbets850-card.cashed .name,
.mrbets850-hr-wrap .mrbets850-card.cashed .team,
.mrbets850-hr-wrap .mrbets850-card.cashed .stat .lbl,
.mrbets850-hr-wrap .mrbets850-card.cashed .stat .val,
.mrbets850-hr-wrap .mrbets850-card.cashed .note {
    color: #ecfdf5;
}
.mrbets850-hr-wrap .mrbets850-card.cashed .stat {
    background: rgba(255,255,255,0.10);
    border-color: rgba(187,247,208,0.40);
}
.mrbets850-hr-wrap .mrbets850-card.cashed .stat .val.na {
    color: #bbf7d0;
}
.mrbets850-hr-wrap .mrbets850-card.cashed .note {
    background: rgba(255,255,255,0.10);
    border-left-color: #facc15;
}
.mrbets850-hr-wrap .mrbets850-card .cashed-badge {
    position: absolute; top: 7px; right: 7px;
    padding: 2px 7px; border-radius: 999px;
    font-size: 0.62rem; font-weight: 900; letter-spacing: 0.04em;
    background: linear-gradient(135deg, #ffe042, #facc15, #f59e0b);
    color: #052e1a;
    border: 1px solid rgba(5,46,26,.40);
    box-shadow: 0 2px 8px rgba(0,0,0,.45);
    text-transform: uppercase;
    line-height: 1.2;
}
/* HR-hits summary chip in the header. */
.mrbets850-hr-wrap .mrbets850-hr-meta .hits-chip {
    display: inline-block; margin-top: 4px;
    padding: 2px 9px; border-radius: 999px;
    background: rgba(34,197,94,0.18); color: #86efac;
    font-size: 0.72rem; font-weight: 800; letter-spacing: 0.04em;
    border: 1px solid rgba(134,239,172,0.35);
}
.mrbets850-hr-wrap .mrbets850-empty {
    padding: 20px; border-radius: 16px;
    border: 1px dashed var(--mrb-empty-border);
    background: var(--mrb-empty-bg);
    color: var(--mrb-empty-text);
    text-align: center; font-weight: 700;
}

/* ---- Editor surfaces (visible only when developer is unlocked) ----
   Selectors here intentionally DO NOT require `.mrbets850-hr-wrap` as
   an ancestor. The editor renders via interactive Streamlit widgets
   that can't be wedged into a single HTML payload, so the wrapper
   div isn't around them. Standalone selectors + inlined light/dark
   colors keep the editor styled correctly regardless of DOM nesting. */
.mrbets850-editor-panel {
    margin-top: 16px; padding: 14px 16px;
    border-radius: 16px;
    background: linear-gradient(180deg, #faf8ff 0%, #f3eeff 100%);
    border: 1.5px solid rgba(124,58,237,0.25);
    color: #1a0b3a;
}
.mrbets850-editor-title {
    color: #7c3aed; font-weight: 900; font-size: 1.05rem;
    letter-spacing: 0.01em; margin: 0 0 4px 0;
}
.mrbets850-editor-sub {
    color: #4c1d95; font-weight: 600; font-size: 0.85rem;
}
.mrbets850-row-team {
    color: #4c1d95; font-size: 0.85rem; font-weight: 700;
    letter-spacing: 0.04em;
}
@media (prefers-color-scheme: dark) {
    .mrbets850-editor-panel {
        background: linear-gradient(180deg, #14062e 0%, #1f0c44 100%);
        border-color: rgba(250,204,21,0.35);
        color: #fafafa;
    }
    .mrbets850-editor-title { color: #facc15; }
    .mrbets850-editor-sub,
    .mrbets850-row-team { color: #fde68a; }
}

/* Mobile portrait — keep 2 columns of compact tiles so 25 picks aren't a
   long vertical scroll. Below ~360px we fall back to 1 column so chip
   labels still fit without truncation. */
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
    .mrbets850-hr-wrap .mrbets850-card-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
    }
    .mrbets850-hr-wrap .mrbets850-card { padding: 8px; border-radius: 12px; }
    .mrbets850-hr-wrap .mrbets850-card .head img.headshot,
    .mrbets850-hr-wrap .mrbets850-card .head .headshot {
        width: 32px; height: 32px; flex-basis: 32px;
    }
    .mrbets850-hr-wrap .mrbets850-card .head .name { font-size: 0.82rem; }
    .mrbets850-hr-wrap .mrbets850-card .stat { padding: 3px 5px; }
    .mrbets850-hr-wrap .mrbets850-card .stat .lbl { font-size: 0.55rem; }
    .mrbets850-hr-wrap .mrbets850-card .stat .val { font-size: 0.72rem; }
}
@media (max-width: 360px) {
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


def _build_header_html(last_updated: str | None, count: int,
                       hr_hits: int | None = None,
                       hr_eligible: int | None = None) -> str:
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
    hits_chip = ""
    if hr_hits is not None and hr_eligible is not None and hr_eligible > 0:
        hits_chip = (
            f'<div class="hits-chip">💣 HR hits: {hr_hits}/{hr_eligible}</div>'
        )
    return (
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
        + hits_chip
        + '</div>'
        '</div>'
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


def build_cashed_badge(hit_info: dict[str, Any] | None) -> str:
    """Return the HTML for the corner ✅/💣 badge, or '' when no HR yet."""
    if not hit_info:
        return ""
    n = int(hit_info.get("count") or 1)
    label = "💣 CASHED HR" if n == 1 else f"💣 CASHED ×{n}"
    return f'<div class="cashed-badge">{label}</div>'


def _render_card(pick: dict[str, Any], batters_df: pd.DataFrame | None,
                 hit_info: dict[str, Any] | None = None) -> str:
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

    cashed_class = " cashed" if hit_info else ""
    cashed_badge_html = build_cashed_badge(hit_info)
    return (
        f'<div class="mrbets850-card{cashed_class}">'
        + cashed_badge_html
        + '<div class="head">'
        f'<div class="rank-pill">#{rank}</div>'
        + head_html
        + '<div class="id">'
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


def _build_public_cards_html(picks: list[dict[str, Any]],
                             batters_df: pd.DataFrame | None,
                             hr_hits_by_rank: dict[int, dict[str, Any]] | None = None,
                             ) -> str:
    if not picks:
        return (
            '<div class="mrbets850-empty">'
            "No homerun picks posted yet. Check back soon — MrBets850 posts the "
            "daily Top 25 once lineups settle."
            '</div>'
        )
    hr_hits_by_rank = hr_hits_by_rank or {}
    cards = [
        _render_card(p, batters_df,
                     hit_info=hr_hits_by_rank.get(int(p.get("rank") or 0)))
        for p in picks
    ]
    return '<div class="mrbets850-card-grid">' + "".join(cards) + '</div>'


def _render_public_block(
    last_updated: str | None,
    picks: list[dict[str, Any]],
    batters_df: pd.DataFrame | None,
    hr_hits_by_rank: dict[int, dict[str, Any]] | None = None,
    hr_status_msg: str = "",
) -> None:
    # Emit header + cards inside a single wrapper in ONE st.markdown call.
    # Splitting these across multiple calls lets Streamlit insert its own
    # element-container divs between them, which breaks descendant CSS
    # selectors like `.mrbets850-hr-wrap .mrbets850-card`. Keeping the
    # whole block in one markdown call guarantees the cards are real
    # descendants of the themed wrapper.
    hr_hits_by_rank = hr_hits_by_rank or {}
    hits_count = len(hr_hits_by_rank)
    header_html = _build_header_html(
        last_updated, count=len(picks),
        hr_hits=hits_count if hr_status_msg.startswith("ok") else None,
        hr_eligible=len(picks) if hr_status_msg.startswith("ok") else None,
    )
    cards_html = _build_public_cards_html(picks, batters_df, hr_hits_by_rank)
    st.markdown(
        '<div class="mrbets850-hr-wrap">'
        + header_html
        + cards_html
        + '</div>',
        unsafe_allow_html=True,
    )
    if hr_status_msg and not hr_status_msg.startswith("ok"):
        st.caption(hr_status_msg)


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


def _render_persistence_status(state: dict[str, Any]) -> None:
    """Surface the persistence backend status so the developer can see at a
    glance whether picks survive a redeploy. Also exposes a one-click
    migration when the remote is empty but a local cache exists."""
    cfg = _remote_config()
    if _remote_enabled(cfg):
        st.success(
            f"💾 Permanent storage: **connected** — GitHub `{cfg['repo']}` "
            f"@ `{cfg['path']}` (branch `{cfg['branch']}`). "
            "Picks survive redeploys."
        )
        remote_state, _, msg = fetch_remote_picks(cfg)
        remote_picks = (remote_state or {}).get("picks") or []
        local_state = _read_local_picks_file()
        local_picks = local_state.get("picks") or []
        if not remote_picks and local_picks:
            st.warning(
                f"Remote board is empty ({msg}) but the local cache has "
                f"{len(local_picks)} pick(s). Migrate now so they survive "
                "the next redeploy."
            )
            if st.button("⬆️ Migrate local picks → GitHub",
                         key="_mrbets850_migrate_btn"):
                ok, new_sha, push_msg = push_remote_picks(
                    local_state, sha=None, cfg=cfg,
                    commit_message="chore(mrbets850): migrate local picks to remote",
                )
                if ok:
                    st.session_state["_mrbets850_remote_sha"] = new_sha
                    st.success(f"Migrated {len(local_picks)} pick(s) to GitHub.")
                    try:
                        st.rerun()
                    except Exception:
                        pass
                else:
                    st.error(push_msg)
    else:
        st.warning(
            "💾 Permanent storage: **local fallback only** — no GitHub token "
            "configured. Add `MRBETS850_GITHUB_TOKEN` (or `GITHUB_TOKEN`) to "
            "Streamlit secrets and optionally `MRBETS850_PICKS_REPO` / "
            "`MRBETS850_PICKS_PATH` / `MRBETS850_PICKS_BRANCH`. Until then, "
            "picks may be lost on redeploy."
        )


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
        cfg = _remote_config()
        if _remote_enabled(cfg):
            st.caption(
                "Clearing wipes today's board on GitHub "
                f"(`{cfg['repo']}` @ `{cfg['path']}`) AND the local cache."
            )
            st.checkbox(
                "Allow remote clear (lets save_picks push an empty board)",
                key="_mrbets850_allow_remote_clear",
                help="By default the remote save refuses to overwrite a "
                     "non-empty remote board with an empty list. Tick this "
                     "to override.",
            )
        else:
            st.caption(
                "Clearing wipes today's board. Picks are persisted at "
                f"`{PICKS_FILENAME}` next to app.py (local fallback only)."
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
    state = load_picks()
    picks = state.get("picks", [])

    # HR-hit activation. Wrap in try/except so any tracker hiccup cannot
    # blank the page — cards still render with the normal styling.
    hr_hits_by_rank: dict[int, dict[str, Any]] = {}
    hr_status_msg = ""
    try:
        events = fetch_todays_hr_events()
        if events:
            hr_hits_by_rank = compute_hr_hits(picks, events)
            hr_status_msg = f"ok · {len(events)} HRs scanned"
        else:
            hr_status_msg = (
                "Live HR tracker returned no events yet — picks render as "
                "normal; the HR-hit highlight will turn on as homers land."
            )
    except Exception as e:
        hr_status_msg = f"Live HR tracker unavailable: {e}"

    # Public block: header + cards emitted as ONE HTML payload so the
    # `.mrbets850-hr-wrap` ancestor genuinely contains the cards in the
    # DOM. Splitting into multiple st.markdown calls lets Streamlit
    # insert its own container divs between siblings and breaks the
    # descendant CSS selectors that style the cards.
    _render_public_block(
        state.get("last_updated"), picks, batters_df,
        hr_hits_by_rank=hr_hits_by_rank, hr_status_msg=hr_status_msg,
    )

    _render_unlock_form()
    if _is_unlocked():
        _render_persistence_status(state)
        _render_editor(state, batters_df)
