"""
infra/cloudwatch_alarms.py
---------------------------
Phase 6 — CloudWatch Alarms for production monitoring.

Sets up alarms for the three most important failure modes:
  1. Lambda errors   — any handler throwing an exception
  2. Textract failures — async jobs that return FAILED status
  3. High latency     — Lambda taking too long (cold start or Bedrock timeout)

Run once after deploying Lambda functions:
    python infra/cloudwatch_alarms.py

To delete all alarms:
    python infra/cloudwatch_alarms.py --delete
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.aws_config import session, ok, info, warn, err, head
from botocore.exceptions import ClientError
from tabulate import tabulate

# ── Config ─────────────────────────────────────────────────────────────────────

# Replace with your email — you'll get a confirmation email from AWS SNS
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "your-email@example.com")

LAMBDA_FUNCTIONS = [
    "lumi-upload",
    "lumi-status",
    "lumi-results",
    "lumi-query",
]

# Alarm thresholds
ERROR_THRESHOLD    = 1     # trigger after 1 error in 5 minutes
LATENCY_THRESHOLD  = 10000 # 10 seconds in milliseconds

cw      = session.client("cloudwatch")
sns     = session.client("sns")
results = []


# ── SNS Topic ─────────────────────────────────────────────────────────────────

def setup_sns_topic() -> str:
    """
    Create (or retrieve) an SNS topic for alarm notifications.
    Subscribes ALERT_EMAIL to the topic.
    Returns the topic ARN.
    """
    head("Setting up SNS alert topic")

    topic = sns.create_topic(Name="lumi-alerts")
    arn   = topic["TopicArn"]
    ok(f"SNS topic ready: {arn}")

    # Subscribe the email address
    sns.subscribe(TopicArn=arn, Protocol="email", Endpoint=ALERT_EMAIL)
    info(f"Subscription pending — check {ALERT_EMAIL} for confirmation email")

    return arn


# ── Lambda Error Alarms ────────────────────────────────────────────────────────

def create_lambda_error_alarm(function_name: str, topic_arn: str):
    """
    Alarm fires when a Lambda function throws any error.
    Threshold: >= 1 error in a 5-minute window.
    """
    alarm_name = f"lumi-{function_name}-errors"
    try:
        cw.put_metric_alarm(
            AlarmName          = alarm_name,
            AlarmDescription   = f"Errors in Lambda function {function_name}",
            Namespace          = "AWS/Lambda",
            MetricName         = "Errors",
            Dimensions         = [{"Name": "FunctionName", "Value": function_name}],
            Statistic          = "Sum",
            Period             = 300,        # 5-minute window
            EvaluationPeriods  = 1,
            Threshold          = ERROR_THRESHOLD,
            ComparisonOperator = "GreaterThanOrEqualToThreshold",
            TreatMissingData   = "notBreaching",
            AlarmActions       = [topic_arn],
            OKActions          = [topic_arn],
        )
        ok(f"Alarm created: {alarm_name}")
        results.append([alarm_name, "✅ Created", "Errors >= 1 in 5 min"])
    except ClientError as e:
        err(f"Failed to create alarm {alarm_name}: {e}")
        results.append([alarm_name, "❌ Failed", str(e)[:50]])


def create_lambda_latency_alarm(function_name: str, topic_arn: str):
    """
    Alarm fires when Lambda p99 latency exceeds 10 seconds.
    This catches Bedrock timeouts and cold start issues.
    """
    alarm_name = f"lumi-{function_name}-latency"
    try:
        cw.put_metric_alarm(
            AlarmName          = alarm_name,
            AlarmDescription   = f"High latency in Lambda {function_name}",
            Namespace          = "AWS/Lambda",
            MetricName         = "Duration",
            Dimensions         = [{"Name": "FunctionName", "Value": function_name}],
            ExtendedStatistic  = "p99",
            Period             = 300,
            EvaluationPeriods  = 2,
            Threshold          = LATENCY_THRESHOLD,
            ComparisonOperator = "GreaterThanOrEqualToThreshold",
            TreatMissingData   = "notBreaching",
            AlarmActions       = [topic_arn],
        )
        ok(f"Alarm created: {alarm_name}")
        results.append([alarm_name, "✅ Created", "p99 duration >= 10s"])
    except ClientError as e:
        err(f"Failed to create alarm {alarm_name}: {e}")
        results.append([alarm_name, "❌ Failed", str(e)[:50]])


# ── Textract Failure Alarm (custom metric) ────────────────────────────────────

def create_textract_failure_alarm(topic_arn: str):
    """
    Textract async jobs don't emit standard CloudWatch metrics on failure.
    Instead we use a custom metric that process_document.py publishes
    whenever a Textract job returns FAILED status.

    The metric namespace is "Lumi/Textract" and the metric name is "JobFailures".
    """
    alarm_name = "lumi-textract-job-failures"
    try:
        cw.put_metric_alarm(
            AlarmName          = alarm_name,
            AlarmDescription   = "Textract async job failures",
            Namespace          = "Lumi/Textract",
            MetricName         = "JobFailures",
            Statistic          = "Sum",
            Period             = 300,
            EvaluationPeriods  = 1,
            Threshold          = 1,
            ComparisonOperator = "GreaterThanOrEqualToThreshold",
            TreatMissingData   = "notBreaching",
            AlarmActions       = [topic_arn],
        )
        ok(f"Alarm created: {alarm_name}")
        results.append([alarm_name, "✅ Created", "Any Textract failure"])
    except ClientError as e:
        err(f"Failed to create Textract alarm: {e}")
        results.append([alarm_name, "❌ Failed", str(e)[:50]])


# ── Delete all alarms ─────────────────────────────────────────────────────────

def delete_all_alarms():
    """Delete all Lumi CloudWatch alarms."""
    head("Deleting all Lumi CloudWatch alarms")
    try:
        all_alarms = cw.describe_alarms(AlarmNamePrefix="lumi-")
        names      = [a["AlarmName"] for a in all_alarms.get("MetricAlarms", [])]

        if not names:
            info("No Lumi alarms found")
            return

        cw.delete_alarms(AlarmNames=names)
        ok(f"Deleted {len(names)} alarm(s): {', '.join(names)}")
    except ClientError as e:
        err(f"Failed to delete alarms: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Lumi CloudWatch alarm setup")
    parser.add_argument("--delete", action="store_true", help="Delete all Lumi alarms")
    args = parser.parse_args()

    if args.delete:
        delete_all_alarms()
        return

    print("\n" + "=" * 55)
    print("  Lumi — CloudWatch Alarm Setup")
    print("=" * 55)

    topic_arn = setup_sns_topic()

    head("Creating Lambda error alarms")
    for fn in LAMBDA_FUNCTIONS:
        create_lambda_error_alarm(fn, topic_arn)

    head("Creating Lambda latency alarms")
    # Only for the heavy handlers that call Bedrock/Textract
    for fn in ["lumi-upload", "lumi-query"]:
        create_lambda_latency_alarm(fn, topic_arn)

    head("Creating Textract failure alarm")
    create_textract_failure_alarm(topic_arn)

    print()
    print("=" * 55)
    print("  Alarm Setup Complete")
    print("=" * 55)
    print()
    print(tabulate(results, headers=["Alarm", "Status", "Trigger"], tablefmt="rounded_outline"))
    print()
    info(f"Check {ALERT_EMAIL} for the SNS subscription confirmation email.")
    info("You must click the link in that email or alarms won't send notifications.")
    print()


if __name__ == "__main__":
    main()
