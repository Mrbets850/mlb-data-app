"""Static audit for app-wide readability/theme coverage.

The UI has several independently-rendered card systems. These tests make sure
the final brand/readability override covers the surfaces that previously
regressed to dark backgrounds or faint text.
"""

from __future__ import annotations

import pathlib


APP = pathlib.Path(__file__).resolve().parent.parent / "app.py"


def _app_text() -> str:
    return APP.read_text()


def test_brand_theme_covers_apps_generator_tiles_and_links():
    text = _app_text()
    assert "HIGH-CONTRAST MRBETS850 BRAND THEME" in text
    assert "STEELERS_THEME_CSS" in text
    for selector in (
        ".top-tab-row",
        ".top-tab-pill",
        ".top-tab-pill.active",
        "[data-testid=\"stMarkdownContainer\"] a",
    ):
        assert selector in text
    assert ".top-tab-pill" in text and "color: var(--steelers-text) !important" in text


def test_brand_theme_covers_streamlit_metrics():
    text = _app_text()
    assert "[data-testid=\"stMetric\"]" in text
    assert "[data-testid=\"stMetricValue\"]" in text
    assert "color: var(--steelers-text) !important" in text


def test_brand_theme_covers_all_major_card_families():
    text = _app_text()
    for cls in (
        ".mc-card",      # Apps & Generators shared cards
        ".rbi-card",     # RBI Edge module cards
        ".spd-card",     # Slate Pitcher dashboard
        ".spc-card",     # Game matchup pitcher mini-cards
        ".pbd-card",     # Pitcher Breakdown
        ".aip-card",     # AI parlay cards
        ".rr-card",      # Round Robin cards
        ".pws-card",     # Pitcher Weak Spots
        ".pdc-card",     # Scout Report modal
        ".scout-row",    # Main Games matchup cards
    ):
        assert cls in text


def test_scout_report_modal_has_light_theme_override():
    text = _app_text()
    assert "Steelers black/gold Scout Report makeover" in text
    assert "div[data-testid=\"stDialog\"] > div > div" in text
    assert ".pdc-hand-pill.pitch" in text


def test_steelers_theme_removes_old_royal_color_sources():
    text = _app_text()
    forbidden = (
        "#3b1f6b", "#5b21b6", "#5b21a8", "#1e0b4a", "#14062e",
        "#0c0420", "#1a0840", "#15102b", "#1c1340", "#2a1e4a",
        "#130b38", "#0d0928", "#1a0f42", "#7c3aed", "#a78bfa",
        "#8b5cf6", "#c4b5fd", "#ddd6fe", "#6b21a8", "#5b3aa0",
        "#4c1d95",
    )
    lowered = text.lower()
    assert "purple" not in lowered
    assert "violet" not in lowered
    assert "royal" not in lowered
    for value in forbidden:
        assert value not in lowered
    assert "--steelers-black: #000000;" in text
    assert "--steelers-gold: #FFB612;" in text


def test_steelers_theme_prevents_washed_out_text():
    text = _app_text()
    assert "opacity: 1 !important" in text
    assert "-webkit-text-fill-color: currentColor !important" in text
    assert "[style*=\"opacity:0.\"]" in text
    assert ".pdc-hrdue-score .num" in text
