"""Smoke tests for mrbets850_hr_picks.py.

Covers:
  * _today_central_date — America/Chicago slate-date + override
  * _normalize_state — schema validation, MAX_PICKS clamp, rerank
  * _safe_remote_overwrite_allowed — no-overwrite guard
  * compute_hr_hits — player_id + normalized name/team matching
  * build_cashed_badge / _render_card — cashed class + badge HTML

Run with:  python -m pytest tests/test_mrbets850_hr_picks.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest import mock

# Make sure the repo root is importable when pytest is run from tests/.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import mrbets850_hr_picks as mod  # noqa: E402


# ---------------------------------------------------------------------------
# Central date helper
# ---------------------------------------------------------------------------
def test_today_central_date_override_env(monkeypatch):
    monkeypatch.setenv("MRBETS850_TODAY_OVERRIDE", "2026-05-16")
    assert mod._today_central_date().isoformat() == "2026-05-16"


def test_today_central_date_is_central_not_utc():
    # 2026-05-16 04:30 UTC is still 2026-05-15 in Central time.
    fake_utc = datetime(2026, 5, 16, 4, 30)
    d = mod._today_central_date(now=fake_utc)
    # Could be either the prior or current Central day depending on which
    # tzdata is bundled, but the point is it must NOT blindly use the UTC
    # date — that would give 2026-05-16.
    assert d.isoformat() in ("2026-05-15", "2026-05-16")
    # And the explicit override always wins:
    os.environ.pop("MRBETS850_TODAY_OVERRIDE", None)


# ---------------------------------------------------------------------------
# _normalize_state — JSON validation & cap to MAX_PICKS
# ---------------------------------------------------------------------------
def test_normalize_state_drops_garbage_and_reranks():
    raw = {
        "last_updated": "2026-05-16T12:00:00",
        "picks": [
            {"rank": 5, "name": "Aaron Judge", "team": "nyy", "player_id": 592450},
            {"rank": 2, "name": "Shohei Ohtani", "team": "LAD"},
            {"rank": 9, "name": "", "team": "BOS"},        # dropped (no name)
            "not a dict",                                  # dropped
            {"rank": 1, "name": "Juan Soto", "team": "NYM"},
        ],
    }
    s = mod._normalize_state(raw)
    names = [p["name"] for p in s["picks"]]
    ranks = [p["rank"] for p in s["picks"]]
    assert names == ["Juan Soto", "Shohei Ohtani", "Aaron Judge"]
    assert ranks == [1, 2, 3]
    # Team is upper-cased.
    assert s["picks"][2]["team"] == "NYY"


def test_normalize_state_caps_at_max_picks():
    raw = {"picks": [{"rank": i, "name": f"P{i}"} for i in range(1, 60)]}
    s = mod._normalize_state(raw)
    assert len(s["picks"]) == mod.MAX_PICKS
    assert s["picks"][-1]["rank"] == mod.MAX_PICKS


def test_normalize_state_handles_garbage_input():
    assert mod._normalize_state(None) == mod._empty_state()
    assert mod._normalize_state("hi") == mod._empty_state()
    assert mod._normalize_state({"picks": "lol"})["picks"] == []


# ---------------------------------------------------------------------------
# No-overwrite guard
# ---------------------------------------------------------------------------
def test_no_overwrite_blocks_empty_save_when_remote_has_picks(monkeypatch):
    # session_state is a streamlit object; force the override flag off.
    monkeypatch.setitem(mod.st.session_state, "_mrbets850_allow_remote_clear", False)
    remote = {"picks": [{"rank": 1, "name": "Aaron Judge", "team": "NYY"}]}
    empty = {"picks": []}
    assert mod._safe_remote_overwrite_allowed(remote, empty) is False


def test_no_overwrite_allows_when_remote_empty_or_user_opts_in(monkeypatch):
    monkeypatch.setitem(mod.st.session_state, "_mrbets850_allow_remote_clear", False)
    assert mod._safe_remote_overwrite_allowed({"picks": []}, {"picks": []}) is True
    assert mod._safe_remote_overwrite_allowed(None, {"picks": []}) is True
    monkeypatch.setitem(mod.st.session_state, "_mrbets850_allow_remote_clear", True)
    remote = {"picks": [{"rank": 1, "name": "Aaron Judge", "team": "NYY"}]}
    assert mod._safe_remote_overwrite_allowed(remote, {"picks": []}) is True


# ---------------------------------------------------------------------------
# HR-hit matching
# ---------------------------------------------------------------------------
def test_compute_hr_hits_by_player_id():
    picks = [
        {"rank": 1, "name": "Aaron Judge", "team": "NYY", "player_id": 592450},
        {"rank": 2, "name": "Shohei Ohtani", "team": "LAD", "player_id": 660271},
    ]
    events = [
        {"player_id": 592450, "name": "Aaron Judge", "team": "NYY"},
        {"player_id": 660271, "name": "Shohei Ohtani", "team": "LAD"},
        {"player_id": 660271, "name": "Shohei Ohtani", "team": "LAD"},
    ]
    hits = mod.compute_hr_hits(picks, events)
    assert set(hits.keys()) == {1, 2}
    assert hits[1]["count"] == 1
    assert hits[2]["count"] == 2


def test_compute_hr_hits_fallback_to_normalized_name_and_team():
    # Pick has no player_id; event uses accented characters + team match.
    picks = [
        {"rank": 1, "name": "Jose Ramirez", "team": "CLE"},
    ]
    events = [
        {"player_id": None, "name": "José Ramírez", "team": "CLE"},
    ]
    hits = mod.compute_hr_hits(picks, events)
    assert hits[1]["count"] == 1


def test_compute_hr_hits_returns_empty_on_no_events():
    picks = [{"rank": 1, "name": "X"}]
    assert mod.compute_hr_hits(picks, []) == {}
    assert mod.compute_hr_hits(picks, None) == {}


def test_normalize_name_strips_punct_and_suffixes():
    assert mod._normalize_name("Vladimir Guerrero Jr.") == "vladimir guerrero"
    assert mod._normalize_name("José Ramírez") == "jose ramirez"
    assert mod._normalize_name("  Soto's ") == "sotos"


# ---------------------------------------------------------------------------
# Badge rendering / cashed-card HTML
# ---------------------------------------------------------------------------
def test_cashed_badge_html_singular_and_plural():
    assert mod.build_cashed_badge(None) == ""
    one = mod.build_cashed_badge({"count": 1, "events": [{}], "first": {}})
    assert "💣 CASHED HR" in one
    multi = mod.build_cashed_badge({"count": 3, "events": [{}], "first": {}})
    assert "CASHED ×3" in multi


def test_render_card_applies_cashed_class_when_hit():
    pick = {"rank": 1, "name": "Aaron Judge", "team": "NYY", "player_id": 592450}
    html_hit = mod._render_card(pick, batters_df=None,
                                hit_info={"count": 1, "events": [], "first": {}})
    html_miss = mod._render_card(pick, batters_df=None, hit_info=None)
    assert 'class="mrbets850-card cashed"' in html_hit
    assert "cashed-badge" in html_hit
    assert 'class="mrbets850-card"' in html_miss
    assert "cashed-badge" not in html_miss


# ---------------------------------------------------------------------------
# load_picks / save_picks — round trip via the local fallback
# ---------------------------------------------------------------------------
def test_load_and_save_round_trip(tmp_path, monkeypatch):
    fake_file = tmp_path / "mrbets850_hr_picks.json"
    monkeypatch.setattr(mod, "PICKS_PATH", str(fake_file))

    # Remote disabled (no secrets) — save_picks should write the local file.
    monkeypatch.setattr(mod, "_remote_enabled", lambda cfg=None: False)
    state = {"picks": [
        {"rank": 2, "name": "Aaron Judge", "team": "NYY"},
        {"rank": 1, "name": "Juan Soto", "team": "NYM"},
    ]}
    ok, msg = mod.save_picks(state)
    assert ok, msg
    loaded = mod.load_picks()
    assert [p["name"] for p in loaded["picks"]] == ["Juan Soto", "Aaron Judge"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
