"""
pipeline/process_document.py
------------------------------
Full document processing pipeline.

Orchestrates:
  Phase 1 → upload to S3, register in DynamoDB
  Phase 2 → Textract sync + async extraction (text, tables, forms)
  Phase 3 → Bedrock AI analysis (summary, entities, classification)
  Phase 4 → RAG indexing (chunk → embed → FAISS) so the doc is queryable

Usage:
    from pipeline.process_document import run_pipeline
    results = run_pipeline("path/to/document.pdf", doc_type="invoices")

    # With AI analysis + RAG indexing:
    results = run_pipeline("path/to/document.pdf", doc_type="invoices", run_ai=True, run_rag=True)
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.aws_config import (
    S3_RAW_BUCKET, OUTPUTS_DIR,
    ok, info, warn, err, head,
)
from services.s3_service       import upload_file, save_json_to_s3
from services.dynamodb_service import put_record, update_record, get_record
from services.textract_service import (
    extract_text_sync,
    start_async_extraction, wait_for_async_job, get_async_results,
    extract_tables, extract_key_value_pairs, print_table,
)
from services.bedrock_service  import summarize_document, extract_entities, classify_document
from services.rag_service      import index_document
from utils.helpers             import generate_job_id, current_timestamp, save_json, truncate, file_size_mb
from tabulate import tabulate


def run_pipeline(local_path: str, doc_type: str = "other", run_ai: bool = False, run_rag: bool = False) -> dict:
    """
    Full end-to-end pipeline for a single document.

    Args:
        local_path : Path to the local PDF or image file.
        doc_type   : Folder/category in S3 (invoices, contracts, medical, ids, other).
        run_ai     : If True, also runs Phase 3 Bedrock analysis.

    Returns:
        dict with all extraction results and metadata.
    """
    filename  = os.path.basename(local_path)
    job_id    = generate_job_id()
    timestamp = current_timestamp()
    s3_key    = f"{doc_type}/{job_id}_{filename}"

    head(f"Pipeline: {filename}  (job_id: {job_id})")

    # ── PHASE 1: Upload to S3 + register in DynamoDB ─────────────────────────
    info("Phase 1 — Uploading to S3...")
    upload_file(local_path, s3_key, S3_RAW_BUCKET)

    put_record({
        "job_id":    job_id,
        "timestamp": timestamp,
        "filename":  filename,
        "s3_key":    s3_key,
        "doc_type":  doc_type,
        "status":    "uploaded",
        "file_size": f"{file_size_mb(local_path)} MB",
    })

    # ── PHASE 2: Textract extraction ─────────────────────────────────────────
    info("Phase 2 — Textract sync extraction...")
    sync_result = extract_text_sync(local_path)

    info("Phase 2 — Starting async Textract job (tables + forms)...")
    textract_job_id = start_async_extraction(s3_key, features=["TABLES", "FORMS"])

    update_record(job_id, timestamp, {
        "status":          "processing",
        "textract_job_id": textract_job_id,
    })

    status = wait_for_async_job(textract_job_id)
    if status != "SUCCEEDED":
        update_record(job_id, timestamp, {"status": "failed"})
        err(f"Textract async job failed for {filename}")
        return {"job_id": job_id, "status": "failed"}

    blocks = get_async_results(textract_job_id)
    tables = extract_tables(blocks)
    kvs    = extract_key_value_pairs(blocks)

    update_record(job_id, timestamp, {
        "status":         "textract_done",
        "extracted_text": truncate(sync_result["full_text"], 500),
        "page_count":     str(sync_result["pages"]),
        "line_count":     str(sync_result["line_count"]),
        "table_count":    str(len(tables)),
        "kv_count":       str(len(kvs)),
    })

    # ── PHASE 3: Bedrock AI analysis (optional) ───────────────────────────────
    summary    = None
    entities   = None
    category   = None

    if run_ai:
        info("Phase 3 — Running Bedrock AI analysis...")
        text = sync_result["full_text"]
        try:
            summary  = summarize_document(text)
            entities = extract_entities(text)
            category = classify_document(text)

            update_record(job_id, timestamp, {
                "status":   "ai_done",
                "summary":  summary,
                "category": category,
                "entities": json.dumps(entities),
            })
        except Exception as e:
            warn(f"Bedrock analysis skipped: {e}")

    # ── PHASE 4: RAG indexing (optional) ─────────────────────────────────────
    rag_indexed = False

    if run_rag:
        info("Phase 4 — Building RAG index (chunking + embedding)...")
        try:
            n_chunks   = index_document(job_id, sync_result["full_text"])
            rag_indexed = True
            update_record(job_id, timestamp, {
                "rag_indexed": "true",
                "rag_chunks":  str(n_chunks),
            })
        except Exception as e:
            warn(f"RAG indexing skipped: {e}")

    # ── Assemble full results ─────────────────────────────────────────────────
    results = {
        "job_id":          job_id,
        "filename":        filename,
        "s3_key":          s3_key,
        "doc_type":        doc_type,
        "timestamp":       timestamp,
        "textract_job_id": textract_job_id,
        "pages":           sync_result["pages"],
        "line_count":      sync_result["line_count"],
        "word_count":      sync_result["word_count"],
        "avg_confidence":  sync_result["avg_confidence"],
        "full_text":       sync_result["full_text"],
        "lines":           sync_result["lines"],
        "tables":          tables,
        "key_value_pairs": kvs,
        "summary":         summary,
        "entities":        entities,
        "category":        category,
        "rag_indexed":     rag_indexed,
        "status":          "ai_done" if run_ai else "textract_done",
    }

    # Save to local outputs/
    out_path = os.path.join(OUTPUTS_DIR, f"{job_id}_{filename.replace('.pdf', '')}_results.json")
    save_json(results, out_path)

    # Save JSON to processed S3 bucket
    save_json_to_s3(
        json.dumps(results, indent=2),
        f"results/{job_id}_{filename}.json",
    )

    # ── Print summary ─────────────────────────────────────────────────────────
    print()
    print("  Pipeline Summary:")
    summary_rows = [
        ["Job ID",          job_id],
        ["File",            filename],
        ["Pages",           sync_result["pages"]],
        ["Lines extracted", sync_result["line_count"]],
        ["Words extracted", sync_result["word_count"]],
        ["Tables found",    len(tables)],
        ["Key-value pairs", len(kvs)],
        ["Avg confidence",  f"{sync_result['avg_confidence']}%"],
        ["AI analysis",     "✅ Done" if run_ai else "⏭️  Skipped (set run_ai=True)"],
        ["RAG indexed",     "✅ Done" if run_rag else "⏭️  Skipped (set run_rag=True)"],
        ["Status",          results["status"]],
    ]
    print(tabulate(summary_rows, tablefmt="rounded_outline"))

    if tables:
        print_table(tables[0], title="First table extracted")

    if kvs:
        print("\n  Key-Value Pairs:")
        for k, v in list(kvs.items())[:10]:
            print(f"    {k:<30} → {v}")

    if summary:
        print(f"\n  Summary: {summary[:300]}")

    return results
