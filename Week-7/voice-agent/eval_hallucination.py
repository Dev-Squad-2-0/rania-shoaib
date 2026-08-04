"""
eval_hallucination.py
Task 5 — Hallucination Evaluation

Runs 20 ground-truth-labeled questions through the real RAG pipeline
(rag_pipeline.retrieve + rag_pipeline.generate_answer) and measures:

    Retrieval Accuracy — did the retrieved chunks actually contain the
        info needed to answer? Checked against human-labeled keywords
        per question (ground truth), NOT judged by an LLM — an LLM
        judge can't know what the "correct" source content was meant
        to be, only a human deciding the test set can.

    Grounding Rate — of the answers where retrieval succeeded, what
        fraction are fully supported by the retrieved context? Judged
        by the gateway LLM acting as a grader.

    Hallucination Rate — what fraction of ALL answers contain at least
        one fabricated claim not present in the retrieved context?
        Also judged by the gateway LLM. Distinct from grounding: an
        answer can correctly say "I don't have that information" (not
        hallucinated) while still technically being "ungrounded" if
        retrieval failed to surface anything relevant.

Production note: LLM-as-judge scores here are a fast, repeatable proxy
for reruns/CI, not a substitute for human review. Spot-check a sample
of JUDGE_VERDICTS in the output against your own reading before
trusting this as a hard quality gate.
"""

import os
import json
import time
from rag_pipeline import load_documents, build_vector_store, retrieve, generate_answer
from openai import OpenAI, APITimeoutError, APIError

GATEWAY_BASE_URL = "https://llm.netixsol.com/v1"
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY")
GATEWAY_MODEL = "smart"

CHUNK_SIZE = 200   # locked in from evaluate_chunk_sizes.py results
N_RESULTS = 5      # widened from 3 — "site visit" section ranked outside top-3 at n=3

# ---------------------------------------------------------------
# Ground-truth test set — 20 questions against the brochure.
# `expect_keywords`: any ONE of these appearing in retrieved chunks
# counts as a retrieval hit (human-labeled, not LLM-judged).
# `answerable`: False = question deliberately has no answer in the
# brochure, so the correct behavior is for the model to say so —
# these test that the system doesn't invent an answer under pressure.
# ---------------------------------------------------------------
TEST_QUESTIONS = [
    {"q": "kya installment plan available hai?", "expect_keywords": ["installment", "down payment"], "answerable": True},
    {"q": "security deposit kitna hota hai rent ke liye?", "expect_keywords": ["security deposit", "two months"], "answerable": True},
    {"q": "commercial property ke liye kya documentation chahiye?", "expect_keywords": ["commercial", "documentation", "NTN"], "answerable": True},
    {"q": "site visit book karne ka process kya hai?", "expect_keywords": ["site visit", "booking"], "answerable": True},
    {"q": "kya aap registry mein madad karte hain?", "expect_keywords": ["registry", "legal", "title"], "answerable": True},
    {"q": "rental agreement kitne mahine ka hota hai?", "expect_keywords": ["eleven-month", "rental agreement"], "answerable": True},
    {"q": "bank financing ke liye kya madad milti hai?", "expect_keywords": ["bank financing", "partner bank"], "answerable": True},
    {"q": "late payment par kya penalty lagti hai?", "expect_keywords": ["payment plan documentation", "penalt"], "answerable": True},
    {"q": "kaunse developers ke saath aap kaam karte hain?", "expect_keywords": ["Bahria Town", "DHA City", "Emaar"], "answerable": True},
    {"q": "site visit ke liye koi charge hai?", "expect_keywords": ["no charge", "free"], "answerable": True},
    {"q": "title dispute hone par kya hota hai?", "expect_keywords": ["title dispute", "legal support team"], "answerable": True},
    {"q": "investment ke liye kya guidance milti hai?", "expect_keywords": ["investment", "historical"], "answerable": True},
    {"q": "kya aap resale properties bhi list karte hain?", "expect_keywords": ["resale", "verified"], "answerable": True},
    {"q": "what is the company's refund policy on cancelled bookings?", "expect_keywords": ["__NONE__"], "answerable": False},
    {"q": "does the company offer international property listings outside Pakistan?", "expect_keywords": ["__NONE__"], "answerable": False},
    {"q": "kya aap crypto mein payment accept karte hain?", "expect_keywords": ["__NONE__"], "answerable": False},
    {"q": "what discount do returning clients get on their third purchase?", "expect_keywords": ["__NONE__"], "answerable": False},
    {"q": "consultants budget aur location ke against kaise match karte hain?", "expect_keywords": ["consultants match", "budget, location"], "answerable": True},
    {"q": "under-construction properties par down payment kitna hota hai?", "expect_keywords": ["20%", "50%"], "answerable": True},
    {"q": "zoning rules commercial properties par kaise apply hoti hain?", "expect_keywords": ["zoning", "usage"], "answerable": True},
]


JUDGE_SYSTEM_PROMPT = """You are a strict grading assistant for a RAG hallucination audit.
You will be given a QUESTION, the CONTEXT that was retrieved for it, and the ANSWER a model generated.

Judge two things:
1. "grounded": true only if EVERY factual claim in the ANSWER is directly supported by the CONTEXT.
   If the ANSWER correctly says the information isn't available, that counts as grounded=true.
2. "hallucinated": true if the ANSWER states ANY specific fact (number, policy, name, process detail)
   that is NOT present in the CONTEXT, even if the rest of the answer is accurate.

Respond ONLY with valid JSON, no markdown, no preamble:
{"grounded": true/false, "hallucinated": true/false, "reasoning": "one sentence"}
"""


def judge_answer(question: str, context: str, answer: str) -> dict:
    client = OpenAI(base_url=GATEWAY_BASE_URL, api_key=GATEWAY_API_KEY)
    user_prompt = f"QUESTION:\n{question}\n\nCONTEXT:\n{context}\n\nANSWER:\n{answer}"
    response = client.chat.completions.create(
        model=GATEWAY_MODEL,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"grounded": None, "hallucinated": None, "reasoning": f"JUDGE_PARSE_FAILED: {raw[:200]}"}


def retrieval_hit(retrieved_chunks: list[dict], expect_keywords: list[str], answerable: bool) -> bool:
    """
    For answerable questions: hit if any expect_keyword appears in any
    retrieved chunk (case-insensitive substring match). Whitespace is
    normalized first — source text can have mid-phrase line breaks
    (e.g. "Security\ndeposits" in the brochure), which would otherwise
    cause false negatives on multi-word keywords like "security deposit".
    For deliberately unanswerable questions: retrieval "succeeds" if it
    does NOT confidently surface irrelevant content as if it were on-topic
    — in practice we just don't score these against keywords (there are
    none to match), they're scored purely on hallucination/grounding.
    """
    if not answerable:
        return True  # not applicable — these are graded on the generation side only
    combined = " ".join(" ".join(c["text"].lower().split()) for c in retrieved_chunks)
    return any(" ".join(kw.lower().split()) in combined for kw in expect_keywords)


def with_retries(fn, *args, retries=3, delay=5, **kwargs):
    """
    Retries a gateway-calling function on timeout/API errors before
    giving up, so one flaky network call doesn't kill the whole 20-question
    batch. Waits `delay` seconds between attempts (gateway may be
    momentarily overloaded, not permanently down).
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except (APITimeoutError, APIError) as e:
            last_err = e
            print(f"    [retry {attempt}/{retries}] gateway error: {e}")
            time.sleep(delay)
    raise last_err


def main():
    docs = load_documents()
    if not docs:
        print("No documents loaded — check ./documents/ contains company_brochure.txt")
        return

    collection, chunk_count = build_vector_store(docs, chunk_size=CHUNK_SIZE)
    print(f"Vector store ready: {chunk_count} chunks (size={CHUNK_SIZE}).\n")

    results = []
    for i, item in enumerate(TEST_QUESTIONS, 1):
        retrieved = retrieve(collection, item["q"], n_results=N_RESULTS)
        hit = retrieval_hit(retrieved, item["expect_keywords"], item["answerable"])
        context = "\n\n".join(c["text"] for c in retrieved)

        try:
            answer = with_retries(generate_answer, item["q"], retrieved)
            verdict = with_retries(judge_answer, item["q"], context, answer)
        except (APITimeoutError, APIError) as e:
            print(f"[{i}/20] SKIPPED — gateway unreachable after retries: {e}")
            results.append({
                "n": i, "question": item["q"], "answerable": item["answerable"],
                "retrieval_hit": hit, "answer": None,
                "grounded": None, "hallucinated": None,
                "reasoning": f"SKIPPED_GATEWAY_ERROR: {e}",
            })
            continue

        results.append({
            "n": i, "question": item["q"], "answerable": item["answerable"],
            "retrieval_hit": hit, "answer": answer, **verdict,
        })

        print(f"[{i}/20] retrieval_hit={hit} grounded={verdict['grounded']} "
              f"hallucinated={verdict['hallucinated']} | {item['q'][:60]}")

    # ---------------------------------------------------------------
    # Aggregate metrics
    # ---------------------------------------------------------------
    n = len(results)
    valid = [r for r in results if r["grounded"] is not None]

    retrieval_accuracy = sum(r["retrieval_hit"] for r in results) / n * 100
    grounding_rate = sum(r["grounded"] for r in valid) / len(valid) * 100 if valid else 0
    hallucination_rate = sum(r["hallucinated"] for r in valid) / len(valid) * 100 if valid else 0

    print("\n" + "=" * 60)
    print("HALLUCINATION EVALUATION — SUMMARY")
    print("=" * 60)
    print(f"Questions evaluated:   {n}")
    print(f"Judge parse failures:  {n - len(valid)}  (excluded from grounding/hallucination %)")
    print(f"Retrieval Accuracy:    {retrieval_accuracy:.1f}%")
    print(f"Grounding Rate:        {grounding_rate:.1f}%")
    print(f"Hallucination Rate:    {hallucination_rate:.1f}%")

    flagged = [r for r in results if r["hallucinated"]]
    if flagged:
        print(f"\n{len(flagged)} answer(s) flagged as hallucinated — review these first:")
        for r in flagged:
            print(f"  [{r['n']}] {r['question']}")
            print(f"      reasoning: {r['reasoning']}")

    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nFull results written to eval_results.json — spot-check a sample of the judge's "
          "verdicts against your own reading before treating this as a hard quality gate.")


if __name__ == "__main__":
    main()