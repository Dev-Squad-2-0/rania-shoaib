"""
fallback_nodes.py
===================

Task 4: the two "don't guess" exit paths.

clarify_node fires either before a tool was ever called (resolve_entities
found zero/ambiguous matches for a team, player, or required year/round)
or after (the tool itself found nothing, or predict.py rejected an out of
range date). Either way the fix is the same shape: tell the user
specifically what's missing or ambiguous, and stop -- don't pick a
candidate on their behalf.

fallback_node fires when the request is a genuine capability gap: a stat
type no model covers, a retrieval sub-type with no matching tool. This is
different from clarify because no follow-up question would help -- the
honest answer is "out of scope," stated plainly.
"""


def clarify_node(state) -> dict:
    from state import log_step
    issues = state.get("_resolution_issues") or []
    reason = (state.get("validation") or {}).get("reason")

    if issues:
        parts = []
        for issue in issues:
            field, err = issue.get("field"), issue.get("error")
            candidates = issue.get("candidates")
            if candidates:
                parts.append(f"'{field}' is ambiguous -- did you mean one of: {', '.join(candidates[:6])}?")
            else:
                parts.append(err)
        message = " ".join(parts)
    elif reason:
        message = reason
    else:
        message = "I need a bit more detail to answer that."

    response = f"I want to make sure I get this right rather than guess: {message}"
    log_step(state, "clarify", message=response)
    return {"final_response": response}


def fallback_node(state) -> dict:
    from state import log_step
    reason = (state.get("validation") or {}).get("reason") or "That's outside what my tools currently cover."
    response = (
        f"That's outside what I can do right now: {reason} "
        "I can share what I do cover instead (match winner predictions, "
        "top fantasy scorer predictions, and real recorded stats/records) "
        "if that helps."
    )
    log_step(state, "fallback", message=response)
    return {"final_response": response}
