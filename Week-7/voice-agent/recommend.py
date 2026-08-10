"""
recommend.py
Task 4 — Property Recommendation Engine

Scores every available property against the criteria the user actually
gave (budget, city, area, bedrooms, purpose, amenities, investment_goals)
and returns a ranked list with a 0-100 match score plus a per-criterion
breakdown, so the calling LLM can explain *why* each result was picked
rather than just reading off a list.

Structured criteria (budget/city/area/bedrooms/purpose/amenities) are
scored from Postgres. Investment goals are free text, so they're scored
via semantic similarity against the property_description chunks already
sitting in the `realestate_knowledge` Chroma collection (see
ingest_chroma.py) — this reuses Task 3's semantic store rather than
building a second one.
"""

import re
from sqlalchemy import create_engine, text
import chromadb
from chromadb.utils import embedding_functions

DATABASE_URL = "postgresql://rania:mm1234@localhost:5432/realestate_agent"
PERSIST_DIR = "./chroma_store"
CHROMA_COLLECTION = "realestate_knowledge"

engine = create_engine(DATABASE_URL)
chroma_client = chromadb.PersistentClient(path=PERSIST_DIR)

multilingual_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)

# BUG FIX: extract_criteria() is an LLM call — it isn't guaranteed to hand
# back a bare city name that matches the DB's `locations.city` column
# exactly. STT/LLM variance routinely produces "Lahore, Pakistan",
# "Lahore City", trailing punctuation, etc. City matching used to be a
# strict `==`, which meant ANY of that variance silently failed to match,
# fetch_candidates()'s city filter fell through to "search every city",
# and a Lahore query could come back with a Karachi result that simply
# scored higher on budget/bedrooms — with nothing telling the caller their
# stated city was actually dropped. area matching already used substring
# containment for exactly this reason; city now does the same, via a
# shared normalizer that strips punctuation/common suffixes so "Lahore,"
# and "lahore city" both collapse to "lahore" before comparing.
def _normalize_place(value: str) -> str:
    if not value:
        return ""
    value = value.lower().strip()
    value = re.sub(r"[,.]", " ", value)
    value = re.sub(r"\b(city|pakistan|punjab|sindh)\b", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    # Phonetic / STT alias table — maps common mis-transcriptions to the
    # canonical spelling stored in the DB. These run AFTER basic normalization
    # so the keys can be written in already-lowercased, stripped form.
    _ALIASES = {
        "joher town": "johar town",
        "joher": "johar",
        "juhar town": "johar town",
        "juhar": "johar",
        "bahriya town": "bahria town",
        "bahriya": "bahria",
        "islambad": "islamabad",
        "krachi": "karachi",
        "lahor": "lahore",
        "rawlpindi": "rawalpindi",
        "faisal abad": "faisalabad",
        "gulburg": "gulberg",
        "gulberg iii": "gulberg iii",
        "f 10 markaz": "f-10 markaz",
        "f10 markaz": "f-10 markaz",
        "f 10": "f-10 markaz",
        "f10": "f-10 markaz",
    }
    return _ALIASES.get(value, value)


def _place_matches(requested: str, actual: str) -> bool:
    requested_norm = _normalize_place(requested)
    actual_norm = _normalize_place(actual)
    if not requested_norm or not actual_norm:
        return False
    return requested_norm in actual_norm or actual_norm in requested_norm


# How much each criterion is worth if the user provides it. Only
# criteria that are actually given are added to the denominator, so a
# property isn't penalized for a criterion nobody asked about.
WEIGHTS = {
    "budget": 25,
    "city": 15,
    "area": 10,
    "bedrooms": 15,
    "purpose": 10,
    "amenities": 15,
    "investment_goals": 10,
}


# ---------------------------------------------------------------
# 1. CANDIDATE FETCH (structured facts + aggregated amenities)
# ---------------------------------------------------------------
def fetch_candidates() -> list[dict]:
    """
    Pulls every available property with its location, developer, and
    aggregated amenity list in one query, so scoring is done in Python
    against a fully-hydrated in-memory set rather than re-querying per
    property.
    """
    query = """
        SELECT p.id, p.title, p.property_type, p.listing_status, p.purpose_tag, p.price,
               p.rent_per_month, p.area_sqft, p.bedrooms,
               l.area_name, l.city, d.name AS developer,
               COALESCE(array_agg(a.name) FILTER (WHERE a.name IS NOT NULL), '{}') AS amenities
        FROM properties p
        JOIN locations l ON p.location_id = l.id
        JOIN developers d ON p.developer_id = d.id
        LEFT JOIN property_amenities pa ON pa.property_id = p.id
        LEFT JOIN amenities a ON a.id = pa.amenity_id
        WHERE p.is_available = TRUE
        GROUP BY p.id, l.area_name, l.city, d.name
    """
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return [dict(row._mapping) for row in result]


# ---------------------------------------------------------------
# 2. SEMANTIC INVESTMENT-GOAL SCORES
# ---------------------------------------------------------------
def investment_goal_scores(investment_goals: str, n_results: int = 20) -> dict[int, float]:
    """
    Queries the property_description chunks for semantic closeness to
    the user's free-text investment goal (e.g. "high rental yield,
    low maintenance"). Returns {property_id: similarity_0_to_1}.
    Distance is Chroma's cosine distance (0 = identical); we convert
    to a 0-1 similarity so it composes cleanly with the other scores.
    """
    collection = chroma_client.get_collection(
        CHROMA_COLLECTION, embedding_function=multilingual_ef
    )
    results = collection.query(
        query_texts=[investment_goals],
        n_results=n_results,
        where={"type": "property_description"},
    )

    scores = {}
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        pid = meta.get("property_id")
        if pid is None:
            continue
        similarity = max(0.0, 1 - dist / 2)  # cosine distance ranges ~0-2
        # Keep the best similarity seen per property if it appears more than once
        scores[pid] = max(scores.get(pid, 0.0), similarity)
    return scores


# ---------------------------------------------------------------
# 3. SCORING
# ---------------------------------------------------------------
def score_property(prop: dict, criteria: dict, goal_scores: dict[int, float]) -> dict:
    """
    Scores a single property against whatever criteria were provided.
    Returns {"score": 0-100, "breakdown": {criterion: points_earned}}.
    """
    earned = 0.0
    possible = 0.0
    breakdown = {}

    if criteria.get("budget") is not None:
        possible += WEIGHTS["budget"]
        # Compare against the price field that actually matches what this
        # property IS (a purchase price for a 'buy' listing, a monthly
        # rent for a 'rent' listing) — never fall back to whichever field
        # happens to be non-null, since that silently compares a monthly
        # rent figure against a purchase budget (or vice versa) and
        # always looks like a huge bargain regardless of fit.
        if prop["listing_status"] == "buy":
            price = prop["price"]
        elif prop["listing_status"] == "rent":
            price = prop["rent_per_month"]
        else:
            price = prop["price"] or prop["rent_per_month"]

        type_mismatch = bool(criteria.get("purpose")) and prop["listing_status"] != criteria["purpose"]
        if type_mismatch:
            # Budget isn't a meaningful comparison across listing types —
            # a "3 crore buy budget" says nothing about whether a rental's
            # monthly rent is reasonable. Zero this dimension out rather
            # than let it accidentally inflate the score, and surface the
            # mismatch so the caller can be told honestly.
            breakdown["budget"] = 0.0
            breakdown["type_mismatch"] = f"customer wants to {criteria['purpose']}, this listing is for {prop['listing_status']}"
        elif price is not None:
            price = float(price)  # Postgres NUMERIC comes back as Decimal; normalize to float
            if price <= criteria["budget"]:
                points = WEIGHTS["budget"]
            else:
                over_pct = (price - criteria["budget"]) / criteria["budget"]
                # Full credit at exact budget, tapering to 0 by +30% over
                points = max(0.0, WEIGHTS["budget"] * (1 - over_pct / 0.30))
            earned += points
            breakdown["budget"] = round(points, 1)
        else:
            breakdown["budget"] = 0.0

    if criteria.get("city"):
        possible += WEIGHTS["city"]
        points = WEIGHTS["city"] if _place_matches(criteria["city"], prop["city"]) else 0.0
        earned += points
        breakdown["city"] = points

    if criteria.get("area"):
        possible += WEIGHTS["area"]
        points = WEIGHTS["area"] if _place_matches(criteria["area"], prop["area_name"]) else 0.0
        earned += points
        breakdown["area"] = points

    if criteria.get("bedrooms") is not None:
        possible += WEIGHTS["bedrooms"]
        beds = prop["bedrooms"]
        if beds is None:
            points = 0.0
        elif beds == criteria["bedrooms"]:
            points = WEIGHTS["bedrooms"]
        elif beds > criteria["bedrooms"]:
            points = WEIGHTS["bedrooms"] * 0.7  # extra room: still usable, partial credit
        else:
            points = 0.0  # fewer bedrooms than required is a hard miss
        earned += points
        breakdown["bedrooms"] = round(points, 1)

    if criteria.get("purpose"):
        possible += WEIGHTS["purpose"]
        # listing_status is the buy/rent column — purpose_tag is a
        # DIFFERENT column (residential/investment/commercial) and was
        # being compared here before, which meant this always scored 0
        # no matter what, silently, for every property.
        points = WEIGHTS["purpose"] if prop["listing_status"] == criteria["purpose"] else 0.0
        earned += points
        breakdown["purpose"] = points

    if criteria.get("amenities"):
        possible += WEIGHTS["amenities"]
        wanted = [a.lower() for a in criteria["amenities"]]
        have = [a.lower() for a in prop["amenities"]]
        # Substring match both ways: "pool" should match "Swimming Pool",
        # "gym" should match "Gymnasium" — real amenity names in the DB
        # are multi-word, but voice queries will use short/casual terms.
        matched = [w for w in wanted if any(w in h or h in w for h in have)]
        missing = [w for w in wanted if w not in matched]
        points = WEIGHTS["amenities"] * (len(matched) / len(wanted)) if wanted else 0.0
        earned += points
        breakdown["amenities"] = {
            "points": round(points, 1),
            "matched": matched,
            "missing": missing,
        }

    if criteria.get("investment_goals"):
        possible += WEIGHTS["investment_goals"]
        similarity = goal_scores.get(prop["id"], 0.0)
        points = WEIGHTS["investment_goals"] * similarity
        earned += points
        breakdown["investment_goals"] = round(points, 1)

    match_pct = round((earned / possible) * 100, 1) if possible > 0 else 0.0
    return {"score": match_pct, "breakdown": breakdown}


# ---------------------------------------------------------------
# 4. MAIN ENTRY POINT
# ---------------------------------------------------------------
RESIDENTIAL_TYPES = {"house", "apartment", "villa", "bungalow"}
COMMERCIAL_TYPES = {"shop", "office", "commercial", "plaza"}


def recommend_properties(budget: float = None, city: str = None, area: str = None,
                          bedrooms: int = None, purpose: str = None,
                          amenities: list[str] = None, investment_goals: str = None,
                          property_category: str = None,
                          top_n: int = 5) -> list[dict]:
    """
    Single entry point the voice agent calls. Fetches all available
    properties, scores each against the given criteria, and returns
    the top_n ranked by match score (highest first).
    """
    criteria = {
        "budget": budget, "city": city, "area": area, "bedrooms": bedrooms,
        "purpose": purpose, "amenities": amenities, "investment_goals": investment_goals,
        "property_category": property_category,
    }

    candidates = fetch_candidates()

    if city:
        # BUG FIX: was strict `==`, see _place_matches() docstring above —
        # normalized substring match now so LLM-extracted city variants
        # ("Lahore, Pakistan" etc.) don't silently fall through to a
        # nationwide search.
        city_matched = [c for c in candidates if _place_matches(city, c["city"])]
        if not city_matched:
            # No listings anywhere in this city at all (not just after
            # other filters narrowed things down) — say so plainly instead
            # of quietly substituting a different city's result. This is
            # what was happening for Faisalabad, and by exact-match luck
            # sometimes for Lahore too.
            return [{
                "no_coverage": True,
                "requested_city": city,
            }]
        candidates = city_matched

    if area:
        area_matched = [c for c in candidates if _place_matches(area, c["area_name"])]
        if area_matched:
            candidates = area_matched

    if purpose:
        purpose_matched = [c for c in candidates if c["listing_status"] == purpose]
        # Buy vs rent is categorical, not a matter of degree — a renter
        # asking to buy shouldn't have rentals ranked alongside real buy
        # options. Filter to the requested type first. Only fall back to
        # the full candidate pool (with the mismatch flagged in scoring
        # above) if literally nothing of that type exists, so the caller
        # still gets *something* rather than an empty result.
        if purpose_matched:
            candidates = purpose_matched

    if property_category in ("residential", "commercial"):
        # BUG FIX (rental_01): nothing was filtering residential vs commercial
        # category at all — bedrooms-based filtering below only kicks in when
        # the caller states a bedroom count, so "rental apartment" with no
        # bedroom count could still surface a commercial shop purely because
        # it scored well on budget/city. Category is categorical like
        # buy/rent, not a matter of degree, so this is a hard filter (with
        # the same fallback-to-full-pool safety net as the purpose filter
        # above, so we still return *something* if nothing of that category
        # exists).
        wanted_types = RESIDENTIAL_TYPES if property_category == "residential" else COMMERCIAL_TYPES
        category_matched = [c for c in candidates if c["property_type"] in wanted_types]
        if category_matched:
            candidates = category_matched

    if bedrooms is not None:
        # Plots and commercial units have no bedroom concept at all
        # (bedrooms is NULL by design, not "fewer than requested") — if
        # the customer specifically asked for N bedrooms, they want a
        # house/apartment, full stop. Without this, a cheap plot with
        # beds=None can still outscore an actual N-bedroom house purely
        # on budget, which is a nonsensical recommendation regardless of
        # how the weighted math works out.
        bedroom_capable = [c for c in candidates if c["property_type"] in ("house", "apartment")]
        if bedroom_capable:
            candidates = bedroom_capable

    if not candidates:
        return []

    goal_scores = investment_goal_scores(investment_goals) if investment_goals else {}

    scored = []
    for prop in candidates:
        result = score_property(prop, criteria, goal_scores)
        scored.append({**prop, "match_score": result["score"], "match_breakdown": result["breakdown"]})

    scored.sort(key=lambda p: p["match_score"], reverse=True)
    return scored[:top_n]


if __name__ == "__main__":
    results = recommend_properties(
        budget=5000000,
        city="Karachi",
        bedrooms=3,
        amenities=["swimming pool", "security"],
        investment_goals="steady rental income with low maintenance",
        top_n=5,
    )

    print(f"Top {len(results)} recommendations:\n")
    for r in results:
        print(f"{r['match_score']}% match — {r['title']} ({r['city']}, {r['area_name']})")
        print(f"  price={r['price']} | bedrooms={r['bedrooms']} | developer={r['developer']}")
        print(f"  breakdown: {r['match_breakdown']}")
        print()