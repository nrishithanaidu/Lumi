"""
services/rag_service.py
------------------------
Phase 4 — RAG (Retrieval-Augmented Generation) Pipeline

What this does, in plain English:
  A user uploads a contract. Later they ask: "When does this contract expire?"
  Instead of sending the entire document to Claude every time (expensive + slow),
  we pre-process it into small searchable chunks, convert them to vectors (numbers
  that represent meaning), and store them locally in a FAISS index.

  When a question comes in:
    1. Convert the question into a vector (same embedding model)
    2. Find the 3 chunks whose vectors are closest to the question vector
    3. Send ONLY those 3 chunks to Claude as context
    4. Claude answers from the retrieved chunks — not from training data

  This is RAG. It's fast, cheap, and the answers are grounded in real document text.

Architecture:
  Document text (from Textract)
      │
      ▼
  chunk_document()       — split into ~500 word overlapping segments
      │
      ▼
  embed_chunks()         — Titan Embeddings → list of 1536-dim float vectors
      │
      ▼
  build_faiss_index()    — store vectors in a local FAISS index
      │
      ▼
  save_index()           — persist index + chunks to disk (outputs/rag/)
      │
  ... later, when a question arrives ...
      │
      ▼
  load_index()           — reload from disk
      │
      ▼
  query()                — embed question → search index → top-k chunks
      │
      ▼
  bedrock_service.answer_question()  — Claude answers from retrieved chunks

How to run standalone:
    python services/rag_service.py
"""

import os
import sys
import json
import pickle
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import faiss
from config.aws_config import bedrock_client, OUTPUTS_DIR, ok, info, warn, err, head
from services.bedrock_service import answer_question
from utils.helpers import chunk_text, load_json

from botocore.exceptions import ClientError


# ── Config ────────────────────────────────────────────────────────────────────

# Titan Text Embeddings v2 — Amazon's embedding model, available on Bedrock.
# Outputs 1536-dimensional vectors. No extra cost beyond the token price,
# which is $0.02 per million tokens — essentially free for a college project.
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

# How many chunks to retrieve per question.
# 3 is the sweet spot: enough context for Claude to answer well,
# not so much that we waste tokens or confuse it with irrelevant text.
TOP_K = 3

# Where we persist FAISS indexes on disk
RAG_DIR = os.path.join(OUTPUTS_DIR, "rag")
os.makedirs(RAG_DIR, exist_ok=True)


# ── 1. Embedding — text → vector ──────────────────────────────────────────────

def embed_text(text: str) -> list:
    """
    Convert a single piece of text into a 1536-dimensional embedding vector
    using Amazon Titan Embeddings via Bedrock.

    An embedding is just a list of 1536 floats that encode the *meaning* of
    the text. Two chunks about "payment terms" will have similar vectors even
    if the exact words differ — that's what makes semantic search work.

    Args:
        text : Any string — a document chunk or a user's question.

    Returns:
        List of 1536 floats (the embedding vector).
    """
    body = json.dumps({
        "inputText": text[:8000],  # Titan's input limit is 8192 tokens
    })

    try:
        response = bedrock_client.invoke_model(
            modelId     = EMBEDDING_MODEL_ID,
            body        = body,
            contentType = "application/json",
            accept      = "application/json",
        )
        result = json.loads(response["body"].read())
        return result["embedding"]

    except ClientError as e:
        err(f"Embedding failed: {e}")
        raise


def embed_chunks(chunks: list) -> np.ndarray:
    """
    Embed a list of text chunks and return them as a numpy float32 array.

    FAISS requires float32 arrays — not Python lists, not float64.
    We convert explicitly here so there's no silent type mismatch later.

    Args:
        chunks : List of text strings (output of chunk_text()).

    Returns:
        numpy array of shape (len(chunks), 1536), dtype=float32.
    """
    info(f"Embedding {len(chunks)} chunks with Titan...")
    vectors = []

    for i, chunk in enumerate(chunks):
        vec = embed_text(chunk)
        vectors.append(vec)
        # Print progress every 5 chunks — embedding can take a few seconds
        if (i + 1) % 5 == 0 or (i + 1) == len(chunks):
            info(f"  Embedded {i+1}/{len(chunks)} chunks")

    arr = np.array(vectors, dtype="float32")
    ok(f"Embedding complete — shape: {arr.shape}")
    return arr


# ── 2. FAISS Index — build, save, load ────────────────────────────────────────

def build_faiss_index(vectors: np.ndarray) -> faiss.IndexFlatIP:
    """
    Build a FAISS index from a numpy array of embedding vectors.

    We use IndexFlatIP (Inner Product) with L2-normalised vectors —
    this is equivalent to cosine similarity, which is the standard for
    semantic search. Cosine similarity measures the *angle* between vectors,
    not their magnitude, which is what we want for meaning-based search.

    Why FAISS over a cloud vector DB (like OpenSearch or Pinecone)?
      - No infrastructure to set up or pay for
      - Fast enough for thousands of chunks locally
      - Easy to persist as a file
      - Perfect for a college project demo

    Args:
        vectors : float32 numpy array of shape (n_chunks, 1536).

    Returns:
        A populated FAISS IndexFlatIP ready to query.
    """
    # L2 normalise so inner product = cosine similarity
    faiss.normalize_L2(vectors)

    dimension = vectors.shape[1]   # 1536 for Titan v2
    index     = faiss.IndexFlatIP(dimension)
    index.add(vectors)

    ok(f"FAISS index built — {index.ntotal} vectors, {dimension} dimensions")
    return index


def save_index(job_id: str, index: faiss.IndexFlatIP, chunks: list):
    """
    Persist the FAISS index and its corresponding text chunks to disk.

    We save two files per document:
      - {job_id}.faiss  : the binary FAISS index (fast to reload)
      - {job_id}.pkl    : the list of text chunks (so we can return actual text)

    The chunks file is essential — FAISS stores vectors, not the original text.
    Without it we'd know *which* chunk is relevant but not what it says.

    Args:
        job_id : The Lumi job ID — used to name the files.
        index  : The populated FAISS index.
        chunks : The list of text strings in the same order as the index vectors.
    """
    index_path  = os.path.join(RAG_DIR, f"{job_id}.faiss")
    chunks_path = os.path.join(RAG_DIR, f"{job_id}.pkl")

    faiss.write_index(index, index_path)

    with open(chunks_path, "wb") as f:
        pickle.dump(chunks, f)

    ok(f"Index saved: {index_path}")
    ok(f"Chunks saved: {chunks_path}")


def load_index(job_id: str) -> tuple:
    """
    Load a previously saved FAISS index and its chunks from disk.

    Args:
        job_id : The Lumi job ID used when the index was saved.

    Returns:
        Tuple of (faiss_index, list_of_chunks).

    Raises:
        FileNotFoundError if the index hasn't been built yet for this job.
    """
    index_path  = os.path.join(RAG_DIR, f"{job_id}.faiss")
    chunks_path = os.path.join(RAG_DIR, f"{job_id}.pkl")

    if not os.path.exists(index_path):
        raise FileNotFoundError(
            f"No FAISS index found for job '{job_id}'. "
            f"Run index_document() first."
        )

    index = faiss.read_index(index_path)

    with open(chunks_path, "rb") as f:
        chunks = pickle.load(f)

    ok(f"Index loaded: {index.ntotal} vectors, {len(chunks)} chunks")
    return index, chunks


def list_indexed_documents() -> list:
    """
    Return a list of job IDs that have been indexed and are ready to query.
    Useful for the frontend to show which documents support Q&A.
    """
    files = os.listdir(RAG_DIR)
    return [f.replace(".faiss", "") for f in files if f.endswith(".faiss")]


# ── 3. Indexing pipeline — text → chunks → vectors → FAISS ───────────────────

def index_document(job_id: str, full_text: str, chunk_size: int = 500, overlap: int = 50):
    """
    Full indexing pipeline for one document.

    Takes the raw extracted text (from Textract), splits it into overlapping
    chunks, embeds each chunk with Titan, and stores the FAISS index on disk.

    Call this once per document after Textract extraction is done.
    After this, the document is queryable via query().

    Args:
        job_id     : Lumi job ID — used to name the saved index files.
        full_text  : The complete extracted text from Textract.
        chunk_size : Target words per chunk (default 500 ≈ ~750 tokens).
        overlap    : Words shared between consecutive chunks (default 50).
                     Overlap prevents answers from being split across chunk
                     boundaries — a sentence at the end of chunk 2 is also
                     at the start of chunk 3.

    Returns:
        Number of chunks indexed.
    """
    head(f"Indexing document: job_id={job_id}")

    # Step 1: chunk
    chunks = chunk_text(full_text, chunk_size=chunk_size, overlap=overlap)
    info(f"Split into {len(chunks)} chunks (size={chunk_size}, overlap={overlap})")

    if not chunks:
        warn("No text to index — skipping")
        return 0

    # Step 2: embed
    vectors = embed_chunks(chunks)

    # Step 3: build FAISS index
    index = build_faiss_index(vectors)

    # Step 4: save
    save_index(job_id, index, chunks)

    ok(f"Document indexed successfully — {len(chunks)} chunks ready to query")
    return len(chunks)


# ── 4. Query pipeline — question → retrieve → answer ─────────────────────────

def query(job_id: str, question: str, top_k: int = TOP_K) -> dict:
    """
    Answer a natural language question about a document using RAG.

    Full flow:
      1. Load the FAISS index for this job_id
      2. Embed the question using the same Titan model
      3. Search the index for the top_k most similar chunks
      4. Pass those chunks to Claude (via bedrock_service.answer_question)
      5. Return the answer plus the source chunks for transparency

    Args:
        job_id   : The Lumi job ID of the document to query.
        question : Any natural language question about the document.
        top_k    : Number of chunks to retrieve (default 3).

    Returns:
        dict with keys:
          answer         : Claude's answer string
          question       : The original question
          retrieved_chunks : The text chunks used as context
          chunk_indices  : Which chunk numbers were retrieved (for debugging)
    """
    head(f"RAG Query: '{question[:60]}'")

    # Load the pre-built index
    index, chunks = load_index(job_id)

    # Embed the question using the same model as the chunks
    # This is critical — both must use identical embedding spaces or
    # similarity search is meaningless
    info("Embedding question...")
    question_vec = np.array([embed_text(question)], dtype="float32")
    faiss.normalize_L2(question_vec)  # must normalise for cosine similarity

    # Search — returns distances and indices of the top_k nearest vectors
    distances, indices = index.search(question_vec, top_k)

    # Retrieve the actual text chunks
    retrieved = []
    for i, idx in enumerate(indices[0]):
        if idx == -1:
            # FAISS returns -1 when there aren't enough vectors to fill top_k
            continue
        chunk_text_content = chunks[idx]
        score = float(distances[0][i])
        retrieved.append({
            "chunk_index": int(idx),
            "score":       round(score, 4),
            "text":        chunk_text_content,
        })
        info(f"  Chunk {idx} — similarity score: {score:.4f}")

    if not retrieved:
        warn("No relevant chunks found")
        return {
            "answer":          "I could not find relevant information in this document.",
            "question":        question,
            "retrieved_chunks": [],
            "chunk_indices":   [],
        }

    # Pass retrieved chunk texts to Claude for answer generation
    chunk_texts = [r["text"] for r in retrieved]
    answer      = answer_question(question, chunk_texts)

    ok("RAG query complete")

    return {
        "answer":           answer,
        "question":         question,
        "retrieved_chunks": retrieved,
        "chunk_indices":    [r["chunk_index"] for r in retrieved],
    }


# ── 5. Batch index from phase2 results ───────────────────────────────────────

def index_all_from_results(results_path: str = None):
    """
    Index all documents from a phase2_results.json or all_results.json file.

    Useful for indexing your test documents in one shot after Phase 2 runs.
    Skips documents that are already indexed.

    Args:
        results_path : Path to the results JSON file.
                       Defaults to outputs/all_results.json.
    """
    if results_path is None:
        results_path = os.path.join(OUTPUTS_DIR, "all_results.json")

    if not os.path.exists(results_path):
        err(f"Results file not found: {results_path}")
        err("Run main.py first to generate document results.")
        return

    results = load_json(results_path)
    info(f"Found {len(results)} document(s) to index")

    already_indexed = list_indexed_documents()

    for doc_type, doc_result in results.items():
        job_id    = doc_result.get("job_id")
        full_text = doc_result.get("full_text", "")
        filename  = doc_result.get("filename", doc_type)

        if not job_id:
            warn(f"No job_id for {doc_type} — skipping")
            continue

        if job_id in already_indexed:
            info(f"Already indexed: {filename} ({job_id}) — skipping")
            continue

        if not full_text.strip():
            warn(f"No text for {filename} — skipping")
            continue

        info(f"Indexing: {filename} ({job_id})")
        n = index_document(job_id, full_text)
        info(f"  → {n} chunks indexed")

    ok("Batch indexing complete")
    indexed = list_indexed_documents()
    info(f"Documents ready to query: {indexed}")


# ── Standalone test ───────────────────────────────────────────────────────────

def _run_tests():
    """
    Smoke test the full RAG pipeline with a hardcoded sample text.
    Run with: python services/rag_service.py
    """
    sample_text = """
    SERVICE AGREEMENT

    This Agreement is entered into as of 15 October 2024, between:
    Party A: Infosys Limited, Electronics City, Bengaluru - 560100 ("Client")
    Party B: Amazon Web Services India Pvt. Ltd., MG Road, Bengaluru - 560001 ("Service Provider")

    1. SCOPE OF SERVICES
    Service Provider agrees to provide cloud migration consulting and AWS infrastructure
    setup as detailed in Schedule A. This includes architecture review, cost optimisation,
    and a 3-month post-migration support period.

    2. TERM
    This Agreement commences on 15 October 2024 and continues until 31 March 2025,
    unless terminated earlier in accordance with Section 7.

    3. PAYMENT
    Total contract value: Rs. 14,50,000 (Rupees Fourteen Lakh Fifty Thousand).
    Advance payment of Rs. 2,00,000 is due upon signing.
    Remaining balance is payable in monthly instalments of Rs. 2,50,000.

    4. CONFIDENTIALITY
    Both parties agree to keep all technical and business information exchanged
    under this Agreement strictly confidential for a period of 3 years after termination.

    5. INTELLECTUAL PROPERTY
    All deliverables, code, and documentation produced by the Service Provider
    under this Agreement shall remain the property of the Client upon full payment.

    6. TERMINATION
    Either party may terminate this Agreement with 30 days written notice.
    Early termination by the Client incurs a penalty of Rs. 1,00,000.

    Signed: Rahul Mehta (Infosys Ltd.)            Date: 15 Oct 2024
    Signed: S. Krishnan (Amazon Web Services)     Date: 15 Oct 2024
    """

    test_job_id = "rag-test-01"

    head("Step 1 — Indexing sample contract")
    n = index_document(test_job_id, sample_text, chunk_size=100, overlap=20)
    print(f"  Indexed {n} chunks\n")

    head("Step 2 — Querying")
    questions = [
        "When does this contract expire?",
        "What is the total contract value?",
        "What happens if the client terminates early?",
        "Who owns the deliverables and code?",
    ]

    for q in questions:
        print(f"\n  Q: {q}")
        result = query(test_job_id, q)
        print(f"  A: {result['answer']}")
        print(f"     (retrieved chunks: {result['chunk_indices']})")

    ok("\nAll RAG tests passed!")


if __name__ == "__main__":
    _run_tests()
