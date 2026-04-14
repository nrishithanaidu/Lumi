"""
api/handlers.py
----------------
Phase 5 — Lambda function handlers for all 4 API endpoints.

Each function in this file is one Lambda handler. AWS API Gateway calls
these functions when an HTTP request hits the corresponding route.

Endpoints:
  POST   /upload          → get_upload_url()   — presigned S3 URL for direct upload
  GET    /status/{jobId}  → get_status()       — poll processing status from DynamoDB
  GET    /results/{jobId} → get_results()      — full extraction + AI results
  POST   /query           → query_document()   — RAG Q&A over an indexed document

How Lambda handlers work:
  Every Lambda function receives two arguments:
    event   : dict — the incoming HTTP request (path, body, headers, params)
    context : LambdaContext — runtime info (function name, timeout, etc.)
  And returns a dict with:
    statusCode : int  — HTTP status code
    headers    : dict — response headers (always include CORS headers)
    body       : str  — JSON string of the response payload

How to deploy (Phase 6):
  Each handler gets zipped with its dependencies and deployed as a Lambda.
  API Gateway routes HTTP requests to the correct Lambda.
  For now you can test locally by calling each function directly.

Local testing:
    python api/handlers.py
"""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.s3_service       import generate_presigned_url
from services.dynamodb_service import get_record, list_records
from services.rag_service      import query, list_indexed_documents
from utils.helpers             import generate_job_id, current_timestamp
from config.aws_config         import S3_RAW_BUCKET, ok, info, err


# ── CORS headers ──────────────────────────────────────────────────────────────
#
# These headers are required on every response so the React frontend
# (running on a different domain) is allowed to receive the response.
# Without CORS headers the browser silently blocks the response.

CORS_HEADERS = {
    "Content-Type":                "application/json",
    "Access-Control-Allow-Origin": "*",   # In production, replace * with your Vercel domain
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


# ── Response helpers ──────────────────────────────────────────────────────────

def _ok(data: dict, status: int = 200) -> dict:
    """Build a successful API response."""
    return {
        "statusCode": status,
        "headers":    CORS_HEADERS,
        "body":       json.dumps(data, default=str),  # default=str handles datetime etc.
    }


def _err(message: str, status: int = 400) -> dict:
    """Build an error API response."""
    return {
        "statusCode": status,
        "headers":    CORS_HEADERS,
        "body":       json.dumps({"error": message}),
    }


def _parse_body(event: dict) -> dict:
    """
    Safely parse the JSON body from an API Gateway event.
    API Gateway passes the body as a string, so we need to json.loads() it.
    Returns an empty dict if the body is missing or invalid.
    """
    body = event.get("body", "{}")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return body or {}


# ── Handler 1: POST /upload ───────────────────────────────────────────────────

def get_upload_url(event: dict, context=None) -> dict:
    """
    Generate a presigned S3 URL so the frontend can upload a file
    directly to S3 without going through the Lambda.

    Why presigned URLs instead of uploading through the API?
      - Lambda has a 6 MB payload limit — most PDFs exceed this
      - Uploading directly to S3 is faster and cheaper
      - The frontend gets a one-time URL that expires after 1 hour

    Flow:
      1. Frontend calls POST /upload with { filename, doc_type }
      2. This Lambda generates a presigned PUT URL for that S3 key
      3. Frontend uploads the file directly to S3 using that URL
      4. Frontend then polls GET /status/{jobId} to track processing

    Request body:
      { "filename": "invoice.pdf", "doc_type": "invoices" }

    Response:
      {
        "job_id":      "a3f9c2b1",
        "upload_url":  "https://s3.amazonaws.com/...",
        "s3_key":      "invoices/a3f9c2b1_invoice.pdf",
        "expires_in":  3600
      }
    """
    info("Handler: get_upload_url")

    body     = _parse_body(event)
    filename = body.get("filename", "").strip()
    doc_type = body.get("doc_type", "other").strip()

    # Validate inputs
    if not filename:
        return _err("'filename' is required in the request body")

    valid_types = {"invoices", "contracts", "medical", "ids", "other"}
    if doc_type not in valid_types:
        doc_type = "other"

    # Generate a unique job ID and S3 key for this upload
    job_id    = generate_job_id()
    s3_key    = f"{doc_type}/{job_id}_{filename}"

    try:
        upload_url = generate_presigned_url(s3_key, bucket=S3_RAW_BUCKET, expiry=3600)
    except Exception as e:
        err(f"Failed to generate presigned URL: {e}")
        return _err("Failed to generate upload URL", status=500)

    ok(f"Presigned URL generated for job_id={job_id}")

    return _ok({
        "job_id":     job_id,
        "upload_url": upload_url,
        "s3_key":     s3_key,
        "expires_in": 3600,
        "message":    "PUT the file to upload_url, then poll /status/{job_id}",
    })


# ── Handler 2: GET /status/{jobId} ───────────────────────────────────────────

def get_status(event: dict, context=None) -> dict:
    """
    Return the current processing status of a document job.

    The frontend polls this endpoint after upload to know when
    Textract and Bedrock have finished processing.

    Status values (in order):
      uploaded       → file is in S3, waiting for Lambda trigger
      processing     → Textract async job is running
      textract_done  → text/tables/forms extracted, Bedrock not yet run
      ai_done        → full pipeline complete including AI analysis
      failed         → something went wrong (check DynamoDB for details)

    Path parameter:
      /status/{jobId}   e.g. /status/a3f9c2b1

    Response:
      {
        "job_id":   "a3f9c2b1",
        "status":   "ai_done",
        "filename": "invoice.pdf",
        "doc_type": "invoices",
        "rag_indexed": "true"
      }
    """
    info("Handler: get_status")

    # API Gateway puts path parameters in event["pathParameters"]
    path_params = event.get("pathParameters") or {}
    job_id      = path_params.get("jobId", "").strip()

    if not job_id:
        return _err("jobId path parameter is required")

    record = get_record(job_id)

    if not record:
        return _err(f"No job found with id '{job_id}'", status=404)

    # Return only the fields the frontend needs for status polling
    return _ok({
        "job_id":      record.get("job_id"),
        "status":      record.get("status", "unknown"),
        "filename":    record.get("filename"),
        "doc_type":    record.get("doc_type"),
        "page_count":  record.get("page_count"),
        "rag_indexed": record.get("rag_indexed", "false"),
        "timestamp":   record.get("timestamp"),
    })


# ── Handler 3: GET /results/{jobId} ──────────────────────────────────────────

def get_results(event: dict, context=None) -> dict:
    """
    Return the full extraction and AI analysis results for a processed document.

    Called by the frontend Results page once status = 'textract_done' or 'ai_done'.

    Path parameter:
      /results/{jobId}

    Response:
      {
        "job_id":         "a3f9c2b1",
        "filename":       "invoice.pdf",
        "status":         "ai_done",
        "pages":          1,
        "extracted_text": "TAX INVOICE...",
        "tables":         [[...], ...],
        "key_value_pairs": { "Invoice No": "INV-2024-00847", ... },
        "summary":        "This is a tax invoice...",
        "entities":       { "names": [...], "dates": [...], ... },
        "category":       "Invoice"
      }
    """
    info("Handler: get_results")

    path_params = event.get("pathParameters") or {}
    job_id      = path_params.get("jobId", "").strip()

    if not job_id:
        return _err("jobId path parameter is required")

    record = get_record(job_id)

    if not record:
        return _err(f"No job found with id '{job_id}'", status=404)

    status = record.get("status", "unknown")

    # If still processing, tell the frontend to keep polling
    if status in ("uploaded", "processing"):
        return _ok({
            "job_id":  job_id,
            "status":  status,
            "message": "Document is still being processed. Poll /status/{jobId}.",
        })

    # Parse entities back from JSON string (DynamoDB stores it as a string)
    entities_raw = record.get("entities")
    entities     = None
    if entities_raw:
        try:
            entities = json.loads(entities_raw) if isinstance(entities_raw, str) else entities_raw
        except json.JSONDecodeError:
            entities = None

    return _ok({
        "job_id":          record.get("job_id"),
        "filename":        record.get("filename"),
        "doc_type":        record.get("doc_type"),
        "status":          status,
        "page_count":      record.get("page_count"),
        "line_count":      record.get("line_count"),
        "table_count":     record.get("table_count"),
        "extracted_text":  record.get("extracted_text"),  # first 500 chars
        "summary":         record.get("summary"),
        "entities":        entities,
        "category":        record.get("category"),
        "rag_indexed":     record.get("rag_indexed", "false"),
        "timestamp":       record.get("timestamp"),
    })


# ── Handler 4: POST /query ────────────────────────────────────────────────────

def query_document(event: dict, context=None) -> dict:
    """
    Answer a natural language question about a document using RAG.

    Called by the frontend Q&A page. Requires the document to have been
    indexed first (rag_indexed = "true" in the status response).

    Request body:
      { "job_id": "a3f9c2b1", "question": "When does this contract expire?" }

    Response:
      {
        "job_id":   "a3f9c2b1",
        "question": "When does this contract expire?",
        "answer":   "The contract expires on 31 March 2025.",
        "chunks_used": 3
      }
    """
    info("Handler: query_document")

    body     = _parse_body(event)
    job_id   = body.get("job_id", "").strip()
    question = body.get("question", "").strip()

    if not job_id:
        return _err("'job_id' is required in the request body")
    if not question:
        return _err("'question' is required in the request body")
    if len(question) > 1000:
        return _err("Question is too long (max 1000 characters)")

    # Check the document exists and is indexed
    indexed_docs = list_indexed_documents()
    if job_id not in indexed_docs:
        return _err(
            f"Document '{job_id}' is not indexed for Q&A. "
            "Process it with run_rag=True first.",
            status=404,
        )

    try:
        result = query(job_id, question)
    except FileNotFoundError as e:
        return _err(str(e), status=404)
    except Exception as e:
        err(f"RAG query failed: {e}\n{traceback.format_exc()}")
        return _err("Query failed — see Lambda logs for details", status=500)

    ok(f"Query answered for job_id={job_id}")

    return _ok({
        "job_id":      job_id,
        "question":    result["question"],
        "answer":      result["answer"],
        "chunks_used": len(result["retrieved_chunks"]),
    })


# ── Handler 5: OPTIONS /* (CORS preflight) ────────────────────────────────────

def cors_preflight(event: dict, context=None) -> dict:
    """
    Handle CORS preflight OPTIONS requests.
    Browsers send an OPTIONS request before any cross-origin POST/PUT
    to check if the server allows it. We just return the CORS headers.
    Wire this to OPTIONS /* in API Gateway.
    """
    return {
        "statusCode": 200,
        "headers":    CORS_HEADERS,
        "body":       "",
    }


# ── Local test runner ─────────────────────────────────────────────────────────

def _run_local_tests():
    """
    Test all handlers locally without deploying to Lambda.
    Run with: python api/handlers.py
    """
    from config.aws_config import head

    head("Test 1 — POST /upload")
    event = {"body": json.dumps({"filename": "test_invoice.pdf", "doc_type": "invoices"})}
    resp  = get_upload_url(event)
    print(f"  Status: {resp['statusCode']}")
    body  = json.loads(resp["body"])
    print(f"  job_id: {body.get('job_id')}")
    print(f"  s3_key: {body.get('s3_key')}")
    print(f"  url starts with: {str(body.get('upload_url', ''))[:60]}...")

    head("Test 2 — GET /status/{jobId} (non-existent)")
    event = {"pathParameters": {"jobId": "nonexistent"}}
    resp  = get_status(event)
    print(f"  Status: {resp['statusCode']}  (expected 404)")

    head("Test 3 — POST /query (not indexed)")
    event = {"body": json.dumps({"job_id": "fakeid", "question": "What is this?"})}
    resp  = query_document(event)
    print(f"  Status: {resp['statusCode']}  (expected 404)")

    head("Test 4 — OPTIONS preflight")
    resp = cors_preflight({})
    print(f"  Status: {resp['statusCode']}  (expected 200)")
    print(f"  CORS header: {resp['headers'].get('Access-Control-Allow-Origin')}")

    print()
    ok("Local handler tests complete — deploy to Lambda for full integration")


if __name__ == "__main__":
    _run_local_tests()
