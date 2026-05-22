"""Regression tests for handedness labels on reusable player cards."""

from __future__ import annotations

import ast
import pathlib


def _load_card_helpers():
    src = pathlib.Path(__file__).resolve().parent.parent / "app.py"
    tree = ast.parse(src.read_text())
    wanted = {
        "_hand_code",
        "format_batter_stance",
        "format_pitcher_hand",
        "_mc_chip",
        "_df_mobile_cards_html",
        "render_matchup_player_card_html",
    }
    keep = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name in wanted
    ]
    ns = {}
    import pandas as pd
    ns["pd"] = pd
    ns["HEATMAP_THRESHOLDS"] = {}
    ns["score_tier"] = lambda score: ("strong", "Strong")
    ns["_heatmap_color_for"] = lambda col, value: (None, "")
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(src), "exec"), ns)
    return ns


def test_generic_player_card_sub_adds_batter_stance():
    ns = _load_card_helpers()
    import pandas as pd
    df = pd.DataFrame([
        {"#": 1, "Hitter": "Kyle Schwarber", "Team": "PHI", "Bat": "L", "Matchup": 123.4},
    ])
    html = ns["_df_mobile_cards_html"](
        df,
        name_col="Hitter",
        sub_col="Team",
        score_col="Matchup",
        rank_col="#",
        always_show=True,
    )
    assert "Kyle Schwarber" in html
    assert "Bats LHB" in html


def test_generic_player_card_sub_adds_pitcher_hand():
    ns = _load_card_helpers()
    import pandas as pd
    df = pd.DataFrame([
        {"#": 1, "Pitcher": "Framber Valdez", "Team": "HOU", "Throws": "L"},
    ])
    html = ns["_df_mobile_cards_html"](
        df,
        name_col="Pitcher",
        sub_col="Team",
        rank_col="#",
        always_show=True,
    )
    assert "Framber Valdez" in html
    assert "Throws LHP" in html


def test_matchup_scout_row_shows_batter_and_pitcher_hand_badges():
    ns = _load_card_helpers()
    html = ns["render_matchup_player_card_html"](
        {
            "Spot": 2,
            "Hitter": "Kyle Schwarber",
            "Team": "PHI",
            "_BatSide": "L",
            "_OppPitcherName": "Gerrit Cole",
            "_OppPitchHand": "R",
            "Matchup": 118.2,
            "Likely": "Strong",
        },
        [],
    )
    assert "Kyle Schwarber" in html
    assert "LHB" in html
    assert "vs RHP" in html
    assert "Gerrit Cole (RHP)" in html
