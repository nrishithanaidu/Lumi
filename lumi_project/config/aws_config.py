"""
config/aws_config.py
---------------------
Central configuration for Lumi.
All AWS clients are created here and imported across the project.
"""

import os
import boto3
from dotenv import load_dotenv
from colorama import Fore, Style, init

import sys
import io

# Force UTF-8 output on Windows to avoid cp1252 emoji encoding errors
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

init(autoreset=True)
load_dotenv()


# ── AWS Config ────────────────────────────────────────────────────────────────

AWS_REGION          = os.getenv("AWS_REGION", "us-east-1")
S3_RAW_BUCKET       = os.getenv("S3_RAW_BUCKET", "lumi-raw")
S3_PROCESSED_BUCKET = os.getenv("S3_PROCESSED_BUCKET", "lumi-processed")
DYNAMODB_TABLE      = os.getenv("DYNAMODB_TABLE", "lumi-metadata")


# ── AWS Session & Clients ─────────────────────────────────────────────────────

session = boto3.Session(
    aws_access_key_id     = os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name           = AWS_REGION,
)

s3_client         = session.client("s3")
s3_resource       = session.resource("s3")
textract_client   = session.client("textract")
dynamodb_client   = session.client("dynamodb")
dynamodb_resource = session.resource("dynamodb")
bedrock_client    = session.client("bedrock-runtime")


# ── Local Paths ───────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DOCS   = os.path.join(BASE_DIR, "test_docs")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

os.makedirs(TEST_DOCS,   exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)


# ── Console Helpers ───────────────────────────────────────────────────────────

def ok(msg):   print(f"{Fore.GREEN}✅ {msg}{Style.RESET_ALL}")
def info(msg): print(f"{Fore.CYAN}ℹ️  {msg}{Style.RESET_ALL}")
def warn(msg): print(f"{Fore.YELLOW}⚠️  {msg}{Style.RESET_ALL}")
def err(msg):  print(f"{Fore.RED}❌ {msg}{Style.RESET_ALL}")
def head(msg): print(f"\n{Fore.BLUE}{'─'*55}\n   {msg}\n{'─'*55}{Style.RESET_ALL}")
