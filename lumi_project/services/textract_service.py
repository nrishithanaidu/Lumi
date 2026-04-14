"""
services/textract_service.py
-----------------------------
All AWS Textract operations:
  - Sync text extraction  (single-page / fast)
  - Async extraction      (multi-page PDFs — production method)
  - Table parsing
  - Key-value (form) extraction
"""

import time
from botocore.exceptions import ClientError
from tabulate import tabulate
from config.aws_config import (
    textract_client, S3_RAW_BUCKET,
    ok, info, warn, err,
)


# ── 1. SYNC TEXT EXTRACTION ───────────────────────────────────────────────────

def extract_text_sync(file_path: str) -> dict:
    """
    Synchronous text extraction — best for single-page docs under 5 MB.
    Reads the file from disk and sends bytes directly to Textract.
    Returns extracted lines, word count, and full text.
    """
    info(f"Running sync text extraction on: {file_path}")

    with open(file_path, "rb") as f:
        doc_bytes = f.read()

    response = textract_client.detect_document_text(
        Document={"Bytes": doc_bytes}
    )

    lines  = []
    words  = []

    for block in response.get("Blocks", []):
        if block["BlockType"] == "LINE":
            lines.append({
                "text":       block["Text"],
                "confidence": round(block["Confidence"], 2),
                "page":       block.get("Page", 1),
            })
        elif block["BlockType"] == "WORD":
            words.append({
                "text":       block["Text"],
                "confidence": round(block["Confidence"], 2),
            })

    full_text = "\n".join(line["text"] for line in lines)
    avg_conf  = round(sum(l["confidence"] for l in lines) / len(lines), 2) if lines else 0

    ok(f"Sync extraction: {len(lines)} lines, {len(words)} words, avg confidence: {avg_conf}%")

    return {
        "method":          "sync",
        "pages":           response["DocumentMetadata"]["Pages"],
        "line_count":      len(lines),
        "word_count":      len(words),
        "full_text":       full_text,
        "lines":           lines,
        "avg_confidence":  avg_conf,
    }


# ── 2. ASYNC EXTRACTION (multi-page PDFs) ─────────────────────────────────────

def start_async_extraction(s3_key: str, features: list = None) -> str:
    """
    Start an async Textract job for a document already in S3.
    Use this for multi-page PDFs — it's the production-ready method.
    Returns the Textract job ID.
    """
    if features is None:
        features = ["TABLES", "FORMS"]

    info(f"Starting async Textract job for: {s3_key}")

    response = textract_client.start_document_analysis(
        DocumentLocation={
            "S3Object": {
                "Bucket": S3_RAW_BUCKET,
                "Name":   s3_key,
            }
        },
        FeatureTypes=features,
    )
    job_id = response["JobId"]
    ok(f"Async job started — Textract Job ID: {job_id}")
    return job_id


def wait_for_async_job(textract_job_id: str, poll_interval: int = 5) -> str:
    """
    Poll Textract until the async job finishes.
    Returns 'SUCCEEDED' or 'FAILED'.
    """
    info(f"Waiting for Textract job: {textract_job_id}")
    attempts = 0

    while True:
        response = textract_client.get_document_analysis(JobId=textract_job_id)
        status   = response["JobStatus"]
        attempts += 1

        if status == "SUCCEEDED":
            ok(f"Textract job complete after {attempts * poll_interval}s")
            return "SUCCEEDED"
        elif status == "FAILED":
            err(f"Textract job failed: {response.get('StatusMessage', 'Unknown error')}")
            return "FAILED"
        else:
            info(f"  Status: {status} — retrying in {poll_interval}s... (attempt {attempts})")
            time.sleep(poll_interval)


def get_async_results(textract_job_id: str) -> list:
    """
    Retrieve all blocks from a completed async Textract job.
    Handles pagination automatically.
    """
    all_blocks = []
    next_token = None

    while True:
        kwargs = {"JobId": textract_job_id}
        if next_token:
            kwargs["NextToken"] = next_token

        response   = textract_client.get_document_analysis(**kwargs)
        all_blocks.extend(response.get("Blocks", []))

        next_token = response.get("NextToken")
        if not next_token:
            break

    info(f"Retrieved {len(all_blocks)} blocks from async job")
    return all_blocks


# ── 3. TABLE EXTRACTION ───────────────────────────────────────────────────────

def extract_tables(blocks: list) -> list:
    """
    Parse Textract blocks and reconstruct tables as 2-D lists.
    Returns a list of tables; each table is a list of rows (list of strings).
    """
    block_map = {block["Id"]: block for block in blocks}
    tables    = []

    for block in blocks:
        if block["BlockType"] != "TABLE":
            continue

        cells = {}
        for rel in block.get("Relationships", []):
            if rel["Type"] == "CHILD":
                for cell_id in rel["Ids"]:
                    cell_block = block_map.get(cell_id)
                    if cell_block and cell_block["BlockType"] == "CELL":
                        row = cell_block["RowIndex"]
                        col = cell_block["ColumnIndex"]

                        cell_text = ""
                        for cell_rel in cell_block.get("Relationships", []):
                            if cell_rel["Type"] == "CHILD":
                                for word_id in cell_rel["Ids"]:
                                    word_block = block_map.get(word_id)
                                    if word_block and word_block["BlockType"] == "WORD":
                                        cell_text += word_block["Text"] + " "
                        cells[(row, col)] = cell_text.strip()

        if not cells:
            continue

        max_row = max(r for r, _ in cells)
        max_col = max(c for _, c in cells)
        table   = [
            [cells.get((r, c), "") for c in range(1, max_col + 1)]
            for r in range(1, max_row + 1)
        ]
        tables.append(table)

    ok(f"Extracted {len(tables)} table(s) from document")
    return tables


def print_table(table: list, title: str = "Table"):
    """Pretty-print an extracted table to the terminal."""
    if not table:
        return
    headers = table[0] if table else []
    rows    = table[1:] if len(table) > 1 else table
    print(f"\n  {title}:")
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))


# ── 4. FORM / KEY-VALUE EXTRACTION ───────────────────────────────────────────

def extract_key_value_pairs(blocks: list) -> dict:
    """
    Extract form fields as key-value pairs from Textract blocks.
    e.g. "Invoice No" → "INV-2024-00847"
         "Date"       → "15 October 2024"
    """
    block_map  = {block["Id"]: block for block in blocks}
    kvs        = {}

    key_blocks = [
        b for b in blocks
        if b["BlockType"] == "KEY_VALUE_SET" and "KEY" in b.get("EntityTypes", [])
    ]

    for key_block in key_blocks:
        key_text = ""
        for rel in key_block.get("Relationships", []):
            if rel["Type"] == "CHILD":
                for word_id in rel["Ids"]:
                    word = block_map.get(word_id)
                    if word and word["BlockType"] == "WORD":
                        key_text += word["Text"] + " "
        key_text = key_text.strip()

        value_text = ""
        for rel in key_block.get("Relationships", []):
            if rel["Type"] == "VALUE":
                for val_id in rel["Ids"]:
                    val_block = block_map.get(val_id)
                    if val_block:
                        for val_rel in val_block.get("Relationships", []):
                            if val_rel["Type"] == "CHILD":
                                for word_id in val_rel["Ids"]:
                                    word = block_map.get(word_id)
                                    if word and word["BlockType"] == "WORD":
                                        value_text += word["Text"] + " "
        value_text = value_text.strip()

        if key_text:
            kvs[key_text] = value_text

    ok(f"Extracted {len(kvs)} key-value pair(s)")
    return kvs
