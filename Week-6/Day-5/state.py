"""
state.py
=========

Task 1: State schema for the full chat + retrieval + prediction graph.

Every node reads from and writes to this one shared dict. Keeping it in
its own file (rather than inline in graph.py) means the schema is the
single place to look when adding a new field, and it can be imported by
the test scripts without pulling in the whole graph.

Design notes:

- `entities` is intentionally a loose dict (not a rigid pydantic model)
  because the router extracts different slots depending on intent
  (team_a/team_b for a prediction, player_name/year/round for a stat
  lookup). Fields that don't apply to the current intent are just absent
  rather than forced to None on some big shared model.

- `validation` is a small dict, not a bare bool, because the fallback and
  clarification nodes need to know *why* something failed, not just that
  it did (unknown team vs. missing year vs. unsupported stat type each
  produce a different message).

- `trace` accumulates one entry per node visited. This is what Task 5's
  annotated state traces are built from: after a run, `state["trace"]`
  is the full router -> tool -> validation -> response path, in order,
  with each node's key decision recorded.
"""

from typing import TypedDict, Optional, Dict, Any, List


class GraphState(TypedDict, total=False):
    # --- input ---
    query: str                          # the current user message
    session_id: str                     # groups turns into one conversation
    chat_history: List[Dict[str, str]]  # prior turns: [{"role": "...", "content": "..."}]

    # --- router output ---
    intent: str                         # "prediction" | "retrieval" | "factual" | "off_topic"
    sub_type: Optional[str]             # e.g. "match" / "player" for prediction;
                                         # "head_to_head" / "player_season" /
                                         # "player_game" / "team_leaders" for retrieval
    router_confidence: Optional[str]    # "high" | "low" -- low confidence gets flagged for review

    # --- entity extraction / resolution ---
    entities: Dict[str, Any]            # raw + resolved slots (team_a, team_b, player_name,
                                         # year, round_number, stat, top_n, date, team_a_is_home)

    # --- tool execution ---
    tool_name: Optional[str]            # which tool function actually got called
    tool_result: Optional[Dict[str, Any]]  # raw dict returned by the tool (or {"error": ...})

    # --- validation / self-correction (Task 4) ---
    validation: Dict[str, Any]          # {"status": "ok" | "clarify" | "fallback" | "error",
                                         #  "reason": str}

    # --- output ---
    final_response: Optional[str]

    # --- observability (Task 5) ---
    trace: List[Dict[str, Any]]


def new_state(query: str, session_id: str = "default", chat_history=None) -> GraphState:
    """Factory for a fresh state at the start of a turn. chat_history is
    passed in from the caller's session store so router/resolution nodes
    can use prior turns for follow-up questions."""
    return GraphState(
        query=query,
        session_id=session_id,
        chat_history=chat_history or [],
        intent=None,
        sub_type=None,
        router_confidence=None,
        entities={},
        tool_name=None,
        tool_result=None,
        validation={},
        final_response=None,
        trace=[],
    )


def log_step(state: GraphState, node: str, **details) -> None:
    """Append one annotated step to the trace. Called at the start of
    every node so the trace reads in execution order."""
    state.setdefault("trace", []).append({"node": node, **details})
