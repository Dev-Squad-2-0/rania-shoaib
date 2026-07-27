"""
The outer control flow. LangGraph owns this because the process is genuinely
sequential/conditional with a hard pause for human approval -- exactly the
shape LangGraph is designed for, versus CrewAI's strength in role-based
collaboration (used inside run_verification, one node below).

Flow:
    validate_input -> run_verification -> human_review (pauses here) -> finalize
Either of the first two nodes can short-circuit straight to END on failure.
"""

import time
import logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

from crew import run_verification

logger = logging.getLogger("traqcheck")


class CheckState(TypedDict, total=False):
    name: str
    claimed_company: str
    claimed_title: str
    claimed_institution: str
    claimed_degree: str
    consent_given: bool
    error: Optional[str]
    report: Optional[dict]
    human_decision: Optional[str]
    final_status: Optional[str]


REQUIRED_FIELDS = [
    "name", "claimed_company", "claimed_title",
    "claimed_institution", "claimed_degree", "consent_given"
]


def validate_input(state: CheckState) -> CheckState:
    missing = [f for f in REQUIRED_FIELDS if f not in state or state[f] in (None, "")]
    if missing:
        msg = f"Rejected: missing required field(s): {', '.join(missing)}."
        logger.warning(msg)
        return {"error": msg, "final_status": "rejected_bad_input"}

    if state["consent_given"] is not True:
        msg = "Rejected: candidate consent is required before any check can run."
        logger.warning(msg)
        return {"error": msg, "final_status": "rejected_no_consent"}

    return {"error": None}


def run_verification_node(state: CheckState) -> CheckState:
    start = time.perf_counter()
    try:
        report = run_verification(state)
        elapsed = time.perf_counter() - start
        token_usage = report.pop("_token_usage", {})
        logger.info(f"Verification completed for '{state['name']}' in {elapsed:.2f}s. "
                     f"Risk level: {report.get('risk_level')}. Token usage: {token_usage}")
        return {"report": report}
    except Exception as exc:
        elapsed = time.perf_counter() - start
        logger.error(f"Verification failed for '{state['name']}' after {elapsed:.2f}s: {exc}")
        return {"error": f"Verification could not be completed: {exc}", "final_status": "error"}


def human_review_node(state: CheckState) -> CheckState:
    decision = interrupt({
        "message": "Review the proposed background check report before release.",
        "report": state["report"],
    })
    return {"human_decision": decision.get("decision", "reject")}


def finalize_node(state: CheckState) -> CheckState:
    decision = state.get("human_decision", "reject")
    mapping = {
        "approve": "released",
        "reject": "rejected_by_reviewer",
        "request_more_info": "sent_back_for_more_info",
    }
    status = mapping.get(decision, "rejected_by_reviewer")
    logger.info(f"Final status for '{state['name']}': {status}")
    return {"final_status": status}


def route_after_validate(state: CheckState) -> str:
    return END if state.get("error") else "run_verification"


def route_after_verification(state: CheckState) -> str:
    return END if state.get("error") else "human_review"


def build_graph():
    graph = StateGraph(CheckState)
    graph.add_node("validate_input", validate_input)
    graph.add_node("run_verification", run_verification_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("validate_input")
    graph.add_conditional_edges("validate_input", route_after_validate)
    graph.add_conditional_edges("run_verification", route_after_verification)
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=MemorySaver())