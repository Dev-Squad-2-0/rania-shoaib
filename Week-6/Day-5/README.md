# Week 6 Day 4: LangGraph Integration

Connects Day 3's chat/retrieval agent and Day 2's prediction models through
a LangGraph state machine that routes between factual answers, stat
retrieval, and predictions, with explicit validation and clarification
steps rather than a single model freely deciding everything.

## Quick start

```bash
pip install -r requirements.txt
python3 graph.py "Will the Pies beat the Cats this week?"
python3 test_router_accuracy.py    # Task 2 accuracy table
python3 test_e2e.py                # Task 5 conversations + traces/
```

No API key is required for prediction, retrieval, clarification, or
fallback paths -- those are all deterministic code. The one path that
needs a live model is `factual` (general AFL history/rules questions with
no dataset tool behind them); set `GATEWAY_API_KEY` (same variable Day
3's `agent.py` uses) to enable it. Without it, that node still routes
correctly, it just returns a note saying the key is unset instead of a
generated answer -- this mirrors `agent.py`'s own behavior on a missing key.

---

## Task 1: State schema and graph design

### State schema (`state.py`)

| field | purpose |
|---|---|
| `query`, `session_id`, `chat_history` | the current turn, and prior turns for follow-ups |
| `intent`, `sub_type`, `router_confidence` | router's decision: which of prediction/retrieval/factual/off_topic, and which flavor |
| `entities` | raw + resolved slots: team_a/team_b, player_name, year, round_number, top_n, resolved_date |
| `tool_name`, `tool_result` | which function actually ran, and what it returned (or `{"error": ...}`) |
| `validation` | `{"status": "ok"/"clarify"/"fallback", "reason": ...}` |
| `final_response` | what the user sees |
| `trace` | one entry per node visited, in order -- the basis for Task 5's annotated traces |

`entities` is a loose dict rather than a fixed pydantic model because
different intents need different slots; forcing every field onto one
rigid schema would mean most fields are `None` most of the time for no
benefit.

### Graph sketch

```
START
  |
router --------------------------------------------+
  |                                                 |
  | off_topic                  factual              |
  v                              v                  |
refusal --> END           direct_answer --> format_response --> END
  |
  | retrieval / prediction
  v
resolve_entities
  |
  +-- unresolved/ambiguous team, player, year, round
  |        v
  |     clarify --> END
  |
  +-- resolved OK
       +-- retrieval  --> retrieval_tool  --+
       +-- prediction --> prediction_tool --+
                                             v
                                          validate
                                             |
                        +--------------------+--------------------+
                     status=ok           status=clarify      status=fallback
                        v                    v                     v
                 format_response--> END   clarify--> END      fallback--> END
```

(This is also inlined as a comment at the top of `graph.py`, next to the
actual `StateGraph` wiring, so the diagram and the code can't drift apart.)

### Why explicit routing, not one free-roaming agent

A single tool-calling agent decides, fresh, on every turn, whether to
call a tool and how to frame the answer. That's an acceptable risk for
retrieval (either the tool got called and the answer is grounded, or it
didn't -- easy to check after the fact). It's a much bigger problem for
predictions specifically, because "always frame as probabilistic, never
certain" is a *global invariant*, not something that should depend on
what the model decides to prioritize on a given turn. An agent that
remembers the disclaimer 95% of the time will still, 5% of the time,
produce a confident-sounding prediction with no code path stopping it.

With explicit routing, every prediction passes through exactly one node
(`format_response_node.py`) before reaching the user, and the disclaimer
and probability framing are written into that node's template, not into
a prompt instruction competing with everything else the model has been
told. The guarantee is structural, not statistical.

---

## Task 2: Router node

`router.py` classifies intent with regex/keyword patterns rather than a
live LLM call -- the brief allows either, and this sandbox has no route
to an LLM gateway (no `GATEWAY_API_KEY`, no network path to one), so a
rule-based classifier is what's actually testable end to end right now.
It's also fully deterministic, which is what makes a clean accuracy table
possible in the first place.

**Result: 20/20 (100%) after two rounds of fixes.** Run
`python3 test_router_accuracy.py` to reproduce.

First pass was 18/20. The two misroutes and their fixes:

| query | first-pass result | issue | fix |
|---|---|---|---|
| "Who's most likely to be best on ground for Sydney next round?" | factual | no pattern covered "most likely" / "best on ground" phrasing | added both as prediction/player cues |
| "Who was the Bulldogs' leading disposal getter in 2021?" | factual | `_TEAM_LEADERS_PATTERNS` only covered "leading goalkicker" literally | broadened to `leading .* (getter\|scorer\|tackler)` |

---

## Task 3: Prediction models as graph tools

`predict.py` (the Day 2 module) is used unmodified. `prediction_node.py`
wraps it and adds two things the raw functions don't provide:

**Input resolution** (`resolver.py`):
- Team nicknames ("Pies", "Cats", "Dons"...) and full-name phrase variants
  are matched via an explicit phrase table, checked longest-phrase-first
  so multi-word names like "Port Adelaide" are matched before the
  ambiguous lone "Adelaide" (which is also a substring of "Port
  Adelaide" -- this was a real bug caught in testing, see below).
- Player names are extracted from capitalized n-grams in the query
  (stripping possessive `'s` -- also a real bug caught in testing) and
  checked against the real player table.
- Dates: since the dataset has **no future fixture list** (it ends at
  the last recorded match, `date_range.joblib`), "this week" is grounded
  against a real `get_current_date()` tool call rather than the
  dataset's own historical range, and `predict.py`'s documented behavior
  (using each team's/player's most recently recorded rolling stats
  regardless of requested date) is surfaced to the user as an explicit
  caveat rather than left implicit.

**Grounding explanation, computed per-prediction, not just from training-time
feature importances:**
- *Match winner*: reaches into the fitted `LogisticRegression` pipeline,
  recomputes the same preprocessed feature vector the real prediction
  used, multiplies by the model's coefficients, and reports the top 3
  contributions by magnitude. This is genuinely instance-specific --
  different matchups surface different driving features.
- *Top player*: the underlying `HistGradientBoostingRegressor` has no
  simple per-instance coefficients, and per-call permutation importance
  is too slow to run on every turn, so this reports each top-ranked
  player's actual `fantasy_last5` value -- the real number behind their
  ranking, consistent with Day 2's own finding that this feature
  dominates by roughly an order of magnitude.

Every prediction response (built in `format_response_node.py`) states the
probability and an explicit "not a certainty" framing, plus a real-variance
caveat -- this is the disclaimer discussed in Task 1, enforced in code.

---

## Task 4: Self-correction and fallback

`validation_node.py` runs after every retrieval/prediction tool call and
produces one of three outcomes:

- **ok**: proceed to `format_response`.
- **clarify**: a team/player couldn't be resolved uniquely (zero or
  multiple matches), or a required field (year, round) was never
  specified. Handled by `fallback_nodes.clarify_node`, which names
  exactly what's missing/ambiguous rather than guessing a default.
  Resolution failures are actually caught *before* the tool is even
  called, in `resolve_entities_node` -- there's a conditional edge
  straight from entity resolution to `clarify` for exactly this reason.
- **fallback**: the request is a genuine capability gap (e.g. asking to
  predict a stat type -- tackles, disposals -- that `predict_top_player`
  doesn't model; it only ranks by predicted fantasy points). No
  clarifying question fixes this, so the honest response states the
  limitation plainly instead of silently substituting the fantasy-points
  ranking as if that's what was asked.

Two real bugs were caught by exercising this path during testing (both
now fixed, see `resolver.py` and `prediction_node.py`):
1. "Adelaide" as a substring inside "Port Adelaide" made the resolver
   report a false ambiguity between Adelaide Crows and Port Adelaide
   Power even when "Port Adelaide" was said outright -- fixed by
   matching phrases longest-first and masking matched spans.
2. "Dustin Martin's stats" failed to resolve because the possessive `'s`
   was included in the extracted token and never stripped before
   matching against the player table -- fixed with a regex strip.

---

## Task 5: End-to-end testing

`test_e2e.py` runs 12 conversations (`python3 test_e2e.py`) covering:
retrieval (all 4 sub-types), prediction (match + player), off-topic
(direct + jailbreak), clarification (missing year, unresolvable team),
fallback (unsupported stat), and a multi-turn follow-up ("they" resolved
against the previous turn's teams via `chat_history`).

Three annotated state traces are saved to `traces/`:
- `traces/prediction_-_match.md`
- `traces/retrieval_-_player_season_stats.md`
- `traces/clarification_-_ambiguous_missing_year.md`

Each shows the full router -> resolve_entities -> tool -> validate ->
format_response path with the actual data at each step.

### LangGraph vs. one monolithic agent -- what actually improved

The single biggest difference isn't speed or code size, it's
*guaranteed structure*: every prediction passes through exactly one
formatting node before reaching the user, so the "probabilistic, not
certain" framing is a property of the code path, not a hope about what
the model chose to include this time. A monolithic agent can also call
the wrong tool, or no tool, for an ambiguous query and there's no
separate checkpoint to catch it -- here, `resolve_entities` and
`validate` are dedicated stops where an unresolved team/player/year is
caught and turned into a clarifying question *before* any answer is
generated, instead of the model quietly guessing or refusing outright.
The tradeoff is real too: this graph can only do what its explicit
branches anticipated (an entirely new intent needs a new branch, not
just a new tool description), where a general agent could improvise
around a request nobody planned for.

## File map

| file | task |
|---|---|
| `state.py` | Task 1: state schema |
| `graph.py` | Task 1: graph wiring / Task 4: validation routing |
| `router.py` | Task 2: intent classifier |
| `test_router_accuracy.py` | Task 2: accuracy table |
| `resolver.py` | Task 3: team/player/date resolution |
| `prediction_node.py` | Task 3: prediction tools + grounding |
| `retrieval_node.py` | Task 3: retrieval tools (Day 3's `tools.py`) |
| `validation_node.py`, `fallback_nodes.py` | Task 4 |
| `direct_and_refusal_nodes.py`, `format_response_node.py` | factual/off-topic branches, response formatting |
| `test_e2e.py`, `traces/` | Task 5 |
| `predict.py`, `artifacts/` | Day 2, unmodified |
| `tools.py`, `data_loader.py`, `data/`, `system_prompt.py` | Day 3, unmodified |
