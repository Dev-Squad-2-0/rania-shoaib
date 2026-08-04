"""
ingest_chroma.py
Reads unstructured_data.json and loads FAQs + property description chunks
into a local, persistent ChromaDB collection for semantic retrieval.

Run with your venv activated:
    python ingest_chroma.py
"""

import json
import chromadb

from chromadb.utils import embedding_functions

multilingual_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)


DATA_FILE = "unstructured_data.json"
PERSIST_DIR = "./chroma_store"   # data will be saved here, survives restarts
COLLECTION_NAME = "realestate_knowledge"



def load_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    data = load_data()

    # Persistent client -> writes to disk in PERSIST_DIR instead of memory-only
    client = chromadb.PersistentClient(path=PERSIST_DIR)

    # Fresh start each run, avoids duplicate entries while you're iterating
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
    name=COLLECTION_NAME,
    embedding_function=multilingual_ef
)

    documents = []
    metadatas = []
    ids = []

    # --- FAQs ---
    for faq in data["faqs"]:
        # Embed question + answer together so a query matches on either
        documents.append(f"Q: {faq['question']} A: {faq['answer']}")
        metadatas.append({
            "type": "faq",
            "category": faq["category"],
            "language": faq.get("language", "unknown")
        })
        ids.append(faq["id"])

    # --- Property description chunks ---
    for desc in data["property_description_chunks"]:
        documents.append(desc["text"])
        metadatas.append({
            "type": "property_description",
            "property_id": desc["property_id"]
        })
        ids.append(desc["id"])

    collection.add(documents=documents, metadatas=metadatas, ids=ids)

    print(f"Ingested {len(documents)} documents into '{COLLECTION_NAME}'.")
    print(f"  - {len(data['faqs'])} FAQs")
    print(f"  - {len(data['property_description_chunks'])} property description chunks")

    # --- Quick retrieval test ---
    test_queries = [
        "kya installment plan hai",
        "sea view apartment with pool",
        "security deposit for rent"
    ]

    print("\n--- Retrieval test ---")
    for q in test_queries:
        results = collection.query(query_texts=[q], n_results=2)
        print(f"\nQuery: '{q}'")
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            print(f"  [{meta['type']}] {doc[:100]}...")


if __name__ == "__main__":
    
    main()