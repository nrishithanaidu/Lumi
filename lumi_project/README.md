# Lumi — AI Document Intelligence System
### AWS Textract + Amazon Bedrock | Phases 1 & 2 (consolidated)

---

## Project Structure

```
lumi/
├── config/
│   └── aws_config.py          # AWS clients, env vars, console helpers
│
├── services/
│   ├── s3_service.py          # S3: bucket setup, upload, download, presigned URLs
│   ├── dynamodb_service.py    # DynamoDB: table setup, put, update, query
│   ├── textract_service.py    # Textract: sync, async, tables, key-value pairs
│   └── bedrock_service.py     # Bedrock/Claude: summarize, entities, classify
│
├── pipeline/
│   └── process_document.py    # Full orchestration pipeline (Phase 1 → 2 → 3)
│
├── scripts/
│   ├── setup_resources.py     # Phase 1: AWS setup + sample doc generation
│   └── cleanup.py             # Delete all AWS resources (reset)
│
├── utils/
│   └── helpers.py             # Shared utilities: IDs, timestamps, JSON, chunking
│
├── test_docs/                 # Auto-generated sample PDFs (gitignored)
├── outputs/                   # Extraction results as JSON (gitignored)
│
├── .env                       # Your AWS credentials (never commit!)
├── .gitignore
├── requirements.txt
└── main.py                    # Central entry point
```

---

## Quick Start

### 1. Clone and set up your virtual environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac / Linux
python -m venv venv
source venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Fill in your `.env` file
```env
AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY_HERE
AWS_SECRET_ACCESS_KEY=YOUR_SECRET_KEY_HERE
AWS_REGION=us-east-1
S3_RAW_BUCKET=lumi-raw-yourname-1234       # must be globally unique
S3_PROCESSED_BUCKET=lumi-processed-yourname-1234
DYNAMODB_TABLE=lumi-metadata
```

### 4. Run Phase 1 — AWS Setup
```bash
python scripts/setup_resources.py
```
This will:
- Verify all AWS connections (S3, Textract, DynamoDB, Bedrock, IAM)
- Create both S3 buckets with folder structure
- Create the DynamoDB metadata table
- Generate 3 sample test PDFs (invoice, contract, medical report)
- Upload them to S3 and register them in DynamoDB
- Run a Textract smoke test

### 5. Run Phase 2 — Textract Extraction
```bash
python main.py
```
Processes all 3 test documents through the full Textract pipeline.

### 6. Run with Bedrock AI Analysis (Phase 3)
```bash
python main.py --ai
```

### 7. Process your own document
```bash
python main.py --file path/to/your_document.pdf --type invoices
python main.py --file path/to/your_document.pdf --type contracts --ai
```

---

## How the Pipeline Works

```
Your PDF / Image
      │
      ▼
  [S3 Upload]  ──────────────────────────► S3 raw bucket
      │
      ▼
  [DynamoDB]   ──── status: "uploaded" ──► lumi-metadata table
      │
      ▼
  [Textract Sync]  ─── raw text, lines, words, confidence scores
      │
      ▼
  [Textract Async] ─── tables, key-value pairs (forms)
      │
      ▼
  [DynamoDB]   ──── status: "textract_done"
      │
      ▼ (if --ai flag)
  [Bedrock / Claude]
      ├── Summarize document (3 sentences)
      ├── Extract entities (names, dates, amounts, orgs)
      └── Classify document type
      │
      ▼
  [DynamoDB]   ──── status: "ai_done"
      │
      ▼
  [outputs/]   ──── {job_id}_results.json  (local)
  [S3 processed] ── results/{job_id}.json  (cloud)
```

---

## Service Reference

### `services/s3_service.py`
| Function | Description |
|---|---|
| `setup_buckets()` | Create raw + processed buckets |
| `upload_file(path, key)` | Upload local file to S3 |
| `download_file(key, path)` | Download from S3 |
| `save_json_to_s3(data, key)` | Write JSON string to processed bucket |
| `generate_presigned_url(key)` | Get a presigned PUT URL (for Phase 5 API) |

### `services/dynamodb_service.py`
| Function | Description |
|---|---|
| `setup_table()` | Create DynamoDB table |
| `put_record(item)` | Insert a new record |
| `update_record(job_id, ts, updates)` | Update fields on an existing record |
| `get_record(job_id)` | Fetch a record by job ID |
| `list_records(limit)` | Scan recent records |

### `services/textract_service.py`
| Function | Description |
|---|---|
| `extract_text_sync(file_path)` | Single-page sync extraction |
| `start_async_extraction(s3_key)` | Start async job for multi-page PDFs |
| `wait_for_async_job(job_id)` | Poll until SUCCEEDED / FAILED |
| `get_async_results(job_id)` | Retrieve all blocks (paginated) |
| `extract_tables(blocks)` | Parse blocks → 2D table list |
| `extract_key_value_pairs(blocks)` | Parse blocks → `{key: value}` dict |

### `services/bedrock_service.py` *(Phase 3)*
| Function | Description |
|---|---|
| `call_claude(prompt)` | Raw prompt → response string |
| `summarize_document(text)` | 3-sentence summary |
| `extract_entities(text)` | JSON dict of names/dates/amounts/orgs |
| `classify_document(text)` | One of: Invoice, Contract, Medical Record, ID Document, Other |

---

## DynamoDB Record Schema

| Field | Type | Description |
|---|---|---|
| `job_id` | String | Unique 8-char job ID (partition key) |
| `timestamp` | String | ISO UTC timestamp (sort key) |
| `filename` | String | Original filename |
| `s3_key` | String | S3 path to raw document |
| `doc_type` | String | invoices / contracts / medical / ids / other |
| `status` | String | uploaded → processing → textract_done → ai_done |
| `textract_job_id` | String | Async Textract job ID |
| `extracted_text` | String | First 500 chars of extracted text |
| `page_count` | String | Number of pages |
| `table_count` | String | Number of tables found |
| `kv_count` | String | Number of key-value pairs found |
| `summary` | String | AI-generated 3-sentence summary |
| `entities` | String | JSON string of extracted entities |
| `category` | String | AI document classification |

---

## Cleanup
```bash
python scripts/cleanup.py
```
⚠️ Permanently deletes all S3 buckets, the DynamoDB table, and local outputs.

---

## Coming Next

| Phase | Description |
|---|---|
| Phase 3 | Amazon Bedrock deep integration (already stubbed in `bedrock_service.py`) |
| Phase 4 | RAG pipeline — FAISS + Titan Embeddings + Claude Q&A |
| Phase 5 | REST API (API Gateway + Lambda) + React frontend |
| Phase 6 | Testing, CloudWatch, CDK infrastructure as code |
