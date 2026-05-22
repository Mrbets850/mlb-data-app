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
    assert "VIBRANT MRBETS850 BRAND THEME" in text
    for selector in (
        ".top-tab-row",
        ".top-tab-pill",
        ".top-tab-pill.active",
        "[data-testid=\"stMarkdownContainer\"] a",
    ):
        assert selector in text
    assert ".top-tab-pill" in text and "color: var(--edge-purple) !important" in text


def test_brand_theme_covers_streamlit_metrics():
    text = _app_text()
    assert "[data-testid=\"stMetric\"]" in text
    assert "[data-testid=\"stMetricValue\"]" in text
    assert "color: var(--edge-purple) !important" in text


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
    assert "White/purple/gold Scout Report makeover" in text
    assert "div[data-testid=\"stDialog\"] > div > div" in text
    assert ".pdc-hand-pill.pitch" in text
