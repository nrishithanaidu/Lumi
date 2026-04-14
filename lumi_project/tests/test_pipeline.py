"""
tests/test_pipeline.py
-----------------------
Integration tests for the full pipeline. These call real AWS services
so they require valid credentials and Phase 1 setup to have run.

They are marked with @pytest.mark.integration so you can skip them
during fast local development:

    pytest tests/ -v                          # runs everything
    pytest tests/ -v -m "not integration"     # skips AWS calls
    pytest tests/ -v -m integration           # only AWS tests

Run all integration tests (requires AWS):
    pytest tests/test_pipeline.py -v -s
"""

import os
import json
import pytest

pytestmark = pytest.mark.integration


class TestTextractSync:
    """Phase 2 — sync text extraction"""

    def test_invoice_extracts_text(self, sample_invoice_pdf):
        from services.textract_service import extract_text_sync
        result = extract_text_sync(sample_invoice_pdf)

        assert result["line_count"] > 0
        assert result["word_count"] > 0
        assert result["pages"] >= 1
        assert "INV" in result["full_text"] or "INVOICE" in result["full_text"]
        assert result["avg_confidence"] > 80.0

    def test_contract_extracts_text(self, sample_contract_pdf):
        from services.textract_service import extract_text_sync
        result = extract_text_sync(sample_contract_pdf)

        assert result["line_count"] > 0
        assert "AGREEMENT" in result["full_text"].upper() or "SERVICE" in result["full_text"].upper()

    def test_medical_extracts_text(self, sample_medical_pdf):
        from services.textract_service import extract_text_sync
        result = extract_text_sync(sample_medical_pdf)

        assert result["line_count"] > 0
        # Medical report should contain patient info
        assert any(word in result["full_text"] for word in ["Patient", "PATIENT", "Blood", "BLOOD"])


class TestBedrockService:
    """Phase 3 — Bedrock / Claude calls"""

    def test_summarize_returns_three_sentences(self, invoice_text):
        from services.bedrock_service import summarize_document
        summary = summarize_document(invoice_text)

        assert isinstance(summary, str)
        assert len(summary) > 50
        # Rough check — 3 sentences means roughly 2-4 full stops
        sentence_count = summary.count(".") + summary.count("!")
        assert 1 <= sentence_count <= 6

    def test_extract_entities_returns_dict(self, invoice_text):
        from services.bedrock_service import extract_entities
        entities = extract_entities(invoice_text)

        assert isinstance(entities, dict)
        # Must have the 5 expected keys
        for key in ["names", "dates", "amounts", "organisations", "ids"]:
            assert key in entities
            assert isinstance(entities[key], list)

    def test_invoice_entities_contain_expected_data(self, invoice_text):
        from services.bedrock_service import extract_entities
        entities = extract_entities(invoice_text)

        all_text = json.dumps(entities).lower()
        # Should find at least one of: amount, date, or name from the invoice
        assert any(kw in all_text for kw in ["rahul", "2024", "80,000", "infosys", "inv-"])

    def test_classify_invoice(self, invoice_text):
        from services.bedrock_service import classify_document
        label = classify_document(invoice_text)
        assert label == "Invoice"

    def test_classify_contract(self, contract_text):
        from services.bedrock_service import classify_document
        label = classify_document(contract_text)
        assert label == "Contract"

    def test_classify_medical(self, medical_text):
        from services.bedrock_service import classify_document
        label = classify_document(medical_text)
        assert label == "Medical Record"

    def test_answer_question_uses_context(self, invoice_text):
        from services.bedrock_service import answer_question
        answer = answer_question(
            question="What is the total amount due?",
            context_chunks=[invoice_text],
        )
        assert isinstance(answer, str)
        assert len(answer) > 10
        # The answer should reference the actual amount
        assert any(kw in answer for kw in ["1,62,840", "162840", "total", "Rs"])

    def test_answer_not_in_context_says_so(self, invoice_text):
        from services.bedrock_service import answer_question
        answer = answer_question(
            question="What is the patient's blood pressure?",
            context_chunks=[invoice_text],
        )
        # Claude should admit it can't find this — not hallucinate
        assert any(phrase in answer.lower() for phrase in [
            "could not find", "not in", "no information", "not mentioned", "not provided"
        ])


class TestRAGPipeline:
    """Phase 4 — RAG indexing and querying"""

    TEST_JOB_ID = "pytest-rag-001"

    def test_index_and_query_roundtrip(self, contract_text):
        from services.rag_service import index_document, query

        # Index
        n_chunks = index_document(self.TEST_JOB_ID, contract_text, chunk_size=80, overlap=10)
        assert n_chunks > 0

        # Query
        result = query(self.TEST_JOB_ID, "When does the contract expire?")
        assert "answer" in result
        assert len(result["answer"]) > 10
        assert len(result["retrieved_chunks"]) > 0

    def test_query_returns_chunk_scores(self, contract_text):
        from services.rag_service import index_document, query
        index_document(self.TEST_JOB_ID, contract_text, chunk_size=80, overlap=10)
        result = query(self.TEST_JOB_ID, "Who are the parties?")

        for chunk in result["retrieved_chunks"]:
            assert "score" in chunk
            assert 0.0 <= chunk["score"] <= 1.5   # cosine similarity range

    def test_contract_date_query(self, contract_text):
        from services.rag_service import index_document, query
        index_document(self.TEST_JOB_ID, contract_text, chunk_size=80, overlap=10)
        result = query(self.TEST_JOB_ID, "What is the payment amount?")

        assert "answer" in result
        # Should retrieve something relevant to payment
        combined = " ".join(r["text"] for r in result["retrieved_chunks"])
        assert any(kw in combined for kw in ["payment", "Payment", "Rs", "value"])


class TestFullPipeline:
    """End-to-end pipeline test across all 3 document types"""

    @pytest.mark.parametrize("filename,doc_type,expected_category", [
        ("sample_invoice.pdf",        "invoices",  "Invoice"),
        ("sample_contract.pdf",       "contracts", "Contract"),
        ("sample_medical_report.pdf", "medical",   "Medical Record"),
    ])
    def test_full_pipeline_all_doc_types(
        self, filename, doc_type, expected_category, tmp_path
    ):
        from config.aws_config import TEST_DOCS
        from pipeline.process_document import run_pipeline

        local_path = os.path.join(TEST_DOCS, filename)
        if not os.path.exists(local_path):
            pytest.skip(f"{filename} not found — run setup_resources.py first")

        result = run_pipeline(
            local_path  = local_path,
            doc_type    = doc_type,
            run_ai      = True,
            run_rag     = True,
        )

        # Basic structure
        assert result is not None
        assert "job_id"     in result
        assert "full_text"  in result
        assert "tables"     in result
        assert "summary"    in result
        assert "entities"   in result
        assert "category"   in result
        assert "rag_indexed" in result

        # Content checks
        assert result["pages"] >= 1
        assert result["line_count"] > 0
        assert result["category"] == expected_category
        assert isinstance(result["summary"], str) and len(result["summary"]) > 20
        assert result["rag_indexed"] is True

        # Entities structure
        for key in ["names", "dates", "amounts", "organisations"]:
            assert key in result["entities"]
