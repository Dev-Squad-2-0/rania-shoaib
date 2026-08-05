# Human Evaluation Rubric — Voice Agent (Ayesha)

Score each conversation 1-5 per criterion. Record scores per conversation,
not per turn — read the whole transcript, then score.

## Naturalness (does it sound like a real Pakistani sales rep, not a script?)
5 — Indistinguishable from a real rep; fillers/tone feel earned, not forced
3 — Understandable and mostly natural, but a few lines feel stiff, robotic, or
    like a direct translation from English
1 — Sounds clearly like a bot; formal/written Urdu, no natural speech patterns

## Persuasiveness (does it move a real caller toward booking a visit?)
5 — Handles objections smoothly, leads with caller's stated priority, closes
    toward a specific next step every time
3 — Generally moves the conversation forward but misses a chance to close, or
    handles an objection weakly (argues instead of reframing)
1 — Doesn't attempt to close; ends on vague/open offers; objections get
    dismissed or ignored

## Fluency (is the Roman-Urdu/UrduLish grammatically and lexically correct?)
5 — No grammar errors, no wrong-register words (Hindi vs Urdu), correct
    gender agreement throughout, no nonsense/invented words
3 — Understandable with 1-2 minor slips (a tense error, a mild register
    mismatch) that don't distract from the conversation
1 — Frequent grammar errors, gender-agreement slips, wrong/invented words, or
    a break in register/language that a native speaker would flag immediately

## Latency (does the response come fast enough for a real phone call?)
Measured automatically from time-to-first-token per turn, not human-judged.
5 — avg TTFT < 1s
3 — avg TTFT 1-2s
1 — avg TTFT > 2s (violates the Task 1 budget)

## Conversation Flow (does it track context and avoid repeating/contradicting itself?)
5 — Never re-asks known info, resolves references correctly, no contradictions
    across turns
3 — Mostly tracks context, but has one instance of re-asking known info or a
    weakly-resolved reference
1 — Repeatedly re-asks answered questions, fabricates references, or
    contradicts something said earlier in the same conversation