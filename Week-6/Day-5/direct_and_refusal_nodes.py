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
import random
import re
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

from hardening import run_with_timeout, ToolTimeoutError

try:
    from system_prompt import SYSTEM_PROMPT
except ImportError:
    SYSTEM_PROMPT = ""


# ---------------------------------------------------------------------------
# social_node: handles the router's "social" intent (see router.py's
# _SOCIAL_RE) -- greetings, thanks, farewells. Templated, not an LLM call,
# on purpose: there's nothing here worth spending gateway latency or
# tokens on, and a bare "hi" shouldn't have to wait behind the same
# unbounded LLM call direct_answer_node makes for real factual questions.
# A short rotation (rather than one fixed string) is enough to keep it
# from feeling robotic without needing a model in the loop.
# ---------------------------------------------------------------------------
_SOCIAL_REPLIES = [
    "Hey! Ask me anything about AFL -- stats, head-to-head records, or a match prediction.",
    "Hi there! I'm your AFL assistant -- happy to look up player stats, team records, or predict a matchup.",
    "Hello! What AFL question can I help with -- a player's stats, a head-to-head, or a prediction?",
]
_SOCIAL_THANKS_REPLIES = [
    "Anytime! Let me know if there's another AFL stat or matchup you want to check.",
    "You're welcome -- happy to help with more AFL stats or predictions whenever.",
]
_SOCIAL_FAREWELL_REPLIES = [
    "See ya! Come back anytime for more AFL stats or predictions.",
    "Bye for now -- happy to help again whenever.",
]

_THANKS_RE = re.compile(r"\b(thanks|thank you|cheers|ta)\b", re.IGNORECASE)
_FAREWELL_RE = re.compile(r"\b(bye|goodbye|see ya|see you|later|good night)\b", re.IGNORECASE)


def social_node(state) -> dict:
    from state import log_step
    query = state["query"]
    if _FAREWELL_RE.search(query):
        response = random.choice(_SOCIAL_FAREWELL_REPLIES)
    elif _THANKS_RE.search(query):
        response = random.choice(_SOCIAL_THANKS_REPLIES)
    else:
        response = random.choice(_SOCIAL_REPLIES)
    log_step(state, "social", query=query, message=response)
    return {"final_response": response}


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
        # Belt-and-suspenders: the client's own request timeout, in
        # addition to the wall-clock wrapper below. Neither alone is
        # enough -- a hung TCP connection needs the client-level timeout,
        # a slow-but-connected/streaming response needs the wrapper.
        timeout=8.0,
    )

    def _call():
        return llm.invoke([("system", SYSTEM_PROMPT), ("human", query)])

    try:
        # Task 1's hardening.py wraps every dataset tool call in a hard
        # timeout (see retrieval_node.py/prediction_node.py); this LLM
        # call was the one path that never got the same guarantee, which
        # is exactly what let a slow/hung gateway block a whole turn
        # indefinitely (e.g. a bare "hi" misrouted here -- now fixed
        # separately by router.py's social short-circuit, but this call
        # still needs its own ceiling for genuine factual questions).
        result = run_with_timeout(_call, timeout=10.0)
    except ToolTimeoutError:
        log_step(state, "direct_answer", query=query, used_llm=True, timed_out=True)
        return {"final_response": (
            "That's taking longer than expected to answer, so I've stopped "
            "rather than leave you waiting. Please try again in a moment."
        )}
    except Exception as e:
        log_step(state, "direct_answer", query=query, used_llm=True, error=f"{type(e).__name__}: {e}")
        return {"final_response": (
            "I couldn't reach the model for that general AFL question just now. "
            "Please try again in a moment."
        )}

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