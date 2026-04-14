"""
tests/test_handlers.py
-----------------------
Unit tests for the Lambda API handlers.

These tests mock all AWS/service calls so they run instantly offline.
We're testing the handler logic (routing, validation, response format),
not the underlying services (those get tested in test_pipeline.py).

Run:
    pytest tests/test_handlers.py -v
"""

import json
import pytest
from unittest.mock import patch, MagicMock


class TestGetUploadUrl:
    """POST /upload"""

    def _call(self, body):
        from api.handlers import get_upload_url
        return get_upload_url({"body": json.dumps(body)})

    @patch("api.handlers.generate_presigned_url", return_value="https://s3.example.com/signed")
    def test_returns_200_with_valid_input(self, mock_url):
        resp = self._call({"filename": "invoice.pdf", "doc_type": "invoices"})
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert "job_id" in body
        assert "upload_url" in body
        assert body["upload_url"] == "https://s3.example.com/signed"

    @patch("api.handlers.generate_presigned_url", return_value="https://s3.example.com/signed")
    def test_s3_key_contains_doc_type_and_filename(self, mock_url):
        resp = self._call({"filename": "my_contract.pdf", "doc_type": "contracts"})
        body = json.loads(resp["body"])
        assert "contracts" in body["s3_key"]
        assert "my_contract.pdf" in body["s3_key"]

    def test_missing_filename_returns_400(self):
        resp = self._call({"doc_type": "invoices"})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "error" in body

    @patch("api.handlers.generate_presigned_url", return_value="https://s3.example.com/signed")
    def test_invalid_doc_type_defaults_to_other(self, mock_url):
        resp = self._call({"filename": "file.pdf", "doc_type": "nonsense"})
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert "other" in body["s3_key"]

    @patch("api.handlers.generate_presigned_url", side_effect=Exception("AWS error"))
    def test_aws_failure_returns_500(self, mock_url):
        resp = self._call({"filename": "file.pdf", "doc_type": "invoices"})
        assert resp["statusCode"] == 500

    def test_cors_headers_always_present(self):
        resp = self._call({})
        assert "Access-Control-Allow-Origin" in resp["headers"]


class TestGetStatus:
    """GET /status/{jobId}"""

    def _call(self, job_id):
        from api.handlers import get_status
        return get_status({"pathParameters": {"jobId": job_id}})

    @patch("api.handlers.get_record", return_value={
        "job_id":    "abc123",
        "status":    "ai_done",
        "filename":  "invoice.pdf",
        "doc_type":  "invoices",
        "timestamp": "2024-10-15T10:00:00+00:00",
    })
    def test_returns_200_for_known_job(self, mock_record):
        resp = self._call("abc123")
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["status"] == "ai_done"
        assert body["job_id"] == "abc123"

    @patch("api.handlers.get_record", return_value=None)
    def test_returns_404_for_unknown_job(self, mock_record):
        resp = self._call("doesnotexist")
        assert resp["statusCode"] == 404

    def test_missing_job_id_returns_400(self):
        from api.handlers import get_status
        resp = get_status({"pathParameters": {}})
        assert resp["statusCode"] == 400

    def test_cors_headers_always_present(self):
        with patch("api.handlers.get_record", return_value=None):
            resp = self._call("x")
            assert "Access-Control-Allow-Origin" in resp["headers"]


class TestGetResults:
    """GET /results/{jobId}"""

    def _call(self, job_id):
        from api.handlers import get_results
        return get_results({"pathParameters": {"jobId": job_id}})

    @patch("api.handlers.get_record", return_value={
        "job_id":         "abc123",
        "status":         "ai_done",
        "filename":       "invoice.pdf",
        "doc_type":       "invoices",
        "extracted_text": "TAX INVOICE...",
        "summary":        "An invoice from TechSolutions.",
        "entities":       '{"names": ["Rahul Mehta"], "dates": ["15 October 2024"]}',
        "category":       "Invoice",
        "page_count":     "1",
        "rag_indexed":    "true",
        "timestamp":      "2024-10-15T10:00:00+00:00",
    })
    def test_returns_full_results_when_done(self, mock_record):
        resp = self._call("abc123")
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["category"] == "Invoice"
        assert body["summary"]  == "An invoice from TechSolutions."
        # Entities should be parsed from JSON string back to dict
        assert isinstance(body["entities"], dict)
        assert "names" in body["entities"]

    @patch("api.handlers.get_record", return_value={
        "job_id": "abc123", "status": "processing"
    })
    def test_returns_polling_message_when_processing(self, mock_record):
        resp = self._call("abc123")
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert "Poll" in body.get("message", "") or body["status"] == "processing"

    @patch("api.handlers.get_record", return_value=None)
    def test_returns_404_for_unknown_job(self, mock_record):
        resp = self._call("ghost")
        assert resp["statusCode"] == 404


class TestQueryDocument:
    """POST /query"""

    def _call(self, body):
        from api.handlers import query_document
        return query_document({"body": json.dumps(body)})

    def test_missing_job_id_returns_400(self):
        resp = self._call({"question": "What is this?"})
        assert resp["statusCode"] == 400

    def test_missing_question_returns_400(self):
        resp = self._call({"job_id": "abc123"})
        assert resp["statusCode"] == 400

    def test_very_long_question_returns_400(self):
        resp = self._call({"job_id": "abc123", "question": "x" * 1001})
        assert resp["statusCode"] == 400

    @patch("api.handlers.list_indexed_documents", return_value=[])
    def test_unindexed_document_returns_404(self, mock_list):
        resp = self._call({"job_id": "notindexed", "question": "Who signed this?"})
        assert resp["statusCode"] == 404

    @patch("api.handlers.list_indexed_documents", return_value=["abc123"])
    @patch("api.handlers.query", return_value={
        "answer":           "The contract expires on 31 March 2025.",
        "question":         "When does it expire?",
        "retrieved_chunks": [{"text": "...", "score": 0.95, "chunk_index": 2}],
    })
    def test_successful_query_returns_answer(self, mock_query, mock_list):
        resp = self._call({"job_id": "abc123", "question": "When does it expire?"})
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert "answer" in body
        assert "31 March 2025" in body["answer"]
        assert body["chunks_used"] == 1


class TestCORSPreflight:
    def test_options_returns_200(self):
        from api.handlers import cors_preflight
        resp = cors_preflight({})
        assert resp["statusCode"] == 200
        assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
