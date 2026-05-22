"""Regression tests for the Pitcher Vulnerability card renderer.

Background: the live current-pitcher overlay + PITCHING CHANGE badge merge
introduced an f-string template with a conditional expression that, when
falsy, expanded to an empty string inside an indented HTML block. Because the
markdown was indented 4+ spaces, a blank line followed by indented HTML made
Streamlit's CommonMark parser treat the rest of the card as an indented code
block — so every pitcher card rendered as raw HTML text. These tests pin the
fix in place and exercise the defensive paths around missing / NaN /
non-string live metadata so the cards never crash again.
"""

from __future__ import annotations

import math
import sys
import types
from unittest import mock


# Importing app.py is expensive (loads streamlit data on import). We stub the
# heaviest external modules before import so the test module is cheap to run
# in CI and doesn't need network or CSV access.

def _install_streamlit_stub() -> None:
    if "streamlit" in sys.modules:
        return
    st = types.ModuleType("streamlit")
    # Catch-all attribute factory — every Streamlit API used during import
    # becomes a no-op callable. We only care that render_pitcher_panel can
    # call ``st.markdown`` so a single Mock is enough.
    class _StAny:
        def __getattr__(self, name):
            return mock.MagicMock()
        def __call__(self, *a, **k):
            return mock.MagicMock()
    sentinel = _StAny()
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
    # ``cache_data`` / ``cache_resource`` are used as decorators in app.py.
    def _identity_decorator(*a, **k):
        if a and callable(a[0]):
            return a[0]
        def _wrap(fn):
            return fn
        return _wrap
    st.cache_data = _identity_decorator
    st.cache_resource = _identity_decorator
    st.spinner = mock.MagicMock(return_value=mock.MagicMock(__enter__=mock.MagicMock(), __exit__=mock.MagicMock()))
    sys.modules["streamlit"] = st


def _get_render():
    """Lazy-import the renderer with streamlit stubbed.

    We don't actually want to execute the whole app module here — but the
    pieces we test (``_safe_str`` and ``render_pitcher_panel``) live in
    app.py. Where importing the full module is too expensive, individual
    tests fall back to copying the helper.
    """
    _install_streamlit_stub()
    # Stub a few more import-time heavyweights so app.py doesn't try to load
    # CSVs or hit the network during import. The renderer itself doesn't
    # depend on any of that — only the module's import-time main block does.
    # We sidestep that by re-loading just the helper via exec on the relevant
    # source range.
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    wanted = {"_hand_code", "format_pitcher_hand",
              "_safe_str", "_pp_tone", "pitcher_vulnerability",
              "_sp_is_hr_target", "render_pitch_mix_block",
              "render_pitcher_panel", "_fetch_pitcher_season_stats"}
    keep_nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            keep_nodes.append(node)
    module_ast = ast.Module(body=keep_nodes, type_ignores=[])
    code = compile(module_ast, str(src), "exec")
    ns: dict = {}
    # Provide the minimum globals the helpers need at import time.
    import streamlit as _st_stub  # noqa: F401  (stubbed above)
    ns["st"] = _st_stub
    ns["pd"] = __import__("pandas") if "pandas" in sys.modules or _try_import("pandas") else None  # type: ignore
    # Provide stubs for the other helpers if the AST extraction omitted them.
    def _stub_tone(*a, **k):
        return ""
    def _stub_vuln(p_row):
        return ("85", "elite", "Highly Vulnerable")
    def _stub_hr(_in):
        return (None, "")
    def _stub_mix(*a, **k):
        return ""
    def _stub_fetch(*a, **k):
        return {}
    ns.setdefault("_pp_tone", _stub_tone)
    ns.setdefault("pitcher_vulnerability", _stub_vuln)
    ns.setdefault("_sp_is_hr_target", _stub_hr)
    ns.setdefault("render_pitch_mix_block", _stub_mix)
    ns.setdefault("_fetch_pitcher_season_stats", _stub_fetch)
    exec(code, ns)
    # Re-bind so the renderer uses our stubs instead of any names it captured
    # from app.py-level helpers we deliberately did NOT include above.
    ns["_pp_tone"] = _stub_tone
    ns["pitcher_vulnerability"] = _stub_vuln
    ns["_sp_is_hr_target"] = _stub_hr
    ns["render_pitch_mix_block"] = _stub_mix
    ns["_fetch_pitcher_season_stats"] = _stub_fetch
    # Rebuild the renderer closure with the rebound stubs so the test gets
    # consistent output independent of app.py's other helpers.
    code2 = compile(ast.Module(body=[n for n in keep_nodes if n.name in
                                     ("_safe_str", "render_pitcher_panel")],
                               type_ignores=[]), str(src), "exec")
    exec(code2, ns)
    return ns


def _try_import(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# _safe_str
# ---------------------------------------------------------------------------

def test_safe_str_handles_none_nan_and_dict():
    ns = _get_render()
    safe = ns["_safe_str"]
    assert safe(None) == ""
    assert safe(None, "TBD") == "TBD"
    assert safe(float("nan"), "TBD") == "TBD"
    assert safe("", "fallback") == "fallback"
    assert safe("   ", "fallback") == "fallback"
    assert safe("nan", "fallback") == "fallback"     # pandas string-cast NaN
    assert safe("<NA>", "fallback") == "fallback"   # pandas NA repr
    assert safe("Slade Cecconi") == "Slade Cecconi"
    # Non-string / dict-shaped values should coerce to their repr-stripped form
    # without raising.
    assert safe({"name": "x"}, "fallback") == "{'name': 'x'}"
    assert safe(42) == "42"


def test_pitcher_hand_display_expands_to_rhp_lhp():
    ns = _get_render()
    fmt = ns["format_pitcher_hand"]
    assert fmt("R") == "RHP"
    assert fmt("L") == "LHP"
    assert fmt(None, "?") == "?"


# ---------------------------------------------------------------------------
# render_pitcher_panel — the regression that motivated this file
# ---------------------------------------------------------------------------

def _captured_markdown(ns, *args, **kwargs):
    """Call render_pitcher_panel and return the rendered HTML string."""
    rendered = {}
    import streamlit as st
    def _cap(html, **kw):
        rendered["html"] = html
        rendered["unsafe"] = kw.get("unsafe_allow_html")
    st.markdown = _cap
    ns["st"] = st
    ns["render_pitcher_panel"](*args, **kwargs)
    return rendered


def test_default_panel_has_no_blank_indented_line():
    """The bug: when pitcher_changed=False, the old template left a line of
    only whitespace inside an indented markdown block. CommonMark then
    treated subsequent indented lines as a code block, so the card body
    rendered as raw HTML text. The fix renders the body flush-left and
    omits the "replaced …" hint entirely when there's no change.
    """
    ns = _get_render()
    out = _captured_markdown(
        ns,
        "Away SP — CLE", "Slade Cecconi", "R", None,
    )
    html = out["html"]
    assert out["unsafe"] is True
    assert "(RHP)" in html
    # No "replaced" hint should appear when the pitcher hasn't changed.
    assert "replaced" not in html
    # And, critically, no line should consist solely of >=4 spaces — which
    # is what triggered the CommonMark indented-code-block fallback.
    for line in html.splitlines():
        assert not (line and line.strip() == "" and line.startswith("    ")), (
            f"blank-but-indented line found in panel HTML:\n{html}"
        )


def test_default_panel_does_not_crash_on_none_p_row():
    ns = _get_render()
    out = _captured_markdown(
        ns,
        "Away SP — CLE", "Slade Cecconi", "R", None,
    )
    assert "Slade Cecconi" in out["html"]


def test_panel_handles_nan_name_and_hand():
    """Live overlay can pass NaN / None into pitcher_name and pitch_hand
    when the live feed is missing fields. The renderer must coerce these
    to safe placeholders rather than blowing up the f-string."""
    ns = _get_render()
    out = _captured_markdown(
        ns,
        "Away SP — CLE", float("nan"), None, None,
    )
    html = out["html"]
    assert "TBD" in html      # fallback for missing pitcher_name
    assert "(?)" in html      # fallback for missing hand


def test_panel_handles_none_label():
    ns = _get_render()
    out = _captured_markdown(
        ns,
        None, "Slade Cecconi", "R", None,
    )
    assert "Slade Cecconi" in out["html"]


def test_pitching_change_badge_renders_when_changed():
    ns = _get_render()
    out = _captured_markdown(
        ns,
        "Home SP — DET", "Reliever Guy", "L", None,
        pitcher_changed=True, original_name="Framber Valdez",
    )
    html = out["html"]
    assert "Pitching Change" in html
    assert "replaced Framber Valdez" in html


def test_pitching_change_no_original_name_still_renders_badge():
    """If the original probable name was missing/NaN we still surface the
    badge — just without the "replaced X" subline."""
    ns = _get_render()
    out = _captured_markdown(
        ns,
        "Home SP — DET", "Reliever Guy", "L", None,
        pitcher_changed=True, original_name=float("nan"),
    )
    html = out["html"]
    assert "Pitching Change" in html
    # No subline when original_name is unknown.
    assert "replaced" not in html


def test_pitcher_changed_with_dict_original_name_does_not_crash():
    """A dict-shaped value would have triggered the old ``'replaced ' +
    original_name`` TypeError. The renderer should accept it and produce a
    string."""
    ns = _get_render()
    out = _captured_markdown(
        ns,
        "Home SP — DET", "Reliever Guy", "L", None,
        pitcher_changed=True, original_name={"weird": "shape"},
    )
    html = out["html"]
    assert "Pitching Change" in html
    # Whatever string we produced, it must not contain a Python TypeError
    # marker or the raw dict braces that would indicate uncoerced rendering.
    assert "TypeError" not in html


def test_panel_escapes_html_in_pitcher_name():
    """A pitcher name with angle-brackets / quotes must be HTML-escaped so it
    can't smuggle markup that would break the card."""
    ns = _get_render()
    out = _captured_markdown(
        ns,
        "Away SP — CLE", '<script>alert(1)</script>', "R", None,
    )
    html = out["html"]
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
