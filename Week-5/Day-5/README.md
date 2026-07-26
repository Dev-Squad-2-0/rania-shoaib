# TraqCheck-Lite

A simplified background-check agent: candidate submits claims → verification
crew checks them against a synthetic registry → human reviewer approves
before the report is released. Built for Week 5 Day 5 capstone.

**Important:** all data is synthetic (generated with Faker, fixed seed).
Real background checks require licensed data providers and FCRA-compliant
consent flows — this project demonstrates the *agent architecture*, not a
production-ready compliance system. See "Known Limitations" below.

## Architecture

```
Candidate submission (name, claimed employment, claimed education, consent)
            |
            v
   [validate_input]  --- missing fields / no consent ---> END (rejected)
            |
            v
   [run_verification]  <-- CrewAI crew runs here -->
       - Employment Verifier agent  (tool: query registry.db)
       - Education Verifier agent   (tool: query registry.db)
       - Reference & Risk Analyst   (tool: fetch reference note, aggregate
         findings into risk_level: low/medium/high)
            |
            v
   [human_review]  <-- LangGraph interrupt, pauses here -->
       Reviewer sees proposed_report, calls /decision with
       approve / reject / request_more_info
            |
            v
   [finalize] --> final_status: released / rejected_by_reviewer /
                                 sent_back_for_more_info
```

**Why this framework split:** LangGraph owns the outer flow because the
process is genuinely sequential/conditional and needs to *pause and persist
state* across a human approval step -- that's LangGraph's core strength.
CrewAI runs inside the `run_verification` node because that step benefits
from real role separation: an employment specialist, an education
specialist, and a risk analyst each reasoning over different data sources,
rather than one prompt trying to do all three jobs credibly at once. This
hybrid (LangGraph orchestrating a CrewAI crew as one node) is the same
pattern used in production agent systems that mix control-flow needs with
role-based collaboration needs.

## Setup

```bash
cd traqcheck-lite
python -m venv venv-capstone
venv-capstone\Scripts\activate.bat        # Windows cmd
# or: .\venv-capstone\Scripts\Activate.ps1   # PowerShell

pip install --only-binary :all: -r requirements.txt
```

Create `.env` in this folder:
```
GROQ_API_KEY=your_key_here
```

Generate the synthetic registry (do this once, or again any time you want
a fresh dataset):
```bash
python data/generate_synthetic_data.py
```

## Running it

**As an API:**
```bash
uvicorn main:app --reload
```
Then POST to `http://localhost:8000/background-check` with a JSON body like:
```json
{
  "name": "Kevin Pacheco",
  "claimed_company": "Blake and Sons",
  "claimed_title": "Junior Developer",
  "claimed_institution": "Bernard LLC University",
  "claimed_degree": "BSc Computer Science",
  "consent_given": true
}
```
Response includes a `thread_id` and a `proposed_report` awaiting review.
Approve/reject it:
```
POST /background-check/{thread_id}/decision
{ "decision": "approve" }
```

**Evaluation suite (requires real GROQ_API_KEY, makes live API calls):**
```bash
python eval/run_eval.py
```
Writes `eval/eval_results.csv` and prints a failure-pattern summary.

## Failure handling built in

1. **Bad input** — FastAPI/Pydantic rejects malformed request bodies (422);
   the LangGraph `validate_input` node additionally rejects missing fields
   even if called directly (bypassing the API layer).
2. **Data not found** — fuzzy name matching (rapidfuzz) handles typos, and
   a genuine not-found result is surfaced as `high` risk rather than
   crashing or silently passing.
3. **Prompt injection in reference notes** — the Risk Analyst agent is
   explicitly instructed to treat note text as data, never as commands, and
   to flag injection attempts rather than obey them. Covered by adversarial
   eval cases #9 and #10.
4. **Model returning unparseable output** — `run_verification` raises a
   clear error the graph catches and reports as `final_status: error`,
   instead of crashing the API.

## Known limitations (for the executive report)

- Uses synthetic data only; a real deployment needs licensed background-check
  data providers and jurisdiction-specific compliant consent language (e.g.
  FCRA in the US).
- Fuzzy-match threshold (85) is a reasonable default but not tuned against
  real name-collision statistics.
- No persistence beyond LangGraph's in-memory checkpointer — a real deployment
  needs a durable checkpointer (e.g. Postgres) so pending reviews survive a
  server restart.
- Single reference note per candidate in this demo; real checks would pull
  from multiple referees and weight recency/relationship.

## Next steps

- Swap `MemorySaver` for a persistent checkpointer before any real deployment
- Add rate limiting / retry-with-backoff around Groq calls for production traffic
- Expand adversarial eval set periodically (see MONITORING_CHECKLIST.md)
- Add authentication to the FastAPI endpoints before exposing beyond localhost
