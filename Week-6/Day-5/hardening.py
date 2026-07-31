"""
hardening.py
=============

Task 1: System Hardening.

Three separate concerns, kept in one small module rather than scattered
across every node file, because "review and tighten the FULL pipeline"
means the fix has to be structural (one place, applied everywhere) or
it's not really a guarantee -- exactly the same argument Day 4 already
made for why the prediction disclaimer lives in format_response_node.py
instead of a prompt instruction.

1. TIMEOUTS -- `run_with_timeout()`
   Every tool call (pandas lookups in retrieval_node.py, sklearn inference
   in prediction_node.py) is currently a direct, unbounded function call.
   In this sandbox they're fast (in-memory dataframes, no network), but a
   production deployment might point AFL_DATA_DIR at a slower store, or
   swap the sklearn pipeline for something that calls out to a hosted
   model. Wrapping every tool call in a hard timeout means a single slow
   call degrades to a clear, user-facing "temporarily unavailable"
   message instead of hanging the whole request indefinitely.

2. CONSISTENT ERROR HANDLING -- `safe_node()`
   Before this, only retrieval_node.py and prediction_node.py had a
   try/except around their tool calls; router.py, resolver.py, and the
   formatting nodes had none. A regex crash in the router or a KeyError
   in format_response_node would previously propagate all the way up and
   crash the whole graph invocation with a raw traceback -- which is a
   bad failure mode for a chat product (the user sees nothing, not even
   a "something went wrong"). `safe_node` is a decorator applied to
   EVERY node when the graph is built (see graph.py), so no single node's
   bug can take down the whole turn: every node either returns its normal
   state update, or a graceful state update that still reaches the user
   as a real (if apologetic) message.

3. CONSISTENT DISCLAIMER LANGUAGE -- the DISCLAIMER_* constants
   format_response_node.py previously had the "not a certainty" wording
   typed out separately in _format_match_prediction and
   _format_player_prediction. Two independent copies of safety-critical
   text is exactly how wording drifts silently over time (one gets edited
   during a future feature request, the other doesn't). Centralizing
   both into one constant here means there is only one string to review,
   and both call sites import the same object.
"""

import concurrent.futures
import functools

# ---------------------------------------------------------------------------
# 1. Timeouts
# ---------------------------------------------------------------------------

# 5s is generous for what these calls actually do today (in-memory pandas
# filtering, a single sklearn .predict() call) -- the point isn't that
# they're close to this limit now, it's having a hard ceiling at all so a
# future slow data source or remote model call can't hang a turn forever.
TOOL_TIMEOUT_SECONDS = 5.0

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="afl-tool")


class ToolTimeoutError(Exception):
    """Raised (and always caught) when a tool call exceeds TOOL_TIMEOUT_SECONDS."""


def run_with_timeout(fn, *args, timeout=TOOL_TIMEOUT_SECONDS, **kwargs):
    """Runs fn(*args, **kwargs) on a worker thread with a hard wall-clock
    timeout. Returns fn's result normally, or re-raises ToolTimeoutError
    if it didn't finish in time. Callers are expected to catch
    ToolTimeoutError alongside whatever domain exceptions the tool itself
    raises (e.g. AFLPredictionError) -- see retrieval_node.py /
    prediction_node.py for the call sites.

    Threads (not multiprocessing) because these tool calls share
    in-memory, already-loaded dataframes/models (data_loader.py's
    lru_cache, predict.py's module-level pipeline objects) -- forking a
    process per call would either lose that cache or require re-pickling
    large dataframes across a process boundary on every single turn.
    """
    future = _EXECUTOR.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise ToolTimeoutError(
            f"'{getattr(fn, '__name__', fn)}' did not respond within {timeout:.0f}s."
        )


# ---------------------------------------------------------------------------
# 2. Consistent error handling across every node
# ---------------------------------------------------------------------------

def safe_node(node_fn):
    """Wraps a LangGraph node function so an unhandled exception anywhere
    inside it can't crash the whole graph invocation. On failure, logs the
    exception into the trace (so it's visible in monitoring/logs, not
    swallowed silently) and returns a state update that routes the SAME
    way a tool-level error already does: a plain-language message, never
    a raw traceback reaching the user.

    Applied to every node at graph-build time (see graph.py's
    build_graph), not selectively -- "consistent" error handling means
    every node gets the same guarantee, not just the two that already had
    a try/except before this file existed.
    """

    @functools.wraps(node_fn)
    def wrapped(state):
        from state import log_step
        try:
            return node_fn(state)
        except Exception as e:  # noqa: BLE001 -- deliberately broad: this is the last line of defense
            log_step(state, node_fn.__name__, error=f"{type(e).__name__}: {e}", unhandled=True)
            return {
                "final_response": (
                    "Something went wrong on my end processing that request. "
                    "Please try rephrasing, or ask again in a moment."
                ),
                "validation": {"status": "error", "reason": f"{type(e).__name__}: {e}"},
            }

    return wrapped


# ---------------------------------------------------------------------------
# 3. Single source of truth for prediction disclaimer language
# ---------------------------------------------------------------------------

# Used verbatim by both _format_match_prediction and _format_player_prediction
# in format_response_node.py, so the two paths can never drift apart.
DISCLAIMER_LEAD_IN = "**Prediction (probabilistic, not a certainty):**"

DISCLAIMER_MATCH_CLOSE = (
    "This is a model estimate based on recent form and history, not a "
    "guarantee -- AFL results have real week-to-week variance the model doesn't capture."
)

DISCLAIMER_PLAYER_CLOSE = (
    "Fantasy scoring is genuinely volatile week to week -- treat this as a "
    "form-based estimate, not a lock."
)

# Shown whenever a tool call timed out (retrieval or prediction) -- kept
# separate from the "clarify" and "fallback" wording in fallback_nodes.py
# because a timeout is not the user's fault and not a capability gap; it's
# a transient system issue, and should read that way rather than sounding
# like the user did something wrong.
TIMEOUT_USER_MESSAGE = (
    "That took longer than expected to look up, so I've stopped rather than "
    "leave you waiting. Please try again -- this is a transient issue, not a "
    "problem with your question."
)
