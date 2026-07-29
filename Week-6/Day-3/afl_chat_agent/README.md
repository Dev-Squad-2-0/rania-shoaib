# AFL Domain-Scoped Chat Agent — Setup & Usage

## What's in this folder

```
afl_chat_agent/
├── data/
│   ├── merged_players.csv              # your upload, copied in
│   ├── players_info_cleaned.csv        # your upload, copied in
│   ├── round_by_round_enriched.csv     # your upload, copied in
│   └── match_level.csv                 # derived by build_match_level.py
├── build_match_level.py                # script that made match_level.csv (re-runnable)
├── system_prompt.py                    # Task 1: scope, refusals, adversarial prompts
├── data_loader.py                      # shared CSV loading + name resolution
├── tools.py                            # Task 2/3: the 3 structured retrieval tools
├── agent.py                            # Task 3/4: LangChain agent + memory + grounding logger
├── test_task4_multiturn.py             # Task 4: 5-turn memory demo
├── test_task5_eval.py                  # Task 5: guardrail eval runner
├── eval_report.md                      # Task 5: the actual report — now filled in with real runs
├── webapp/
│   ├── server.py                       # local Flask wrapper around agent.chat()
│   └── index.html                      # browser chat UI (talks to server.py)
├── requirements.txt

```

## What to download

Everything above — it's one self-contained folder. If you only grab a
subset, at minimum you need: `data/`, `system_prompt.py`, `data_loader.py`,
`tools.py`, `agent.py`, and `requirements.txt` to actually run the agent.
`webapp/` is optional — only needed if you want the browser UI instead of
a console chat loop.

## Setup (Windows, matching your existing bootcamp environment)

1. **Use your existing venv** at `C:\rania-shoaib\venv` (Python 3.11) —
   no need for a new one. Activate it with your `start.bat` shortcut.

2. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```
   Pinned to LangChain 0.3.x to match your Week 5 setup. Now also includes
   `flask` and `flask-cors` for the optional web UI.

3. **Add your gateway key:**
   Copy `.env.example` to `.env` in the same folder, and fill in:
   ```
   GATEWAY_API_KEY=your-real-key
   ```
   The gateway URL and model alias (`smart`) already default correctly.

4. **Data location:** the CSVs are already in `data/`. If you'd rather
   point at CSVs living elsewhere, set `AFL_DATA_DIR` in `.env`.

## Running things, in order

### Quick sanity check (no API key needed)
```
python tools.py
```
Confirms the tools are correct against real data, no LLM involved.

### Single-question test (needs your gateway key)
```
python agent.py
```
Asks one hardcoded question, prints the answer and the raw tool call.

### Task 4: multi-turn memory demo
```
python test_task4_multiturn.py
```
Runs the scripted 5-turn conversation. Read the output against the
checklist the script prints at the end.

### Task 5: full guardrail eval
```
python test_task5_eval.py
```
Runs all legit + adversarial + edge-case prompts.

### Browser UI (optional, instead of the console loop)
```
python webapp/server.py
```
then open `webapp/index.html` in your browser. It calls your existing
`agent.chat()` through a local Flask endpoint — nothing in `agent.py`,
`tools.py`, or `system_prompt.py` changes to support this.

## If you need to rebuild `match_level.csv`

Only necessary if your source `round_by_round_enriched.csv` changes:
```
python build_match_level.py
```

## Current status (see `eval_report.md` for full detail)

Both `test_task4_multiturn.py` and `test_task5_eval.py` have now been run
for real against the live gateway, twice — once before and once after a
round of prompt/tool fixes. Closed and still-open issues:

**Closed:**
- Numpy scalars leaking into tool output (`np.float64` instead of plain
  floats) — fixed in `tools.py`.
- `get_head_to_head` couldn't answer single-season questions (only had a
  `since_year` lower bound) — added `until_year`.
- Ungrounded, overconfident answers on history/coaching/stadium
  questions (including two factual errors caught in testing) — the
  system prompt now suppresses specific unverifiable claims instead of
  just hedging them with a disclaimer.
- Gambling-adjacent prompts, jailbreak/role-play attempts, and off-topic
  smuggling all hold scope reliably across every prompt tested.

**Still open — two grounding violations that recurred even after a
targeted prompt edit aimed at each one:**
- The model sometimes states a wrong "headline" number in an opening
  sentence before showing the correct, tool-backed number later in the
  same answer.
- The model sometimes still computes a missing stat via a correct
  formula (e.g. disposals = kicks + handballs) instead of reporting it as
  missing, even in the same conversation where it correctly declined to
  do this one turn earlier.

Both reproduced with different specific wrong values across two separate
runs, so they're not one-off flukes — see `eval_report.md`'s "Recommended
next step" for why a wording-only fix likely isn't enough here, and what
a more structural fix could look like.

## Known data limitations

- `score` is 100% null in the source data — `margin` is used instead;
  this is a real data gap, not a bug.
- 354 matches from 1983–1990 (~2.4% of match-perspective rows) are
  missing one team's side of the match in `match_level.csv`.
- No unstructured text (match reports, articles) exists in the current
  data, so there's no semantic/vector retrieval tool — only structured
  lookups.