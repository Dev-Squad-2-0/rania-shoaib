"""
query_agent.py
Task 4 wiring — connects the LLM to real Postgres data instead of only Chroma chunks.

Pipeline:
    user_query
      -> extract_criteria()      : LLM turns natural language into structured
                                    slots (budget, city, bedrooms, etc.)
      -> recommend_properties()  : recommend.py scores real Postgres rows
                                    against those slots
      -> generate_grounded_answer(): LLM answers using ONLY the returned
                                    property rows as context

This replaces the old flow where generate_answer() only ever saw Chroma
brochure chunks and never touched Postgres.

Run directly for a demo:
    python query_agent.py
"""

import os
import json
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI

from recommend import recommend_properties

load_dotenv(find_dotenv())

GATEWAY_BASE_URL = "https://llm.netixsol.com/v1"
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY")
GATEWAY_MODEL = "smart"

client = OpenAI(base_url=GATEWAY_BASE_URL, api_key=GATEWAY_API_KEY)


# ---------------------------------------------------------------
# 1. SLOT EXTRACTION — natural language -> structured criteria
# ---------------------------------------------------------------
EXTRACTION_SYSTEM_PROMPT = """You extract structured property search criteria from a customer's message.

Respond with ONLY a JSON object, no other text, no markdown fences. Use this exact shape:
{
  "budget": number or null,
  "city": string or null,
  "area": string or null,
  "bedrooms": integer or null,
  "purpose": "buy" or "rent" or null,
  "amenities": [list of strings] or [],
  "investment_goals": string or null
}

Rules:
- budget: convert "crore"/"lakh"/"karod"/"lac" to a plain PKR number (1 crore = 10000000, 1 lakh = 100000).
- Only fill a field if the customer actually said something matching it. Leave everything else null/empty.
- Do not guess a city or area that wasn't mentioned.
"""


def extract_criteria(user_query: str) -> dict:
    """Calls the gateway LLM to turn a natural-language query into structured slots."""
    response = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_query},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()

    # Defensive: strip code fences if the model adds them anyway
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        criteria = json.loads(raw)
    except json.JSONDecodeError:
        # If extraction fails, return all-null criteria rather than crashing —
        # recommend_properties() with no criteria just returns top properties
        # unranked, which is a safe fallback, not a silent wrong answer.
        criteria = {
            "budget": None, "city": None, "area": None, "bedrooms": None,
            "purpose": None, "amenities": [], "investment_goals": None,
        }
        criteria["_extraction_error"] = raw

    return criteria


# ---------------------------------------------------------------
# 2. GROUNDED ANSWER GENERATION — LLM answers using ONLY real matches
# ---------------------------------------------------------------
def _format_matches_as_context(matches: list[dict], criteria: dict = None) -> str:
    if not matches:
        return "No matching properties were found in the database for these criteria."

    criteria = criteria or {}
    lines = []
    for m in matches:
        price = m.get("price") or m.get("rent_per_month")
        amenities = m.get("amenities") or []
        amenities_str = ", ".join(amenities) if amenities else "none listed"

        # Compute real gaps so the LLM never has to guess or round a figure itself.
        gaps = []
        budget = criteria.get("budget")
        if budget and price is not None and float(price) > budget:
            gaps.append(f"over budget by {float(price) - budget:,.0f} PKR")

        breakdown = m.get("match_breakdown", {})
        amenities_breakdown = breakdown.get("amenities")
        if isinstance(amenities_breakdown, dict) and amenities_breakdown.get("missing"):
            gaps.append(f"missing requested amenities: {', '.join(amenities_breakdown['missing'])}")

        gap_str = f" | GAPS: {'; '.join(gaps)}" if gaps else " | GAPS: none (exact match on stated criteria)"

        lines.append(
            f"- {m['title']} | {m['property_type']} | {m['city']}, {m['area_name']} | "
            f"price: {price} | bedrooms: {m['bedrooms']} | developer: {m['developer']} | "
            f"amenities: {amenities_str} | match_score: {m['match_score']}%{gap_str}"
        )
    return "\n".join(lines)


def generate_grounded_answer(user_query: str, matches: list[dict], criteria: dict = None) -> str:
    """
    Same voice-safe output rules as rag_pipeline.generate_answer, but the
    context here is real Postgres rows from recommend_properties(), not
    Chroma brochure chunks. This is the actual fix for "random" answers.
    """
    context = _format_matches_as_context(matches, criteria)

    system_prompt = (
        "You are a real estate assistant speaking to a customer over the phone. "
        "Answer ONLY using the property listings provided below. Never invent a "
        "price, location, developer, or bedroom count that isn't in the listings. "
        "Each listing includes a GAPS field computed directly from the data — use "
        "it exactly as given, never estimate or round a gap figure yourself.\n\n"
        "If a listing has 'GAPS: none', present it as an exact match confidently. "
        "If the best listing has small GAPS (over budget by a modest amount, or "
        "missing one requested amenity) and nothing better exists, present that "
        "listing and state its exact gap honestly — do not claim it matches "
        "perfectly, and do not flatly say nothing matches when a real near-miss "
        "exists. If nothing close exists at all, say so plainly.\n\n"
        "STRICT OUTPUT FORMAT — your text is read aloud by text-to-speech:\n"
        "- Reply ONLY in Roman script (Urdu written in English letters mixed with "
        "English words). NEVER use Urdu/Nastaliq script.\n"
        "- NEVER use emojis or symbols.\n"
        "- Keep sentences short and natural, like a real consultant speaking on a call."
    )

    user_prompt = f"Property listings:\n{context}\n\nCustomer question: {user_query}"

    response = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------
# 3. SINGLE ENTRY POINT
# ---------------------------------------------------------------
def answer_query(user_query: str, top_n: int = 5) -> dict:
    """
    Full wired pipeline: extract slots -> match real properties -> grounded answer.
    Returns everything (not just the answer) so a test can verify each stage.
    """
    criteria = extract_criteria(user_query)

    matches = recommend_properties(
        budget=criteria.get("budget"),
        city=criteria.get("city"),
        area=criteria.get("area"),
        bedrooms=criteria.get("bedrooms"),
        purpose=criteria.get("purpose"),
        amenities=criteria.get("amenities") or None,
        investment_goals=criteria.get("investment_goals"),
        top_n=top_n,
    )

    answer = generate_grounded_answer(user_query, matches, criteria)

    return {
        "query": user_query,
        "extracted_criteria": criteria,
        "matches": matches,
        "answer": answer,
    }


if __name__ == "__main__":
    demo_query = "3 bedroom ghar chahiye Karachi mein, budget 5 crore tak, swimming pool ho"
    result = answer_query(demo_query)

    print("QUERY:", result["query"])
    print("\nEXTRACTED CRITERIA:")
    print(json.dumps(result["extracted_criteria"], indent=2))
    print(f"\nMATCHES FOUND: {len(result['matches'])}")
    for m in result["matches"][:3]:
        print(f"  {m['match_score']}% — {m['title']} ({m['city']}, {m['area_name']}) PKR {m['price']}")
        print(f"    amenities: {m.get('amenities')}")
    print("\nGENERATED ANSWER:")
    print(result["answer"])