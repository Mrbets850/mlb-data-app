import os
import pandas as pd
from datetime import datetime, timezone

from pybaseball import (
    batting_stats,
    pitching_stats,
    statcast_batter_expected_stats,
    statcast_pitcher_expected_stats
)

DATA_DIR = "data"
SEASON = int(os.getenv("MLB_SEASON", datetime.now().year))
BATTER_QUAL = int(os.getenv("BATTER_QUAL", "20"))
PITCHER_QUAL = int(os.getenv("PITCHER_QUAL", "20"))

os.makedirs(DATA_DIR, exist_ok=True)

TEAM_FIXES = {
    "CHW": "CWS",
    "KCR": "KC",
    "SDP": "SD",
    "SFG": "SF",
    "TBR": "TB",
    "WSN": "WSH",
    "OAK": "ATH"
}

def norm_team(x):
    if pd.isna(x):
        return ""
    t = str(x).strip().upper()
    return TEAM_FIXES.get(t, t)

def first_existing(df, cols, default=None):
    for c in cols:
        if c in df.columns:
            return df[c]
    return pd.Series([default] * len(df))

def prepare_batters():
    fg = batting_stats(SEASON, qual=BATTER_QUAL).copy()
    sc = statcast_batter_expected_stats(SEASON, BATTER_QUAL).copy()

    fg.columns = [str(c).strip() for c in fg.columns]
    sc.columns = [str(c).strip() for c in sc.columns]

    fg["Name_key"] = first_existing(fg, ["Name"]).astype(str).str.strip().str.lower()
    sc["Name_key"] = first_existing(sc, ["last_name, first_name", "player_name", "Name"]).astype(str).str.strip().str.lower()

    fg["Team_fix"] = first_existing(fg, ["Team"]).apply(norm_team)
    if "team_abbr" in sc.columns:
        sc["Team_fix"] = sc["team_abbr"].apply(norm_team)
    else:
        sc["Team_fix"] = ""

    batter = fg.merge(sc, on="Name_key", how="left", suffixes=("", "_sc"))

    out = pd.DataFrame()
    out["Name"] = first_existing(batter, ["Name"])
    out["Team"] = first_existing(batter, ["Team_fix"])
    out["HR"] = pd.to_numeric(first_existing(batter, ["HR"]), errors="coerce")
    out["AVG"] = pd.to_numeric(first_existing(batter, ["AVG"]), errors="coerce")
    out["OBP"] = pd.to_numeric(first_existing(batter, ["OBP"]), errors="coerce")
    out["SLG"] = pd.to_numeric(first_existing(batter, ["SLG"]), errors="coerce")
    out["OPS"] = pd.to_numeric(first_existing(batter, ["OPS"]), errors="coerce")
    out["ISO"] = pd.to_numeric(first_existing(batter, ["ISO"]), errors="coerce")
    out["wOBA"] = pd.to_numeric(first_existing(batter, ["wOBA"]), errors="coerce")

    out["xwOBA"] = pd.to_numeric(first_existing(batter, ["xwoba", "xwOBA"]), errors="coerce")
    out["xSLG"] = pd.to_numeric(first_existing(batter, ["xslg", "xSLG"]), errors="coerce")
    out["Barrel%"] = pd.to_numeric(first_existing(batter, ["brl_percent", "barrel_batted_rate", "Barrel%"]), errors="coerce")
    out["HardHit%"] = pd.to_numeric(first_existing(batter, ["hard_hit_percent", "HardHit%"]), errors="coerce")
    out["EV"] = pd.to_numeric(first_existing(batter, ["exit_velocity_avg", "avg_hit_speed", "EV"]), errors="coerce")
    out["LA"] = pd.to_numeric(first_existing(batter, ["launch_angle_avg", "LA"]), errors="coerce")
    out["bat_side"] = first_existing(batter, ["stand", "bat_side"], default="")

    out = out.dropna(subset=["Name"])
    out = out[out["Name"].astype(str).str.len() > 0]
    out = out.drop_duplicates(subset=["Name", "Team"])
    out = out.sort_values(["Team", "Name"]).reset_index(drop=True)

    return out

def prepare_pitchers():
    fg = pitching_stats(SEASON, qual=PITCHER_QUAL).copy()
    sc = statcast_pitcher_expected_stats(SEASON, PITCHER_QUAL).copy()

    fg.columns = [str(c).strip() for c in fg.columns]
    sc.columns = [str(c).strip() for c in sc.columns]

    fg["Name_key"] = first_existing(fg, ["Name"]).astype(str).str.strip().str.lower()
    sc["Name_key"] = first_existing(sc, ["last_name, first_name", "player_name", "Name"]).astype(str).str.strip().str.lower()

    fg["Team_fix"] = first_existing(fg, ["Team"]).apply(norm_team)
    if "team_abbr" in sc.columns:
        sc["Team_fix"] = sc["team_abbr"].apply(norm_team)
    else:
        sc["Team_fix"] = ""

    pitcher = fg.merge(sc, on="Name_key", how="left", suffixes=("", "_sc"))

    out = pd.DataFrame()
    out["Name"] = first_existing(pitcher, ["Name"])
    out["Team"] = first_existing(pitcher, ["Team_fix"])
    out["pitch_hand"] = first_existing(pitcher, ["Throws", "throws", "pitch_hand"], default="")
    out["K%"] = pd.to_numeric(first_existing(pitcher, ["K%", "k_percent"]), errors="coerce")
    out["BB%"] = pd.to_numeric(first_existing(pitcher, ["BB%", "bb_percent"]), errors="coerce")
    out["xSLG"] = pd.to_numeric(first_existing(pitcher, ["xslg", "xSLG"]), errors="coerce")
    out["xwOBA"] = pd.to_numeric(first_existing(pitcher, ["xwoba", "xwOBA"]), errors="coerce")
    out["Barrel%"] = pd.to_numeric(first_existing(pitcher, ["brl_percent", "barrel_batted_rate", "Barrel%"]), errors="coerce")
    out["HardHit%"] = pd.to_numeric(first_existing(pitcher, ["hard_hit_percent", "HardHit%"]), errors="coerce")
    out["EV"] = pd.to_numeric(first_existing(pitcher, ["exit_velocity_avg", "avg_hit_speed", "EV"]), errors="coerce")

    out = out.dropna(subset=["Name"])
    out = out[out["Name"].astype(str).str.len() > 0]
    out = out.drop_duplicates(subset=["Name", "Team"])
    out = out.sort_values(["Team", "Name"]).reset_index(drop=True)

    return out

def main():
    batters = prepare_batters()
    pitchers = prepare_pitchers()

    batters.to_csv(f"{DATA_DIR}/savant_batters.csv", index=False)
    pitchers.to_csv(f"{DATA_DIR}/savant_pitchers.csv", index=False)

    stamp = pd.DataFrame([{
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "season": SEASON,
        "batters_rows": len(batters),
        "pitchers_rows": len(pitchers)
    }])
    stamp.to_csv(f"{DATA_DIR}/last_update.csv", index=False)

    print("Update complete")
    print(f"Season: {SEASON}")
    print(f"Batters rows: {len(batters)}")
    print(f"Pitchers rows: {len(pitchers)}")

if __name__ == "__main__":
    main()
