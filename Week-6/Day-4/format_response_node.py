"""
format_response_node.py
=========================

The convergence point every "ok" path runs through before returning to
the user. This is where the brief's core safety requirement actually
lives in code, not in a prompt instruction: every prediction response is
built here with the probability and a "not a certainty" framing baked
into the template itself, so it cannot come out any other way regardless
of what the tool returned. That's the concrete version of the Task 1
justification for explicit routing -- the guarantee is structural.
"""


def format_response_node(state) -> dict:
    from state import log_step
    intent = state.get("intent")
    sub_type = state.get("sub_type")
    result = state.get("tool_result") or {}

    if intent == "prediction" and sub_type == "match":
        response = _format_match_prediction(result)
    elif intent == "prediction" and sub_type == "player":
        response = _format_player_prediction(result)
    elif intent == "retrieval":
        response = _format_retrieval(sub_type, result)
    else:
        response = state.get("final_response") or "Here's what I found."

    log_step(state, "format_response", intent=intent, sub_type=sub_type)
    return {"final_response": response}


def _format_match_prediction(result: dict) -> str:
    winner, prob = result["winner"], result["probability"]
    lines = [
        f"**Prediction (probabilistic, not a certainty):** {winner} is favoured to win, "
        f"with an estimated {prob:.0%} win probability "
        f"(that leaves roughly a {1 - prob:.0%} chance of the other result)."
    ]
    grounding = result.get("grounding") or []
    if grounding:
        lines.append("\nTop factors driving this estimate:")
        for g in grounding:
            lines.append(f"- {g['feature']} ({g['direction']}, magnitude {g['magnitude']})")
    if result.get("caveat"):
        lines.append(f"\nNote: {result['caveat']}")
    lines.append(
        "\nThis is a model estimate based on recent form and history, not a "
        "guarantee -- AFL results have real week-to-week variance the model doesn't capture."
    )
    return "\n".join(lines)


def _format_player_prediction(result: dict) -> str:
    players = result.get("ranked_players", [])
    lines = [f"**Prediction (probabilistic, not a certainty):** projected top {len(players)} "
             f"fantasy scorers for {result.get('team')}:"]
    for i, p in enumerate(players, 1):
        g = p.get("grounding") or {}
        detail = ""
        if g.get("fantasy_points_last5_avg") is not None:
            detail = f" (recent form: {g['fantasy_points_last5_avg']} fantasy pts/game over their last 5)"
        lines.append(f"{i}. {p['player_name']}: predicted {p['predicted_fantasy_points']} fantasy points{detail}")
    if result.get("caveat"):
        lines.append(f"\nNote: {result['caveat']}")
    lines.append(
        "\nFantasy scoring is genuinely volatile week to week -- treat this as a "
        "form-based estimate, not a lock."
    )
    return "\n".join(lines)


def _format_retrieval(sub_type: str, result: dict) -> str:
    if sub_type == "head_to_head":
        return (
            f"{result['team_a']} vs {result['team_b']}: {result['matches_found']} matches found. "
            f"{result['team_a']} record: {result['team_a_wins']}W-{result['team_a_losses']}L-{result['draws']}D. "
            f"Most recent meeting: {result['most_recent_match_date']}."
        )
    if sub_type == "player_season":
        totals = result.get("totals", {})
        return (
            f"{result['player_name']}, {result['year']} season ({result.get('games_played', '?')} games): "
            f"{totals.get('disposals', '?')} disposals, {totals.get('goals', '?')} goals, "
            f"{totals.get('tackles', '?')} tackles (season totals)."
        )
    if sub_type == "player_game":
        stats = result.get("stats", {})
        return (
            f"{result['player_name']}, {result['year']} round {result['round']} vs {stats.get('opponent', '?')}: "
            f"{stats.get('disposals', '?')} disposals, {stats.get('goals', '?')} goals, "
            f"{stats.get('tackles', '?')} tackles."
        )
    if sub_type == "team_leaders":
        leaders = result.get("leaders", [])
        stat = result.get("stat")
        lines = [f"{result.get('team')} {stat} leaders, {result.get('year')}:"]
        for i, l in enumerate(leaders, 1):
            lines.append(f"{i}. {l['player_name']}: {l[stat]}")
        return "\n".join(lines)
    return str(result)
