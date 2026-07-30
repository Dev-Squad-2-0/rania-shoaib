"""
retrieval_node.py
==================

Task 3 (retrieval half of "wire tools as LangGraph nodes"): calls straight
into Day 3's existing `tools.py` functions. These are already LangChain
`@tool`-decorated, so `.invoke(dict)` gives free pydantic validation of
the arguments on top of whatever resolver.py already resolved.

Deliberately NOT going through Day 3's `agent.py` (the free-form
tool-calling agent) here. That agent decides for itself, per turn,
whether/which tool to call -- fine for a chat-only surface, but this
graph's router already made that decision explicitly, so re-opening it to
another layer of free-form reasoning would be redundant and would
reintroduce exactly the "did it actually call the tool" uncertainty
Day 3's own GroundingLogger was built to catch.
"""

from tools import (
    get_head_to_head, get_player_season_stats, get_player_game_stats, get_team_stat_leaders,
)

_DISPATCH = {
    "head_to_head": get_head_to_head,
    "player_season": get_player_season_stats,
    "player_game": get_player_game_stats,
    "team_leaders": get_team_stat_leaders,
}


def retrieval_node(state) -> dict:
    from state import log_step
    sub_type = state.get("sub_type")
    entities = state.get("entities", {})
    tool = _DISPATCH.get(sub_type)

    if tool is None:
        log_step(state, "retrieval_tool", error=f"No retrieval tool for sub_type={sub_type}")
        return {"tool_name": None, "tool_result": {"error": f"Unsupported retrieval type: {sub_type}"}}

    args = _build_args(sub_type, entities)
    try:
        result = tool.invoke(args)
    except Exception as e:  # pydantic validation errors, etc.
        result = {"error": f"Tool call failed: {e}"}

    log_step(state, "retrieval_tool", tool=tool.name, args=args, result=result)
    return {"tool_name": tool.name, "tool_result": result}


def _build_args(sub_type, entities) -> dict:
    if sub_type == "head_to_head":
        args = {"team_a": entities.get("team_a"), "team_b": entities.get("team_b")}
        if entities.get("year"):
            args["since_year"] = entities["year"]
            args["until_year"] = entities["year"]
        return args
    if sub_type == "player_season":
        return {"player_name": entities.get("player_name"), "year": entities.get("year")}
    if sub_type == "player_game":
        return {
            "player_name": entities.get("player_name"),
            "year": entities.get("year"),
            "round_number": str(entities.get("round_number")),
        }
    if sub_type == "team_leaders":
        args = {"team": entities.get("team"), "year": entities.get("year")}
        if entities.get("stat"):
            args["stat"] = entities["stat"]
        if entities.get("top_n"):
            args["top_n"] = entities["top_n"]
        return args
    return {}
