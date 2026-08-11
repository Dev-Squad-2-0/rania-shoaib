"""
unified_retriever.py
Task 3 — Structured Retrieval

Routes a query to the correct retrieval method:
    - Postgres/SQL  -> exact structured facts (price, availability, plot size, agent/developer names)
    - ChromaDB       -> semantic/natural-language content (brochures, descriptions, FAQs)

This is a simple keyword-based router for now, good enough to prove the
split works. A production version would let the LLM itself decide which
tool to call (this is exactly what the LangGraph agent will do later —
this file is the retrieval logic the agent's tool nodes will wrap).
"""

from sqlalchemy import create_engine, text
import chromadb
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://rania:mm1234@localhost:5432/realestate_agent"
)
PERSIST_DIR = "./chroma_store"
CHROMA_COLLECTION = "realestate_knowledge"  # from ingest_chroma.py

engine = create_engine(DATABASE_URL)

# CHROMA_MODE=http  -> connect to a ChromaDB container (docker-compose)
# CHROMA_MODE=local -> connect to a local PersistentClient directory (dev)
_chroma_mode = os.environ.get("CHROMA_MODE", "local").lower()
if _chroma_mode == "http":
    chroma_client = chromadb.HttpClient(
        host=os.environ.get("CHROMA_HOST", "localhost"),
        port=int(os.environ.get("CHROMA_PORT", "8001")),
    )
else:
    chroma_client = chromadb.PersistentClient(path=PERSIST_DIR)

from chromadb.utils import embedding_functions

multilingual_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)


# ---------------------------------------------------------------
# STRUCTURED RETRIEVAL (SQL) — prices, availability, plot sizes, developer names
# ---------------------------------------------------------------
def structured_search(location: str = None, max_price: float = None,
                       property_type: str = None, min_bedrooms: int = None) -> list[dict]:
    """
    Exact, filterable lookups. This is the kind of question that has one
    correct, verifiable answer — semantic search would only approximate it.
    """
    query = """
        SELECT p.id, p.title, p.property_type, p.listing_status, p.price,
               p.rent_per_month, p.area_sqft, p.bedrooms, l.area_name, d.name AS developer
        FROM properties p
        JOIN locations l ON p.location_id = l.id
        JOIN developers d ON p.developer_id = d.id
        WHERE p.is_available = TRUE
    """
    params = {}
    if location:
        query += " AND l.area_name ILIKE :location"
        params["location"] = f"%{location}%"
    if max_price:
        query += " AND (p.price IS NULL OR p.price <= :max_price)"
        params["max_price"] = max_price
    if property_type:
        query += " AND p.property_type = :property_type"
        params["property_type"] = property_type
    if min_bedrooms:
        query += " AND p.bedrooms >= :min_bedrooms"
        params["min_bedrooms"] = min_bedrooms

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        return [dict(row._mapping) for row in result]


# ---------------------------------------------------------------
# SEMANTIC RETRIEVAL (Chroma) — brochures, descriptions, FAQs
# ---------------------------------------------------------------
def semantic_search(query_text: str, n_results: int = 3, property_id: int = None) -> list[dict]:
    """
    property_id: when given, prefer results scoped to that specific
    property's own description chunks (see rag_node in agent_graph.py —
    this stops follow-up questions about a property already on the table
    from getting answered with a semantically-similar but unrelated
    property's facts).

    BUG FIX: FAQ chunks (ingest_chroma.py's `data["faqs"]` loop) are never
    given a property_id in their metadata at all — only
    property_description chunks are. A naive `where={"property_id": ...}`
    filter on the whole collection would silently exclude every FAQ
    whenever a property was on the table, so a genuine policy question
    ("installment plan hai?") asked mid-property-discussion would come
    back empty even though the FAQ obviously exists. Instead: when scoped,
    query property_description chunks for that property AND faq chunks
    (unfiltered) separately, then return whichever set is the better
    semantic match — so property follow-ups stay grounded, but FAQs are
    never accidentally hidden just because a property is in context.
    """
    collection = chroma_client.get_collection(
        CHROMA_COLLECTION,
        embedding_function=multilingual_ef
    )

    if property_id is None:
        results = collection.query(query_texts=[query_text], n_results=n_results)
        if not results["documents"][0]:
            return []
        return [
            {"text": doc, "metadata": meta}
            for doc, meta in zip(results["documents"][0], results["metadatas"][0])
        ]

    scoped = collection.query(
        query_texts=[query_text],
        n_results=n_results,
        where={"property_id": property_id},
    )
    faq = collection.query(
        query_texts=[query_text],
        n_results=n_results,
        where={"type": "faq"},
    )

    scoped_hits = list(zip(
        scoped["documents"][0], scoped["metadatas"][0], scoped["distances"][0]
    )) if scoped["documents"][0] else []
    faq_hits = list(zip(
        faq["documents"][0], faq["metadatas"][0], faq["distances"][0]
    )) if faq["documents"][0] else []

    combined = sorted(scoped_hits + faq_hits, key=lambda hit: hit[2])[:n_results]
    return [{"text": doc, "metadata": meta} for doc, meta, _dist in combined]


# ---------------------------------------------------------------
# ROUTER — decides which retrieval method a query needs
# ---------------------------------------------------------------
STRUCTURED_KEYWORDS = [
    "price", "qeemat", "budget", "available", "kitne", "bedroom", "bed",
    "marla", "kanal", "sqft", "size", "developer", "kitna", "rent per month",
    # BUG FIX: "how many listings do you have" / "kitni listings hain" is a
    # factual, DB-answerable question exactly like price/availability —
    # it had no keyword and was falling into the semantic branch, which
    # searches brochure text (unhelpful and prone to reading out one
    # random property's description instead of an actual count/list).
    "listing", "listings",
]


def route_query(user_query: str) -> str:
    """Very simple heuristic router based on keyword presence."""
    lowered = user_query.lower()
    if any(kw in lowered for kw in STRUCTURED_KEYWORDS):
        return "structured"
    return "semantic"


def unified_retrieve(user_query: str, property_id: int = None, **structured_filters) -> dict:
    """
    Single entry point the agent will call. Returns both the routing
    decision and the results, so the calling LLM can see what was used.

    property_id, when given, scopes semantic_search to that property's own
    chunks (see semantic_search's docstring) — the fix for follow-up
    questions hallucinating facts from an unrelated property.
    """
    route = route_query(user_query)
    if route == "structured":
        results = structured_search(**structured_filters)
    else:
        results = semantic_search(user_query, property_id=property_id)
    return {"route": route, "results": results, "grounded_to_property_id": property_id}


if __name__ == "__main__":
    # Example 1: clearly structured — has a price filter
    r1 = unified_retrieve("3 bedroom houses under 5 crore", max_price=50000000, min_bedrooms=3)
    print(f"Query 1 routed to: {r1['route']}")
    for row in r1["results"][:3]:
        print(f"  {row['title']} | PKR {row['price']}")

    # Example 2: clearly semantic — natural language, no hard filter
    r2 = unified_retrieve("kya aap registry mein madad karte hain")
    print(f"\nQuery 2 routed to: {r2['route']}")
    for row in r2["results"][:2]:
        print(f"  {row['text'][:100]}...")