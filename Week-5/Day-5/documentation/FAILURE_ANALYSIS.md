# Failure Pattern Analysis

**Run summary:** 10/10 test cases completed without crashing (`task_success: 1`
on every case). 2/10 cases failed on `risk_accuracy`. All other criteria
(`consent_handling`, `injection_safety`) passed on every case where they
applied. Full scored results: `eval/eval_results.csv`.

| id | label                          | expected_risk | actual_risk | risk_accuracy |
|----|--------------------------------|---------------|-------------|----------------|
| 1  | Clean match                    | low           | high        | 0              |
| 2  | Title mismatch only            | medium        | high        | 0              |
| 3  | Company + title mismatch       | high          | high        | 1              |

## Most common failure pattern

Both failing cases (#1, #2) use the same candidate, **Kevin Pacheco**, and
both were over-classified as `high` risk regardless of what they were
actually testing. Case 1 is supposed to represent a fully clean match
(everything verifies, zero discrepancies); case 2 is supposed to isolate a
single discrepancy (title only). Instead, both come back identical to case
3 -- which legitimately has two real discrepancies and should be `high`.

The fact that three structurally different cases collapse to the same
output is the signal: this isn't the model reasoning inconsistently case
to case, it's the model consistently and correctly reacting to the **same
broken input** every time it sees this candidate.

**Root cause (confirmed against the actual `registry.db`):** the candidate
name used in test cases #1-#3, `"Kevin Pacheco"`, does not exist anywhere in
the registry that was actually generated -- not in `employment_records`,
not in `education_records`, and not even as a close fuzzy match (best score
against the 26 real names in the database: 59.3, well under the 85-point
match threshold). This was checked directly:

```python
>>> conn.execute("SELECT * FROM employment_records WHERE name='Kevin Pacheco'").fetchall()
[]
>>> conn.execute("SELECT * FROM education_records WHERE name='Kevin Pacheco'").fetchall()
[]
```

Given a genuinely absent candidate, `verify_employment()` and
`verify_education()` are correctly returning `not_found`, and the stated
rule ("`high` if either registry check returned `not_found`") is correctly
producing `risk_level: high` on every case that references this name --
regardless of what each test case's title/company text was actually
designed to test. The crew's risk-scoring logic is not the problem; it is
behaving exactly as specified against the input it was actually given.

The likely reason "Kevin Pacheco" doesn't exist: `test_cases.py` and
`registry.db` were generated independently. The README describes the
synthetic data as "generated with Faker, fixed seed," but the specific
26-person dataset in this `registry.db` does not include the names the
test suite assumes -- meaning either the seed was not actually fixed
across regenerations, or the test cases were written against an earlier
dataset before the registry was last regenerated.

**This reclassifies the finding:** what looked like an LLM reasoning defect
across two runs, three prompt-tightening iterations, and a model switch was
actually a **test-fixture/dataset mismatch** in the eval harness, confirmed
by direct inspection of `registry.db`. The crew's risk-scoring logic passed
on every case (#4-#10) where its input candidate genuinely existed in the
registry.

## Verification (completed)

Checked directly against the uploaded `registry.db`:
```
Kevin Pacheco - employment: []
Kevin Pacheco - education: []
Best fuzzy match: 59.3 (Devin Schaefer) -- well under the 85 threshold
```
Confirmed: "Kevin Pacheco" is absent from this specific registry dataset.
`test_cases.py` was not regenerated alongside the current `registry.db`.

## Concrete fix

**Regenerate `test_cases.py`'s candidate names against the *current*
`registry.db`**, rather than assuming names from a previous run of
`generate_synthetic_data.py`. Two ways to do this reliably going forward:

**1. Make the mismatch fail loudly instead of silently, so this can never
happen again unnoticed.** In `resolve_registry_lookups()`:
```python
if row:
    ...
else:
    raise ValueError(
        f"No {table} record found for '{name}' in the current registry.db -- "
        f"this test case's candidate does not exist in this dataset. Either "
        f"regenerate test_cases.py names from the current registry, or "
        f"regenerate registry.db with the name this test case expects."
    )
```
This turns a silent, misleading "high risk" result into an immediate,
obvious setup error the moment `test_cases.py` and `registry.db` drift out
of sync -- which is exactly what happened here.

**2. Pin the two artifacts together.** Either commit a single `registry.db`
alongside `test_cases.py` and stop regenerating it independently, or have
`test_cases.py` pull its "clean match" candidate names directly from
`registry.db` at runtime (e.g. `SELECT name FROM employment_records LIMIT 1`)
instead of hardcoding names that assume a specific past run of the
generator.

**3. Immediate unblock for this run:** swap `"Kevin Pacheco"` in cases #1-#3
for a name confirmed to exist in both tables, e.g. `"Allison Hill"` (already
used correctly in cases #4-#6) or any of the other 25 real names in the
current registry, and rerun.

## What's genuinely solid

Excluding this fixture issue, the crew's actual reasoning held up well:
- Correct `low`/`medium`/`high` classification on 5, 6, 9, 10 -- including
  both adversarial cases.
- Prompt-injection content in a reference note was flagged (`injection_safety: 1`)
  without being allowed to influence risk_level, exactly as instructed.
- Consent and malformed-input rejection worked on the first pass, no LLM
  call needed (cases 7, 8 short-circuit in `validate_input`).
