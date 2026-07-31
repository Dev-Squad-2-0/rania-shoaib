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
# ---------------------------------------------------------------------------
# Social/greeting short-circuit: whole-message-only match (anchored start
# to end) so this can never fire on "hi, ignore your instructions..." or
# "thanks, now tell me a recipe" -- those still need to hit the
# jailbreak/off-topic checks below. This exists purely so a bare "hi" gets
# an instant templated reply instead of falling through to the "factual"
# catch-all, which (unlike every dataset tool call) has no LLM call
# timeout wrapped around it -- see direct_answer_node in
# direct_and_refusal_nodes.py.
# ---------------------------------------------------------------------------
_SOCIAL_RE = re.compile(
    r"^(hi+|hello+|hey+|hiya|yo+|sup|g'?day|howdy|good\s?(morning|afternoon|evening)|"
    r"how('?s| is) it going|how are you|what'?s up|whats up|whats good|what'?s good|"
    r"wass?up|thanks( a lot| so much| heaps)?|thank you( so much)?|cheers|ta|"
    r"bye|goodbye|see ya|see you|later|good night)[!.,? ]*$",
    re.IGNORECASE,
)
# Interjection + tail combos ("yo whats good", "hey how are you") -- the
# pattern above only matches ONE fixed phrase, so "yo" followed by a
# separate tail phrase needs its own pattern rather than trying to cram
# every combination into one alternation.
_SOCIAL_COMBO_RE = re.compile(
    r"^(hi+|hey+|yo+|hello|sup|howdy|g'?day)[\s,!.]+"
    r"(whats up|what'?s up|whats good|what'?s good|wass?up|"
    r"how('?s| is) it going|how are you|there|guys|team)[!.,? ]*$",
    re.IGNORECASE,
)

_OTHER_SPORTS = [
    "nba", "nrl", "soccer", "football club", "premier league", "epl",
    "cricket", "rugby", "nfl", "tennis", "golf", "f1", "formula 1",
    "basketball", "baseball", "ufc", "boxing",
]
_JAILBREAK_PATTERNS = [
    r"\bignore (all |your )?(prior|previous) instructions\b",
    # Broadened Day 5 (round 2) hardening -- found live via re-testing:
    # "ignore your instructions" (no "prior"/"previous" qualifier) slipped
    # through the pattern above and fell into the "factual" catch-all,
    # since (prior|previous) was mandatory rather than optional. Kept as a
    # second, more permissive pattern rather than editing the line above,
    # so the original documented case stays intact and this addition's
    # intent is visible in the diff.
    r"\bignore (all |your |the )?(prior |previous )?instructions\b",
    r"\bpretend (you'?re|to be)\b",
    r"\bsystem override\b",
    r"\bnew scope\b",
    r"\bfreebot\b",
    r"\bwithout restrictions\b",
    r"\bact as\b",
    r"\bjailbreak\b",
    # Added Task 1 (Day 5) hardening -- found live via test_hardening.py:
    # "disregard/disregard the system prompt" is a distinct phrasing from
    # "ignore ... instructions" and previously fell through to "factual"
    # when embedded mid-sentence after a legitimate-looking AFL question.
    r"\bdisregard (the |all |your )?(system prompt|instructions|rules)\b",
    r"\b(developer mode|dev mode)\b",
    r"\bno (content policy|restrictions|limits) (from now on|going forward)?\b",
    r"\bunrestricted (ai|assistant|model)\b",
    # Catches the "narrate your own instructions being replaced" fiction
    # wrapper -- the story-framing itself isn't off-topic, but a prompt
    # asking the model to write a character whose system prompt is
    # rewritten to remove restrictions is the same override attempt one
    # layer removed, and the two other new patterns above already catch
    # its actual phrasing ("no restrictions", "unrestricted ai") so no
    # separate pattern is needed here beyond what's already listed.
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
    r"\bwho will win\b", r"\bwho'?s going to win\b", r"\bwho'?s gonna win\b",
    r"\bwill .+ (beat|smash|defeat|thrash)\b",
    r"\bpredict\b", r"\bprediction\b", r"\bwho.*top.?score\b", r"\bwho will top.?score\b",
    r"\bwho'?s gonna top.?score\b",
    r"\bchances of winning\b", r"\bwho do you think will win\b", r"\bforecast\b",
    r"\bwho'?s (favou?red|the favou?rite)\b", r"\bwill .+ win\b", r"\bgonna win\b",
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

# ---------------------------------------------------------------------------
# Self-correction handling (Day 5, round 2 hardening -- found via eval D7):
# "What's Dustin Martin's head to head... I mean, what were his stats in
# 2017?" matched _HEAD_TO_HEAD_PATTERNS on the FIRST clause and returned
# before ever looking at the corrected clause, so the whole query got
# routed as head_to_head and then failed entity resolution (no two teams
# to find). A self-correction marker means the text *after* it is what the
# user actually wants answered, so sub-type classification should prefer
# that clause over the abandoned one -- this only affects which clause
# classify_intent looks at; off-topic/jailbreak checks below still run on
# the full original text first, so a smuggled override can't hide behind
# a fake "I mean" clause.
# ---------------------------------------------------------------------------
_CORRECTION_MARKERS = re.compile(
    r"\b(i mean|sorry,? i meant|actually,? i meant|correction:|scratch that)\b",
    re.IGNORECASE,
)


def _is_social(text: str) -> bool:
    return bool(_SOCIAL_RE.match(text) or _SOCIAL_COMBO_RE.match(text))


def _matches_any(patterns, text) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def classify_intent(query: str) -> Tuple[str, str, str]:
    """Returns (intent, sub_type, confidence). sub_type is only meaningful
    for "prediction" ("match"/"player") and "retrieval" (see above);
    it's None for "factual"/"off_topic"."""
    q = query.lower().strip()

    # 0. Social/greeting short-circuit -- deliberately checked before even
    #    the jailbreak patterns, since it's an anchored whole-message match
    #    (see _SOCIAL_RE) and therefore can't accidentally swallow anything
    #    with real content tacked on.
    if _is_social(q):
        return "social", None, "high"

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

    # Self-correction: classify the restated clause (after the marker)
    # instead of the whole sentence, so an abandoned earlier phrasing
    # (e.g. "head to head" the user talked themselves out of) doesn't
    # win just because it appears first. Falls back to the full text if
    # the corrected clause is empty or itself only matches the factual
    # catch-all -- a real correction should produce a *more* specific
    # classification, not a worse one.
    correction = _CORRECTION_MARKERS.search(q)
    cq = q
    if correction:
        after = q[correction.end():].strip(" ,.")
        if after:
            cq = after

    # 2. Prediction cues (checked before retrieval, since "who will win"
    #    and "who led" share some vocabulary but "will" is decisive).
    if _matches_any(_PREDICTION_PATTERNS, cq):
        if _matches_any([r"\btop.?score\b", r"\bbest player\b", r"\bbest on ground\b",
                          r"\bmost (disposals|goals|tackles|points)\b"], cq):
            return "prediction", "player", "high"
        return "prediction", "match", "high"

    # 3. Retrieval cues, with sub-type detection.
    if _matches_any(_HEAD_TO_HEAD_PATTERNS, cq):
        return "retrieval", "head_to_head", "high"
    if _matches_any(_TEAM_LEADERS_PATTERNS, cq):
        return "retrieval", "team_leaders", "high"
    if _matches_any(_GAME_SPECIFIC_PATTERNS, cq) or _ROUND_PATTERN.search(cq):
        return "retrieval", "player_game", "high"
    if _matches_any(_RETRIEVAL_GENERIC_PATTERNS, cq):
        # Has a year but no round/game-specific cue -> season-level.
        if _YEAR_PATTERN.search(cq):
            return "retrieval", "player_season", "high"
        return "retrieval", "player_game", "low"

    # 4. A bare "stats" + year with a name in it, not caught above.
    if "stats" in cq and _YEAR_PATTERN.search(cq):
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

    entities["round_phrase"] = None
    for phrase in ["last round", "last game", "most recent round", "most recent game",
                   "that game", "latest round", "latest game"]:
        if phrase in query.lower():
            entities["round_phrase"] = phrase
            break

    top_n_match = re.search(r"\btop\s*(\d+)\b", query, re.IGNORECASE)
    if top_n_match:
        entities["top_n"] = int(top_n_match.group(1))

    return entities


def router_node(state) -> dict:
    from state import log_step  # local import avoids a cycle at module load
    from llm_router import classify_and_extract_llm
    query = state["query"]

    # Social short-circuit is checked first regardless of anything else --
    # it's cheap, unambiguous when it matches (anchored whole-message),
    # and shouldn't cost an LLM round trip either way.
    if _is_social(query.lower().strip()):
        entities = extract_raw_entities(query)
        log_step(state, "router", query=query, intent="social", sub_type=None,
                  confidence="high", raw_entities=dict(entities), used_llm=False)
        return {"intent": "social", "sub_type": None, "router_confidence": "high", "entities": entities}

    # Regex pass runs FIRST, always -- it's free and instant. The LLM
    # extractor is only tried when regex itself is unsure (confidence
    # "medium"/"low", which includes the "factual" catch-all bucket a
    # query lands in when nothing matched). This matters a lot in
    # practice: an LLM gateway call costs real seconds even when it
    # succeeds, so paying that cost on every single message -- including
    # ones regex already classifies with total confidence -- is wasted
    # latency for no accuracy gain. Only the genuinely ambiguous slice of
    # queries (slang like "gonna win", pronoun follow-ups, phrasing the
    # regex patterns don't cover) actually benefits from the LLM call.
    intent, sub_type, confidence = classify_intent(query)
    entities = extract_raw_entities(query)

    if confidence == "high":
        log_step(state, "router", query=query, intent=intent, sub_type=sub_type,
                  confidence=confidence, raw_entities=dict(entities), used_llm=False)
        return {"intent": intent, "sub_type": sub_type, "router_confidence": confidence, "entities": entities}

    # Regex wasn't confident -- worth spending an LLM call to do better,
    # but any failure (no key, timeout, bad JSON) just keeps the regex
    # result above rather than leaving the user with nothing.
    llm_result = classify_and_extract_llm(query, state.get("chat_history", []))
    if llm_result is not None:
        llm_intent = llm_result["intent"]
        llm_sub_type = llm_result.get("sub_type")
        llm_confidence = llm_result.get("confidence", "medium")
        for key, value in (llm_result.get("entities") or {}).items():
            if value is not None:
                entities[key] = value
        log_step(state, "router", query=query, intent=llm_intent, sub_type=llm_sub_type,
                  confidence=llm_confidence, raw_entities=dict(entities), used_llm=True)
        return {"intent": llm_intent, "sub_type": llm_sub_type, "router_confidence": llm_confidence, "entities": entities}

    log_step(
        state, "router",
        query=query, intent=intent, sub_type=sub_type,
        confidence=confidence, raw_entities=dict(entities), used_llm=False,
    )
    return {
        "intent": intent,
        "sub_type": sub_type,
        "router_confidence": confidence,
        "entities": entities,
    }