"""
scripts/cleanup.py
-------------------
Lumi — Full Cleanup Script

Wipes everything back to a clean slate. Runs in two modes:

  SOFT reset  (default) — keeps AWS infrastructure, clears data only
    - Empties both S3 buckets (keeps the buckets themselves)
    - Deletes all DynamoDB records (keeps the table)
    - Deletes all local outputs/, test_docs/, outputs/rag/
    - Use this between test runs — setup_resources.py doesn't need to re-run

  HARD reset  (--hard flag) — tears down everything
    - Deletes both S3 buckets entirely
    - Deletes the DynamoDB table
    - Deletes all local files
    - Deletes Lambda functions (if deployed)
    - Deletes API Gateway (if deployed)
    - Use this when you're done with the project or want a full restart

Usage:
    python scripts/cleanup.py           # soft reset (data only)
    python scripts/cleanup.py --hard    # hard reset (infrastructure too)
    python scripts/cleanup.py --local   # local files only (no AWS calls)
"""

import os
import sys
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.aws_config import (
    s3_client, s3_resource, dynamodb_client, dynamodb_resource, session,
    S3_RAW_BUCKET, S3_PROCESSED_BUCKET, DYNAMODB_TABLE,
    TEST_DOCS, OUTPUTS_DIR, BASE_DIR,
    ok, info, warn, err, head,
)
from botocore.exceptions import ClientError
from tabulate import tabulate


# ── Tracker — records what was actually deleted for the summary ───────────────

results = []

def _record(label, status, detail=""):
    results.append([label, status, detail])


# ── S3 helpers ────────────────────────────────────────────────────────────────

def empty_bucket(bucket_name: str):
    """Delete all objects (and all versions) inside a bucket, keep the bucket."""
    info(f"Emptying bucket: {bucket_name}")
    try:
        bucket = s3_resource.Bucket(bucket_name)

        # Delete all current objects
        objects = list(bucket.objects.all())
        if objects:
            bucket.delete_objects(
                Delete={"Objects": [{"Key": o.key} for o in objects]}
            )
            ok(f"Deleted {len(objects)} object(s) from {bucket_name}")
        else:
            info(f"Bucket already empty: {bucket_name}")

        # Delete any versioned objects (if versioning was enabled)
        versions = list(bucket.object_versions.all())
        if versions:
            bucket.delete_objects(
                Delete={"Objects": [{"Key": v.object_key, "VersionId": v.id} for v in versions]}
            )
            ok(f"Deleted {len(versions)} version(s) from {bucket_name}")

        _record(f"S3 {bucket_name}", "✅ Emptied", f"{len(objects)} objects deleted")

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NoSuchBucket":
            warn(f"Bucket not found: {bucket_name}")
            _record(f"S3 {bucket_name}", "⚠️  Not found", "Already deleted or never created")
        else:
            err(f"Failed to empty {bucket_name}: {e}")
            _record(f"S3 {bucket_name}", "❌ Failed", str(e)[:60])


def delete_bucket(bucket_name: str):
    """Empty and permanently delete an S3 bucket."""
    empty_bucket(bucket_name)
    try:
        s3_client.delete_bucket(Bucket=bucket_name)
        ok(f"Deleted bucket: {bucket_name}")
        _record(f"S3 {bucket_name}", "✅ Deleted", "Bucket removed")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NoSuchBucket":
            warn(f"Bucket already gone: {bucket_name}")
        else:
            err(f"Failed to delete bucket: {e}")
            _record(f"S3 {bucket_name}", "❌ Failed", str(e)[:60])


# ── DynamoDB helpers ──────────────────────────────────────────────────────────

def clear_dynamodb_table():
    """
    Delete all records from the table but keep the table itself.
    Much faster than delete + recreate for a soft reset.
    """
    info(f"Clearing all records from: {DYNAMODB_TABLE}")
    try:
        table    = dynamodb_resource.Table(DYNAMODB_TABLE)
        scan     = table.scan(ProjectionExpression="job_id, #ts",
                              ExpressionAttributeNames={"#ts": "timestamp"})
        items    = scan.get("Items", [])
        deleted  = 0

        while items:
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(Key={
                        "job_id":    item["job_id"],
                        "timestamp": item["timestamp"],
                    })
                    deleted += 1

            # Handle pagination
            if "LastEvaluatedKey" not in scan:
                break
            scan  = table.scan(
                ProjectionExpression="job_id, #ts",
                ExpressionAttributeNames={"#ts": "timestamp"},
                ExclusiveStartKey=scan["LastEvaluatedKey"],
            )
            items = scan.get("Items", [])

        ok(f"Cleared {deleted} record(s) from DynamoDB")
        _record("DynamoDB records", "✅ Cleared", f"{deleted} records deleted")

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            warn(f"Table not found: {DYNAMODB_TABLE}")
            _record("DynamoDB records", "⚠️  Not found", "Table doesn't exist yet")
        else:
            err(f"Failed to clear table: {e}")
            _record("DynamoDB records", "❌ Failed", str(e)[:60])


def delete_dynamodb_table():
    """Permanently delete the DynamoDB table."""
    info(f"Deleting DynamoDB table: {DYNAMODB_TABLE}")
    try:
        dynamodb_client.delete_table(TableName=DYNAMODB_TABLE)
        ok(f"Deleted table: {DYNAMODB_TABLE}")
        _record("DynamoDB table", "✅ Deleted", "Table removed")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            warn(f"Table not found: {DYNAMODB_TABLE}")
            _record("DynamoDB table", "⚠️  Not found", "Already deleted")
        else:
            err(f"Failed to delete table: {e}")
            _record("DynamoDB table", "❌ Failed", str(e)[:60])


# ── Lambda helpers ────────────────────────────────────────────────────────────

LAMBDA_FUNCTIONS = [
    "lumi-upload",
    "lumi-status",
    "lumi-results",
    "lumi-query",
    "lumi-cors",
]

def delete_lambda_functions():
    """Delete all Lumi Lambda functions if they exist."""
    info("Deleting Lambda functions...")
    try:
        lambda_client = session.client("lambda")
        for name in LAMBDA_FUNCTIONS:
            try:
                lambda_client.delete_function(FunctionName=name)
                ok(f"Deleted Lambda: {name}")
                _record(f"Lambda {name}", "✅ Deleted", "")
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code == "ResourceNotFoundException":
                    info(f"Lambda not found (not deployed?): {name}")
                    _record(f"Lambda {name}", "⚠️  Not found", "Not deployed")
                else:
                    err(f"Failed to delete Lambda {name}: {e}")
                    _record(f"Lambda {name}", "❌ Failed", str(e)[:50])
    except Exception as e:
        warn(f"Lambda cleanup skipped: {e}")


def delete_api_gateway():
    """Delete Lumi API Gateway REST APIs."""
    info("Looking for Lumi API Gateway APIs...")
    try:
        apigw = session.client("apigateway")
        apis  = apigw.get_rest_apis().get("items", [])
        found = [a for a in apis if "lumi" in a.get("name", "").lower()]

        if not found:
            info("No Lumi API Gateway found (not deployed?)")
            _record("API Gateway", "⚠️  Not found", "Not deployed")
            return

        for api in found:
            apigw.delete_rest_api(restApiId=api["id"])
            ok(f"Deleted API Gateway: {api['name']} ({api['id']})")
            _record(f"API Gateway {api['name']}", "✅ Deleted", api["id"])

    except Exception as e:
        warn(f"API Gateway cleanup skipped: {e}")
        _record("API Gateway", "⚠️  Skipped", str(e)[:60])


# ── Local file helpers ────────────────────────────────────────────────────────

LOCAL_DIRS = [
    ("outputs/",         OUTPUTS_DIR),
    ("test_docs/",       TEST_DOCS),
    ("outputs/rag/",     os.path.join(OUTPUTS_DIR, "rag")),
]

def delete_local_files():
    """Delete all locally generated files: outputs, test docs, FAISS indexes."""
    info("Deleting local files...")

    # Delete top-level dirs (rag/ is inside outputs/ so it goes with it)
    top_level = [TEST_DOCS, OUTPUTS_DIR]
    for folder in top_level:
        if os.path.exists(folder):
            count = sum(len(files) for _, _, files in os.walk(folder))
            shutil.rmtree(folder)
            ok(f"Deleted: {os.path.relpath(folder, BASE_DIR)}/ ({count} files)")
            _record(
                os.path.relpath(folder, BASE_DIR) + "/",
                "✅ Deleted",
                f"{count} files removed",
            )
        else:
            info(f"Already clean: {os.path.relpath(folder, BASE_DIR)}/")
            _record(
                os.path.relpath(folder, BASE_DIR) + "/",
                "⚠️  Already clean",
                "",
            )

    # Recreate empty dirs so the project doesn't break on next run
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    os.makedirs(TEST_DOCS,   exist_ok=True)
    info("Recreated empty outputs/ and test_docs/ directories")


# ── Confirmation prompt ───────────────────────────────────────────────────────

def confirm_action(mode: str) -> bool:
    descriptions = {
        "soft":  (
            "SOFT RESET — clears all data, keeps AWS infrastructure:\n"
            "  • Empty both S3 buckets (buckets kept)\n"
            "  • Delete all DynamoDB records (table kept)\n"
            "  • Delete local outputs/, test_docs/, FAISS indexes"
        ),
        "hard": (
            "HARD RESET — tears down everything:\n"
            "  • Delete both S3 buckets entirely\n"
            "  • Delete DynamoDB table\n"
            "  • Delete Lambda functions (if deployed)\n"
            "  • Delete API Gateway (if deployed)\n"
            "  • Delete all local files"
        ),
        "local": (
            "LOCAL ONLY — deletes local files, no AWS calls:\n"
            "  • Delete local outputs/, test_docs/, FAISS indexes"
        ),
    }

    print()
    print("⚠️  " + descriptions[mode])
    print()
    answer = input("  Type YES to confirm: ").strip()
    return answer == "YES"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lumi cleanup — reset the project to a clean slate"
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="Hard reset: delete all AWS infrastructure (buckets, table, Lambda, API Gateway)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Local only: delete local files only, no AWS calls",
    )
    args = parser.parse_args()

    mode = "hard" if args.hard else "local" if args.local else "soft"

    print("\n" + "=" * 60)
    print("  Lumi — Cleanup")
    print("=" * 60)

    # Skip interactive prompt if NO_CONFIRM env var is set (triggered via web UI)
    if not os.getenv("LUMI_NO_CONFIRM"):
        if not confirm_action(mode):
            warn("Cleanup cancelled — nothing was deleted.")
            return
    else:
        print(f"  Auto-confirmed {mode} reset (triggered via web UI)")

    # ── SOFT reset ────────────────────────────────────────────────────────────
    if mode == "soft":
        head("Clearing S3 buckets")
        empty_bucket(S3_RAW_BUCKET)
        empty_bucket(S3_PROCESSED_BUCKET)

        head("Clearing DynamoDB records")
        clear_dynamodb_table()

        head("Deleting local files")
        delete_local_files()

    # ── HARD reset ────────────────────────────────────────────────────────────
    elif mode == "hard":
        head("Deleting S3 buckets")
        delete_bucket(S3_RAW_BUCKET)
        delete_bucket(S3_PROCESSED_BUCKET)

        head("Deleting DynamoDB table")
        delete_dynamodb_table()

        head("Deleting Lambda functions")
        delete_lambda_functions()

        head("Deleting API Gateway")
        delete_api_gateway()

        head("Deleting local files")
        delete_local_files()

    # ── LOCAL only ────────────────────────────────────────────────────────────
    elif mode == "local":
        head("Deleting local files")
        delete_local_files()

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  Cleanup complete ({mode} reset)")
    print("=" * 60)
    print()
    print(tabulate(
        results,
        headers=["Resource", "Status", "Detail"],
        tablefmt="rounded_outline",
    ))
    print()

    if mode in ("soft", "local"):
        print("  Next: python scripts/setup_resources.py  (not needed for soft reset)")
    else:
        print("  Next: python scripts/setup_resources.py  (required after hard reset)")
    print()


if __name__ == "__main__":
    main()
