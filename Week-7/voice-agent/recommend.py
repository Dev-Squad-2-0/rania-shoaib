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
        SELECT p.id, p.title, p.property_type, p.purpose_tag, p.price,
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
        price = prop["price"] or prop["rent_per_month"]
        if price is not None:
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
        points = WEIGHTS["city"] if prop["city"].lower() == criteria["city"].lower() else 0.0
        earned += points
        breakdown["city"] = points

    if criteria.get("area"):
        possible += WEIGHTS["area"]
        points = WEIGHTS["area"] if criteria["area"].lower() in prop["area_name"].lower() else 0.0
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
        points = WEIGHTS["purpose"] if prop["purpose_tag"] == criteria["purpose"] else 0.0
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
def recommend_properties(budget: float = None, city: str = None, area: str = None,
                          bedrooms: int = None, purpose: str = None,
                          amenities: list[str] = None, investment_goals: str = None,
                          top_n: int = 5) -> list[dict]:
    """
    Single entry point the voice agent calls. Fetches all available
    properties, scores each against the given criteria, and returns
    the top_n ranked by match score (highest first).
    """
    criteria = {
        "budget": budget, "city": city, "area": area, "bedrooms": bedrooms,
        "purpose": purpose, "amenities": amenities, "investment_goals": investment_goals,
    }

    candidates = fetch_candidates()
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