"""Regression tests for the dark game-header card renderer.

Background: the live + final box-score merge added `{box_html}` as a
conditional interpolation inside an indented multi-line f-string. For
pregame games `box_html` is empty, which collapsed that source line to
whitespace-only. After ``st.markdown`` runs ``textwrap.dedent + strip``
on the body, the whitespace-only line becomes a true blank line that
closes the CommonMark HTML block. The subsequent indented `<div>` for
the weather card and KPI row was then rendered as a literal indented
code block — visible to the user as raw ``<div style="background:...``
text inside the card.

These tests pin the fix in place by inspecting the rendered HTML and
asserting there are no blank-but-indented lines that would re-trigger
the bug.
"""

from __future__ import annotations

import sys
import types
from unittest import mock


def _install_streamlit_stub() -> None:
    if "streamlit" in sys.modules:
        return
    st = types.ModuleType("streamlit")
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
    st.spinner = mock.MagicMock(
        return_value=mock.MagicMock(__enter__=mock.MagicMock(),
                                    __exit__=mock.MagicMock()))
    sys.modules["streamlit"] = st


def _load_render_game_header():
    """Extract render_game_header from app.py without executing the module."""
    _install_streamlit_stub()
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    wanted = {"_hand_code", "format_pitcher_hand", "render_game_header"}
    keep_nodes = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name in wanted
    ]
    code = compile(ast.Module(body=keep_nodes, type_ignores=[]),
                   str(src), "exec")
    ns: dict = {}
    import streamlit as _st_stub  # noqa: F401
    ns["st"] = _st_stub
    # Stub every external helper render_game_header references.
    ns["logo_url"] = lambda _id: "https://example.test/logo.png"
    ns["get_park_history_totals"] = lambda *a, **k: []
    ns["get_odds_totals_map"] = lambda: {}
    ns["summarize_park_ou"] = lambda *a, **k: {"n": 0}
    ns["render_weather_impact_card"] = lambda *a, **k: (
        '<div style="background:#ffffff; border:1px solid #e2e8f0; '
        'border-radius:14px;">weather</div>'
    )
    ns["_live_state_snapshot"] = lambda _pk: None
    ns["_render_box_score_html"] = lambda *a, **k: ""
    exec(code, ns)
    return ns


def _capture(ns, game_row, ctx, weather):
    out = {}
    import streamlit as st
    def _cap(html, **kw):
        out["html"] = html
        out["unsafe"] = kw.get("unsafe_allow_html")
    st.markdown = _cap
    ns["st"] = st
    ns["render_game_header"](game_row, ctx, weather)
    return out


def _base_row(status="Preview"):
    return {
        "away_id": 144, "home_id": 146,
        "away_abbr": "ATL", "home_abbr": "MIA",
        "game_time_ct": "6:40 PM", "venue": "loanDepot park",
        "status": status,
        "away_probable": "Spencer Strider",
        "home_probable": "Sandy Alcantara",
        "park_factor": 99.0,
        "venue_id": 0,
        "game_pk": 0,
    }


def _base_ctx():
    return {
        "away_status": "Confirmed", "home_status": "Confirmed",
        "away_pitch_hand": "R", "home_pitch_hand": "R",
    }


# ---------------------------------------------------------------------------
# Regression: no blank-but-indented lines anywhere in the rendered card.
# ---------------------------------------------------------------------------

def test_pregame_card_has_no_blank_indented_lines():
    """The exact bug: when no live box-score exists, the empty interpolation
    must not collapse to a blank line that closes the CommonMark HTML block.
    """
    ns = _load_render_game_header()
    out = _capture(ns, _base_row(), _base_ctx(), {})
    html = out["html"]
    assert out["unsafe"] is True
    assert "(RHP)" in html
    # The outer div must be at column 0 — anything else risks
    # an indented-code-block fallback.
    assert html.startswith('<div class="section-card dark">'), html[:80]
    # No line in the body should be blank, period — and certainly not blank
    # AND indented (the literal repro for the bug).
    for i, line in enumerate(html.splitlines()):
        assert line.strip() != "" or not line.startswith(" "), (
            f"blank-indented line at {i}:\n{html}"
        )


def test_card_contains_weather_html_inline():
    """The weather card must appear inline (not split by a newline that
    would let CommonMark see it as a new block)."""
    ns = _load_render_game_header()
    out = _capture(ns, _base_row(), _base_ctx(), {})
    html = out["html"]
    # The stubbed weather html is verbatim in the output, NOT on its own
    # indented line.
    assert 'weather</div>' in html
    # Specifically: there should be no newline immediately before the
    # weather card (that would re-enable the bug).
    assert "\n" not in html or all(
        not line.lstrip().startswith('<div style="background:#ffffff;')
        or not line.startswith(" ")
        for line in html.splitlines()
    )


def test_live_game_with_box_html_still_clean():
    """When the game IS live, box_html is non-empty. The output should still
    be a single flat HTML string with no blank-indented lines."""
    ns = _load_render_game_header()
    ns["_render_box_score_html"] = lambda *a, **k: (
        '<div class="gh-scorebox"><div class="status-row">'
        '<span class="status-pill live">Live</span></div>'
        '<div class="scrollwrap"><table><thead><tr><th>Team</th>'
        '<th>1</th><th>R</th></tr></thead>'
        '<tbody><tr><td>ATL</td><td>0</td><td>0</td></tr></tbody>'
        '</table></div></div>'
    )
    out = _capture(ns, _base_row(status="In Progress"), _base_ctx(), {})
    html = out["html"]
    assert "gh-scorebox" in html
    assert 'class="status-pill live"' in html
    for line in html.splitlines():
        assert line.strip() != "" or not line.startswith(" "), html


def test_card_renders_probable_pitchers():
    """Scheduled-game display: probables must still appear with handedness."""
    ns = _load_render_game_header()
    out = _capture(ns, _base_row(), _base_ctx(), {})
    html = out["html"]
    assert "Spencer Strider" in html
    assert "Sandy Alcantara" in html
    assert ">(R)<" in html  # handedness rendered, not the raw {ctx[...]} key


def test_card_uses_team_abbrs_and_logos():
    ns = _load_render_game_header()
    out = _capture(ns, _base_row(), _base_ctx(), {})
    html = out["html"]
    assert "ATL" in html and "MIA" in html
    assert "https://example.test/logo.png" in html


def test_status_string_is_rendered_in_meta():
    ns = _load_render_game_header()
    out = _capture(ns, _base_row(status="Final"), _base_ctx(), {})
    assert "Final" in out["html"]
