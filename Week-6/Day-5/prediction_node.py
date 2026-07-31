"""
prediction_node.py
====================

Task 3 (prediction half): wraps Day 2's `predict_match_winner` and
`predict_top_player` as the prediction node's tool calls, and adds the
one thing the raw functions don't provide on their own -- a grounding
explanation of *why* the model landed on that number.

## Grounding approach for match winner

`predict.py` returns a bare probability. To get real per-prediction
feature contributions (not just "these features mattered on average
across the training set", which is what the Day 2 notebook's permutation
importance plot shows), this reaches into the same fitted pipeline
`predict.py` already loaded (`predict._match_model`) and:
  1. runs the same feature preprocessing (impute/scale/one-hot) the model
     itself uses,
  2. multiplies each transformed feature by the logistic regression's
     coefficient for that feature,
  3. takes the top 3 by absolute contribution.

This is the actual decision-relevant explanation for *this specific*
prediction, e.g. West Coast can be predicted to lose mostly on
`diff_avgmargin_last10` while Richmond's home prediction leans on
`h2h_winrate_vs_opp` instead. Reusing predict.py's private helpers
(`_validate_team`, `_team_state`, `_head_to_head`) rather than
reimplementing them means the explanation is guaranteed to be computed
from the exact same feature values the real prediction used.

## Grounding approach for top player

`HistGradientBoostingRegressor` doesn't expose linear coefficients, and
per-call permutation importance is too slow to run on every turn. Day 2's
notebook already established (Task 4 there) that `player_fantasy_last5`
dominates by roughly an order of magnitude over every other feature, so
the grounding here reports each top-ranked player's own actual
`fantasy_last5` / `disposals_last5` values -- the real numbers driving
their ranking, not a generic disclaimer.
"""

import numpy as np
import pandas as pd

import predict as _predict_module
from predict import predict_match_winner, predict_top_player, AFLPredictionError
from hardening import run_with_timeout, ToolTimeoutError, TIMEOUT_USER_MESSAGE

_FEATURE_LABELS = {
    "is_home": "home ground advantage",
    "is_interstate_travel": "interstate travel",
    "diff_winrate_last3": "win rate over the last 3 games (differential)",
    "diff_winrate_last5": "win rate over the last 5 games (differential)",
    "diff_winrate_last10": "win rate over the last 10 games (differential)",
    "diff_avgmargin_last3": "average winning margin, last 3 games (differential)",
    "diff_avgmargin_last5": "average winning margin, last 5 games (differential)",
    "diff_avgmargin_last10": "average winning margin, last 10 games (differential)",
    "diff_streak": "current win/loss streak (differential)",
    "diff_season_winrate": "season win rate so far (differential)",
    "diff_ladder_rank": "ladder position (differential)",
    "diff_rest_days": "days of rest before the match (differential)",
    "h2h_winrate_vs_opp": "historical head-to-head win rate",
    "h2h_games_played": "number of prior head-to-head games",
    "venue_state": "venue state",
}


def _explain_match_prediction(team_a, team_b, team_a_is_home, as_of) -> list:
    """Recomputes the same feature row predict_match_winner used
    internally, then returns the top 3 (label, contribution_direction,
    magnitude) tuples driving THIS prediction specifically."""
    a = _predict_module._team_state(team_a)
    b = _predict_module._team_state(team_b)
    h2h_winrate, h2h_games = _predict_module._head_to_head(team_a, team_b, as_of)
    is_interstate = int(team_a_is_home is False and a["team_home_state"] != b["team_home_state"])

    row = {
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
    }
    features = pd.DataFrame([row])

    model = _predict_module._match_model
    prep = model.named_steps["prep"]
    clf = model.named_steps["clf"]

    Xt = prep.transform(features[_predict_module._MATCH_NUM + _predict_module._MATCH_CAT])
    Xt = np.asarray(Xt.todense()) if hasattr(Xt, "todense") else np.asarray(Xt)
    feat_names = prep.get_feature_names_out()
    contributions = Xt[0] * clf.coef_[0]

    ranked = sorted(zip(feat_names, contributions), key=lambda x: abs(x[1]), reverse=True)[:3]
    explanation = []
    for raw_name, contribution in ranked:
        base_name = raw_name.split("__", 1)[-1]
        base_name = base_name.rsplit("_", 1)[0] if base_name.startswith("venue_state_") else base_name
        label = _FEATURE_LABELS.get(base_name, base_name)
        direction = "favours team_a" if contribution > 0 else "favours team_b"
        explanation.append({"feature": label, "direction": direction, "magnitude": round(float(abs(contribution)), 3)})
    return explanation


def _detect_unsupported_stat(query: str) -> str:
    """predict_top_player only ranks by predicted fantasy points. If the
    user asked for a different specific stat (tackles, disposals, goals,
    marks) explicitly, that's a genuine capability gap, not something to
    silently answer with the fantasy-points ranking instead."""
    q = query.lower()
    for stat in ["tackles", "disposals", "marks", "goals", "clearances", "hitouts", "intercepts"]:
        if stat in q and "fantasy" not in q:
            return stat
    return None


def prediction_node(state) -> dict:
    from state import log_step
    sub_type = state.get("sub_type")
    entities = state.get("entities", {})
    date = entities.get("resolved_date")

    if sub_type == "match":
        team_a, team_b = entities.get("team_a"), entities.get("team_b")
        try:
            # Task 1 hardening: sklearn inference + grounding explanation
            # is wrapped in a timeout too, same as retrieval tool calls --
            # a model swap that adds network latency shouldn't be able to
            # hang a prediction turn indefinitely.
            result = run_with_timeout(predict_match_winner, team_a, team_b, date=date, team_a_is_home=True)
            explanation = run_with_timeout(
                _explain_match_prediction, team_a, team_b, True,
                as_of=(pd.to_datetime(date) if date else _predict_module._DATA_MAX_DATE),
            )
            result["grounding"] = explanation
            if entities.get("date_note"):
                result["caveat"] = entities["date_note"]
        except ToolTimeoutError:
            result = {"timeout": True, "error": TIMEOUT_USER_MESSAGE}
        except AFLPredictionError as e:
            result = {"error": str(e)}
        log_step(state, "prediction_tool", tool="predict_match_winner",
                  args={"team_a": team_a, "team_b": team_b, "date": date}, result=result)
        return {"tool_name": "predict_match_winner", "tool_result": result}

    if sub_type == "player":
        team = entities.get("team")
        top_n = entities.get("top_n", 5)
        unsupported_stat = _detect_unsupported_stat(state["query"])
        if unsupported_stat:
            result = {"error": f"'{unsupported_stat}' is not a supported stat for player prediction "
                                f"(only fantasy points is modeled)."}
            log_step(state, "prediction_tool", tool="predict_top_player", args={"team": team},
                      result=result, note="unsupported stat requested")
            return {"tool_name": "predict_top_player", "tool_result": result}
        try:
            ranked = run_with_timeout(predict_top_player, team, date=date, top_n=top_n)
            state_df = _predict_module._latest_player_state
            for player in ranked:
                row = state_df[state_df["player_id"] == player["player_id"]]
                if not row.empty:
                    r = row.iloc[0]
                    player["grounding"] = {
                        "fantasy_points_last5_avg": None if pd.isna(r["player_fantasy_last5"]) else round(float(r["player_fantasy_last5"]), 1),
                        "disposals_last5_avg": None if pd.isna(r["player_disposals_last5"]) else round(float(r["player_disposals_last5"]), 1),
                    }
            result = {"team": team, "top_n": top_n, "ranked_players": ranked}
            if entities.get("date_note"):
                result["caveat"] = entities["date_note"]
        except ToolTimeoutError:
            result = {"timeout": True, "error": TIMEOUT_USER_MESSAGE}
        except AFLPredictionError as e:
            result = {"error": str(e)}
        log_step(state, "prediction_tool", tool="predict_top_player",
                  args={"team": team, "date": date, "top_n": top_n}, result=result)
        return {"tool_name": "predict_top_player", "tool_result": result}

    log_step(state, "prediction_tool", error=f"Unsupported prediction sub_type={sub_type}")
    return {"tool_name": None, "tool_result": {"error": f"Unsupported prediction type: {sub_type}"}}
