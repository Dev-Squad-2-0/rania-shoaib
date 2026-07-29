"""
tools.py
=========

Task 2: Structured retrieval tools over the AFL dataset.
Task 3: These are registered as LangChain tools further down.

## The structured-vs-semantic decision (Task 2 justification)

None of the four source files are unstructured text — no match reports,
no commentary, no articles. Everything is numeric/categorical columns.
That makes the retrieval decision simple: 100% structured lookup, 0%
vector search, and there's no missing-text-corpus workaround needed
because there's genuinely nothing to embed.

This is actually the *safer* outcome, not a compromise: a stat like
"disposals in round 14, 2023" has exactly one correct value sitting in a
cell in round_by_round_enriched.csv. Vector search would retrieve the
*nearest-sounding* passage, not the *correct* number — if you had match
reports and asked "how many disposals did X have," a similarity search
might surface a report mentioning a different game with similar language,
and the LLM would confidently repeat whatever number appears in it. Exact
lookups don't have that failure mode: either the row exists and the
number is right, or the row doesn't exist and the tool says so.

## Grounding format

Every tool below returns a small dict that always includes the literal
values pulled from the dataframe, never a pre-written sentence. This
matters for Task 3's grounding check: if the tool returned a paragraph,
you couldn't tell whether the LLM copied the number from the tool or
substituted one from memory while paraphrasing. Returning raw structured
data forces the LLM to restate the number itself, and it also makes the
tool's own output independently checkable against the source CSV in a
test (see test_task5_eval.py) without needing an LLM in the loop at all.
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from typing import Optional
import numpy as np
import pandas as pd


def _native(value):
    """Cast numpy/pandas scalar types to plain Python types so tool outputs
    are JSON-serializable. Caught via smoke-testing tools.py directly before
    ever wiring in the LLM — np.float64 prints fine in a REPL but breaks
    when LangChain serializes the tool result to send back to the model."""
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value

from data_loader import (
    load_match_level,
    load_player_season_stats,
    load_player_game_stats,
    resolve_player_name,
    resolve_team_name,
)


# ---------------------------------------------------------------------------
# Tool 1: Head-to-head team record
# ---------------------------------------------------------------------------
class HeadToHeadArgs(BaseModel):
    team_a: str = Field(..., description="First team name, e.g. 'Richmond' or 'Richmond Tigers'.")
    team_b: str = Field(..., description="Second team name, e.g. 'Collingwood' or 'Collingwood Magpies'.")
    since_year: Optional[int] = Field(
        None, description="Optional. Only count matches from this year onward (inclusive)."
    )
    until_year: Optional[int] = Field(
        None, description="Optional. Only count matches up to and including this year. "
        "Use since_year == until_year to answer a single-season question "
        "(e.g. 'in 2023' or 'the following year'), rather than leaving "
        "until_year unset, which would include all years after since_year too."
    )


@tool("get_head_to_head", args_schema=HeadToHeadArgs)
def get_head_to_head(
    team_a: str, team_b: str, since_year: Optional[int] = None, until_year: Optional[int] = None
) -> dict:
    """Get the exact win/loss/draw record of team_a against team_b, from real
    match data. Use this whenever a user asks about a head-to-head record,
    rivalry history, or 'how many times has X beaten Y'. Team names can be
    partial (e.g. 'tigers'); the tool resolves them against real team names
    in the dataset. Never guess a head-to-head record from memory — always
    call this tool.

    If the user asks about a single specific season (e.g. 'in 2023', 'that
    year', 'the following year'), pass the SAME value for since_year and
    until_year to get exactly that one season. Passing only since_year
    returns that year onward, which will silently over-answer a
    single-season question."""
    a_matches = resolve_team_name(team_a)
    b_matches = resolve_team_name(team_b)

    if len(a_matches) != 1:
        return {"error": f"Could not uniquely resolve team_a='{team_a}'. Candidates: {a_matches}"}
    if len(b_matches) != 1:
        return {"error": f"Could not uniquely resolve team_b='{team_b}'. Candidates: {b_matches}"}

    team_a_resolved, team_b_resolved = a_matches[0], b_matches[0]

    df = load_match_level()
    subset = df[(df["team"] == team_a_resolved) & (df["opponent"] == team_b_resolved)]
    if since_year is not None:
        subset = subset[subset["year"] >= since_year]
    if until_year is not None:
        subset = subset[subset["year"] <= until_year]

    if subset.empty:
        return {
            "team_a": team_a_resolved,
            "team_b": team_b_resolved,
            "matches_found": 0,
            "note": "No matches found for this pairing in the dataset (check spelling/years).",
        }

    wins = int((subset["result"] == "W").sum())
    losses = int((subset["result"] == "L").sum())
    draws = int((subset["result"] == "D").sum())

    return {
        "team_a": team_a_resolved,
        "team_b": team_b_resolved,
        "since_year": since_year,
        "until_year": until_year,
        "matches_found": int(len(subset)),
        "team_a_wins": wins,
        "team_a_losses": losses,
        "draws": draws,
        "most_recent_match_date": str(subset["match_date"].max().date()),
    }


# ---------------------------------------------------------------------------
# Tool 2: Player season stats
# ---------------------------------------------------------------------------
class PlayerSeasonStatsArgs(BaseModel):
    player_name: str = Field(..., description="Player name, e.g. 'Dustin Martin'. Partial names allowed.")
    year: int = Field(..., description="Season year, e.g. 2023.")


@tool("get_player_season_stats", args_schema=PlayerSeasonStatsArgs)
def get_player_season_stats(player_name: str, year: int) -> dict:
    """Get a player's real season totals and averages (kicks, marks,
    handballs, disposals, goals, tackles, etc.) for a given year, combining
    home-and-away and finals if the player played both. Use this for
    questions like 'what were X's stats in 2022' or 'how many goals did X
    kick that season'. Averages are recomputed from totals/games_played
    rather than averaged-of-averages, so combined-season numbers are
    mathematically correct rather than approximate."""
    matches = resolve_player_name(player_name)
    if matches.empty:
        return {"error": f"No player found matching '{player_name}'."}
    if matches["player_name"].nunique() > 1:
        return {
            "error": f"Multiple distinct players match '{player_name}'.",
            "candidates": matches["player_name"].unique().tolist(),
        }

    player_id = matches.iloc[0]["id"]
    resolved_name = matches.iloc[0]["player_name"]

    season = load_player_season_stats()
    rows = season[(season["player_id"] == player_id) & (season["year"] == year)]
    if rows.empty:
        return {"player_name": resolved_name, "year": year, "note": "No season data found for this player/year."}

    total_cols = [
        "games_played", "kicks", "marks", "handballs", "disposals", "goals",
        "behinds", "hit_outs", "tackles", "clearances", "contested_possessions",
        "uncontested_possessions", "total_fantasy_points",
    ]
    totals = rows[total_cols].sum(numeric_only=True)
    games = totals["games_played"]

    result = {
        "player_name": resolved_name,
        "year": year,
        "games_played": int(games),
        "totals": {c: _native(totals[c]) for c in total_cols if c != "games_played"},
    }
    if games > 0:
        games_native = float(games)
        result["averages_per_game"] = {
            c: _native(round(float(totals[c]) / games_native, 2)) for c in total_cols if c != "games_played"
        }
    return result


# ---------------------------------------------------------------------------
# Tool 3: Player single-game stats (round-by-round)
# ---------------------------------------------------------------------------
class PlayerGameStatsArgs(BaseModel):
    player_name: str = Field(..., description="Player name, e.g. 'Marcus Bontempelli'. Partial names allowed.")
    year: int = Field(..., description="Season year the round falls in, e.g. 2023.")
    round_number: str = Field(
        ..., description="Round identifier as it appears in the data, e.g. '14' or 'QF' for finals rounds."
    )


@tool("get_player_game_stats", args_schema=PlayerGameStatsArgs)
def get_player_game_stats(player_name: str, year: int, round_number: str) -> dict:
    """Get a player's exact stat line for one specific match, identified by
    year and round. Use this for questions like 'how many disposals did X
    have last round' or 'what did X do in round 14, 2023'. This is the most
    granular tool available — always prefer this over season stats when the
    user asks about a specific game rather than a whole season."""
    matches = resolve_player_name(player_name)
    if matches.empty:
        return {"error": f"No player found matching '{player_name}'."}
    if matches["player_name"].nunique() > 1:
        return {
            "error": f"Multiple distinct players match '{player_name}'.",
            "candidates": matches["player_name"].unique().tolist(),
        }

    player_id = matches.iloc[0]["id"]
    resolved_name = matches.iloc[0]["player_name"]

    games = load_player_game_stats()
    row = games[
        (games["player_id"] == player_id)
        & (games["year"] == year)
        & (games["round"].astype(str) == str(round_number))
    ]
    if row.empty:
        return {
            "player_name": resolved_name, "year": year, "round": round_number,
            "note": "No game found for this player/year/round combination.",
        }

    r = row.iloc[0]
    stat_cols = [
        "opponent", "result", "kicks", "marks", "handballs", "disposals",
        "goals", "behinds", "tackles", "clearances", "contested_possessions",
        "uncontested_possessions", "fantasy_points", "score", "margin",
        "home_away", "venue",
    ]
    return {
        "player_name": resolved_name,
        "year": year,
        "round": round_number,
        "match_date": str(r["match_date"].date()),
        "stats": {c: _native(r[c]) for c in stat_cols},
    }


ALL_TOOLS = [get_head_to_head, get_player_season_stats, get_player_game_stats]