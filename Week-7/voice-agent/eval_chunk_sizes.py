"""
evaluate_chunk_sizes.py
Task 2 — "Evaluate different chunk sizes"

Builds separate vector stores at chunk_size = 200, 500, 1000 characters,
runs the same set of test queries against each, and prints the retrieval
distance (lower = closer semantic match) so the sizes can be compared
side by side.
"""

from rag_pipeline import load_documents, build_vector_store, retrieve

CHUNK_SIZES = [200, 500, 1000]
OVERLAP = 50

TEST_QUERIES = [
    "kya installment plan hai aur late payment par kya hota hai",
    "security deposit kitna hota hai rent ke liye",
    "commercial property ke liye kya documentation chahiye",
    "site visit book karne ka process kya hai",
]


def main():
    docs = load_documents()

    for size in CHUNK_SIZES:
        collection, chunk_count = build_vector_store(docs, chunk_size=size, overlap=OVERLAP)
        print(f"\n{'=' * 60}")
        print(f"CHUNK SIZE = {size} chars  |  total chunks = {chunk_count}")
        print(f"{'=' * 60}")

        for query in TEST_QUERIES:
            results = retrieve(collection, query, n_results=1)
            top = results[0]
            print(f"\nQuery: '{query}'")
            print(f"  best distance: {top['distance']:.4f}")
            print(f"  matched text: {top['text'][:120]}...")


if __name__ == "__main__":
    main()