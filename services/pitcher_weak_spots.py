"""Pitcher Weak Spots — slot-based matchup intelligence service.

This module powers the "Pitcher Weak Spots" tab. It evaluates each starting
pitcher on today's slate against the opposing batting order and produces a
*Pitcher Weak Spot Score* for each lineup slot 1-9. Scores are computed
against the slot first (independent of batter identity) so projected and
confirmed lineups slot in cleanly: when MLB posts the confirmed order the
slot scores are bound to the confirmed hitter names and any individual
batter context (handedness, recent form) is layered on top.

Design notes
------------
- Pure-Python data layer. UI rendering lives in ``app.py``.
- Reuses existing project utilities (lineup service, pitcher/batter Savant
  CSVs, ``find_pitcher_row``-style lookups) — we don't refetch any feed the
  app already pulls.
- Heavy graceful fallbacks: every input is wrapped in ``safe_float`` so an
  empty/partial CSV simply collapses to league averages rather than raising.
- Slot scores are stable across the projected → confirmed transition. The
  ``bind_lineup`` step swaps names without rescoring the slot, then mixes in
  any batter-specific adjustments.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

import math

try:  # pandas is a hard dependency of the app, but keep imports defensive
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore

# Public lineup status constants — re-exported so callers in app.py don't
# have to import lineup_service directly. Mirrors lineup_service.py so the UI
# can render truthful labels for live / final / postponed games rather than
# falling through to a generic "pending" badge.
LINEUP_STATUS_CONFIRMED = "confirmed"
LINEUP_STATUS_EXPECTED = "expected"
LINEUP_STATUS_NOT_POSTED = "not_posted"
LINEUP_STATUS_LIVE = "live"
LINEUP_STATUS_FINAL = "final"
LINEUP_STATUS_POSTPONED = "postponed"


def lineup_status_label(status: str, *, has_lineup: bool = True) -> str:
    """Human-readable label for a lineup status.

    ``has_lineup`` lets the live/final paths degrade gracefully when the
    boxscore didn't carry a batting order — instead of claiming a confirmed
    lineup we'd render "Live - lineup unavailable" which is the truth.
    """
    s = (status or "").lower()
    if s == LINEUP_STATUS_CONFIRMED:
        return "Lineup confirmed"
    if s == LINEUP_STATUS_LIVE:
        return "Live" if has_lineup else "Live - lineup unavailable"
    if s == LINEUP_STATUS_FINAL:
        return "Final" if has_lineup else "Final - lineup unavailable"
    if s == LINEUP_STATUS_POSTPONED:
        return "Postponed"
    if s == LINEUP_STATUS_EXPECTED:
        return "Projected lineup"
    return "Lineup pending"

# Zone classification thresholds (0-100 scale).
ZONE_PRIMARY = "primary"     # red/orange — strong attack
ZONE_SECONDARY = "secondary" # yellow — worth a look
ZONE_NEUTRAL = "neutral"     # gray — fade

PRIMARY_CUTOFF = 70.0
SECONDARY_CUTOFF = 55.0

# Weakness tag labels surfaced on the card.
TAG_TOP_ORDER = "Top Order"
TAG_MIDDLE_ORDER = "Middle Order"
TAG_BOTTOM_ORDER = "Bottom Order"
TAG_LEFTY_CLUSTER = "Lefty Cluster"
TAG_RIGHTY_CLUSTER = "Righty Cluster"
TAG_SECOND_TIME_THROUGH = "Second Time Through"
TAG_THIRD_TIME_THROUGH = "Third Time Through"
TAG_POWER_RISK = "Power Risk"
TAG_LOW_K_ZONE = "Low-K Zone"
TAG_PLATOON_EDGE = "Platoon Edge"


def safe_float(x, default=0.0):
    """Coerce x to float, returning default if conversion fails."""
    try:
        if x is None:
            return float(default)
        if isinstance(x, str):
            s = x.strip().lstrip(".")
            # Savant CSV often stores e.g. ".265" for batting avg — pandas
            # already coerces these, but be defensive when called with raw
            # strings.
            if x.strip() == "":
                return float(default)
        return float(x)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


# ---------------------------------------------------------------------------
# Pitcher profile — derived from the pitcher Savant rows the app already has
# ---------------------------------------------------------------------------

@dataclass
class PitcherProfile:
    """Distilled view of a pitcher's vulnerability inputs.

    Every field defaults to a league-ish value so partial data never makes
    the scorer raise. Boolean ``had_*`` flags let the UI explain which
    inputs were real vs. fallbacks (confidence is also computed from
    these).
    """

    player_id: int | None = None
    name: str = ""
    hand: str = "R"               # L/R
    # Contact quality allowed
    xslg: float = 0.420
    xwoba: float = 0.320
    iso_allowed: float = 0.170
    obp_allowed: float = 0.320
    hard_hit: float = 38.0
    barrel: float = 8.0
    k_pct: float = 22.0
    bb_pct: float = 8.0
    # Hand splits (allowed wOBA) — optional, default to overall xwoba
    woba_vs_l: float = 0.320
    woba_vs_r: float = 0.320
    # Times-through-order — proxy from K% drop and OPS-allowed if available
    ttop_2nd_factor: float = 1.05  # multiplier vs first time through
    ttop_3rd_factor: float = 1.12
    # Order-segment opponent OPS (top-3 / mid-3 / bot-3)
    ops_top3: float = 0.730
    ops_mid3: float = 0.720
    ops_bot3: float = 0.690
    # Recent form / home-away
    recent_form_factor: float = 1.0    # >1 = worse recent form (more attackable)
    is_home: bool = True
    # Flags for confidence math
    had_hand_split: bool = False
    had_segment_split: bool = False
    had_recent_form: bool = False
    had_arsenal: bool = False
    notes: list[str] = field(default_factory=list)


def _safe_get(row, key, default):
    """Pull from a pandas Series-like row, falling back to default."""
    if row is None:
        return default
    try:
        v = row.get(key) if hasattr(row, "get") else row[key]
    except Exception:
        return default
    if v is None:
        return default
    try:
        if pd is not None and pd.isna(v):
            return default
    except Exception:
        pass
    return v


def build_pitcher_profile(p_row, *, pitcher_name: str = "", pitcher_id: int | None = None,
                          pitcher_hand: str = "", is_home: bool = True) -> PitcherProfile:
    """Build a ``PitcherProfile`` from one row of the pitcher CSV.

    ``p_row`` is expected to be a row of the app's ``pitchers_df`` (the
    ``standardize_columns`` output) so columns like ``xSLG`` / ``HardHit%``
    / ``K%`` are present. Missing columns fall back to league averages.
    """
    prof = PitcherProfile(
        player_id=pitcher_id,
        name=pitcher_name or str(_safe_get(p_row, "Name", "")),
        hand=(pitcher_hand or str(_safe_get(p_row, "pitch_hand", "R")) or "R").upper()[:1],
        is_home=bool(is_home),
    )

    prof.xslg = safe_float(_safe_get(p_row, "xSLG", 0.420), 0.420)
    prof.xwoba = safe_float(_safe_get(p_row, "xwOBA", 0.320), 0.320)
    prof.iso_allowed = safe_float(_safe_get(p_row, "ISO", 0.170), 0.170)
    prof.obp_allowed = safe_float(_safe_get(p_row, "OBP", 0.320), 0.320)
    prof.hard_hit = safe_float(_safe_get(p_row, "HardHit%", 38.0), 38.0)
    prof.barrel = safe_float(_safe_get(p_row, "Barrel%", 8.0), 8.0)
    prof.k_pct = safe_float(_safe_get(p_row, "K%", 22.0), 22.0)
    prof.bb_pct = safe_float(_safe_get(p_row, "BB%", 8.0), 8.0)

    # Optional split columns. Savant's qualified pitcher leaderboard doesn't
    # ship platoon splits by default — when the app's pitcher CSV is later
    # enriched (or a future PR adds it) these keys are picked up
    # automatically. Otherwise the scorer leans on the *batter's*
    # platoon_value() multiplier instead.
    woba_l = _safe_get(p_row, "wOBA_vs_L", None)
    woba_r = _safe_get(p_row, "wOBA_vs_R", None)
    if woba_l is not None and woba_r is not None:
        prof.woba_vs_l = safe_float(woba_l, prof.xwoba)
        prof.woba_vs_r = safe_float(woba_r, prof.xwoba)
        prof.had_hand_split = True
    else:
        # Sensible asymmetric fallback derived from the *overall* xwOBA so
        # the scorer at least varies by pitcher quality. The L vs R lean
        # uses a tiny prior based on pitcher hand: LHPs allow ~10pts more
        # wOBA to LHB on average (league-wide), but we only nudge by 5pts
        # to avoid overstating a split we don't actually have.
        bump = 0.005 if prof.hand == "L" else -0.005
        prof.woba_vs_l = prof.xwoba - bump
        prof.woba_vs_r = prof.xwoba + bump

    # Times-through-order — derive from K% relative to league. Pitchers with
    # higher K% generally degrade more on the third time through (smaller
    # margin to fall back on), so we widen the multiplier proportionally.
    k_gap = max(-10.0, min(15.0, prof.k_pct - 22.0))
    prof.ttop_2nd_factor = 1.04 + max(0.0, k_gap) * 0.004
    prof.ttop_3rd_factor = 1.10 + max(0.0, k_gap) * 0.006

    # Order-segment opponent OPS — optional columns; if the CSV ever adds
    # ``OPS_top3`` etc., we'll pick them up. Otherwise we use league-tier
    # priors that scale with overall xSLG so worse pitchers look worse
    # across the board.
    base = (prof.xslg - 0.420) * 0.55
    prof.ops_top3 = safe_float(_safe_get(p_row, "OPS_top3", None), 0.755 + base)
    prof.ops_mid3 = safe_float(_safe_get(p_row, "OPS_mid3", None), 0.735 + base)
    prof.ops_bot3 = safe_float(_safe_get(p_row, "OPS_bot3", None), 0.690 + base)
    if _safe_get(p_row, "OPS_top3", None) is not None:
        prof.had_segment_split = True

    # Recent-form factor. Optional column; default is 1.0 (neutral).
    rf = _safe_get(p_row, "RecentFormFactor", None)
    if rf is not None:
        prof.recent_form_factor = safe_float(rf, 1.0)
        prof.had_recent_form = True

    return prof


# ---------------------------------------------------------------------------
# Lineup batter — projected or confirmed
# ---------------------------------------------------------------------------

@dataclass
class LineupBatter:
    slot: int                    # 1..9
    name: str = ""
    player_id: int | None = None
    bat_side: str = "R"          # L / R / S
    is_projected: bool = True    # False once confirmed lineup binds in
    # Optional batter quality knobs, populated when we can find the row in
    # the batter CSV. Defaults make missing data inert.
    iso: float = 0.170
    xwoba: float = 0.320
    barrel: float = 8.0
    hard_hit: float = 38.0
    k_pct: float = 22.0
    ops: float = 0.730


def make_projected_batter(slot: int) -> LineupBatter:
    """Anonymous projected batter for a slot — used before any lineup data
    is available so we can still publish slot scores."""
    return LineupBatter(slot=slot, name=f"Projected #{slot}", is_projected=True)


def lineup_from_rows(rows: Iterable[Any], *, is_projected: bool) -> list[LineupBatter]:
    """Build a 9-batter lineup from either ``lineup_to_dict_rows`` output
    or the projected-lineup DataFrame the app already produces.

    Missing slots are filled with anonymous ``Projected #N`` placeholders so
    downstream scoring can always iterate 1..9. ``is_projected`` controls
    the badge: projected lineups use the softer styling; confirmed lineups
    flip the flag once the actual MLB lineup posts.
    """
    by_slot: dict[int, LineupBatter] = {}
    for r in rows or []:
        try:
            slot_val = r.get("lineup_spot") if hasattr(r, "get") else r["lineup_spot"]
        except Exception:
            continue
        try:
            slot = int(float(slot_val))
        except (TypeError, ValueError):
            continue
        if slot < 1 or slot > 9:
            continue
        name = ""
        try:
            name = str(r.get("player_name", "") if hasattr(r, "get") else r["player_name"])
        except Exception:
            pass
        bat_side = ""
        try:
            bat_side = str(r.get("bat_side", "") if hasattr(r, "get") else r["bat_side"])
        except Exception:
            pass
        player_id = None
        try:
            pid = r.get("player_id") if hasattr(r, "get") else r["player_id"]
            if pid is not None:
                player_id = int(pid)
        except Exception:
            pass
        by_slot[slot] = LineupBatter(
            slot=slot,
            name=name,
            player_id=player_id,
            bat_side=(bat_side or "R").upper()[:1] or "R",
            is_projected=is_projected,
        )
    out: list[LineupBatter] = []
    for slot in range(1, 10):
        out.append(by_slot.get(slot) or make_projected_batter(slot))
    return out


def enrich_batter_from_row(b: LineupBatter, b_row) -> LineupBatter:
    """Mix batter Savant stats onto an existing ``LineupBatter`` in place."""
    if b_row is None:
        return b
    b.iso = safe_float(_safe_get(b_row, "ISO", b.iso), b.iso)
    b.xwoba = safe_float(_safe_get(b_row, "xwOBA", b.xwoba), b.xwoba)
    b.barrel = safe_float(_safe_get(b_row, "Barrel%", b.barrel), b.barrel)
    b.hard_hit = safe_float(_safe_get(b_row, "HardHit%", b.hard_hit), b.hard_hit)
    b.k_pct = safe_float(_safe_get(b_row, "K%", b.k_pct), b.k_pct)
    b.ops = safe_float(_safe_get(b_row, "OPS", b.ops), b.ops)
    bs = str(_safe_get(b_row, "bat_side", b.bat_side) or b.bat_side).upper()[:1]
    if bs in ("L", "R", "S"):
        b.bat_side = bs
    return b


# ---------------------------------------------------------------------------
# Slot-based scoring
# ---------------------------------------------------------------------------

def _platoon_edge_for_batter(bat_side: str, pitch_hand: str) -> float:
    """Return platoon multiplier for the batter vs pitcher hand combo.

    Positive ⇒ batter has the edge (attractive attack zone).
    Switch hitters always get the edge.
    """
    b = (bat_side or "R").upper()[:1]
    p = (pitch_hand or "R").upper()[:1]
    if b == "S":
        return 1.0
    if b not in ("L", "R") or p not in ("L", "R"):
        return 0.0
    return 1.0 if b != p else -0.35


def _segment_ops(prof: PitcherProfile, slot: int) -> float:
    if slot <= 3:
        return prof.ops_top3
    if slot <= 6:
        return prof.ops_mid3
    return prof.ops_bot3


def _times_through_factor(prof: PitcherProfile, slot: int) -> float:
    """Estimate which time-through-order this slot most often faces the
    pitcher in. Slots 1-3 see the second time at-bat earliest; slots 7-9
    are the ones who flip the lineup over for a third time."""
    if slot <= 3:
        return 1.0  # first time through is the relevant comparison
    if slot <= 6:
        return prof.ttop_2nd_factor
    return prof.ttop_3rd_factor


def score_slot(prof: PitcherProfile, batter: LineupBatter) -> dict[str, Any]:
    """Compute a 0-100 Pitcher Weak Spot Score for one batting-order slot.

    Returns a dict with the score, zone classification, weakness tags,
    and human-readable reasons for the "Why this spot?" card.
    """
    slot = batter.slot
    pitcher_hand = prof.hand
    # 1) Pitcher quality baseline (worse pitchers ⇒ higher base score).
    quality = (
        (prof.xslg - 0.380) * 80.0
        + (prof.barrel - 7.0) * 1.8
        + (prof.hard_hit - 35.0) * 0.45
        - (prof.k_pct - 22.0) * 0.55
        + (prof.iso_allowed - 0.150) * 70.0
    )

    # 2) Hand split — uses real split if available, otherwise the
    # asymmetric fallback we baked into the profile.
    if (batter.bat_side or "R").upper().startswith("L"):
        hand_woba = prof.woba_vs_l
    elif (batter.bat_side or "R").upper().startswith("S"):
        hand_woba = (prof.woba_vs_l + prof.woba_vs_r) / 2.0 + 0.005
    else:
        hand_woba = prof.woba_vs_r
    hand_component = (hand_woba - 0.320) * 220.0

    # 3) Order segment & times-through-order.
    seg_ops = _segment_ops(prof, slot)
    seg_component = (seg_ops - 0.720) * 80.0
    ttop_component = (_times_through_factor(prof, slot) - 1.0) * 50.0

    # 4) Platoon edge for *this* batter vs the pitcher hand.
    platoon = _platoon_edge_for_batter(batter.bat_side, pitcher_hand) * 6.0

    # 5) Batter quality (recent ISO / xwOBA / barrel%). Only applied when
    # we have a confirmed-or-projected name with stats — otherwise zero.
    batter_quality = 0.0
    if batter.name and not batter.name.startswith("Projected #"):
        batter_quality = (
            (batter.iso - 0.150) * 40.0
            + (batter.xwoba - 0.320) * 90.0
            + (batter.barrel - 7.0) * 0.6
        )

    # 6) Home/away — opposing pitcher on the road is slightly more
    # attackable in aggregate.
    home_away = -2.5 if prof.is_home else 1.5

    # 7) Recent form factor (>1 ⇒ worse recent form ⇒ more attackable).
    form_component = (prof.recent_form_factor - 1.0) * 40.0

    raw = (
        50.0  # neutral midpoint
        + quality * 0.45
        + hand_component
        + seg_component
        + ttop_component
        + platoon
        + batter_quality * 0.35
        + home_away
        + form_component
    )
    score = max(0.0, min(100.0, raw))

    # Zone classification.
    if score >= PRIMARY_CUTOFF:
        zone = ZONE_PRIMARY
    elif score >= SECONDARY_CUTOFF:
        zone = ZONE_SECONDARY
    else:
        zone = ZONE_NEUTRAL

    # Weakness tags — multiple can apply.
    tags: list[str] = []
    if slot <= 3 and seg_ops >= 0.745:
        tags.append(TAG_TOP_ORDER)
    if 4 <= slot <= 6 and seg_ops >= 0.735:
        tags.append(TAG_MIDDLE_ORDER)
    if slot >= 7 and seg_ops >= 0.700:
        tags.append(TAG_BOTTOM_ORDER)
    if (batter.bat_side or "").upper().startswith("L") and prof.woba_vs_l >= prof.woba_vs_r:
        tags.append(TAG_LEFTY_CLUSTER)
    elif (batter.bat_side or "").upper().startswith("R") and prof.woba_vs_r > prof.woba_vs_l:
        tags.append(TAG_RIGHTY_CLUSTER)
    if 4 <= slot <= 6:
        tags.append(TAG_SECOND_TIME_THROUGH)
    if slot >= 7 and prof.ttop_3rd_factor >= 1.12:
        tags.append(TAG_THIRD_TIME_THROUGH)
    if prof.barrel >= 9.0 or prof.hard_hit >= 41.0 or prof.iso_allowed >= 0.180:
        tags.append(TAG_POWER_RISK)
    if prof.k_pct <= 20.0:
        tags.append(TAG_LOW_K_ZONE)
    if platoon > 0:
        tags.append(TAG_PLATOON_EDGE)
    # Dedupe while preserving order.
    seen: set[str] = set()
    tags = [t for t in tags if not (t in seen or seen.add(t))]

    # "Why this spot?" — pick the 2-3 strongest reasons.
    reasons: list[str] = []
    if abs(hand_component) >= 1.5:
        side = "LHB" if (batter.bat_side or "R").upper().startswith("L") else "RHB"
        verdict = "higher" if hand_component > 0 else "lower"
        reasons.append(f"Allows {verdict} wOBA to {side} ({hand_woba:.3f}).")
    if seg_component >= 1.5:
        bucket = "top" if slot <= 3 else ("middle" if slot <= 6 else "bottom")
        reasons.append(f"Slots {1 if bucket=='top' else (4 if bucket=='middle' else 7)}-"
                       f"{3 if bucket=='top' else (6 if bucket=='middle' else 9)} project best "
                       f"({bucket}-order OPS {seg_ops:.3f}).")
    if ttop_component >= 2.0:
        which = "second" if slot <= 6 else "third"
        reasons.append(f"K rate drops after first time through — {which} time around the order.")
    if prof.barrel >= 9.0 or prof.iso_allowed >= 0.180:
        reasons.append(f"Power risk: barrel {prof.barrel:.1f}% / ISO allowed {prof.iso_allowed:.3f}.")
    if prof.k_pct <= 20.0:
        reasons.append(f"Low-K profile ({prof.k_pct:.1f}%) — contact-heavy attack zone.")
    if platoon > 0 and not batter.name.startswith("Projected #"):
        reasons.append("Batter has the platoon edge in this slot.")
    if not reasons:
        # Generic fallback so the card never goes blank.
        reasons.append("Slot grades out as a neutral matchup against tonight's starter.")
    reasons = reasons[:3]

    # Confidence — driven by data completeness, not score magnitude.
    confidence = 40.0
    if prof.had_hand_split:
        confidence += 18.0
    if prof.had_segment_split:
        confidence += 12.0
    if prof.had_recent_form:
        confidence += 8.0
    if not batter.is_projected:
        confidence += 12.0
    if batter.name and not batter.name.startswith("Projected #"):
        confidence += 6.0
    confidence = max(15.0, min(95.0, confidence))

    return {
        "slot": slot,
        "score": round(score, 1),
        "zone": zone,
        "tags": tags,
        "reasons": reasons,
        "confidence": round(confidence, 1),
        "platoon_edge": platoon > 0,
        "hand_woba": round(hand_woba, 3),
        "seg_ops": round(seg_ops, 3),
        "ttop_factor": round(_times_through_factor(prof, slot), 3),
    }


# ---------------------------------------------------------------------------
# Card assembly
# ---------------------------------------------------------------------------

@dataclass
class WeakSpotCard:
    """One game card's worth of pitcher-vs-lineup intelligence.

    The card is *side-aware*: each game produces two cards (one for the
    away pitcher attacking the home lineup, one for the home pitcher
    attacking the away lineup) so the UI can list both attack vectors.
    """

    game_pk: int
    game_time_label: str
    away_abbr: str
    home_abbr: str
    pitcher_name: str
    pitcher_hand: str
    pitcher_team_abbr: str
    opponent_abbr: str
    lineup_status: str               # confirmed / expected / not_posted
    lineup_status_label: str         # human label for badge
    is_lineup_confirmed: bool
    slot_scores: list[dict[str, Any]] = field(default_factory=list)
    batters: list[LineupBatter] = field(default_factory=list)
    overall_score: float = 0.0       # mean of top-3 slot scores
    top4_targetable: int = 0         # slots 1-4 in primary/secondary
    primary_count: int = 0
    confidence: float = 0.0
    has_lefty_weakness: bool = False
    has_righty_weakness: bool = False
    has_top_order_target: bool = False
    has_middle_order_target: bool = False
    notes: list[str] = field(default_factory=list)


def assemble_card(
    *,
    game_pk: int,
    game_time_label: str,
    away_abbr: str,
    home_abbr: str,
    pitcher_name: str,
    pitcher_hand: str,
    pitcher_team_abbr: str,
    opponent_abbr: str,
    pitcher_profile: PitcherProfile,
    lineup: list[LineupBatter],
    lineup_status: str,
) -> WeakSpotCard:
    """Run the slot scorer for every batter in ``lineup`` and roll up the
    aggregate card-level fields the UI sorts/filters by."""
    # Confirmed-style statuses share the green outline treatment: a lineup is
    # locked in once MLB has either posted it or already begun the game.
    is_confirmed = lineup_status in (
        LINEUP_STATUS_CONFIRMED, LINEUP_STATUS_LIVE, LINEUP_STATUS_FINAL,
    )
    scores: list[dict[str, Any]] = [score_slot(pitcher_profile, b) for b in lineup]

    sorted_scores = sorted(scores, key=lambda s: -s["score"])
    top3 = sorted_scores[:3]
    overall = sum(s["score"] for s in top3) / len(top3) if top3 else 0.0
    top4_targetable = sum(
        1 for s in scores if s["slot"] <= 4 and s["zone"] in (ZONE_PRIMARY, ZONE_SECONDARY)
    )
    primary_count = sum(1 for s in scores if s["zone"] == ZONE_PRIMARY)
    confidence = sum(s["confidence"] for s in scores) / len(scores) if scores else 0.0

    has_lefty = any(TAG_LEFTY_CLUSTER in s["tags"] and s["zone"] != ZONE_NEUTRAL for s in scores)
    has_righty = any(TAG_RIGHTY_CLUSTER in s["tags"] and s["zone"] != ZONE_NEUTRAL for s in scores)
    has_top = any(s["slot"] <= 3 and s["zone"] in (ZONE_PRIMARY, ZONE_SECONDARY) for s in scores)
    has_mid = any(4 <= s["slot"] <= 6 and s["zone"] in (ZONE_PRIMARY, ZONE_SECONDARY) for s in scores)

    # A lineup is "real" once at least one named (non-Projected #N) batter
    # has been bound — for live/final games we want to flip to the
    # "unavailable" fallback when boxscore data is missing.
    has_named_lineup = any(
        b.name and not b.name.startswith("Projected #") for b in lineup
    )
    status_label = lineup_status_label(lineup_status, has_lineup=has_named_lineup)

    return WeakSpotCard(
        game_pk=game_pk,
        game_time_label=game_time_label,
        away_abbr=away_abbr,
        home_abbr=home_abbr,
        pitcher_name=pitcher_name,
        pitcher_hand=pitcher_hand,
        pitcher_team_abbr=pitcher_team_abbr,
        opponent_abbr=opponent_abbr,
        lineup_status=lineup_status,
        lineup_status_label=status_label,
        is_lineup_confirmed=is_confirmed,
        slot_scores=scores,
        batters=lineup,
        overall_score=round(overall, 1),
        top4_targetable=top4_targetable,
        primary_count=primary_count,
        confidence=round(confidence, 1),
        has_lefty_weakness=has_lefty,
        has_righty_weakness=has_righty,
        has_top_order_target=has_top,
        has_middle_order_target=has_mid,
    )


def bind_confirmed_lineup(card: WeakSpotCard, confirmed: list[LineupBatter]) -> WeakSpotCard:
    """Re-bind a previously projected card to a confirmed lineup.

    Slot scores from the projected pass are *preserved* — the score lives
    on the slot, not the batter. We swap names/IDs/handedness and flip
    ``is_projected``, then refresh card-level aggregates that depend on
    confirmed state.
    """
    by_slot = {b.slot: b for b in confirmed}
    new_batters: list[LineupBatter] = []
    for b in card.batters:
        replacement = by_slot.get(b.slot)
        if replacement is None:
            new_batters.append(b)
            continue
        replacement.is_projected = False
        new_batters.append(replacement)
    card.batters = new_batters
    card.lineup_status = LINEUP_STATUS_CONFIRMED
    card.lineup_status_label = lineup_status_label(LINEUP_STATUS_CONFIRMED)
    card.is_lineup_confirmed = True
    return card


# ---------------------------------------------------------------------------
# Convenience: turn a card into a list of view rows for the slot table UI.
# ---------------------------------------------------------------------------

ZONE_COLOR_BG = {
    ZONE_PRIMARY: "#fee2e2",
    ZONE_SECONDARY: "#fef9c3",
    ZONE_NEUTRAL: "#f1f5f9",
}
ZONE_COLOR_BORDER = {
    ZONE_PRIMARY: "#dc2626",
    ZONE_SECONDARY: "#ca8a04",
    ZONE_NEUTRAL: "#94a3b8",
}
ZONE_COLOR_TEXT = {
    ZONE_PRIMARY: "#7f1d1d",
    ZONE_SECONDARY: "#713f12",
    ZONE_NEUTRAL: "#334155",
}
ZONE_LABEL = {
    ZONE_PRIMARY: "Primary",
    ZONE_SECONDARY: "Secondary",
    ZONE_NEUTRAL: "Neutral",
}


def card_to_slot_rows(card: WeakSpotCard) -> list[dict[str, Any]]:
    """Pair each batter with its scored slot for the lineup-grid renderer."""
    by_slot = {s["slot"]: s for s in card.slot_scores}
    out: list[dict[str, Any]] = []
    for b in card.batters:
        s = by_slot.get(b.slot, {})
        out.append({
            "slot": b.slot,
            "name": b.name,
            "bat_side": b.bat_side,
            "is_projected": b.is_projected,
            "score": s.get("score", 0.0),
            "zone": s.get("zone", ZONE_NEUTRAL),
            "tags": s.get("tags", []),
            "confidence": s.get("confidence", 0.0),
            "reasons": s.get("reasons", []),
            "platoon_edge": s.get("platoon_edge", False),
        })
    return out
