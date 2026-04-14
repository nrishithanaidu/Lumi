"""
services/s3_service.py
-----------------------
All S3 operations: bucket setup, file upload, download, presigned URLs.
"""

import os
from botocore.exceptions import ClientError
from config.aws_config import (
    s3_client, S3_RAW_BUCKET, S3_PROCESSED_BUCKET, AWS_REGION,
    ok, info, warn, err,
)


def create_bucket(bucket_name: str, region: str = AWS_REGION):
    """Create an S3 bucket with public access blocked."""
    try:
        if region == "us-east-1":
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls":       True,
                "IgnorePublicAcls":      True,
                "BlockPublicPolicy":     True,
                "RestrictPublicBuckets": True,
            },
        )
        ok(f"Bucket created: {bucket_name}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            info(f"Bucket already exists: {bucket_name}")
        else:
            err(f"Failed to create bucket {bucket_name}: {e}")
            raise


def setup_buckets():
    """Create raw and processed S3 buckets with folder structure."""
    create_bucket(S3_RAW_BUCKET, AWS_REGION)
    create_bucket(S3_PROCESSED_BUCKET, AWS_REGION)

    folders = ["invoices/", "contracts/", "medical/", "ids/", "other/"]
    for folder in folders:
        try:
            s3_client.put_object(Bucket=S3_RAW_BUCKET, Key=folder)
            ok(f"Folder ready: s3://{S3_RAW_BUCKET}/{folder}")
        except ClientError as e:
            warn(f"Could not create folder {folder}: {e}")


def upload_file(local_path: str, s3_key: str, bucket: str = S3_RAW_BUCKET) -> str:
    """Upload a local file to S3. Returns the full S3 URI."""
    s3_client.upload_file(local_path, bucket, s3_key)
    ok(f"Uploaded: {os.path.basename(local_path)} → s3://{bucket}/{s3_key}")
    return f"s3://{bucket}/{s3_key}"


def download_file(s3_key: str, local_path: str, bucket: str = S3_RAW_BUCKET):
    """Download a file from S3 to a local path."""
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    s3_client.download_file(bucket, s3_key, local_path)
    ok(f"Downloaded: s3://{bucket}/{s3_key} → {local_path}")


def save_json_to_s3(data: str, s3_key: str, bucket: str = S3_PROCESSED_BUCKET):
    """Save a JSON string directly to S3 (no local file needed)."""
    s3_client.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=data.encode("utf-8"),
        ContentType="application/json",
    )
    ok(f"JSON saved to s3://{bucket}/{s3_key}")


def generate_presigned_url(s3_key: str, bucket: str = S3_RAW_BUCKET, expiry: int = 3600) -> str:
    """Generate a presigned URL for uploading a file directly to S3."""
    url = s3_client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expiry,
    )
    return url
