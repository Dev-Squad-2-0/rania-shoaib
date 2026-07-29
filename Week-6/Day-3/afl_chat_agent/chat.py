"""
chat.py
========

A plain interactive loop for actually talking to the AFL agent, instead
of editing a hardcoded question inside agent.py each time.

Normal use — just the clean answer, nothing else:
    python chat.py

Debug use — also shows the tool call(s) behind each answer, so you can
keep doing the grounding check (does the number in the sentence match
the raw tool output) whenever you want to spot-check it:
    python chat.py --debug

Type 'exit' or 'quit' to stop. Memory persists for the whole session
(same session_id throughout), so follow-up questions work naturally —
this is the same conversation each turn belongs to, per Task 4.
"""

import sys
from agent import chat, GroundingLogger

SESSION_ID = "interactive"


def main():
    debug = "--debug" in sys.argv

    print("AFL chat agent. Type 'exit' or 'quit' to stop.")
    if debug:
        print("(debug mode: tool calls will be shown under each answer)")
    print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if user_input.lower() in ("exit", "quit"):
            print("Exiting.")
            break
        if not user_input:
            continue

        logger = GroundingLogger() if debug else None
        result = chat(user_input, session_id=SESSION_ID, logger=logger)

        print(f"\nAgent: {result['answer']}\n")
        if debug and result["tool_calls"]:
            for call in result["tool_calls"]:
                print(f"  [tool call] {call['tool']}({call['input']}) -> {call['output']}")
            print()


if __name__ == "__main__":
    main()