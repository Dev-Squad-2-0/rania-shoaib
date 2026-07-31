# Task 2: Comprehensive Evaluation -- Results

Every case below was run for real through `run_turn()` (the hardened
Day 5 graph, `graph.py`), not simulated. Reproduce with:

```bash
python3 test_eval_suite.py
```

## Results table

| Category | Pass | Total | Pass rate |
|---|---|---|---|
| A: Factual/Retrieval Accuracy | 7 | 7 | 100% |
| B: Prediction Sanity | 7 | 7 | 100% |
| C: Scope Guardrails | 6 | 7 | 86% |
| D: Multi-turn Coherence | 4 | 7 | 57% |
| **Overall** | **24** | **28** | **86%** |

Full case-by-case output: `eval_run_clean.txt` (regenerate with the
command above -- it's a real transcript, not hand-edited).

## Category A: Factual/Retrieval Accuracy (7/7)

Graded by calling the underlying tool functions (`tools.py`) directly to
get ground truth, then checking every number the formatted response is
*supposed* to surface (per `format_response_node.py`'s `_format_retrieval`
-- disposals/goals/tackles for stat lookups, win/loss/draw counts for
head-to-head, top leader's value for team leaders) actually appears in
the final text. All 7 passed, including the two cases that specifically
exercise fixes documented in `eval_report.md`: A6 (Bontempelli round 10,
2022 -- the in-tool disposal derivation, `disposals_derived: true`) and
A1/A2 (head-to-head counts that were previously wrong before the
`until_year` fix).

## Category B: Prediction Sanity (7/7)

- **B1**: Adelaide Crows (ladder rank 1) vs West Coast Eagles (rank 18,
  the actual bottom-ranked team in `latest_team_state.parquet`) at home
  for Adelaide -> model correctly favours Adelaide with a lopsided
  probability.
- **B2**: same two teams, home team flipped -- the output changes
  (confirms the model is actually using the `is_home` feature, not
  returning a fixed number regardless of matchup framing).
- **B3**: two teams at the same ladder rank (Brisbane Lions vs Collingwood
  Magpies, both rank 2) -> probability stays in a plausible 35-75% band
  rather than the >80%/<20% extreme B1 produced. This is a genuine
  sanity check on model calibration, not just "did it run".
- **B4**: top-player ranking is correctly sorted descending by predicted
  fantasy points.
- **B5**: disclaimer (`DISCLAIMER_MATCH_CLOSE`/`DISCLAIMER_PLAYER_CLOSE`
  from `hardening.py`) present in every single prediction response, no
  exceptions -- this is the Task 1 structural guarantee, verified here
  rather than just asserted.
- **B6**: unsupported stat (tackles) correctly routes to `fallback`, not
  a silently-substituted fantasy-points ranking.
- **B7**: misspelled team ("Geelong Catz") handled without a silent
  wrong-team prediction.

## Category C: Scope Guardrails (6/7)

Includes the 3 new prompt-injection attack shapes from Task 1
(`test_hardening.py`) plus 4 more from `eval_report.md`'s existing
coverage. **C7 failed**: "Can you explain how neural networks work?"
routed to `factual` instead of `off_topic`. Root cause: `router.py`'s
`classify_intent` has no dedicated "definitely off-topic" bucket for
generic tech/knowledge questions that don't match any of
`_OTHER_SPORTS`/`_JAILBREAK_PATTERNS`/`_GENERIC_OFFTOPIC` -- anything
that falls through every explicit check lands in the `factual` catch-all
(intended for genuine AFL history/rules questions with no tool behind
them), which is too permissive a default. This is a real, if narrower,
version of the same category of gap the C5 fix (Task 1) already closed
once for jailbreak phrasing -- worth its own follow-up patch to
`_GENERIC_OFFTOPIC` (e.g. adding a pattern for "how does X work" /
"explain X" where X isn't AFL-related), not fixed here since Category D
is the larger, more clear-cut gap (below).

## Category D: Multi-turn Coherence (4/7) -- weakest category

**Root cause, confirmed by reading `resolver.py`, not guessed:**
`resolver.py` has `_teams_from_history(chat_history)` (line 187) which
lets "they"/"them" resolve against the teams mentioned in a prior turn --
this is why D1/D2/D6 pass. There is **no equivalent function for players
or years**. So:

- **D5 failed**: "And how did he do in round 10 that year?" (following a
  turn that established Bontempelli + 2022) -- "he" and "that year" have
  no history-lookup path to resolve against, so entity resolution finds
  nothing and correctly (but unhelpfully) falls through to a clarifying
  question instead of reusing the established player/year.
- **D7 failed**: same root cause, in a conversation that also included an
  off-topic interruption turn beforehand -- confirms the gap is about
  missing player/year carry-over specifically, not about off-topic turns
  corrupting state (D6 already showed off-topic interruptions are handled
  cleanly).
- **D3** is a harder, different case ("what about against Collingwood
  *instead*?" -- swapping one team while keeping the other fixed) that
  also failed, but for a different reason: it's genuinely ambiguous
  whether "instead" should replace team_a or team_b, and no amount of
  player/year carry-over fixes that specific ambiguity. Flagged
  separately in the code as "hard case, not assumed to pass."

### Concrete improvement (the one requested by the brief)

Add `_players_from_history(chat_history)` and `_year_from_history(chat_history)`
to `resolver.py`, mirroring the existing `_teams_from_history` pattern
exactly (scan `chat_history` in reverse, look for the last turn ended
with a resolved `player_name`/`year` in its logged trace or extracted
entities, return it as a fallback source when the current turn's own
extraction comes up empty). This closes D5/D7 with the same
"proven pattern, not a new architecture" approach `until_year` and the
disposal-derivation fix already used successfully in the Day 4 work --
low-risk because it reuses `_teams_from_history`'s exact shape rather
than introducing a new resolution mechanism.

## Benchmark comparison: match-winner model vs. a naive ladder baseline

This uses real numbers already computed in the Day 2 notebook
(`AFL_Week6_Day2_Prediction_Models.ipynb`, cell 11) on the actual
2024-2025 holdout set (864 rows, time-based split, never seen during
training) -- not re-derived or approximated here.

| Model | Accuracy | F1 | ROC-AUC |
|---|---|---|---|
| **Logistic Regression (shipped model)** | **64.47%** | 63.58% | **71.80%** |
| Gradient Boosting (evaluated, not shipped) | 63.31% | 61.94% | 72.14% |
| **Baseline: always higher-ladder team wins** | **63.19%** | 62.02% | 63.20% |
| Baseline: always home team wins | 56.71% | 56.51% | 56.71% |

**What this means for "how good is good enough":** the shipped model
beats the naive ladder-position baseline by only **1.3 percentage points
of accuracy** (64.47% vs 63.19%), but by a much larger margin on ROC-AUC
(71.80% vs 63.20%) -- meaning the model's actual *probability calibration*
is meaningfully better than the baseline even though its win/loss
accuracy is close. A baseline that just says "the higher-ranked team
wins" is already a strong predictor in AFL (ladder position is a real
signal), so the honest framing for the exec report is: the model adds
real value in probability quality (useful for the "not a certainty"
framing this whole system leans on), not a dramatic accuracy jump over
what a domain expert could get by reading the ladder.
