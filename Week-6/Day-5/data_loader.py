"""
data_loader.py
================

Why a separate loader instead of reading CSVs inside each tool function:
these files are big (round_by_round_enriched.csv alone is ~274k rows,
~50MB). If every tool call re-read from disk, a single conversation with
a few follow-up questions would re-parse tens of MB repeatedly, which is
slow and pointless since the data doesn't change mid-conversation. This
module loads each file exactly once per process and caches it.

DATA_DIR can be overridden with the AFL_DATA_DIR environment variable —
useful since your bootcamp setup already has a pattern of keeping a shared
venv/config and pointing scripts at wherever the data actually lives on
your machine, rather than hardcoding paths.
"""

import os
import functools
import pandas as pd

DATA_DIR = os.environ.get("AFL_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))


@functools.lru_cache(maxsize=1)
def load_match_level() -> pd.DataFrame:
    """One row per (team, opponent, match_date) — team's own perspective of a match.
    Built by build_match_level.py from round_by_round_enriched.csv."""
    path = os.path.join(DATA_DIR, "match_level.csv")
    df = pd.read_csv(path, parse_dates=["match_date"])
    return df


@functools.lru_cache(maxsize=1)
def load_player_season_stats() -> pd.DataFrame:
    """One row per player per year (split by is_finals True/False)."""
    path = os.path.join(DATA_DIR, "merged_players.csv")
    df = pd.read_csv(path)
    return df


@functools.lru_cache(maxsize=1)
def load_player_game_stats() -> pd.DataFrame:
    """One row per player per match (round-by-round granularity)."""
    path = os.path.join(DATA_DIR, "round_by_round_enriched.csv")
    df = pd.read_csv(path, parse_dates=["match_date"])
    return df


@functools.lru_cache(maxsize=1)
def load_player_info() -> pd.DataFrame:
    """One row per player — bio fields and the id used to join to stats tables."""
    path = os.path.join(DATA_DIR, "players_info_cleaned.csv")
    df = pd.read_csv(path)
    return df


def resolve_player_name(name: str) -> pd.DataFrame:
    """
    Case-insensitive, partial-match lookup of a player name against the bio
    table. Returns ALL matches rather than guessing one, because AFL has
    plenty of repeated surnames (multiple Ablett's, multiple Selwood's
    across eras) — silently picking the first match is exactly the kind
    of quiet wrong-answer bug that's hard to notice later. Callers (the
    tools) are responsible for deciding what to do when there's more than
    one match: usually surface the ambiguity to the user rather than guess.
    """
    info = load_player_info()
    mask = info["player_name"].str.contains(name, case=False, na=False, regex=False)
    return info[mask]


@functools.lru_cache(maxsize=1)
def _known_team_names() -> tuple:
    df = load_match_level()
    return tuple(sorted(df["team"].dropna().unique()))


def resolve_team_name(name: str) -> list:
    """
    Partial, case-insensitive match against real team names in the dataset
    (e.g. 'tigers' -> 'Richmond Tigers'). Returns a list of matches so the
    caller can handle zero or multiple matches explicitly instead of
    silently picking a possibly-wrong team.
    """
    name_lower = name.lower()
    return [t for t in _known_team_names() if name_lower in t.lower()]
