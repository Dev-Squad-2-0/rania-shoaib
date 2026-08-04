"""
rag_pipeline.py
Task 2 — Build RAG Pipeline

Implements each stage as a separate, testable function:
    load_documents()   -> Document Loader
    chunk_text()        -> Chunking
    build_vector_store() -> Embedding + Vector Store (Chroma)
    retrieve()           -> Retriever
    generate_answer()    -> Answer generation (calls company LLM gateway)

Run directly to see the pipeline work end-to-end on the brochure document.
"""

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

import os
import glob
import chromadb
from openai import OpenAI  # gateway is OpenAI-compatible

DOCS_DIR = "./documents"
PERSIST_DIR = "./chroma_store"

# --- Company LLM gateway config (from bootcamp environment) ---
GATEWAY_BASE_URL = "https://llm.netixsol.com/v1"
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY")
GATEWAY_MODEL = "smart"

from chromadb.utils import embedding_functions

multilingual_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="paraphrase-multilingual-MiniLM-L12-v2"
)


# ---------------------------------------------------------------
# 1. DOCUMENT LOADER
# ---------------------------------------------------------------
def load_documents(docs_dir: str = DOCS_DIR) -> list[dict]:
    """
    Loads all .txt files from a directory.
    Returns a list of {"source": filename, "text": full_text}.
    Kept deliberately simple (.txt only) — swap in PyPDF2/python-docx
    loaders here later if brochures arrive as PDF/Word instead.
    """
    documents = []
    for path in glob.glob(os.path.join(docs_dir, "*.txt")):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        documents.append({"source": os.path.basename(path), "text": text})
    return documents


# ---------------------------------------------------------------
# 2. CHUNKING
# ---------------------------------------------------------------
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    Simple fixed-size character chunker with overlap.
    chunk_size / overlap are in characters, not tokens (good enough
    for evaluation purposes at this stage).
    Overlap prevents a sentence from being awkwardly split with no
    shared context between adjacent chunks.
    """
    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap  # move forward, re-including the overlap

    return chunks


# ---------------------------------------------------------------
# 3. EMBEDDING + VECTOR STORE
# ---------------------------------------------------------------
def build_vector_store(documents: list[dict], chunk_size: int, overlap: int = 50,
                        collection_name: str = "brochure_chunks"):
    """
    Chunks each document and loads the chunks into a persistent
    Chroma collection. Collection name includes chunk_size so multiple
    chunk-size experiments can coexist without overwriting each other.
    """
    client = chromadb.PersistentClient(path=PERSIST_DIR)

    full_collection_name = f"{collection_name}_{chunk_size}"
    try:
        client.delete_collection(full_collection_name)
    except Exception:
        pass

    collection = client.create_collection(
    name=full_collection_name,
    embedding_function=multilingual_ef
)
    all_chunks, all_ids, all_metadata = [], [], []
    for doc in documents:
        chunks = chunk_text(doc["text"], chunk_size=chunk_size, overlap=overlap)
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_ids.append(f"{doc['source']}_chunk{i}_size{chunk_size}")
            all_metadata.append({"source": doc["source"], "chunk_index": i})

    collection.add(documents=all_chunks, metadatas=all_metadata, ids=all_ids)
    return collection, len(all_chunks)


# ---------------------------------------------------------------
# 4. RETRIEVER
# ---------------------------------------------------------------
def retrieve(collection, query: str, n_results: int = 3) -> list[dict]:
    """
    Returns top-n matching chunks with their similarity distance,
    so retrieval quality can be inspected directly during evaluation.
    """
    results = collection.query(query_texts=[query], n_results=n_results)
    output = []
    for doc, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        output.append({"text": doc, "metadata": meta, "distance": dist})
    return output


# ---------------------------------------------------------------
# 5. ANSWER GENERATION
# ---------------------------------------------------------------
def generate_answer(query: str, retrieved_chunks: list[dict]) -> str:
    """
    Sends the retrieved context + the user's question to the company
    LLM gateway and returns a grounded answer. If the answer isn't
    supported by the retrieved context, the model is instructed to say so
    rather than guessing — this is the core anti-hallucination guardrail.
    """
    context = "\n\n".join(c["text"] for c in retrieved_chunks)

    system_prompt = (
        "You are a real estate assistant. Answer ONLY using the provided "
        "context. If the context does not contain the answer, say clearly "
        "that this information isn't available rather than guessing. "
        "Keep the answer concise and in UrduLish (mixed Urdu-English, "
        "natural conversational tone)."
    )

    user_prompt = f"Context:\n{context}\n\nQuestion: {query}"

    client = OpenAI(base_url=GATEWAY_BASE_URL, api_key=GATEWAY_API_KEY)
    response = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------
# Demo run
# ---------------------------------------------------------------
if __name__ == "__main__":
    docs = load_documents()
    print(f"Loaded {len(docs)} document(s): {[d['source'] for d in docs]}")

    collection, chunk_count = build_vector_store(docs, chunk_size=500)
    print(f"Created {chunk_count} chunks (size=500) in vector store.")

    query = "kya installment plan available hai aur late payment par kya hota hai?"
    retrieved = retrieve(collection, query, n_results=3)

    print(f"\nTop retrieved chunks for: '{query}'")
    for r in retrieved:
        print(f"  distance={r['distance']:.3f} | {r['text'][:100]}...")

    answer = generate_answer(query, retrieved)
    print(f"\nGenerated answer:\n{answer}")