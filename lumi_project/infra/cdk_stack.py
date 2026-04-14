"""
infra/cdk_stack.py
-------------------
Phase 6 — AWS CDK Infrastructure as Code.

Defines every AWS resource Lumi needs in Python code.
When you run `cdk deploy`, CDK creates/updates everything automatically —
no clicking around in the AWS Console.

Resources created:
  - S3 raw bucket        (with Lambda trigger)
  - S3 processed bucket
  - DynamoDB table
  - 4 Lambda functions   (upload, status, results, query)
  - API Gateway REST API (wired to each Lambda)
  - CloudWatch log groups for each Lambda
  - IAM roles and policies (least-privilege)

Prerequisites:
    pip install aws-cdk-lib constructs
    npm install -g aws-cdk
    cdk bootstrap   (one-time per AWS account/region)

Deploy:
    cd infra/
    cdk deploy

Tear down everything:
    cdk destroy
"""

import os
import sys

# CDK imports — install with: pip install aws-cdk-lib constructs
try:
    import aws_cdk as cdk
    from aws_cdk import (
        Stack,
        Duration,
        RemovalPolicy,
        aws_s3             as s3,
        aws_dynamodb       as dynamodb,
        aws_lambda         as lambda_,
        aws_apigateway     as apigw,
        aws_logs           as logs,
        aws_s3_notifications as s3n,
        aws_iam            as iam,
    )
    from constructs import Construct
    CDK_AVAILABLE = True
except ImportError:
    CDK_AVAILABLE = False


# ── Stack ──────────────────────────────────────────────────────────────────────

class LumiStack(Stack):
    """
    One CDK Stack = one CloudFormation stack = all Lumi resources.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ── S3 buckets ────────────────────────────────────────────────────────

        raw_bucket = s3.Bucket(
            self, "RawBucket",
            bucket_name       = os.getenv("S3_RAW_BUCKET", "lumi-raw"),
            removal_policy    = RemovalPolicy.DESTROY,    # delete bucket on cdk destroy
            auto_delete_objects = True,
            block_public_access = s3.BlockPublicAccess.BLOCK_ALL,
            encryption        = s3.BucketEncryption.S3_MANAGED,
            cors              = [s3.CorsRule(
                allowed_methods = [s3.HttpMethods.PUT],
                allowed_origins = ["*"],                  # Restrict to Vercel domain in prod
                allowed_headers = ["*"],
            )],
        )

        processed_bucket = s3.Bucket(
            self, "ProcessedBucket",
            bucket_name         = os.getenv("S3_PROCESSED_BUCKET", "lumi-processed"),
            removal_policy      = RemovalPolicy.DESTROY,
            auto_delete_objects = True,
            block_public_access = s3.BlockPublicAccess.BLOCK_ALL,
            encryption          = s3.BucketEncryption.S3_MANAGED,
        )

        # ── DynamoDB ──────────────────────────────────────────────────────────

        table = dynamodb.Table(
            self, "MetadataTable",
            table_name     = os.getenv("DYNAMODB_TABLE", "lumi-metadata"),
            partition_key  = dynamodb.Attribute(name="job_id",    type=dynamodb.AttributeType.STRING),
            sort_key       = dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode   = dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy = RemovalPolicy.DESTROY,
            point_in_time_recovery = True,
        )

        # ── Shared Lambda environment variables ───────────────────────────────

        env_vars = {
            "S3_RAW_BUCKET":       raw_bucket.bucket_name,
            "S3_PROCESSED_BUCKET": processed_bucket.bucket_name,
            "DYNAMODB_TABLE":      table.table_name,
            "AWS_REGION":          self.region,
        }

        # ── Shared IAM policy for Lambda functions ────────────────────────────
        # Least-privilege: only what each Lambda actually needs

        lambda_policy = iam.ManagedPolicy(
            self, "LumiLambdaPolicy",
            statements=[
                iam.PolicyStatement(
                    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                    resources = [
                        raw_bucket.bucket_arn + "/*",
                        processed_bucket.bucket_arn + "/*",
                    ],
                ),
                iam.PolicyStatement(
                    actions   = ["s3:GeneratePresignedUrl"],
                    resources = [raw_bucket.bucket_arn + "/*"],
                ),
                iam.PolicyStatement(
                    actions   = [
                        "dynamodb:PutItem", "dynamodb:GetItem",
                        "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                        "dynamodb:Query", "dynamodb:Scan",
                    ],
                    resources = [table.table_arn],
                ),
                iam.PolicyStatement(
                    actions   = ["textract:*"],
                    resources = ["*"],
                ),
                iam.PolicyStatement(
                    actions   = ["bedrock:InvokeModel"],
                    resources = ["*"],
                ),
                iam.PolicyStatement(
                    actions   = ["cloudwatch:PutMetricData"],
                    resources = ["*"],
                ),
            ],
        )

        lambda_role = iam.Role(
            self, "LumiLambdaRole",
            assumed_by      = iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies = [
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                lambda_policy,
            ],
        )

        # ── Lambda functions ──────────────────────────────────────────────────

        # Common Lambda settings
        lambda_defaults = dict(
            runtime     = lambda_.Runtime.PYTHON_3_11,
            code        = lambda_.Code.from_asset(
                "..",
                bundling=cdk.BundlingOptions(
                    image   = lambda_.Runtime.PYTHON_3_11.bundling_image,
                    command = [
                        "bash", "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -r . /asset-output",
                    ],
                ),
            ),
            role        = lambda_role,
            environment = env_vars,
            timeout     = Duration.seconds(300),   # 5 min — async Textract can take a while
            memory_size = 512,
            log_retention = logs.RetentionDays.ONE_MONTH,
        )

        upload_fn  = lambda_.Function(self, "UploadFn",
            function_name = "lumi-upload",
            handler       = "api.handlers.get_upload_url",
            **lambda_defaults,
        )

        status_fn  = lambda_.Function(self, "StatusFn",
            function_name = "lumi-status",
            handler       = "api.handlers.get_status",
            **lambda_defaults,
        )

        results_fn = lambda_.Function(self, "ResultsFn",
            function_name = "lumi-results",
            handler       = "api.handlers.get_results",
            **lambda_defaults,
        )

        query_fn   = lambda_.Function(self, "QueryFn",
            function_name  = "lumi-query",
            handler        = "api.handlers.query_document",
            memory_size    = 1024,    # FAISS needs more memory
            timeout        = Duration.seconds(60),
            **{k: v for k, v in lambda_defaults.items() if k not in ("memory_size", "timeout")},
        )

        # ── API Gateway ───────────────────────────────────────────────────────

        api = apigw.RestApi(
            self, "LumiApi",
            rest_api_name = "lumi-api",
            description   = "Lumi AI Document Intelligence API",
            default_cors_preflight_options = apigw.CorsOptions(
                allow_origins  = apigw.Cors.ALL_ORIGINS,
                allow_methods  = apigw.Cors.ALL_METHODS,
                allow_headers  = ["Content-Type", "Authorization"],
            ),
            deploy_options = apigw.StageOptions(stage_name="dev"),
        )

        # POST /upload
        upload_resource = api.root.add_resource("upload")
        upload_resource.add_method(
            "POST",
            apigw.LambdaIntegration(upload_fn),
        )

        # GET /status/{jobId}
        status_resource = api.root.add_resource("status")
        status_job      = status_resource.add_resource("{jobId}")
        status_job.add_method("GET", apigw.LambdaIntegration(status_fn))

        # GET /results/{jobId}
        results_resource = api.root.add_resource("results")
        results_job      = results_resource.add_resource("{jobId}")
        results_job.add_method("GET", apigw.LambdaIntegration(results_fn))

        # POST /query
        query_resource = api.root.add_resource("query")
        query_resource.add_method("POST", apigw.LambdaIntegration(query_fn))

        # ── Outputs ───────────────────────────────────────────────────────────
        # These print to the console after `cdk deploy` finishes

        cdk.CfnOutput(self, "ApiUrl",
            value       = api.url,
            description = "Paste this into frontend/.env as VITE_API_BASE_URL",
        )
        cdk.CfnOutput(self, "RawBucketName",   value=raw_bucket.bucket_name)
        cdk.CfnOutput(self, "TableName",       value=table.table_name)


# ── Entry point ───────────────────────────────────────────────────────────────

if CDK_AVAILABLE:
    app   = cdk.App()
    stack = LumiStack(app, "LumiStack",
        env=cdk.Environment(
            account = os.getenv("CDK_DEFAULT_ACCOUNT"),
            region  = os.getenv("AWS_REGION", "us-east-1"),
        ),
    )
    cdk.Tags.of(app).add("Project", "Lumi")
    app.synth()
else:
    print()
    print("  aws-cdk-lib is not installed.")
    print("  To use CDK deployment, run:")
    print()
    print("    pip install aws-cdk-lib constructs")
    print("    npm install -g aws-cdk")
    print("    cdk bootstrap")
    print("    cd infra && cdk deploy")
    print()
