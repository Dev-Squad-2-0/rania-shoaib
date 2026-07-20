"""
Week 5 Day 1 agent, adapted for an OpenAI-protocol gateway
(e.g. a LiteLLM-style proxy your internship set up), instead of hitting
api.anthropic.com directly.

Same ReAct loop and same two real tools (math.js calculator, Open-Meteo
weather) as agent_from_scratch.py. What's different is the request/response
shape: OpenAI's tool-calling format uses `tool_calls` on the assistant
message and a `role: "tool"` message for results, instead of Anthropic's
`tool_use` / `tool_result` content blocks.

Setup:
    pip install openai requests
    export GATEWAY_API_KEY=your-key-here
    Run check_gateway.py first to confirm MODEL_NAME below is correct
    for your gateway, then run this script.
"""

import os
import json
import traceback
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # reads variables from a local .env file into os.environ

BASE_URL = "https://llm.netixsol.com/v1"
MODEL_NAME = "smart"  # general-purpose alias; try "reasoner" if this struggles with multi-step tool use
MAX_ITERATIONS = 6


# ---------------------------------------------------------------------------
# Tool schemas: OpenAI's function-calling format wraps the same three
# fields (name, description, parameters) inside a "function" object.
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate a math expression using the math.js public API "
                "(real external service). Supports standard arithmetic "
                "(+, -, *, /, ^, parentheses) and functions like sqrt(), "
                "sin(), log(). Does not support word problems, convert "
                "those to a plain expression first. Example: 'sqrt(16) + 2^3'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A math.js compatible expression, e.g. '3 * (4 + 5)'",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Look up current, real weather for a named city using the "
                "Open-Meteo API (free, live data). Give a plain city name; "
                "the tool geocodes it internally. Returns an explicit "
                "error string if the city can't be found or the service "
                "is unavailable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name, e.g. 'Tokyo' or 'Lagos'",
                    }
                },
                "required": ["city"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations, identical to agent_from_scratch.py, real APIs,
# real error handling. Only the surrounding request/response glue differs.
# ---------------------------------------------------------------------------

def run_calculator(expression: str) -> str:
    try:
        response = requests.get(
            "https://api.mathjs.org/v4/", params={"expr": expression}, timeout=5
        )
    except requests.exceptions.RequestException as e:
        return f"ERROR: could not reach math.js API: {e}"
    if response.status_code != 200:
        return f"ERROR: math.js API returned {response.status_code}: {response.text.strip()}"
    return response.text.strip()


def run_get_weather(city: str) -> str:
    try:
        geo_resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
            timeout=5,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
    except requests.exceptions.RequestException as e:
        return f"ERROR: could not reach geocoding API: {e}"

    results = geo_data.get("results")
    if not results:
        return f"ERROR: no location found for city '{city}'"

    lat = results[0]["latitude"]
    lon = results[0]["longitude"]
    resolved_name = results[0].get("name", city)

    try:
        weather_resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current_weather": "true"},
            timeout=5,
        )
        weather_resp.raise_for_status()
        weather_data = weather_resp.json()
    except requests.exceptions.RequestException as e:
        return f"ERROR: could not reach forecast API: {e}"

    current = weather_data.get("current_weather")
    if not current:
        return f"ERROR: forecast API returned no current_weather for '{city}'"

    return json.dumps(
        {
            "city": resolved_name,
            "temp_c": current.get("temperature"),
            "windspeed_kmh": current.get("windspeed"),
            "weather_code": current.get("weathercode"),
        }
    )


TOOL_FUNCTIONS = {"calculator": run_calculator, "get_weather": run_get_weather}


def execute_tool(name: str, tool_input: dict) -> str:
    if name not in TOOL_FUNCTIONS:
        return f"ERROR: unknown tool '{name}' requested by the model."
    try:
        return TOOL_FUNCTIONS[name](**tool_input)
    except TypeError as e:
        return f"ERROR: bad arguments for tool '{name}': {e}"
    except Exception as e:
        return f"ERROR: tool '{name}' raised an exception: {e}"


# ---------------------------------------------------------------------------
# The agent loop, OpenAI protocol version.
#
# Key differences from the Anthropic version:
#   - tool calls arrive as `message.tool_calls`, a list of objects with
#     .id, .function.name, .function.arguments (arguments is a JSON
#     STRING here, not a dict, so it must be json.loads()'d)
#   - stopping condition is `finish_reason == "stop"` instead of
#     `stop_reason != "tool_use"`
#   - tool results are appended as their own message with role="tool"
#     and a matching tool_call_id, instead of a tool_result content block
# ---------------------------------------------------------------------------

def run_agent(client, user_task: str, max_iterations: int = MAX_ITERATIONS):
    print(f"\n=== AGENT START: {user_task!r} ===")
    messages = [{"role": "user", "content": user_task}]
    scratchpad = {"tool_calls_made": []}

    for step in range(1, max_iterations + 1):
        print(f"\n--- Step {step} ---")
        response = client.chat.completions.create(
            model=MODEL_NAME, messages=messages, tools=TOOLS, max_tokens=1000
        )
        message = response.choices[0].message

        if message.content:
            print("[REASON] model text:", message.content[:300])

        if response.choices[0].finish_reason != "tool_calls":
            final_text = message.content or "(no text returned)"
            print("[DONE] final answer:", final_text)
            return final_text

        # Append the assistant's tool-calling message as-is so the model
        # has the full history of its own request on the next turn.
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            }
        )

        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as e:
                result = f"ERROR: model sent malformed JSON arguments: {e}"
                args = {}
            else:
                print(f"[ACT] calling tool: {tc.function.name}({args})")
                result = execute_tool(tc.function.name, args)
                print(f"[OBSERVE] result: {result}")

            scratchpad["tool_calls_made"].append(
                {"tool": tc.function.name, "input": args, "result": result}
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    print("[STOPPED] hit max_iterations safeguard without a final answer.")
    return None


def test_calculator_directly():
    """Tests the real math.js API call with no LLM involved at all.
    Fastest way to confirm the tool implementation itself works before
    trusting the model to call it correctly."""
    print("\n=== Direct calculator tool test (no LLM) ===")
    test_expressions = ["2 + 2", "sqrt(16) + 2^3", "this is not math"]
    for expr in test_expressions:
        result = run_calculator(expr)
        print(f"  run_calculator({expr!r}) -> {result}")


def demo_single_tool_call(client):
    """Task 2: one manual tool call, no loop yet, so you see the raw
    request -> tool_call -> execute -> tool_result -> final answer
    mechanics before Task 3 wraps it in a loop."""
    print("\n=== Task 2: single manual tool call ===")
    messages = [{"role": "user", "content": "What is 12.5 times 4, plus the square root of 81?"}]

    response = client.chat.completions.create(
        model=MODEL_NAME, messages=messages, tools=TOOLS, max_tokens=1000
    )
    message = response.choices[0].message
    print("finish_reason:", response.choices[0].finish_reason)

    if not message.tool_calls:
        print("Model answered directly without using a tool:", message.content)
        return

    tc = message.tool_calls[0]
    args = json.loads(tc.function.arguments)
    print(f"Model wants to call: {tc.function.name}({args})")
    result = execute_tool(tc.function.name, args)
    print("Tool result:", result)

    messages.append(
        {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    final = client.chat.completions.create(
        model=MODEL_NAME, messages=messages, tools=TOOLS, max_tokens=1000
    )
    print("Final answer:", final.choices[0].message.content)


def demo_failure_modes(client):
    """Task 5: deliberately break the agent and observe what happens."""
    print("\n=== Task 5: failure mode demos ===")

    print("\n-- Ambiguous request (no city named) --")
    run_agent(client, "Is it warmer there than here?")

    print("\n-- Task needing an undefined tool (no news tool exists) --")
    run_agent(client, "Get me today's top news headline.")

    print("\n-- Tool that will return an error (unknown city) --")
    run_agent(client, "What's the weather in Xqzzblorp7719?")


if __name__ == "__main__":
    api_key = os.environ.get("GATEWAY_API_KEY")
    if not api_key:
        print(
            "No GATEWAY_API_KEY set. Set it and re-run:\n\n"
            "    export GATEWAY_API_KEY=your-key-here\n"
            "    python agent_gateway.py\n"
        )
    else:
        client = OpenAI(base_url=BASE_URL, api_key=api_key)
        try:
            test_calculator_directly()
            demo_single_tool_call(client)
            run_agent(client, "What's the weather in Tokyo and Reykjavik, and which is warmer?")
            demo_failure_modes(client)
        except Exception:
            traceback.print_exc()