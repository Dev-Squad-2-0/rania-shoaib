"""
direct_and_refusal_nodes.py
=============================

Two nodes for the branches that never touch a prediction/retrieval tool:

- direct_answer_node: general AFL knowledge (club history, rules,
  competition structure) that no tool backs. Reuses Day 3's SYSTEM_PROMPT
  rules verbatim (no specific unverified facts, no exact scores/years/
  names) since those constraints don't change just because the call is
  arriving through a different node.

- refusal_node: off-topic redirection. Templated rather than a live LLM
  call, both for the same "no gateway access in this sandbox" reason as
  router.py, and because a fixed, reviewed refusal template is exactly
  the kind of consistency the router justification argues for: a
  guaranteed redirect beats a model deciding fresh each time.
"""

import os

try:
    from system_prompt import SYSTEM_PROMPT
except ImportError:
    SYSTEM_PROMPT = ""


def direct_answer_node(state) -> dict:
    from state import log_step
    query = state["query"]
    gateway_key = os.environ.get("GATEWAY_API_KEY")

    if not gateway_key:
        # Mirrors Day 3 agent.py's own behavior when GATEWAY_API_KEY is
        # unset: rather than fabricate history/rules content without a
        # live model, say plainly that this path needs one.
        response = (
            "That's a general AFL knowledge question (no dataset tool covers it), "
            "which this node answers via an LLM call using the same SYSTEM_PROMPT "
            "constraints as Day 3's agent (no invented scores, years, or names -- "
            "only general, non-specific background). Set GATEWAY_API_KEY to enable "
            "it; routing for this query correctly landed on the factual/direct-answer "
            "node either way."
        )
        log_step(state, "direct_answer", note="GATEWAY_API_KEY unset, returned placeholder", query=query)
        return {"final_response": response}

    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(
        base_url=os.environ.get("GATEWAY_BASE_URL", "https://llm.netixsol.com/v1"),
        api_key=gateway_key,
        model=os.environ.get("AFL_AGENT_MODEL", "smart"),
        temperature=0,
    )
    result = llm.invoke([("system", SYSTEM_PROMPT), ("human", query)])
    log_step(state, "direct_answer", query=query, used_llm=True)
    return {"final_response": result.content}


_REFUSAL_TEMPLATE = (
    "That's outside what I can help with here -- I'm scoped specifically to AFL "
    "teams, players, matches, and predictions. {redirect}"
)
_REDIRECTS = {
    "betting": "I can't give betting/odds advice either, but I can share real "
               "team or player stats and data-backed match predictions instead.",
    "default": "Happy to help with something AFL-related if that's useful instead.",
}


def refusal_node(state) -> dict:
    from state import log_step
    sub_type = state.get("sub_type")
    redirect = _REDIRECTS.get(sub_type, _REDIRECTS["default"])
    response = _REFUSAL_TEMPLATE.format(redirect=redirect)
    log_step(state, "refusal", message=response)
    return {"final_response": response}
