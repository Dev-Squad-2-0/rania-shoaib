"""
graph.py
=========

Task 1 (graph sketch, as actual code) + Task 4 (validation/fallback
wiring). This is the one file that shows the whole shape of the system;
every other file is a single node's implementation.

    START
      |
    router  ------------------------------------------------+
      |                                                      |
      | intent=off_topic                intent=factual       |
      v                                     v                |
    refusal --> END                  direct_answer --> format_response --> END
      |
      | intent in {retrieval, prediction}
      v
    resolve_entities
      |
      +-- issues found (unresolved/ambiguous team, player, year, round)
      |        v
      |     clarify --> END
      |
      +-- resolved OK
               |
               +-- intent=retrieval  --> retrieval_tool  --+
               |                                            |
               +-- intent=prediction --> prediction_tool  --+
                                                             v
                                                         validate
                                                             |
                                    +------------------------+------------------------+
                                    |                        |                        |
                                 status=ok             status=clarify           status=fallback
                                    v                        v                        v
                             format_response --> END      clarify --> END       fallback --> END

Why this shape (rather than one agent deciding everything, Task 1's
justification): every branch that can produce a user-facing prediction
passes through exactly one node (format_response) before returning,
and that node is the only place prediction-disclaimer wording lives.
There's no path through this graph that reaches the user with a
prediction that skipped it. A single free-roaming agent has no such
structural guarantee -- the disclaimer is one instruction among many in
its prompt, competing with everything else it's been told, and prompts
are followed *usually*, not *always*.
"""

from langgraph.graph import StateGraph, END

from state import GraphState, new_state
from router import router_node
from resolver import resolve_entities_node
from retrieval_node import retrieval_node
from prediction_node import prediction_node
from validation_node import validate_node
from fallback_nodes import clarify_node, fallback_node
from direct_and_refusal_nodes import direct_answer_node, refusal_node, social_node
from format_response_node import format_response_node
from hardening import safe_node


def _after_router(state) -> str:
    # Task 1 hardening: if safe_node caught an exception inside
    # router_node itself, "intent" was never set. Route straight to
    # refusal in that case -- it's the cheapest, safest terminal node
    # (a fixed template, no further tool calls), and final_response is
    # already populated by safe_node's fallback message, so refusal_node
    # will just overwrite it with its own templated text, which is fine
    # since either way the user gets a plain-language message, not a
    # crash. Missing "intent" should be effectively unreachable in
    # practice, but this graph shouldn't rely on that being true.
    intent = state.get("intent")
    if intent is None:
        return "refusal"
    if intent == "off_topic":
        return "refusal"
    if intent == "social":
        return "social"
    if intent == "factual":
        return "direct_answer"
    return "resolve_entities"


def _after_resolve(state) -> str:
    if state.get("_resolution_issues"):
        return "clarify"
    # Same defensive fallback as _after_router: if resolve_entities itself
    # failed and safe_node caught it, "intent" is still present from the
    # router (this only clears if router failed, in which case we never
    # get here), but guard anyway rather than assume.
    return "prediction_tool" if state.get("intent") == "prediction" else "retrieval_tool"


def _after_validate(state) -> str:
    status = (state.get("validation") or {}).get("status", "ok")
    if status == "clarify":
        return "clarify"
    if status == "fallback":
        return "fallback"
    return "format_response"


def build_graph():
    g = StateGraph(GraphState)

    # Task 1 hardening: every node is wrapped in safe_node so an
    # unhandled exception in ANY of them (router regex crash, a malformed
    # dataframe row, a KeyError while formatting) can't take down the
    # whole graph invocation -- "consistent error handling across every
    # node/tool" means literally every node here, not just the two
    # (retrieval/prediction) that already had a try/except of their own.
    g.add_node("router", safe_node(router_node))
    g.add_node("resolve_entities", safe_node(resolve_entities_node))
    g.add_node("retrieval_tool", safe_node(retrieval_node))
    g.add_node("prediction_tool", safe_node(prediction_node))
    g.add_node("validate", safe_node(validate_node))
    g.add_node("clarify", safe_node(clarify_node))
    g.add_node("fallback", safe_node(fallback_node))
    g.add_node("direct_answer", safe_node(direct_answer_node))
    g.add_node("refusal", safe_node(refusal_node))
    g.add_node("social", safe_node(social_node))
    g.add_node("format_response", safe_node(format_response_node))

    g.set_entry_point("router")

    g.add_conditional_edges("router", _after_router, {
        "refusal": "refusal",
        "social": "social",
        "direct_answer": "direct_answer",
        "resolve_entities": "resolve_entities",
    })
    g.add_conditional_edges("resolve_entities", _after_resolve, {
        "clarify": "clarify",
        "retrieval_tool": "retrieval_tool",
        "prediction_tool": "prediction_tool",
    })
    g.add_edge("retrieval_tool", "validate")
    g.add_edge("prediction_tool", "validate")
    g.add_conditional_edges("validate", _after_validate, {
        "clarify": "clarify",
        "fallback": "fallback",
        "format_response": "format_response",
    })

    g.add_edge("refusal", END)
    g.add_edge("social", END)
    g.add_edge("clarify", END)
    g.add_edge("fallback", END)
    g.add_edge("direct_answer", "format_response")
    g.add_edge("format_response", END)

    return g.compile()


_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = build_graph()
    return _APP


def run_turn(query: str, session_id: str = "default", chat_history=None) -> dict:
    """Single entry point: runs one user turn through the compiled graph
    and returns the final state (so callers/tests can inspect the full
    trace, not just the response text)."""
    app = get_app()
    state = new_state(query, session_id=session_id, chat_history=chat_history)
    final_state = app.invoke(state)
    return final_state


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "Will the Pies beat the Cats this week?"
    result = run_turn(q)
    print("QUERY:", q)
    print("INTENT:", result["intent"], "/", result.get("sub_type"))
    print("RESPONSE:\n", result["final_response"])