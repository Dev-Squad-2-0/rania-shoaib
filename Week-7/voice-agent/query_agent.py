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

import json
import re
import time
from dotenv import load_dotenv, find_dotenv
from openai import RateLimitError, APIError, APITimeoutError

from system_prompt import SYSTEM_PROMPT
from recommend import recommend_properties
from llm_client import client, MODEL as GATEWAY_MODEL
from price_format import format_pkr_with_raw, format_pkr_delta, format_pkr

load_dotenv(find_dotenv())


def _call_llm_with_retry(**kwargs):
    """Small retry wrapper for gateway calls.

    Query flows can fan out into multiple LLM calls per turn. A transient
    429 or timeout should degrade gracefully instead of crashing the whole
    /converse request with a 500.
    """
    last_error = None
    for attempt in range(1, 4):
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_error = exc
            if attempt == 3:
                raise

            wait_seconds = 5
            message = str(exc)
            match = re.search(r"try again in ([\d.]+)s", message)
            if match:
                wait_seconds = float(match.group(1)) + 1
            time.sleep(wait_seconds)

    raise last_error


# ---------------------------------------------------------------
# 1. SLOT EXTRACTION — natural language -> structured criteria
# ---------------------------------------------------------------
# These are the ONLY valid values the DB knows about.
# The extraction LLM must snap to one of these — never invent a new spelling.
KNOWN_CITIES = ["Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad"]
KNOWN_AREAS  = [
    "DHA Phase 6", "DHA Phase 2",
    "Bahria Town Phase 8", "Bahria Town Precinct 10",
    "Clifton Block 5",
    "Gulberg III",
    "Johar Town",
    "F-10 Markaz",
]

EXTRACTION_SYSTEM_PROMPT = """You extract structured property search criteria from a customer's message (which may be in English, Roman Urdu, or Urdu Arabic script from STT — speech-to-text output is often phonetically approximate).

Respond with ONLY a JSON object, no other text, no markdown fences. Use this exact shape:
{{
  "budget": number or null,
  "city": string or null,
  "area": string or null,
  "bedrooms": integer or null,
  "purpose": "buy" or "rent" or null,
  "property_category": "residential" or "commercial" or null,
  "amenities": [list of strings] or [],
  "investment_goals": string or null
}}

CRITICAL — city and area MUST be chosen from the canonical lists below.
STT often mis-transcribes Urdu place names (e.g. "جوہر ٹاؤن" may come out as
"Joher Town", "Juhar Town", "Yohar Town" etc.). Your job is to figure out
which canonical name the customer MEANT and output that exact spelling.
If nothing in the list is a plausible match, output null — never invent a spelling.

VALID CITIES (output one of these exactly, or null):
{cities}

VALID AREAS (output one of these exactly, or null):
{areas}

Rules:
- budget: convert any script/words to plain PKR integer.
  ("3 crore" -> 30000000, "85 lakh" -> 8500000, "6 to 7 crore" -> 70000000)
- city: pick the closest match from VALID CITIES using your knowledge of
  Pakistani geography and common STT errors. E.g.:
  "لاہور" / "lahor" / "lahore city" -> "Lahore"
  "اسلام آباد" / "islambad" / "islamabad" -> "Islamabad"
  "کراچی" / "krachi" / "karachi" -> "Karachi"
  "راولپنڈی" / "rawlpindi" -> "Rawalpindi"
  "فیصل آباد" / "faisal abad" -> "Faisalabad"
- area: pick the closest match from VALID AREAS. E.g.:
  "جوہر ٹاؤن" / "joher town" / "juhar" / "yohar town" -> "Johar Town"
  "بحریہ ٹاؤن" / "bahriya" / "bahria" -> best matching Bahria Town entry
  "ڈی ایچ اے" / "dha" / "dia phase 6" -> best matching DHA entry
  "گلبرگ" / "gulburg" / "gulberg" -> "Gulberg III"
  "کلفٹن" / "clifton" -> "Clifton Block 5"
  "ایف ٹین" / "f10" / "f 10" -> "F-10 Markaz"
- bedrooms: extract numbers ("چار بیڈ"->4, "تھری بیڈ"->3, "2 bed"->2).
- purpose: "buy" for purchasing/خریدنا/چاہیے (ownership context),
           "rent" for kiraya/کرایہ/rent pe lena.
- property_category:
  "residential" for house/ghar/apartment/فلیٹ/villa/bungalow/مکان.
  "commercial" for shop/دکان/office/دفتر/plaza/godown.
  null if only location/budget given with no property type.
- Only fill a field if the customer actually expressed that preference. Leave others null.
""".format(
    cities="\n".join(f"  - {c}" for c in KNOWN_CITIES),
    areas="\n".join(f"  - {a}" for a in KNOWN_AREAS),
)


def _fuzzy_snap(value: str, candidates: list[str]) -> str | None:
    """
    Post-extraction safety net: if the LLM output a place name that isn't an
    exact match for any canonical value, find the closest one by character
    overlap and return it — as long as it's a convincing match (>50% of the
    shorter string's characters appear in the longer one in order).
    Returns None if nothing is close enough, so the caller can leave the
    field null rather than guess wrongly.
    """
    if not value:
        return None
    v = value.lower().strip()
    # Exact match first (case-insensitive)
    for c in candidates:
        if c.lower() == v:
            return c
    # Substring match: candidate contained in value or vice versa
    for c in candidates:
        c_low = c.lower()
        if c_low in v or v in c_low:
            return c
    # Token overlap: share majority of words
    v_tokens = set(v.split())
    best, best_score = None, 0.0
    for c in candidates:
        c_tokens = set(c.lower().split())
        overlap = len(v_tokens & c_tokens)
        score = overlap / max(len(v_tokens), len(c_tokens), 1)
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.5 else None


def extract_criteria(user_query: str) -> dict:
    """Calls the gateway LLM to turn a natural-language query into structured slots."""
    response = _call_llm_with_retry(
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
        criteria = {
            "budget": None, "city": None, "area": None, "bedrooms": None,
            "purpose": None, "property_category": None, "amenities": [], "investment_goals": None,
        }
        criteria["_extraction_error"] = raw

    # Post-extraction fuzzy snap: if the LLM produced a city/area that isn't
    # in the canonical list (e.g. "Joher Town" slipped through), snap it to
    # the closest valid value. This is a silent correction — the LLM already
    # did the heavy lifting; this just cleans up edge cases.
    if criteria.get("city"):
        snapped = _fuzzy_snap(criteria["city"], KNOWN_CITIES)
        if snapped:
            criteria["city"] = snapped
        else:
            # Nothing close enough — treat as unknown city so the agent says
            # "we don't have listings there" rather than searching nationwide.
            criteria["city"] = criteria["city"]  # leave as-is for no_coverage path

    if criteria.get("area"):
        snapped = _fuzzy_snap(criteria["area"], KNOWN_AREAS)
        if snapped:
            criteria["area"] = snapped
        # If no snap found, leave the raw value — recommend.py's _normalize_place
        # alias table is the final fallback.

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
        # BUG FIX: previously the raw PKR integer (e.g. 45000000) was handed
        # to the LLM and the LLM converted it to crore/lakh itself when
        # phrasing the spoken answer. It consistently got this wrong by 10x
        # (dividing by 1,000,000 like "million" instead of 10,000,000 for
        # "crore") — 45,000,000 PKR (4.5 crore) came out as "45 crore" on
        # calls. price_format.format_pkr_with_raw() does the conversion
        # deterministically in Python and hands the LLM both the correct
        # natural phrasing AND the exact raw figure (for any arithmetic it
        # needs, like computing an over-budget gap) — see price_format.py.
        price_str = format_pkr_with_raw(price)
        amenities = m.get("amenities") or []
        amenities_str = ", ".join(amenities) if amenities else "none listed"

        # Compute real gaps so the LLM never has to guess or round a figure itself.
        gaps = []
        budget = criteria.get("budget")
        if budget and price is not None and float(price) > budget:
            gaps.append(f"over budget by {format_pkr_delta(float(price) - budget)}")

        requested_city = criteria.get("city")
        if requested_city and m.get("city") and m["city"].lower() != requested_city.lower():
            gaps.append(f"not in requested city: {requested_city}")

        requested_area = criteria.get("area")
        if requested_area and m.get("area_name") and requested_area.lower() not in m["area_name"].lower():
            gaps.append(f"not in requested area: {requested_area}")

        breakdown = m.get("match_breakdown", {})
        amenities_breakdown = breakdown.get("amenities")
        if isinstance(amenities_breakdown, dict) and amenities_breakdown.get("missing"):
            gaps.append(f"missing requested amenities: {', '.join(amenities_breakdown['missing'])}")

        if breakdown.get("type_mismatch"):
            gaps.append(breakdown["type_mismatch"])

        gap_str = f" | GAPS: {'; '.join(gaps)}" if gaps else " | GAPS: none (exact match on stated criteria)"

        lines.append(
            f"- {m['title']} | {m['property_type']} | {m['city']}, {m['area_name']} | "
            f"price: {price_str} | bedrooms: {m['bedrooms']} | developer: {m['developer']} | "
            f"amenities: {amenities_str} | match_score: {m['match_score']}%{gap_str}"
        )
    return "\n".join(lines)


def _stable_variant(text: str, options: list[str]) -> str:
    if not options:
        return ""
    seed = sum(ord(char) for char in text)
    return options[seed % len(options)]


def _summarize_location(match: dict) -> str:
    city = match.get("city")
    area = match.get("area_name")
    if city and area:
        return f"{city}, {area}"
    if city:
        return city
    if area:
        return area
    return ""


def _summarize_gaps(match: dict, criteria: dict) -> str:
    breakdown = match.get("match_breakdown", {}) or {}
    gaps: list[str] = []

    requested_city = (criteria or {}).get("city")
    if requested_city and match.get("city") and match["city"].lower() != requested_city.lower():
        gaps.append(f"requested city not matched: {requested_city}")

    requested_area = (criteria or {}).get("area")
    if requested_area and match.get("area_name") and requested_area.lower() not in match["area_name"].lower():
        gaps.append(f"requested area not matched: {requested_area}")

    budget = (criteria or {}).get("budget")
    price = match.get("price") or match.get("rent_per_month")
    if budget and price is not None and float(price) > budget:
        gaps.append(f"over budget by {format_pkr_delta(float(price) - budget)}")

    if breakdown.get("type_mismatch"):
        gaps.append(breakdown["type_mismatch"])

    amenities_breakdown = breakdown.get("amenities")
    if isinstance(amenities_breakdown, dict):
        missing = amenities_breakdown.get("missing") or []
        if missing:
            gaps.append(f"missing {', '.join(missing)}")

    return "; ".join(gaps)


def _render_spoken_answer(user_query: str, matches: list[dict], criteria: dict) -> str:
    # BUG FIX: recommend_properties() now returns a single {"no_coverage": True,
    # "requested_city": ...} sentinel when the requested city has zero listings
    # anywhere in the DB (not just after other filters narrowed things down).
    # Previously this case fell through to the generic "no data" line below,
    # which is honest but vague — or, before the recommend.py fix, silently
    # substituted a different city's listing entirely. Say plainly, in
    # UrduLish, that this specific city isn't covered.
    if len(matches) == 1 and matches[0].get("no_coverage"):
        city = matches[0].get("requested_city") or ""
        return _stable_variant(
            user_query,
            [
                f"{city} mein hamare paas abhi listings nahi hain, lekin main aapko is area mein availability aane par batata hoon.",
                f"{city} ke liye hamare paas abhi koi property available nahi hai. Kya aap kisi doosre city ya area mein dekhna chahenge?",
            ],
        )

    if not matches:
        return _stable_variant(
            user_query,
            [
                "Mere paas is requirement ke liye abhi data nahi hai.",
                "Is requirement ke liye abhi mere paas data available nahi hai.",
            ],
        )

    top = matches[0]
    price = top.get("price") or top.get("rent_per_month")
    price_str = format_pkr(price)
    location_str = _summarize_location(top)
    gap_str = _summarize_gaps(top, criteria)
    exact_match = not gap_str and top.get("match_score", 0) >= 99.9

    if exact_match:
        opener = _stable_variant(
            user_query + top.get("title", ""),
            ["Jee bilkul", "Achha", "Jee zaroor"],
        )
        body_options = [
            f"aap ke liye yeh option theek hai: {top['title']}",
            f"yeh option aapki requirement ke kaafi close hai: {top['title']}",
        ]
        followups = [
            "Kya aap iske liye visit schedule karna chahenge?",
            "Aap chahenge to mein visit ke liye next step bata deta hoon.",
        ]
        return f"{opener}, {_stable_variant(top['title'], body_options)}{f', {location_str}' if location_str else ''}, price {price_str}. {_stable_variant(top['title'], followups)}"

    opener = _stable_variant(
        user_query + top.get("title", ""),
        ["Jee", "Achha", "Theek hai"],
    )
    intro = _stable_variant(
        user_query + (criteria.get("city") or criteria.get("area") or ""),
        [
            "exact match nahi mila, lekin closest option yeh hai",
            "perfect match nahi mila, lekin sab se qareeb option yeh hai",
            "aapki requirement ke liye closest option yeh hai",
        ],
    )
    location_part = f"{location_str}" if location_str else ""

    # Build a natural UrduLish caveat only for gaps that matter to the customer,
    # without ever exposing raw internal field names or English debug strings.
    caveat_parts = []
    breakdown = top.get("match_breakdown", {}) or {}

    requested_city = (criteria or {}).get("city")
    if requested_city and top.get("city") and top["city"].lower() != requested_city.lower():
        caveat_parts.append(f"yeh {requested_city} mein nahi hai")

    requested_area = (criteria or {}).get("area")
    if requested_area and top.get("area_name") and requested_area.lower() not in top["area_name"].lower():
        caveat_parts.append(f"exact area match nahi mila")

    budget = (criteria or {}).get("budget")
    price_val = top.get("price") or top.get("rent_per_month")
    if budget and price_val is not None and float(price_val) > budget:
        caveat_parts.append(f"budget se thoda zyada hai")

    if breakdown.get("type_mismatch"):
        caveat_parts.append("listing type alag hai")

    amenities_breakdown = breakdown.get("amenities")
    if isinstance(amenities_breakdown, dict) and amenities_breakdown.get("missing"):
        missing_str = ", ".join(amenities_breakdown["missing"])
        caveat_parts.append(f"{missing_str} available nahi")

    gap_part = f" Waise {', aur '.join(caveat_parts)}." if caveat_parts else ""

    followup = _stable_variant(
        top.get("title", "") + user_query,
        [
            "Kya aap isko dekhna chahenge?",
            "Aap chahenge to mein iski visit arrange kar doon?",
            "Kya mein is option ke liye next step share karun?",
        ],
    )
    pieces = [opener, intro]
    if top.get("title"):
        pieces.append(top["title"])
    if location_part:
        pieces.append(location_part)
    pieces.append(f"price {price_str}.")
    if gap_part:
        pieces.append(gap_part.strip())
    pieces.append(followup)
    return " ".join(piece.strip() for piece in pieces if piece.strip())


def _turn_role_and_content(turn) -> tuple[str, str]:
    """Accept both dict history items and LangChain message objects."""
    if isinstance(turn, dict):
        return turn.get("role", "user"), turn.get("content", "")

    role = getattr(turn, "type", None) or getattr(turn, "role", None) or "user"
    content = getattr(turn, "content", "")
    return role, content


def generate_grounded_answer(
    user_query: str,
    matches: list[dict],
    criteria: dict = None,
    conversation_history: list[dict] = None,
) -> str:
    """
    Produces a short spoken UrduLish response from grounded matches.
    The final wording is deterministic so the model cannot invent a city,
    area, or fallback option that was not actually returned.
    """
    return _render_spoken_answer(user_query, matches, criteria or {})




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
        property_category=criteria.get("property_category"),
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