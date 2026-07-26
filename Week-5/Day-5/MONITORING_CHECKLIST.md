# Monitoring Checklist — TraqCheck-Lite

## What to track in production

| Metric | What it catches | Where it comes from |
|---|---|---|
| Error rate (% of runs ending in `error`/`crashed`) | Tool failures, malformed LLM output, timeouts | `logs/app.log`, ERROR-level entries |
| Latency per run (p50/p95) | Slow Groq responses, tool bottlenecks | timestamps around `run_verification` in `graph.py` |
| Cost drift (tokens per run over time) | Prompt bloat, model switch, runaway retries | crew token usage (add `crew.usage_metrics` logging if not already present) |
| Discrepancy-flag rate | Sudden spike/drop may mean a broken tool, not real world change | aggregate `report["flags"]` across runs |
| Human override rate (approve vs reject vs more-info) | If reviewers reject almost everything, the agent's report quality has drifted | `/background-check/{id}/decision` logs |
| Not-found rate | Registry data going stale, or fuzzy-match threshold too strict/loose | `verify_employment`/`verify_education` `not_found` responses |
| Prompt-injection flag rate | Whether adversarial reference notes are increasing or whether the safety flag stops firing | `report["flags"]` containing "suspicious"/"injection" |

## Alert thresholds (starting points — tune after 2-4 weeks of real data)

- Error rate > 5% of runs in a rolling 1-hour window → page on-call
- p95 latency > 15 seconds → investigate (Groq rate limiting is the most likely cause)
- Human rejection rate > 30% over a rolling 50 runs → the report quality has likely regressed, pause and re-evaluate prompts
- Not-found rate suddenly > 2x baseline → check if registry data source changed/broke
- Any single run where risk_level is "low" AND flags contains injection-related text → this is a contradiction that should never happen; alert immediately, it means the safety instruction is being ignored

## Re-evaluation cadence

- **Weekly**: spot-check 5-10 real runs against the eval test-case rubric (Task 3 criteria)
- **On any prompt/model change**: rerun the full `eval/run_eval.py` suite before deploying
- **Monthly**: regenerate a fresh batch of adversarial test cases (new injection phrasings) since attackers adapt faster than a static test suite
- **Quarterly**: re-check whether the fuzzy-match threshold (currently 85) is still producing the right balance of catching typos vs over-matching different people
