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
    }
    keep = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name in wanted
    ]
    ns = {}
    import pandas as pd
    ns["pd"] = pd
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
