"""
scripts/setup_resources.py
---------------------------
Phase 1 — AWS Environment Setup

Run this FIRST before anything else:
    python scripts/setup_resources.py

What this does:
    1. Verifies all AWS service connections (S3, Textract, DynamoDB, Bedrock, IAM)
    2. Creates S3 buckets (raw + processed) with folder structure
    3. Creates DynamoDB metadata table
   """

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.aws_config import (
    s3_client, textract_client, dynamodb_client, bedrock_client, session,
    S3_RAW_BUCKET, S3_PROCESSED_BUCKET, DYNAMODB_TABLE,
    AWS_REGION, TEST_DOCS, OUTPUTS_DIR,
    ok, info, warn, err, head,
)
from services.s3_service       import setup_buckets, upload_file
from services.dynamodb_service import setup_table, put_record
from utils.helpers             import generate_job_id, current_timestamp, save_json, file_size_mb

from botocore.exceptions import ClientError
from tabulate import tabulate


# ── STEP 1: Verify AWS Connections ────────────────────────────────────────────

def verify_connections():
    head("Step 1 — Verifying AWS Connections")
    results = []

    services = [
        ("Amazon S3",       lambda: s3_client.list_buckets(),                                "Can list buckets"),
        ("AWS Textract",    lambda: textract_client.meta.events,                             "Client ready"),
        ("Amazon DynamoDB", lambda: dynamodb_client.list_tables(Limit=1),                   "Can list tables"),
        ("Amazon Bedrock",  lambda: session.client("bedrock").list_foundation_models(byOutputModality="TEXT"), "Models accessible"),
        ("IAM / STS",       lambda: session.client("sts").get_caller_identity()["Arn"],      None),
    ]

    for name, fn, note in services:
        try:
            ret = fn()
            detail = ret if isinstance(ret, str) and note is None else (note or "OK")
            results.append([name, "✅ Connected", detail])
        except Exception as e:
            hint = "Enable Claude in Bedrock console" if "Bedrock" in name else str(e)[:60]
            results.append([name, "⚠️  Check access" if "Bedrock" in name else "❌ Failed", hint])

    print(tabulate(results, headers=["Service", "Status", "Details"], tablefmt="rounded_outline"))
    print()


# ── STEP 2 & 3: S3 + DynamoDB ─────────────────────────────────────────────────

def setup_infrastructure():
    head("Step 2 — Setting up S3 Buckets")
    setup_buckets()

    head("Step 3 — Setting up DynamoDB Table")
    setup_table()



# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*55)
    print("  Lumi — Phase 1: Environment Setup")
    print("="*55)

    verify_connections()
    setup_infrastructure()
    
    print()
    print("="*55)
    print("  Phase 1 Complete! ✅")
    print("="*55)
    summary = [
        ["S3 raw bucket",       S3_RAW_BUCKET],
        ["S3 processed bucket", S3_PROCESSED_BUCKET],
        ["DynamoDB table",      DYNAMODB_TABLE],
        
    ]
    


if __name__ == "__main__":
    main()
