"""
predict.py
AFL match winner and top player prediction functions.

These wrap the two trained pipelines saved in Week 6 Day 2 as simple,
validated Python functions, ready to be exposed as LangChain/LangGraph
tools on Day 4.

Artifacts expected in ./artifacts/:
  match_winner_pipeline.joblib   sklearn Pipeline (LogisticRegression)
  top_player_pipeline.joblib     sklearn Pipeline (HistGradientBoostingRegressor)
  latest_team_state.parquet      most recent known rolling stats per team
  match_history.parquet          full team-match history (for head-to-head lookups)
  latest_player_state.parquet    most recent known rolling stats per player
  valid_teams.joblib             list of valid team name strings
  date_range.joblib              (min_date, max_date) covered by the training data

Known approximation, stated plainly rather than hidden:
  "Latest known state" means each team's or player's rolling features as of
  their most recently recorded match. If you ask about a date further in the
  future than that, the function still uses that same latest snapshot rather
  than projecting form forward, so predictions for a team that hasn't played
  in a while are slightly stale. This mirrors exactly what the Day 1 rolling
  features already store: "form entering the next match", so no future
  information ever leaks in, but it does mean the tool describes "the team
  as we last saw them" and not "the team we hope they are now".
"""

import os
import joblib
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_ARTIFACTS = os.path.join(_DIR, "artifacts")
# Day 2 folder lives one level under Week 6, afl_players_info_raw.csv lives in Week 6 itself.
_NAME_FILE = os.path.join(_DIR, "..", "afl_players_info_raw.csv")

_match_model = joblib.load(os.path.join(_ARTIFACTS, "match_winner_pipeline.joblib"))
_player_model = joblib.load(os.path.join(_ARTIFACTS, "top_player_pipeline.joblib"))
_latest_team_state = pd.read_parquet(os.path.join(_ARTIFACTS, "latest_team_state.parquet"))
_match_history = pd.read_parquet(os.path.join(_ARTIFACTS, "match_history.parquet"))
_latest_player_state = pd.read_parquet(os.path.join(_ARTIFACTS, "latest_player_state.parquet"))
_valid_teams = joblib.load(os.path.join(_ARTIFACTS, "valid_teams.joblib"))
_DATA_MIN_DATE, _DATA_MAX_DATE = joblib.load(os.path.join(_ARTIFACTS, "date_range.joblib"))

try:
    _name_lookup = pd.read_csv(_NAME_FILE)
    _name_lookup["player_name"] = _name_lookup["player_name"].str.strip()
    _id_to_name = _name_lookup.drop_duplicates(subset="id").set_index("id")["player_name"]
except FileNotFoundError:
    print(f"[predict.py] Warning: {_NAME_FILE} not found, predict_top_player will return "
          f"player_id only, no player_name.")
    _id_to_name = pd.Series(dtype=str)

_MATCH_NUM = [
    "is_home", "is_interstate_travel",
    "diff_winrate_last3", "diff_winrate_last5", "diff_winrate_last10",
    "diff_avgmargin_last3", "diff_avgmargin_last5", "diff_avgmargin_last10",
    "diff_streak", "diff_season_winrate", "diff_ladder_rank", "diff_rest_days",
    "h2h_winrate_vs_opp", "h2h_games_played",
]
_MATCH_CAT = ["venue_state"]

_PLAYER_NUM = [
    "player_disposals_last3", "player_disposals_last5",
    "player_fantasy_last3", "player_fantasy_last5",
    "career_game_count", "is_home",
]


class AFLPredictionError(ValueError):
    """Raised for bad inputs: unknown team, unknown player, or date out of range."""


def _validate_team(team_name):
    if team_name not in _valid_teams:
        raise AFLPredictionError(
            f"Unknown team '{team_name}'. Valid teams are: {', '.join(_valid_teams)}"
        )


def _validate_date(date):
    date = pd.to_datetime(date)
    if date < _DATA_MIN_DATE:
        raise AFLPredictionError(
            f"Date {date.date()} is before the earliest data available "
            f"({_DATA_MIN_DATE.date()})."
        )
    return date


def _team_state(team_name):
    row = _latest_team_state[_latest_team_state["team"] == team_name]
    if row.empty:
        raise AFLPredictionError(f"No historical state found for team '{team_name}'.")
    return row.iloc[0]


def _head_to_head(team_a, team_b, as_of_date):
    prior = _match_history[
        (_match_history["team"] == team_a)
        & (_match_history["opponent"] == team_b)
        & (_match_history["match_date"] < as_of_date)
    ]
    games = len(prior)
    winrate = prior["win_flag"].mean() if games > 0 else 0.5  # neutral prior if no history
    return winrate, games


def predict_match_winner(team_a, team_b, date=None, team_a_is_home=True):
    """
    Predict the winner of a hypothetical match between team_a and team_b.

    Parameters
    ----------
    team_a, team_b : str
        Full team names, e.g. "Richmond Tigers". Must match the names used
        in the AFL dataset. Case-sensitive.
    date : str or datetime-like, optional
        Date of the match. Used to compute head-to-head history strictly
        before this date. Defaults to the most recent date in the dataset.
    team_a_is_home : bool, default True
        Whether team_a is hosting. Determines is_home and, if team_a and
        team_b are in different states, is_interstate_travel for team_a.

    Returns
    -------
    dict with keys:
        winner : str, predicted winning team name
        probability : float, model's win probability for the predicted winner
        team_a_win_probability : float
        team_b_win_probability : float

    Example
    -------
    >>> predict_match_winner("Geelong Cats", "Richmond Tigers", date="2026-04-05")
    {'winner': 'Geelong Cats', 'probability': 0.71,
     'team_a_win_probability': 0.71, 'team_b_win_probability': 0.29}
    """
    _validate_team(team_a)
    _validate_team(team_b)
    if team_a == team_b:
        raise AFLPredictionError("team_a and team_b must be different teams.")

    as_of = _validate_date(date) if date is not None else _DATA_MAX_DATE

    a = _team_state(team_a)
    b = _team_state(team_b)

    h2h_winrate, h2h_games = _head_to_head(team_a, team_b, as_of)

    is_interstate = int(team_a_is_home is False and a["team_home_state"] != b["team_home_state"])

    features = pd.DataFrame([{
        "is_home": int(team_a_is_home),
        "is_interstate_travel": is_interstate,
        "diff_winrate_last3": a["team_winrate_last3"] - b["team_winrate_last3"],
        "diff_winrate_last5": a["team_winrate_last5"] - b["team_winrate_last5"],
        "diff_winrate_last10": a["team_winrate_last10"] - b["team_winrate_last10"],
        "diff_avgmargin_last3": a["team_avgmargin_last3"] - b["team_avgmargin_last3"],
        "diff_avgmargin_last5": a["team_avgmargin_last5"] - b["team_avgmargin_last5"],
        "diff_avgmargin_last10": a["team_avgmargin_last10"] - b["team_avgmargin_last10"],
        "diff_streak": a["team_streak_entering"] - b["team_streak_entering"],
        "diff_season_winrate": a["season_winrate_to_date"] - b["season_winrate_to_date"],
        "diff_ladder_rank": b["ladder_rank_to_date"] - a["ladder_rank_to_date"],
        "diff_rest_days": a["days_since_last_game"] - b["days_since_last_game"],
        "h2h_winrate_vs_opp": h2h_winrate,
        "h2h_games_played": h2h_games,
        "venue_state": a["team_home_state"] if team_a_is_home else b["team_home_state"],
    }])

    proba_a = float(_match_model.predict_proba(features[_MATCH_NUM + _MATCH_CAT])[0, 1])

    winner = team_a if proba_a >= 0.5 else team_b
    win_prob = proba_a if proba_a >= 0.5 else 1 - proba_a

    return {
        "winner": winner,
        "probability": round(win_prob, 3),
        "team_a_win_probability": round(proba_a, 3),
        "team_b_win_probability": round(1 - proba_a, 3),
    }


def predict_top_player(team, date=None, top_n=5):
    """
    Predict the top-N fantasy point scorers for a team's next match, based
    on each listed player's most recently recorded form.

    Parameters
    ----------
    team : str
        Full team name, e.g. "Sydney Swans".
    date : str or datetime-like, optional
        Reference date. Only used for validation against the data range;
        defaults to the most recent date in the dataset.
    top_n : int, default 5
        Number of players to return, ranked by predicted fantasy points.

    Returns
    -------
    list of dicts, ranked highest predicted fantasy points first:
        [{'player_id': int, 'player_name': str, 'predicted_fantasy_points': float}, ...]

    Note
    ----
    player_name comes from afl_players_info_raw.csv, loaded from the parent
    (Week 6) folder at import time. If that file isn't found, player_name
    falls back to "[unknown id <player_id>]" rather than failing the call.

    Example
    -------
    >>> predict_top_player("Geelong Cats", date="2026-04-05", top_n=3)
    [{'player_id': 12345, 'player_name': 'Patrick Dangerfield', 'predicted_fantasy_points': 94.2}, ...]
    """
    _validate_team(team)
    if date is not None:
        _validate_date(date)
    if top_n < 1:
        raise AFLPredictionError("top_n must be at least 1.")

    roster = _latest_player_state[_latest_player_state["team"] == team].copy()
    if roster.empty:
        raise AFLPredictionError(f"No player history found for team '{team}'.")

    roster["is_home"] = 1  # neutral assumption: ranking is for this team's own lineup
    preds = _player_model.predict(roster[_PLAYER_NUM])
    roster["predicted_fantasy_points"] = preds

    ranked = roster.sort_values("predicted_fantasy_points", ascending=False).head(top_n)
    return [
        {
            "player_id": int(row.player_id),
            "player_name": _id_to_name.get(row.player_id, f"[unknown id {row.player_id}]"),
            "predicted_fantasy_points": round(float(row.predicted_fantasy_points), 1),
        }
        for row in ranked.itertuples()
    ]