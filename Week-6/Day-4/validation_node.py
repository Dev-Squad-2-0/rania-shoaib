"""
validation_node.py
====================

Task 4: the self-correction gate every tool call passes through before a
response gets formatted. Nothing from retrieval_node/prediction_node
reaches the user directly -- this is the one place that decides whether
the tool's output is trustworthy enough to answer from, needs a
clarifying question, or is a flat "not something I can do" case.

Three outcomes, deliberately kept distinct rather than collapsed into a
single boolean:

- "ok"       : tool returned a real result. Proceed to formatting.
- "clarify"  : the tool (or resolve_entities_node before it) couldn't
               pin down a team/player/season uniquely, or found nothing
               for the combination given. The right move is to ask the
               user, not to guess which candidate they meant or silently
               assume a default -- guessing here is exactly the failure
               mode Task 4 calls out.
- "fallback" : the request is outside what any tool models (e.g. asking
               to predict a stat type no model covers). No amount of
               clarification fixes this -- the honest answer is "this
               isn't something I can do," not a best-effort guess dressed
               up as a real prediction.
"""

_UNSUPPORTED_MARKERS = ["Unsupported retrieval type", "Unsupported prediction type",
                         "is not a supported stat for player prediction"]


def validate_node(state) -> dict:
    from state import log_step
    result = state.get("tool_result") or {}
    reason = None
    status = "ok"

    if "error" in result:
        error_text = str(result["error"])
        if any(marker in error_text for marker in _UNSUPPORTED_MARKERS):
            status, reason = "fallback", error_text
        else:
            # Unknown team/player, ambiguous match, date out of range, etc.
            # -- all recoverable by asking the user, so route to clarify
            # rather than fabricating an answer around the gap.
            status, reason = "clarify", error_text
    elif "note" in result and result.get("matches_found", None) == 0:
        status, reason = "clarify", result["note"]
    elif isinstance(result, dict) and result.get("note") and "No" in str(result.get("note", "")):
        status, reason = "clarify", result["note"]

    log_step(state, "validate", status=status, reason=reason)
    return {"validation": {"status": status, "reason": reason}}
