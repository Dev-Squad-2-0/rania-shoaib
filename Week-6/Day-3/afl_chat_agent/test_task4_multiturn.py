"""
test_task4_multiturn.py
=========================

Task 4: Memory & Multi-Turn AFL Conversations

Why this specific 5-turn shape (team -> player on that team -> stat
comparison -> vague follow-up): it's designed to fail loudly if memory
isn't actually wired correctly, rather than fail subtly. Turn 4 in
particular ("how does that compare to his career average?") has NO
proper noun in it at all — no team, no player name, no year. If the
agent answers that correctly, memory is definitely working, because
there's nothing in the sentence itself to go on.

Run this after your GATEWAY_API_KEY is set (see .env.example / README).
It prints each turn's answer plus the raw tool calls, so you can visually
confirm both (a) context carried across turns and (b) each stat answer
is grounded in an actual tool result, not the model quietly answering
turn 4 from its own memory of Bontempelli instead of resolving "his" and
"that" from the conversation.
"""

from agent import chat, GroundingLogger

SESSION_ID = "multiturn-demo"

TURNS = [
    "Tell me about the Western Bulldogs' head-to-head record against Richmond.",
    "Who's their captain, Marcus Bontempelli — can you get his 2022 season stats?",
    "How did he do specifically in round 10 that year?",
    "How does that compare to his career average that season overall?",
    "And how did the Bulldogs go against Richmond the following year, 2023?",
]


def main():
    print(f"Running {len(TURNS)}-turn conversation, session_id='{SESSION_ID}'\n")
    for i, turn in enumerate(TURNS, start=1):
        logger = GroundingLogger()
        result = chat(turn, session_id=SESSION_ID, logger=logger)
        print(f"--- Turn {i} ---")
        print(f"USER: {turn}")
        print(f"AGENT: {result['answer']}")
        print(f"TOOL CALLS: {result['tool_calls']}")
        print()

    print(
        "Manual check: does turn 3 answer stay about Bontempelli without you "
        "repeating his name? Does turn 4 correctly resolve 'that' (round 10 "
        "stats) vs. 'his career average that season' (season totals), without "
        "mixing the two up? That's the actual memory test here."
    )


if __name__ == "__main__":
    main()
