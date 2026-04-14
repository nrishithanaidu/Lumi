"""
services/bedrock_service.py
----------------------------
Phase 3 — Amazon Bedrock + Claude integration.

This module is the AI brain of Lumi. It takes raw extracted text
(from Textract) and runs three jobs on it:

  1. summarize_document()  → 3-sentence human-readable summary
  2. extract_entities()    → structured JSON: names, dates, amounts, orgs
  3. classify_document()   → one label: Invoice / Contract / Medical Record / etc.

All three call _call_claude() under the hood, which handles:
  - Building the correct Bedrock request body
  - Retrying on throttling (Bedrock free tier has low TPS)
  - Parsing the response safely
  - Logging token usage so you can track costs

How to run:
  Called automatically by pipeline/process_document.py when --ai flag is set.
  You can also test it standalone:

    python services/bedrock_service.py
"""

import json
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from botocore.exceptions import ClientError
from config.aws_config import bedrock_client, ok, info, warn, err, head


# ── Model config ──────────────────────────────────────────────────────────────
#
# Claude Sonnet is the sweet spot for this project:
#   - Fast enough for real-time use
#   - Smart enough to reliably output JSON
#   - Cheaper than Opus, better than Haiku for structured extraction
#
# If you hit "model not available", go to:
#   AWS Console → Amazon Bedrock → Model Access → Request access to Claude

MODEL_ID     = "amazon.nova-lite-v1:0"   # Amazon Nova Lite 2
MAX_RETRIES  = 3       # retry on throttling
RETRY_DELAY  = 5       # seconds between retries


# ── Core Bedrock caller (Nova Lite via Converse API) ──────────────────────────

def _call_claude(
    prompt: str,
    system: str = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> str:
    """
    Send a prompt to Claude via Bedrock and return the response text.

    Args:
        prompt      : The user message / instruction.
        system      : Optional system prompt to set Claude's role/behaviour.
        max_tokens  : Cap on output length. Keep low for classification (20),
                      higher for summaries (512), highest for entities (1024).
        temperature : 0.0 = deterministic/consistent, 1.0 = creative.
                      Use low values (0.1-0.3) for structured extraction so
                      you get the same format every time.

    Returns:
        Raw text string from Claude's response.

    Raises:
        RuntimeError if all retries fail.
    """
    # Nova Lite uses the Converse API
    converse_messages = [{"role": "user", "content": [{"text": prompt}]}]
    kwargs = {
        "modelId":         MODEL_ID,
        "messages":        converse_messages,
        "inferenceConfig": {
            "maxTokens":   max_tokens,
            "temperature": temperature,
        },
    }
    if system:
        kwargs["system"] = [{"text": system}]

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = bedrock_client.converse(**kwargs)
            text = response["output"]["message"]["content"][0]["text"].strip()
            usage = response.get("usage", {})
            info(
                f"Nova Lite: {usage.get('inputTokens', '?')} in / "
                f"{usage.get('outputTokens', '?')} out tokens"
            )
            return text

        except ClientError as e:
            code = e.response["Error"]["Code"]

            if code == "ThrottlingException" and attempt < MAX_RETRIES:
                wait = RETRY_DELAY * attempt  # backoff: 5s, 10s, 15s
                warn(f"Throttled — waiting {wait}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue

            elif code == "ValidationException":
                err(f"Bedrock validation error: {e}")
                err("Check MODEL_ID in bedrock_service.py and that you have model access.")
                raise

            elif code == "AccessDeniedException":
                err("Access denied. Enable Claude in: AWS Console → Bedrock → Model Access")
                raise

            else:
                err(f"Bedrock error ({code}): {e}")
                raise

    raise RuntimeError(f"Bedrock call failed after {MAX_RETRIES} retries")


# ── 1. Document Summarization ─────────────────────────────────────────────────

def summarize_document(text: str) -> str:
    """
    Summarize a document in 3 clear sentences using Claude.

    The system prompt locks Claude into the role of a document analyst,
    which keeps summaries professional and consistent across doc types.
    The user prompt explicitly says "3 sentences" — without this Claude
    tends to write 5-6 sentences with filler phrases.

    We trim to 6000 chars — Textract output on a 3-page doc can be 10k+.
    6000 chars is about 1500 tokens, well within Claude's context window
    and sufficient to understand any of our test documents fully.
    """
    info("Summarizing document with Claude...")

    system = (
        "You are a professional document analyst. "
        "You produce concise, factual summaries. "
        "Never add opinions, caveats, or filler phrases like "
        "'This document appears to be...'. Go straight to the facts."
    )

    prompt = (
        f"Summarize the following document in exactly 3 sentences. "
        f"Cover: what type of document it is, who the key parties are, "
        f"and the most important figures or outcomes.\n\n"
        f"Document:\n{text[:6000]}"
    )

    summary = _call_claude(prompt, system=system, max_tokens=300, temperature=0.1)
    ok(f"Summary generated ({len(summary)} chars)")
    return summary


# ── 2. Entity Extraction ──────────────────────────────────────────────────────

def extract_entities(text: str) -> dict:
    """
    Extract named entities from document text and return as a structured dict.

    The prompt is very explicit about output format — "raw JSON only, no markdown"
    because by default Claude wraps JSON in ```json ... ``` code fences,
    which break json.loads(). The _parse_json() helper strips those fences
    as a safety net.

    Entity categories:
        names         : People's full names
        dates         : Any dates mentioned
        amounts       : Monetary values, quantities
        organisations : Company names, hospitals, institutions
        ids           : Document IDs, invoice numbers, patient IDs

    Falls back to a safe empty dict if JSON parsing fails, so the pipeline
    never crashes on a bad Claude response.
    """
    info("Extracting entities with Claude...")

    system = (
        "You are a precise information extraction engine. "
        "You output only valid JSON with no commentary, no markdown, no code fences. "
        "If a category has no entries, return an empty list for that key."
    )

    prompt = (
        "Extract all named entities from the text below.\n"
        "Return ONLY a JSON object with exactly these keys:\n"
        "  names         (list of strings) — people's full names\n"
        "  dates         (list of strings) — all dates in their original format\n"
        "  amounts       (list of strings) — all monetary values or quantities\n"
        "  organisations (list of strings) — company names, hospitals, institutions\n"
        "  ids           (list of strings) — document IDs, invoice numbers, patient IDs\n\n"
        "Rules:\n"
        "  - No markdown, no code fences, no explanation — raw JSON only\n"
        "  - Preserve the original text exactly as it appears\n"
        "  - Do not infer or guess — only extract what is literally present\n\n"
        f"Text:\n{text[:6000]}"
    )

    raw      = _call_claude(prompt, system=system, max_tokens=1024, temperature=0.0)
    entities = _parse_json(raw, fallback_keys=["names", "dates", "amounts", "organisations", "ids"])

    total = sum(len(v) for v in entities.values() if isinstance(v, list))
    ok(f"Entities extracted: {total} total items")
    return entities


# ── 3. Document Classification ────────────────────────────────────────────────

def classify_document(text: str) -> str:
    """
    Classify the document into one of five categories.

    We give Claude a fixed label set and tell it to reply with the label only.
    Temperature 0.0 makes this fully deterministic — same doc always gets
    the same label.

    We only send the first 1500 chars — document type is always clear
    from the header/title, so reading the full text wastes tokens.

    Valid labels:
        Invoice        — bills, receipts, purchase orders, tax invoices
        Contract       — agreements, MoUs, service contracts, NDAs
        Medical Record — lab reports, prescriptions, discharge summaries
        ID Document    — passports, Aadhaar, driving licences, PAN cards
        Other          — anything that doesn't clearly fit the above
    """
    info("Classifying document with Claude...")

    system = (
        "You are a document classification engine. "
        "You reply with a single label from the provided list. "
        "No punctuation, no explanation, no extra words."
    )

    prompt = (
        "Classify the following document. "
        "Reply with exactly one of these labels:\n"
        "  Invoice\n"
        "  Contract\n"
        "  Medical Record\n"
        "  ID Document\n"
        "  Other\n\n"
        "Output the label only — nothing else.\n\n"
        f"Document (first 1500 characters):\n{text[:1500]}"
    )

    label = _call_claude(prompt, system=system, max_tokens=10, temperature=0.0)
    label = label.strip()

    valid = {"Invoice", "Contract", "Medical Record", "ID Document", "Other"}
    if label not in valid:
        warn(f"Unexpected label '{label}' — defaulting to 'Other'")
        label = "Other"

    ok(f"Document classified as: {label}")
    return label


# ── 4. Q&A over a document (called by rag_service.py in Phase 4) ─────────────

def answer_question(question: str, context_chunks: list) -> str:
    """
    Answer a natural language question grounded in retrieved document chunks.

    This is called by rag_service.py AFTER it retrieves the top-k most
    relevant chunks from FAISS. Claude sees only those chunks as its
    knowledge source — it cannot fill gaps with training data.

    The "Answer only from the context" instruction is critical — without it
    Claude will hallucinate answers confidently, which defeats the whole
    point of RAG.

    Args:
        question       : The user's natural language question.
        context_chunks : List of text snippets retrieved from FAISS.

    Returns:
        Claude's answer as a plain text string.
    """
    info(f"Answering: '{question[:80]}'")

    system = (
        "You are a helpful document assistant. "
        "You answer questions strictly based on the provided document context. "
        "If the answer is not clearly stated in the context, say: "
        "'I could not find that information in the document.' "
        "Never guess or use outside knowledge."
    )

    # Number each chunk so Claude can mentally reference them
    context = "\n\n".join(
        f"[Chunk {i+1}]:\n{chunk}"
        for i, chunk in enumerate(context_chunks)
    )

    prompt = (
        f"Using only the document context below, answer the following question.\n\n"
        f"Question: {question}\n\n"
        f"Context:\n{context}"
    )

    answer = _call_claude(prompt, system=system, max_tokens=512, temperature=0.2)
    ok("Answer generated")
    return answer


# ── JSON parsing helper ───────────────────────────────────────────────────────

def _parse_json(raw: str, fallback_keys: list = None) -> dict:
    """
    Safely parse JSON from Claude's output.

    Claude sometimes wraps JSON in ```json ... ``` even when instructed not to.
    This strips those fences before parsing. If parsing still fails, returns a
    safe fallback dict with empty lists so the pipeline never crashes.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines   = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        err(f"JSON parse failed: {e}")
        warn(f"Raw Claude output: {raw[:300]}")
        fallback = {key: [] for key in (fallback_keys or [])}
        fallback["_parse_error"] = str(e)
        fallback["_raw"]         = raw[:500]
        return fallback


# ── Standalone smoke test ─────────────────────────────────────────────────────

def _run_tests():
    """
    Run this directly to verify your Bedrock setup is working:
        python services/bedrock_service.py
    """
    sample_text = """
    TAX INVOICE
    Invoice No: INV-2024-00847
    Date: 15 October 2024

    From: TechSolutions Pvt. Ltd., 42 MG Road, Bengaluru - 560001
    To: Rahul Mehta, Infosys Ltd., Electronic City, Bengaluru - 560100

    Description                          Qty     Unit Price    Total
    AWS Cloud Architecture Consulting    10 hrs  Rs. 8,000     Rs. 80,000
    Textract Integration & Setup          5 hrs  Rs. 8,000     Rs. 40,000
    Documentation & Training              3 hrs  Rs. 6,000     Rs. 18,000

    Subtotal: Rs. 1,38,000
    GST 18%:  Rs. 24,840
    TOTAL DUE: Rs. 1,62,840

    Payment due within 30 days.
    Bank: HDFC Bank, Acc: 5020001234567, IFSC: HDFC0001234
    """

    head("Test 1 — Summarization")
    summary = summarize_document(sample_text)
    print(f"\n  {summary}\n")

    head("Test 2 — Entity Extraction")
    entities = extract_entities(sample_text)
    for key, values in entities.items():
        if isinstance(values, list) and values:
            print(f"  {key:<16}: {', '.join(values)}")

    head("Test 3 — Classification")
    label = classify_document(sample_text)
    print(f"\n  Classified as: {label}\n")

    head("Test 4 — Q&A")
    answer = answer_question(
        question="What is the total amount due and when is it payable?",
        context_chunks=[sample_text]
    )
    print(f"\n  {answer}\n")

    ok("All Bedrock tests passed!")


if __name__ == "__main__":
    _run_tests()
