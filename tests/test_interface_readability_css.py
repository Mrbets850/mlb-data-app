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
    assert "BLACK_YELLOW_READABILITY_CSS" in text
    for selector in (
        ".top-tab-row",
        ".top-tab-pill",
        ".top-tab-pill.active",
        "[data-testid=\"stMarkdownContainer\"] a",
    ):
        assert selector in text
    assert ".top-tab-pill" in text and "color: #111111 !important" in text


def test_brand_theme_covers_streamlit_metrics():
    text = _app_text()
    assert "[data-testid=\"stMetric\"]" in text
    assert "[data-testid=\"stMetricValue\"]" in text
    assert "color: #000000 !important" in text


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
    assert "White/yellow/black Scout Report makeover" in text
    assert "div[data-testid=\"stDialog\"] > div > div" in text
    assert ".pdc-hand-pill.pitch" in text


def test_black_yellow_theme_explicitly_overrides_old_purple_sources():
    text = _app_text()
    assert "[style*=\"background:#3b1f6b\"]" in text
    assert "[style*=\"color:#7c3aed\"]" in text
    assert "--edge-purple: #111111;" in text
    assert "--edge-purple-soft: #fff3b0;" in text


def test_black_yellow_theme_prevents_washed_out_text():
    text = _app_text()
    assert "opacity: 1 !important" in text
    assert "-webkit-text-fill-color: currentColor !important" in text
    assert "[style*=\"opacity:0.\"]" in text
    assert ".pdc-hrdue-score .num" in text
