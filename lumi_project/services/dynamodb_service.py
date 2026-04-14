"""
services/dynamodb_service.py
-----------------------------
All DynamoDB operations: table setup, record creation, updates, queries.
"""

from botocore.exceptions import ClientError
from config.aws_config import (
    dynamodb_client, dynamodb_resource, DYNAMODB_TABLE,
    ok, info, err,
)
from tabulate import tabulate


def setup_table():
    """Create the DynamoDB metadata table if it doesn't exist."""
    try:
        table = dynamodb_resource.create_table(
            TableName=DYNAMODB_TABLE,
            KeySchema=[
                {"AttributeName": "job_id",    "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "job_id",    "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        info("Waiting for DynamoDB table to be ready...")
        table.wait_until_exists()
        ok(f"Table created: {DYNAMODB_TABLE}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            info(f"Table already exists: {DYNAMODB_TABLE}")
        else:
            err(f"Failed to create table: {e}")
            raise

    # Print schema for reference
    schema = [
        ["job_id",          "String", "Unique job ID (partition key)"],
        ["timestamp",       "String", "Upload time ISO string (sort key)"],
        ["filename",        "String", "Original filename"],
        ["s3_key",          "String", "S3 path to raw document"],
        ["doc_type",        "String", "invoice / contract / medical / id / other"],
        ["status",          "String", "uploaded / processing / textract_done / ai_done / failed"],
        ["textract_job_id", "String", "Async Textract job ID"],
        ["extracted_text",  "String", "First 500 chars of extracted text"],
        ["entities",        "Map",    "Names, dates, amounts, organisations"],
        ["summary",         "String", "AI summary from Bedrock"],
        ["category",        "String", "Document category from Bedrock"],
        ["page_count",      "Number", "Number of pages"],
    ]
    print()
    print(tabulate(schema, headers=["Field", "Type", "Description"], tablefmt="rounded_outline"))


def put_record(item: dict):
    """Insert a new record into DynamoDB."""
    table = dynamodb_resource.Table(DYNAMODB_TABLE)
    table.put_item(Item=item)
    ok(f"DynamoDB record created: job_id={item.get('job_id')}")


def update_record(job_id: str, timestamp: str, updates: dict):
    """Update an existing DynamoDB record with new fields."""
    table = dynamodb_resource.Table(DYNAMODB_TABLE)
    try:
        update_expr  = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
        expr_names   = {f"#{k}": k for k in updates}
        expr_values  = {f":{k}": v for k, v in updates.items()}

        table.update_item(
            Key={"job_id": job_id, "timestamp": timestamp},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        ok(f"DynamoDB updated: job_id={job_id}")
    except ClientError as e:
        err(f"DynamoDB update failed: {e}")


def get_record(job_id: str) -> dict | None:
    """Fetch the first DynamoDB record matching a job_id."""
    table = dynamodb_resource.Table(DYNAMODB_TABLE)
    response = table.query(
        KeyConditionExpression="job_id = :jid",
        ExpressionAttributeValues={":jid": job_id},
    )
    items = response.get("Items", [])
    return items[0] if items else None


def list_records(limit: int = 20) -> list:
    """Scan and return recent DynamoDB records."""
    table = dynamodb_resource.Table(DYNAMODB_TABLE)
    response = table.scan(Limit=limit)
    return response.get("Items", [])
