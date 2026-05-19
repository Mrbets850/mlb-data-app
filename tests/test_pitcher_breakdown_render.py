"""Regression tests for the Pitcher Breakdown card renderers.

These tests load only the new render helpers via AST extraction so the
suite is cheap to run and doesn't need network access or the heavy
import-time data loads in ``app.py``.

They verify:
  * The KPI header block produces non-empty HTML for a minimal row.
  * Each tab renderer renders a graceful fallback (no raw ``<div>`` blanks,
    no exceptions) when its data source is empty.
  * The projection helper picks reasonable defaults when only season-rate
    stats are available.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import types
from unittest import mock


def _install_streamlit_stub() -> None:
    if "streamlit" in sys.modules:
        return
    st = types.ModuleType("streamlit")

    class _StAny:
        def __getattr__(self, name):
            return mock.MagicMock()
        def __call__(self, *a, **k):
            return mock.MagicMock()

    for name in (
        "set_page_config", "markdown", "caption", "warning", "info",
        "error", "spinner", "columns", "container", "expander",
        "cache_data", "cache_resource", "session_state", "sidebar",
        "title", "header", "subheader", "write", "image", "dataframe",
        "table", "metric", "button", "selectbox", "multiselect",
        "radio", "checkbox", "text_input", "number_input", "date_input",
        "tabs", "empty", "stop", "secrets", "query_params", "rerun",
        "experimental_rerun", "toast", "divider", "code",
    ):
        setattr(st, name, mock.MagicMock())

    def _identity_decorator(*a, **k):
        if a and callable(a[0]):
            return a[0]
        def _wrap(fn):
            return fn
        return _wrap

    st.cache_data = _identity_decorator
    st.cache_resource = _identity_decorator
    sys.modules["streamlit"] = st


def _load_helpers():
    """Extract just the Pitcher Breakdown helpers from app.py via AST."""
    _install_streamlit_stub()
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    wanted = {
        "_safe_str",
        "_fmt_or_dash",
        "_kpi_tile_html",
        "_project_pitcher_targets",
        "render_pitcher_breakdown_header",
        "render_pitcher_breakdown_kpis",
        "render_pitcher_breakdown_arsenal",
        "render_pitcher_breakdown_game_log",
        "render_pitcher_breakdown_splits",
        "render_pitcher_breakdown_styles",
        "_compute_opp_k_rank",
    }
    keep = [n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name in wanted]
    code = compile(ast.Module(body=keep, type_ignores=[]),
                   str(src), "exec")
    ns: dict = {}
    import pandas as pd
    ns["pd"] = pd
    ns["player_headshot_url"] = lambda pid: ""
    ns["logo_url"] = lambda *a, **k: ""
    ns["PITCH_NAME_MAP"] = {"FF": "Four-seam"}
    ns["PITCH_EMOJI"] = {"FF": "🔥"}
    exec(code, ns)
    return ns


# ---------------------------------------------------------------------------
# Header / KPI block
# ---------------------------------------------------------------------------

def test_header_renders_pill_title_and_identity():
    ns = _load_helpers()
    row = {
        "Pitcher": "Spencer Strider",
        "Team": "ATL", "Opp": "NYM", "Loc": "@",
        "Throws": "R", "_player_id": 675911,
    }
    html = ns["render_pitcher_breakdown_header"](row, "#1 Projected Ks")
    assert "LIVE PREVIEW" in html
    assert "Pitcher Breakdown" in html
    assert "Spencer Strider" in html
    assert "ATL" in html and "NYM" in html
    assert "#1 Projected Ks" in html


def test_kpi_block_renders_six_tiles_with_dashes_when_missing():
    ns = _load_helpers()
    # All-missing row should still render the six KPI tiles with em-dash values.
    row = {"Pitcher": "TBD"}
    html = ns["render_pitcher_breakdown_kpis"](
        row, {"PROJ_K": None, "PROJ_IP": None, "PROJ_HR": None}, (None, None)
    )
    for label in ("PROJ K", "PROJ IP", "ERA", "HR ALLOW", "WHIP", "OPP K RK"):
        assert label in html
    # Em-dash is the graceful fallback for every missing value.
    assert html.count("—") >= 6


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def test_projection_falls_back_to_league_avg_ip_when_no_game_log():
    ns = _load_helpers()
    row = {"K/9": 10.0, "HR/9": 1.0, "IP": 60.0}
    proj = ns["_project_pitcher_targets"](row, None)
    # League-avg starter is ~5.5 IP — projection should pick it up and yield
    # sensible PROJ K / PROJ HR.
    assert proj["PROJ_IP"] is not None
    assert 3.0 <= proj["PROJ_IP"] <= 7.5
    assert proj["PROJ_K"] is not None and proj["PROJ_K"] > 0
    assert proj["PROJ_HR"] is not None and proj["PROJ_HR"] > 0


def test_projection_handles_empty_row():
    ns = _load_helpers()
    proj = ns["_project_pitcher_targets"]({}, None)
    # An empty row means TBD — every projection is unavailable.
    assert proj["PROJ_IP"] is None
    assert proj["PROJ_K"] is None
    assert proj["PROJ_HR"] is None


def test_projection_with_only_ip_uses_league_floor_for_k_hr():
    ns = _load_helpers()
    # Row has rate stats but no game log: should still produce a projection.
    proj = ns["_project_pitcher_targets"](
        {"K/9": 9.0, "HR/9": 1.0}, None
    )
    assert proj["PROJ_IP"] is not None
    assert proj["PROJ_K"] is not None and proj["PROJ_K"] > 0


# ---------------------------------------------------------------------------
# Tab renderers — graceful fallbacks
# ---------------------------------------------------------------------------

def test_arsenal_empty_renders_friendly_fallback():
    ns = _load_helpers()
    import pandas as pd
    html = ns["render_pitcher_breakdown_arsenal"](pd.DataFrame())
    assert "pbd-empty" in html
    # Must not silently produce zero output.
    assert len(html) > 50


def test_game_log_empty_renders_friendly_fallback():
    ns = _load_helpers()
    import pandas as pd
    html = ns["render_pitcher_breakdown_game_log"](pd.DataFrame())
    assert "pbd-empty" in html
    assert "game log" in html.lower()


def test_splits_empty_renders_friendly_fallback():
    ns = _load_helpers()
    html = ns["render_pitcher_breakdown_splits"]({})
    assert "pbd-empty" in html


def test_game_log_renders_recent_starts():
    ns = _load_helpers()
    import pandas as pd
    df = pd.DataFrame([
        {"date": pd.to_datetime("2026-05-10").date(), "opp": "BOS",
         "ip": 6.0, "h": 4, "r": 2, "er": 2, "bb": 1, "k": 9, "hr": 0,
         "pitches": 92, "era_game": 3.0},
        {"date": pd.to_datetime("2026-05-15").date(), "opp": "TOR",
         "ip": 7.0, "h": 5, "r": 1, "er": 1, "bb": 2, "k": 11, "hr": 1,
         "pitches": 101, "era_game": 1.29},
    ])
    html = ns["render_pitcher_breakdown_game_log"](df, n=5)
    assert "BOS" in html and "TOR" in html
    assert "pbd-glog-row" in html


def test_splits_renders_present_buckets():
    ns = _load_helpers()
    splits = {
        "vsR": {"pa": 100, "avg": 0.220, "obp": 0.300, "slg": 0.380,
                "ops": 0.680, "k": 30, "bb": 8, "hr": 2, "ip": 25.0},
        "vsL": {},
        "home": {"pa": 80, "avg": 0.240, "obp": 0.310, "slg": 0.400,
                 "ops": 0.710, "k": 22, "bb": 6, "hr": 3, "ip": 20.0},
        "away": {},
    }
    html = ns["render_pitcher_breakdown_splits"](splits)
    assert "vs RHB" in html and "vs LHB" in html
    assert "Home" in html and "Away" in html
    # Present bucket numbers should appear in the output.
    assert ".220" in html or "0.220" in html


# ---------------------------------------------------------------------------
# Styles block
# ---------------------------------------------------------------------------

def test_styles_block_exposes_required_class_names():
    ns = _load_helpers()
    css = ns["render_pitcher_breakdown_styles"]()
    for cls in (
        "pbd-card", "pbd-pill", "pbd-title", "pbd-kpi-grid",
        "pbd-arsenal", "pbd-glog", "pbd-lineup", "pbd-splits",
    ):
        assert cls in css


def test_fmt_or_dash_handles_nan_and_none():
    ns = _load_helpers()
    import math
    assert ns["_fmt_or_dash"](None) == "—"
    assert ns["_fmt_or_dash"](float("nan")) == "—"
    assert ns["_fmt_or_dash"](1.234, "{:.2f}") == "1.23"


def test_opp_k_rank_returns_none_when_no_team_table_loaded():
    ns = _load_helpers()
    out = ns["_compute_opp_k_rank"](None, "NYY", None)
    assert out == (None, None)
