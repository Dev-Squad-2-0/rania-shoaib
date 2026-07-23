# Comparison Report: Sequential vs Hierarchical vs Single-Agent

## Overview

This report compares three approaches to the same task, turning raw
customer feedback into a ranked, stakeholder-ready product report, run
as a CrewAI sequential crew, a CrewAI hierarchical crew, and (for
reference) a single-agent LangGraph solution from Day 3.

## Cost

| Approach | Total Tokens | Relative Cost |
|---|---|---|
| Sequential (CrewAI) | 17,544 | Baseline |
| Hierarchical (CrewAI) | 51,801 | ~3x sequential |
| Single-agent (LangGraph, Day 3) | Not logged, output capped at 1000 tokens per call | Likely lowest, but not directly comparable without logged data |

Sequential was the clear winner on cost between the two CrewAI runs.
Hierarchical's cost increase came primarily from the manager agent's
extra reasoning and review overhead, and in this specific run, from the
manager choosing to execute all three tasks itself rather than
delegating, meaning the cost went up without the intended benefit of
specialization being realized.

## Quality

Sequential and hierarchical produced reports of comparable quality,
both were factually accurate against the source data, complete in
covering all extracted issues, and written in clear business language.
Neither meaningfully outperformed the other on the three success
criteria defined in Task 5. The single-agent LangGraph solution was not
re-run on this exact task, so a direct quality comparison isn't
available, though the smaller max_tokens cap used that day suggests it
was optimized for shorter, more constrained outputs rather than a full
multi-section report.

## Reliability

Sequential ran exactly as designed both times it was executed, with the
three specialist agents each doing their intended job in order.
Hierarchical completed without technical errors, but did not delegate
to the specialist agents in its first run, the manager self-executed
the entire pipeline instead. This is an important reliability caveat,
`Process.hierarchical` does not guarantee delegation just because
`allow_delegation=True` is set, the manager's backstory needs to
explicitly instruct it not to perform tasks itself.

## When to Use Each

| Approach | Best suited for |
|---|---|
| Single-agent | Small, well-scoped tasks where one clear prompt can hold the full instruction set without losing focus |
| Sequential multi-agent | Tasks that naturally break into distinct stages with different skills, where each stage's output can be tightly specified and handed to the next |
| Hierarchical multi-agent | Tasks where dynamic judgment calls about delegation or review genuinely add value, and only once delegation behavior has been verified to work as intended, otherwise it adds cost without benefit |

## Overall Takeaway

For this specific feedback-to-report task and dataset size, sequential
CrewAI was the most cost-efficient approach that still delivered full
role specialization, and hierarchical did not justify its roughly 3x
token cost since the manager bypassed the specialist crew entirely in
practice. A single well-prompted agent, based on the token ceiling used
on Day 3, was likely the cheapest option of all three, though this
can't be confirmed precisely since usage wasn't logged at the time.
The clearest lesson from this comparison is procedural rather than
architectural, multi-agent systems are only as good as the delegation
and formatting instructions given to them, and both need to be
verified by inspecting the actual execution log, not assumed from the
configuration alone.