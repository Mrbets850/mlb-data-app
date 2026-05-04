#!/usr/bin/env python3
"""
Refresh Baseball Savant CSVs for the MrBets850 MLB Edge app.

Downloads four leaderboards from baseballsavant.mlb.com and writes them
to the repo root with the exact filenames the app expects:

  Data:savant_batters.csv.csv      - batter season Statcast leaderboard (qualified)
  Data:savant_batters_all.csv.csv  - batter Statcast leaderboard with min=10 PA
                                     so non-qualified players (rookies, call-ups,
                                     low-PA bench bats) appear in lineups instead
                                     of rendering as empty cells in the Matchup
                                     heat-map board. Used as a backfill source
                                     merged in only for player_ids missing from
                                     the qualified leaderboard.
  Data:savant_pitchers.csv.csv     - pitcher arsenal / pitch-mix leaderboard
  Data:savant_pitcher_stats.csv    - pitcher results leaderboard
  Data:savant_bat_tracking.csv     - per-batter Statcast bat-tracking (avg_bat_speed, swing_length)

Run nightly via GitHub Actions (.github/workflows/refresh-data.yml).

The script:
  - Computes the current MLB season year (Mar-Nov uses current year, else previous).
  - Hits each Savant CSV URL with a real-browser User-Agent so we don't get blocked.
  - Validates the response is actually a CSV (>1 KB and has a header line).
  - Writes the file atomically only if the response looks valid.
  - Skips writing (and exits 0) if the new file is identical to the existing one,
    so GitHub Actions can detect "no change" and skip the commit.

Usage:
    python scripts/refresh_savant.py [--year 2026] [--out-dir .]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys
import time
import urllib.request
import urllib.error


def current_season_year() -> int:
    """MLB season runs late-Mar through Oct, with playoffs into early Nov.
    Before mid-March we still want last year's data (current season hasn't started)."""
    today = dt.date.today()
    if today.month < 3 or (today.month == 3 and today.day < 15):
        return today.year - 1
    return today.year


# Each entry: (output filename, Savant CSV URL template with {year})
# All three URLs end in &csv=true, which makes Savant return a raw CSV.
TARGETS = [
    (
        "Data:savant_batters.csv.csv",
        "https://baseballsavant.mlb.com/leaderboard/custom?"
        "year={year}&type=batter&filter=&sort=4&sortDir=desc&min=q"
        "&selections=ab,pa,hit,single,double,triple,home_run,strikeout,walk,"
        "k_percent,bb_percent,batting_avg,slg_percent,on_base_percent,"
        "on_base_plus_slg,isolated_power,xba,xslg,woba,xwoba,xobp,xiso,"
        "exit_velocity_avg,launch_angle_avg,sweet_spot_percent,"
        "barrel_batted_rate,hard_hit_percent,avg_best_speed,whiff_percent,"
        "swing_percent,pull_percent,opposite_percent,groundballs_percent,"
        "flyballs_percent,linedrives_percent"
        "&chart=false&x=ab&y=ab&r=no&chartType=beeswarm&csv=true",
    ),
    (
        # Low-PA backfill: same selections, but min=1 instead of min=q so
        # rookies, call-ups, and low-PA bench bats appear with whatever real
        # Statcast data they have, even after only a handful of PAs. Merged
        # in app.py only for player_ids absent from the qualified leaderboard,
        # so it doesn't dilute or replace qualified-batter Statcast values.
        # min=1 (>=1 PA) ensures every active hitter on a daily lineup is
        # represented — eliminates blank rows for main-team batters who hadn't
        # crossed the old min=10 threshold.
        "Data:savant_batters_all.csv.csv",
        "https://baseballsavant.mlb.com/leaderboard/custom?"
        "year={year}&type=batter&filter=&sort=4&sortDir=desc&min=1"
        "&selections=ab,pa,hit,single,double,triple,home_run,strikeout,walk,"
        "k_percent,bb_percent,batting_avg,slg_percent,on_base_percent,"
        "on_base_plus_slg,isolated_power,xba,xslg,woba,xwoba,xobp,xiso,"
        "exit_velocity_avg,launch_angle_avg,sweet_spot_percent,"
        "barrel_batted_rate,hard_hit_percent,avg_best_speed,whiff_percent,"
        "swing_percent,pull_percent,opposite_percent,groundballs_percent,"
        "flyballs_percent,linedrives_percent"
        "&chart=false&x=ab&y=ab&r=no&chartType=beeswarm&csv=true",
    ),
    (
        # Prior-season backfill for batters: same min=1 leaderboard but for
        # the previous MLB season. Used in app.py as a final fallback when a
        # current-season player_id has too small a sample for any real values
        # (e.g. recently called up rookies, players returning from injury who
        # only have 2-3 PA so far). Joined by player_id only — never replaces
        # current-season values.
        "Data:savant_batters_prev.csv.csv",
        "https://baseballsavant.mlb.com/leaderboard/custom?"
        "year={prev_year}&type=batter&filter=&sort=4&sortDir=desc&min=1"
        "&selections=ab,pa,hit,single,double,triple,home_run,strikeout,walk,"
        "k_percent,bb_percent,batting_avg,slg_percent,on_base_percent,"
        "on_base_plus_slg,isolated_power,xba,xslg,woba,xwoba,xobp,xiso,"
        "exit_velocity_avg,launch_angle_avg,sweet_spot_percent,"
        "barrel_batted_rate,hard_hit_percent,avg_best_speed,whiff_percent,"
        "swing_percent,pull_percent,opposite_percent,groundballs_percent,"
        "flyballs_percent,linedrives_percent"
        "&chart=false&x=ab&y=ab&r=no&chartType=beeswarm&csv=true",
    ),
    (
        "Data:savant_pitchers.csv.csv",
        # min=1 (>=1 batter faced) instead of min=q so the slate-pitchers
        # board has data for every probable starter, not just qualified
        # workhorses. Qualified-only filter caps the leaderboard at ~80
        # pitchers and leaves the majority of slate starters with blanks.
        "https://baseballsavant.mlb.com/leaderboard/custom?"
        "year={year}&type=pitcher&filter=&sort=4&sortDir=desc&min=1"
        "&selections=pa,hit,single,double,triple,home_run,strikeout,walk,"
        "k_percent,bb_percent,batting_avg,slg_percent,on_base_percent,"
        "on_base_plus_slg,isolated_power,xba,xslg,woba,xwoba,xobp,xiso,"
        "exit_velocity_avg,launch_angle_avg,sweet_spot_percent,"
        "barrel_batted_rate,hard_hit_percent,avg_best_speed,whiff_percent,"
        "swing_percent,pull_percent,opposite_percent,groundballs_percent,"
        "flyballs_percent,linedrives_percent"
        "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm&csv=true",
    ),
    (
        "Data:savant_bat_tracking.csv",
        # Baseball Savant per-batter bat-tracking leaderboard. Provides real
        # avg_bat_speed (mph), swing_length (ft), batted_ball_events (BIP) and
        # swings_competitive (Pitches) per player_id. The custom batter
        # leaderboard does NOT expose these. Keyed by `id` (= player_id).
        # minSwings=10 (was minSwings=q) so non-qualified hitters and
        # recent call-ups appear on the leaderboard with whatever bat-tracking
        # data they have. Combined with the new min=1 batters_all backfill,
        # this is what guarantees Pitches/BIP cells are populated for every
        # active major-league hitter.
        "https://baseballsavant.mlb.com/leaderboard/bat-tracking?"
        "attackZone=&batSide=&contact=&count=&dateRangeStart=&dateRangeEnd="
        "&gameType=R&groupBy=&isHardHit=&minSwings=10&minGroupSwings=1"
        "&pitchHand=&pitchType=&seasonStart={year}&seasonEnd={year}"
        "&team=&type=batter&csv=true",
    ),
    (
        # Prior-season bat-tracking backfill: same minSwings=10 leaderboard
        # for the previous MLB season. Used in app.py as a final fallback
        # when a current-season player_id has no bat-tracking data yet (e.g.
        # April rookies). Joined by player_id only.
        "Data:savant_bat_tracking_prev.csv",
        "https://baseballsavant.mlb.com/leaderboard/bat-tracking?"
        "attackZone=&batSide=&contact=&count=&dateRangeStart=&dateRangeEnd="
        "&gameType=R&groupBy=&isHardHit=&minSwings=10&minGroupSwings=1"
        "&pitchHand=&pitchType=&seasonStart={prev_year}&seasonEnd={prev_year}"
        "&team=&type=batter&csv=true",
    ),
    (
        "Data:savant_pitcher_stats.csv",
        # min=1 — see note on Data:savant_pitchers.csv.csv above. This
        # leaderboard is the primary data source for the Slate Pitchers tab.
        "https://baseballsavant.mlb.com/leaderboard/custom?"
        "year={year}&type=pitcher&filter=&sort=4&sortDir=desc&min=1"
        "&selections=pa,k_percent,bb_percent,batting_avg,slg_percent,"
        "on_base_percent,on_base_plus_slg,xba,xslg,woba,xwoba,xobp,"
        "exit_velocity_avg,launch_angle_avg,sweet_spot_percent,"
        "barrel_batted_rate,hard_hit_percent,whiff_percent,swing_percent,"
        "pull_percent,opposite_percent,groundballs_percent,flyballs_percent,"
        "linedrives_percent"
        "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm&csv=true",
    ),
]

# Real-browser-ish UA. Savant 403s some default Python user-agents.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MIN_CSV_BYTES = 1024  # smaller than this = something went wrong


def download(url: str, retries: int = 3, timeout: int = 60) -> bytes:
    """Download a URL with retries. Raises RuntimeError on persistent failure."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            if not data or len(data) < MIN_CSV_BYTES:
                raise RuntimeError(
                    f"Response too small ({len(data) if data else 0} bytes); "
                    "likely an error page or empty leaderboard."
                )
            head = data[:200].decode("utf-8", errors="replace")
            # Accept any of the canonical Savant identifier columns. The
            # bat-tracking leaderboard uses "id"/"name" instead of
            # "player_id"/"last_name, first_name".
            head_lower = head.lower()
            if not any(tok in head_lower for tok in ("last_name", "player_id", '"id"', "avg_bat_speed")):
                raise RuntimeError(
                    "Response does not look like a Savant CSV header. "
                    f"First 200 bytes: {head!r}"
                )
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
            last_err = e
            print(f"  attempt {attempt}/{retries} failed: {e}", flush=True)
            if attempt < retries:
                time.sleep(2 * attempt)  # 2s, 4s back-off
    raise RuntimeError(f"All {retries} attempts failed: {last_err}")


def write_if_changed(path: str, data: bytes) -> bool:
    """Atomically write `data` to `path` if it differs from the current file.
    Returns True if the file was updated, False if the content was identical."""
    new_hash = hashlib.sha256(data).hexdigest()
    if os.path.exists(path):
        with open(path, "rb") as f:
            old_hash = hashlib.sha256(f.read()).hexdigest()
        if old_hash == new_hash:
            print(f"  unchanged: {path}", flush=True)
            return False

    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    print(f"  wrote: {path} ({len(data):,} bytes)", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Baseball Savant CSVs.")
    parser.add_argument("--year", type=int, default=None,
                        help="MLB season year (default: auto-detect).")
    parser.add_argument("--out-dir", default=".",
                        help="Directory to write CSVs into (default: cwd).")
    args = parser.parse_args()

    year = args.year or current_season_year()
    print(f"Refreshing Baseball Savant CSVs for season {year}...", flush=True)

    failures = []
    changes = 0
    for filename, url_tmpl in TARGETS:
        url = url_tmpl.format(year=year, prev_year=year - 1)
        out_path = os.path.join(args.out_dir, filename)
        print(f"\n-> {filename}", flush=True)
        try:
            data = download(url)
            if write_if_changed(out_path, data):
                changes += 1
        except Exception as e:
            print(f"  FAILED: {e}", flush=True)
            failures.append((filename, str(e)))

    print(f"\nDone. {changes} file(s) updated, {len(failures)} failure(s).", flush=True)
    if failures:
        print("Failures:")
        for f, err in failures:
            print(f"  - {f}: {err}")
        # Non-zero exit only if we got nothing usable; partial success is OK
        # so the workflow still commits whatever did refresh.
        if changes == 0:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
