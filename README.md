# Lumi — AI Document Intelligence

> A production-grade document intelligence platform powered by AWS Textract, Amazon Bedrock Nova Lite, and RAG.

---

## Quick Start

### 1. Install & Run

**macOS / Linux:**
```bash
chmod +x start.sh
./start.sh
```

**Windows:**
```
Double-click start.bat
```

Then open **http://localhost:5000** in your browser.

> All setup, configuration, and document processing happens **entirely through the web UI**.

---

## Architecture

```
Browser (Gold & Black UI)
        │
        ▼
Flask Server (server.py :5000)
        │
        ├── /api/upload        → S3 upload + Textract pipeline
        ├── /api/status/:id    → DynamoDB polling
        ├── /api/results/:id   → Full extraction results
        ├── /api/query         → RAG Q&A via Bedrock Nova Lite
        ├── /api/setup/start   → Runs setup_resources.py (web button)
        ├── /api/health        → AWS connection check
        └── /api/config        → Save AWS credentials via UI
                │
                ▼
        lumi_project/
        ├── services/
        │   ├── bedrock_service.py   ← Amazon Nova Lite v1 (Converse API)
        │   ├── textract_service.py  ← AWS Textract sync + async
        │   ├── s3_service.py        ← S3 upload & presigned URLs
        │   ├── dynamodb_service.py  ← Metadata store
        │   └── rag_service.py       ← FAISS vector search
        ├── pipeline/
        │   └── process_document.py  ← Full orchestration
        └── scripts/
            └── setup_resources.py   ← Phase 1 setup (triggered via UI)
```

---

## Configuration (Via Web UI)

1. Open http://localhost:5000
2. Click **Settings** in the nav
3. Enter your AWS credentials:
   - AWS Access Key ID
   - AWS Secret Access Key
   - Region (default: `us-east-1`)
   - S3 bucket names
   - DynamoDB table name
4. Click **Save Configuration**

---

## AWS Setup (Via Web UI)

1. Go to **Setup** in the nav
2. Click **Run Setup**
3. Watch the terminal — all 6 steps run automatically:
   - Step 1: Verify AWS connections
   - Step 2: Create S3 buckets
   - Step 3: Create DynamoDB table
   - Step 4: Generate 3 sample PDFs (invoice, contract, medical)
   - Step 5: Upload to S3 + register in DynamoDB
   - Step 6: Textract smoke test

---

## AI Model

| Model | ID | Purpose |
|---|---|---|
| Amazon Nova Lite v1 | `amazon.nova-lite-v1:0` | Summary, entities, Q&A |
| Amazon Titan Embed v2 | `amazon.titan-embed-text-v2:0` | RAG embeddings |
| AWS Textract | — | OCR, tables, forms |

Enable **Nova Lite** and **Titan Embeddings** in:
`AWS Console → Amazon Bedrock → Model Access`

---

## Usage Flow

```
Upload Document → Textract Extracts Text & Tables
               → Bedrock Nova Lite Summarises & Classifies
               → FAISS Indexes for RAG Q&A
               → View Results in Browser
               → Ask AI questions about the document
```

---

## Project Structure

```
lumi_final/
├── server.py              ← Flask server (entry point)
├── requirements.txt        ← Python dependencies
├── start.sh               ← macOS/Linux launcher
├── start.bat              ← Windows launcher
├── .env                   ← AWS credentials (edit this)
├── templates/
│   └── index.html         ← Full frontend (gold & black UI)
└── lumi_project/
    ├── main.py
    ├── api/handlers.py
    ├── config/aws_config.py
    ├── pipeline/process_document.py
    ├── services/
    │   ├── bedrock_service.py   ← Nova Lite
    │   ├── textract_service.py
    │   ├── s3_service.py
    │   ├── dynamodb_service.py
    │   └── rag_service.py
    ├── scripts/setup_resources.py
    └── utils/helpers.py
```

---

## Requirements

- Python 3.10+
- AWS account with:
  - IAM user with S3, DynamoDB, Textract, Bedrock permissions
  - Nova Lite model access enabled in Bedrock
  - Titan Embeddings model access enabled in Bedrock

---

## IAM Policy (Minimum Required)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["dynamodb:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["textract:*"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:Converse"], "Resource": "*" }
  ]
}
```

---

Built with AWS Textract · Amazon Bedrock Nova Lite · FAISS · Flask
