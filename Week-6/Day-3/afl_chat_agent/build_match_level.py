"""
build_match_level.py

Derives a match-level (team-perspective) table from the player-level
round_by_round_enriched.csv so that team head-to-head records can be
computed by exact lookup instead of guessing from player rows.

Grain of output: one row per (team, opponent, match_date) — i.e. one row
per team's own perspective of a match. Querying team==A & opponent==B
gives team A's full history against team B directly.

Usage:
    python build_match_level.py
Reads:
    /mnt/user-data/uploads/round_by_round_enriched.csv
Writes:
    /mnt/user-data/outputs/match_level.csv
"""

import os
import pandas as pd

# Relative to this script's own folder, same pattern as data_loader.py.
# Override with the AFL_DATA_DIR environment variable if your CSVs live
# somewhere else (e.g. a shared data folder outside this project).
DATA_DIR = os.environ.get("AFL_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
SRC = os.path.join(DATA_DIR, "round_by_round_enriched.csv")
OUT = os.path.join(DATA_DIR, "match_level.csv")

KEEP_COLS = [
    "team", "opponent", "year", "round", "match_date",
    "result", "home_away", "venue", "crowd", "score", "margin",
]


def load_raw(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["match_date"])
    missing = [c for c in KEEP_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Expected columns missing from source: {missing}")
    return df


def dedupe_to_match_level(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse player-per-match rows to one row per (team, opponent, match_date).
    All player rows for the same team in the same match should share the
    same team-level fields (result, score, margin, venue, crowd, home_away),
    so taking the first row per group is safe *if* that assumption holds
    (validated separately in validate()).
    """
    subset = df[KEEP_COLS].copy()
    match_level = (
        subset.sort_values(["match_date", "team", "opponent"])
        .drop_duplicates(subset=["team", "opponent", "match_date"], keep="first")
        .reset_index(drop=True)
    )
    return match_level


def validate(raw: pd.DataFrame, match_level: pd.DataFrame) -> list:
    """
    Returns a list of warning strings. Does not raise, so the caller can
    decide whether issues are acceptable to proceed with.
    """
    warnings = []

    # 1. Check that team-level fields are actually consistent across all
    #    player rows for the same match (result/score/margin shouldn't vary).
    check_cols = ["result", "score", "margin", "venue", "home_away"]
    grouped = raw.groupby(["team", "opponent", "match_date"])[check_cols]
    nunique = grouped.nunique(dropna=False)
    inconsistent = nunique[(nunique > 1).any(axis=1)]
    if len(inconsistent) > 0:
        warnings.append(
            f"{len(inconsistent)} (team, opponent, match_date) groups have "
            f"inconsistent result/score/margin/venue/home_away across player rows. "
            f"Example groups: {inconsistent.index[:5].tolist()}"
        )

    # 2. Every match should have a reciprocal row: if (A, B, date) exists,
    #    (B, A, date) should usually also exist (each side's own perspective).
    keys = set(zip(match_level["team"], match_level["opponent"], match_level["match_date"]))
    reciprocal_keys = set(zip(match_level["opponent"], match_level["team"], match_level["match_date"]))
    missing_reciprocal = keys - reciprocal_keys
    if missing_reciprocal:
        warnings.append(
            f"{len(missing_reciprocal)} matches have no reciprocal opponent-side row "
            f"(one team's perspective missing). Example: {list(missing_reciprocal)[:5]}"
        )

    # 3. Result sanity: values should be within expected set.
    bad_results = set(match_level["result"].dropna().unique()) - {"W", "L", "D"}
    if bad_results:
        warnings.append(f"Unexpected result values found: {bad_results}")

    # 4. Duplicate match_date+team+opponent after dedupe should be zero.
    dupes = match_level.duplicated(subset=["team", "opponent", "match_date"]).sum()
    if dupes:
        warnings.append(f"{dupes} duplicate rows remain after dedupe (should be 0).")

    return warnings


def main():
    raw = load_raw(SRC)
    print(f"Loaded raw player-match rows: {len(raw):,}")

    match_level = dedupe_to_match_level(raw)
    print(f"Derived match-level (team-perspective) rows: {len(match_level):,}")

    warnings = validate(raw, match_level)
    if warnings:
        print("\nVALIDATION WARNINGS:")
        for w in warnings:
            print(f" - {w}")
    else:
        print("\nValidation passed: no inconsistencies found.")

    match_level.to_csv(OUT, index=False)
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()