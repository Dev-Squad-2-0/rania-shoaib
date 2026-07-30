"""
router.py
==========

Task 2: The router node.

## Why a lightweight rule-based classifier, not an LLM call

This graph is built to run inside the same sandbox used to build it,
which has no route to an LLM gateway (no GATEWAY_API_KEY, no network path
to an inference endpoint). The task brief explicitly allows either option
("a small LLM call with structured output, or a lightweight classifier"),
so this router is a deterministic keyword/regex classifier: it is fully
testable right now, gives 100% reproducible routing decisions (useful for
the accuracy table below), and has no latency or cost per turn.

If you run this with a GATEWAY_API_KEY set (same env var agent.py from
Day 3 uses), swap `classify_intent` below for an LLM structured-output
call -- the rest of the graph doesn't care which one produced the
(intent, sub_type, entities) tuple, since `router_node` is the only place
that decision gets made.

## Intent taxonomy

- "prediction" : asking the model to forecast a future/hypothetical
  outcome ("who will win", "who's going to top-score"). Always routes to
  the prediction node, which must attach a probability + disclaimer.
- "retrieval"  : asking for a real, already-recorded stat or record
  ("what were his stats", "head-to-head record", "who led the team in
  goals"). Routes to a direct dataset lookup, never a guess.
- "factual"    : general AFL knowledge that no tool covers (club history,
  rules, competition structure) -- handled by a constrained LLM call with
  no numeric specifics, per Day 3's SYSTEM_PROMPT rules.
- "off_topic"  : anything outside AFL scope, including jailbreak/role-play
  attempts, reusing Day 3's ADVERSARIAL_PROMPTS patterns as a reference
  set for what this needs to catch.

## Why explicit routing beats a free-roaming agent here (Task 1 justification)

A single generic tool-calling agent decides on every turn, via its own
free-form reasoning, whether to call a tool and how to frame the answer.
That is fine for retrieval (either the tool was called and grounded, or it
wasn't -- Day 3's grounding checks exist for exactly this reason) but it
is a much bigger problem for predictions specifically: the "always frame
as probabilistic, never certain" rule is a *global invariant*, not
something that should depend on the model's mood on a given turn. An
agent that is 95% reliable about adding the disclaimer will still produce
a 5%-of-the-time confident-sounding prediction, and there is no code path
forcing it back into line. Routing explicitly means the disclaimer and
probability framing live in the formatting node's code, not in a prompt
instruction the model could forget or be talked out of -- structurally
guaranteed instead of merely likely.
"""

import re
from typing import Tuple, Dict, Any


# ---------------------------------------------------------------------------
# Off-topic detection: reuses the same attack surface Day 3's
# ADVERSARIAL_PROMPTS already probes (other sports, jailbreak/role-play,
# instruction override, general chit-chat) plus a few generic categories.
# ---------------------------------------------------------------------------
_OTHER_SPORTS = [
    "nba", "nrl", "soccer", "football club", "premier league", "epl",
    "cricket", "rugby", "nfl", "tennis", "golf", "f1", "formula 1",
    "basketball", "baseball", "ufc", "boxing",
]
_JAILBREAK_PATTERNS = [
    r"\bignore (all |your )?(prior|previous) instructions\b",
    r"\bpretend (you'?re|to be)\b",
    r"\bsystem override\b",
    r"\bnew scope\b",
    r"\bfreebot\b",
    r"\bwithout restrictions\b",
    r"\bact as\b",
    r"\bjailbreak\b",
]
_GENERIC_OFFTOPIC = [
    r"\brecipe\b", r"\bpizza dough\b", r"\bbanana bread\b",
    r"\bstock(s|broker)?\b", r"\bday trading\b", r"\bpython script\b",
    r"\bscrape\b", r"\bweather\b", r"\bfavorite movie\b", r"\bmy day\b",
    r"\bwrite (a|an) (poem|essay|email)\b",
]
_BETTING = [r"\bbet\b", r"\bwager\b", r"\bodds\b(?! of winning)", r"\bhow much should i bet\b"]

# ---------------------------------------------------------------------------
# Prediction (forward-looking / hypothetical) cues
# ---------------------------------------------------------------------------
_PREDICTION_PATTERNS = [
    r"\bwho will win\b", r"\bwho'?s going to win\b", r"\bwill .+ (beat|smash|defeat|thrash)\b",
    r"\bpredict\b", r"\bprediction\b", r"\bwho.*top.?score\b", r"\bwho will top.?score\b",
    r"\bchances of winning\b", r"\bwho do you think will win\b", r"\bforecast\b",
    r"\bwho'?s (favou?red|the favou?rite)\b", r"\bwill .+ win\b",
    r"\btop scorer (this week|next week|this round)\b",
    r"\bbest player (this week|next round)\b", r"\bwho will get the most\b",
    r"\bmost likely\b", r"\bwho'?s most likely\b",
]

# ---------------------------------------------------------------------------
# Retrieval (already-recorded stat / record) cues, with sub-type hints
# ---------------------------------------------------------------------------
_HEAD_TO_HEAD_PATTERNS = [
    r"\bhead.to.head\b", r"\bh2h\b", r"\brecord against\b", r"\bhistory against\b",
    r"\bhow many times has\b", r"\bbeaten\b.+\btimes\b",
]
_TEAM_LEADERS_PATTERNS = [
    r"\bwho led\b", r"\bleading goalkicker\b", r"\btop(ped)? .+ in (goals|disposals|tackles)\b",
    r"\bwho topped\b", r"\bleading\b.*\b(getter|scorer|tackler)\b",
]
_ROUND_PATTERN = re.compile(r"\bround\s*([a-z0-9]+)\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
_GAME_SPECIFIC_PATTERNS = [r"\blast round\b", r"\bthat game\b", r"\bin round\b"]

_RETRIEVAL_GENERIC_PATTERNS = [
    r"\bwhat were\b.+\bstats\b", r"\bhow many (disposals|kicks|marks|goals|tackles|handballs)\b",
    r"\bstats (in|for|last)\b", r"\bstat line\b",
]


def _matches_any(patterns, text) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def classify_intent(query: str) -> Tuple[str, str, str]:
    """Returns (intent, sub_type, confidence). sub_type is only meaningful
    for "prediction" ("match"/"player") and "retrieval" (see above);
    it's None for "factual"/"off_topic"."""
    q = query.lower().strip()

    # 1. Off-topic checks first -- a jailbreak attempt embedded inside an
    #    AFL-sounding question should still be caught (Day 3's "smuggled"
    #    adversarial case), so this runs before anything else.
    if _matches_any(_JAILBREAK_PATTERNS, q):
        return "off_topic", None, "high"
    if _matches_any(_BETTING, q):
        return "off_topic", "betting", "high"
    if _matches_any(_GENERIC_OFFTOPIC, q):
        return "off_topic", None, "high"
    if _matches_any(_OTHER_SPORTS, q):
        return "off_topic", None, "high"

    # 2. Prediction cues (checked before retrieval, since "who will win"
    #    and "who led" share some vocabulary but "will" is decisive).
    if _matches_any(_PREDICTION_PATTERNS, q):
        if _matches_any([r"\btop.?score\b", r"\bbest player\b", r"\bbest on ground\b",
                          r"\bmost (disposals|goals|tackles|points)\b"], q):
            return "prediction", "player", "high"
        return "prediction", "match", "high"

    # 3. Retrieval cues, with sub-type detection.
    if _matches_any(_HEAD_TO_HEAD_PATTERNS, q):
        return "retrieval", "head_to_head", "high"
    if _matches_any(_TEAM_LEADERS_PATTERNS, q):
        return "retrieval", "team_leaders", "high"
    if _matches_any(_GAME_SPECIFIC_PATTERNS, q) or _ROUND_PATTERN.search(q):
        return "retrieval", "player_game", "high"
    if _matches_any(_RETRIEVAL_GENERIC_PATTERNS, q):
        # Has a year but no round/game-specific cue -> season-level.
        if _YEAR_PATTERN.search(q):
            return "retrieval", "player_season", "high"
        return "retrieval", "player_game", "low"

    # 4. A bare "stats" + year with a name in it, not caught above.
    if "stats" in q and _YEAR_PATTERN.search(q):
        return "retrieval", "player_season", "medium"

    # 5. Default: AFL-flavoured question with no tool cue -> factual/history.
    #    (Confidence marked low/medium since this is the catch-all bucket;
    #    Task 2's test table is where these get checked against reality.)
    return "factual", None, "medium"


def extract_raw_entities(query: str) -> Dict[str, Any]:
    """Cheap regex-level extraction, run regardless of intent. Team/player
    name resolution against the real dataset happens later in
    resolver.py -- this just pulls out the literal substrings/numbers a
    human would point to in the sentence."""
    entities: Dict[str, Any] = {}

    years = _YEAR_PATTERN.findall(query)
    if years:
        # findall on a group-only regex returns the group text ("19"/"20");
        # re-extract full matches instead.
        full_years = re.findall(r"\b(?:19|20)\d{2}\b", query)
        entities["year"] = int(full_years[-1]) if full_years else None

    round_match = _ROUND_PATTERN.search(query)
    if round_match:
        entities["round_number"] = round_match.group(1)

    entities["date_phrase"] = None
    for phrase in ["this week", "this weekend", "next round", "next week", "upcoming",
                   "last year", "this season", "this year", "next season"]:
        if phrase in query.lower():
            entities["date_phrase"] = phrase
            break

    top_n_match = re.search(r"\btop\s*(\d+)\b", query, re.IGNORECASE)
    if top_n_match:
        entities["top_n"] = int(top_n_match.group(1))

    return entities


def router_node(state) -> dict:
    from state import log_step  # local import avoids a cycle at module load
    query = state["query"]
    intent, sub_type, confidence = classify_intent(query)
    entities = extract_raw_entities(query)

    log_step(
        state, "router",
        query=query, intent=intent, sub_type=sub_type,
        confidence=confidence, raw_entities=dict(entities),
    )
    return {
        "intent": intent,
        "sub_type": sub_type,
        "router_confidence": confidence,
        "entities": entities,
    }
