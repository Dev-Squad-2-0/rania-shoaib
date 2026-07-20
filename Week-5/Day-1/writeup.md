# Agent Foundations, Week 5 Day 1 Writeup

## Task 1: Concepts

**Chatbot**: takes one message, returns one reply. No memory of tools, no ability to take actions in the world, no multi-step planning. It just talks.

**Workflow**: a fixed sequence of steps that you, the developer, wrote in advance (step 1 then step 2 then step 3, maybe with a couple of if/else branches). The order of operations is decided by your code, not by the model. Predictable and easy to debug, but it cannot handle situations you did not anticipate.

**Agent**: the model itself decides, at each step, what to do next: which tool to call, what arguments to pass, and when it has enough information to stop. The control flow is decided at run time by the model's reasoning, not hardcoded by us in advance.

What makes something "agentic" is some combination of:
- Autonomy: it chooses its own next action instead of following a fixed script.
- Tool use: it can call functions/APIs/read data, not just generate text.
- Multi step planning: it can break a goal into sub-steps and pursue them in sequence.
- Self correction: it can notice a tool failed or a result looks wrong, and try something different instead of blindly continuing.

**When an agent is overkill**: if the task is a single lookup, a single transformation, or something you could write as five lines of deterministic code, an agent adds latency, cost, and a new source of unpredictable behavior for no benefit. A plain prompt (or plain script) is better whenever the number of steps and the tools needed are known ahead of time and do not depend on what happens mid task.

### The ReAct pattern

Reason, Act, Observe, repeat.

```
loop:
    thought = model.reason(current_state)
    if thought.is_final_answer:
        return thought.answer
    action = thought.chosen_tool_call
    observation = execute(action)
    current_state = current_state + action + observation
```

In terms of the Anthropic API specifically:

```
messages = [user_message]
loop up to max_iterations:
    response = client.messages.create(model=..., tools=TOOLS, messages=messages)
    if response.stop_reason != "tool_use":
        return response.text   # final answer, loop ends
    append response.content to messages as an assistant turn
    for each tool_use block in response.content:
        result = run_the_actual_tool(block.name, block.input)
        collect a tool_result block referencing block.id
    append all tool_result blocks to messages as a user turn
```

## Task 2: Tool schemas used

Two tools were defined, `calculator` and `get_weather`. Both are declared with `name`, `description`, and `input_schema` (JSON Schema). See `agent_from_scratch.py` for the exact schemas.

Descriptions matter because the description is the only information the model has about when and how to use a tool. It never sees your implementation code. A vague description ("gets weather") leads to the model guessing at units, argument formats, and edge cases, which shows up later as wrong tool arguments or unnecessary tool calls. A precise description states what the tool does, what format its arguments must be in, and what it explicitly does not do (e.g. the calculator does not accept word problems, the weather tool is a stub with a fixed set of cities). That precision is what makes tool calling reliable instead of a coin flip.

## Task 3 and 4: Loop, memory, and logging

The loop in `run_agent()` sends the full message list every call, checks `stop_reason`, executes any `tool_use` blocks, appends `tool_result` blocks, and repeats. It stops either when the model returns plain text (`stop_reason != "tool_use"`) or after `MAX_ITERATIONS` (set to 6) as a safeguard against infinite loops.

- **Conversation memory** is the `messages` list: the full transcript of everything said, including every tool call and every tool result, sent back to the model on every single API call. This is how the model "remembers" earlier steps, since the API itself is stateless between calls.
- **Working memory** is the `scratchpad` dict tracked in our own Python code (in this case, a log of every tool call made and its result). It is state we keep on our side and can choose to expose to the model or use for our own bookkeeping and debugging; the model does not automatically see it unless we insert it into `messages`.

Logging in the script prints, on every iteration: the model's reasoning text (REASON), the tool it decided to call and with what arguments (ACT), and the result returned (OBSERVE). This three part log is the debugging habit worth carrying into every framework afterward, since frameworks often hide this trace unless you turn on verbose mode.

## Task 5: Failure modes observed and mitigations

1. **Ambiguous request** ("Is it warmer there than here?" with no cities named): the model either asks a clarifying question in plain text (loop ends immediately with no tool call) or guesses cities that were never mentioned. Mitigation: validate that required entities are present before running the agent, or have the tool return a structured "missing parameter" error the model can react to instead of guessing.

2. **Task needing an undefined tool** ("get me today's news headline" with no news tool defined): the model may hallucinate an answer from its own training data, or attempt to call a tool that doesn't exist. Our `execute_tool()` catches unknown tool names and returns an explicit `ERROR: unknown tool` string instead of crashing, which lets the model react (apologize / say it cannot do this) rather than the program dying silently. Mitigation: dispatch through a lookup table with a default "unknown tool" branch, never assume the model will only call tools you defined.

3. **Tool returns an error** (unknown city for weather): the tool returns a plain string starting with `ERROR:`. Mitigation: always return errors as text content in the `tool_result` rather than raising an exception, so the model can see the failure and adjust, instead of the whole process crashing.

4. **Wrong tool arguments** (e.g. model passes `"12 + 5 apples"` to the calculator): mitigation is input validation inside the tool implementation (a character whitelist here) and a `try/except` around execution so a bad argument produces a clear error message back to the model rather than an unhandled exception.

5. **Infinite / runaway loops** (model keeps calling tools without ever converging on an answer): mitigation is the `max_iterations` cap, with a clear log message when it is hit, so the program always terminates.

6. **Silent errors** (a tool implementation raises an exception outside the loop's control flow): mitigation is wrapping every tool call in try/except inside `execute_tool()` so an exception becomes an observable `ERROR:` result instead of taking down the whole agent process.

## Why frameworks exist

Having built this by hand, the value of LangChain, LangGraph, or CrewAI becomes clearer: they are not doing anything magical, they are packaging exactly this loop (reason, act, observe, repeat, with memory and a stop condition) along with a lot of the boilerplate around it: retries, structured output parsing, multi-agent handoffs, built in tracing/logging UIs, checkpointing so a long run can resume after a crash, and pre-built integrations for dozens of tools so you are not writing `execute_tool()` dispatch tables by hand every time. The tradeoff is you give up some visibility into what is actually happening on each iteration unless you turn on their debug/tracing features, which is exactly why building one raw first is useful: you now know what to look for underneath the abstraction.
