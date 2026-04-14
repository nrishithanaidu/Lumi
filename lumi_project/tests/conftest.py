"""
tests/conftest.py
------------------
Shared pytest fixtures for all Lumi tests.

conftest.py is automatically loaded by pytest before any test file runs.
Fixtures defined here are available to every test without importing them.

Run all tests:
    pytest tests/ -v

Run a specific file:
    pytest tests/test_pipeline.py -v

Run with output visible (don't capture prints):
    pytest tests/ -v -s
"""

import os
import sys
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.aws_config import TEST_DOCS, OUTPUTS_DIR


# ── Sample document texts ─────────────────────────────────────────────────────
# We use hardcoded text rather than real PDFs for most unit tests so they
# run fast and don't require AWS Textract to be called on every test run.

@pytest.fixture
def invoice_text():
    return """
    TAX INVOICE
    Invoice No: INV-2024-00847
    Date: 15 October 2024
    From: TechSolutions Pvt. Ltd., 42 MG Road, Bengaluru - 560001
    To: Rahul Mehta, Infosys Ltd., Electronic City, Bengaluru - 560100
    Description                           Qty     Unit Price    Total
    AWS Cloud Architecture Consulting     10 hrs  Rs. 8,000     Rs. 80,000
    Textract Integration & Setup           5 hrs  Rs. 8,000     Rs. 40,000
    Documentation & Training               3 hrs  Rs. 6,000     Rs. 18,000
    Subtotal: Rs. 1,38,000
    GST 18%:  Rs. 24,840
    TOTAL DUE: Rs. 1,62,840
    Payment due within 30 days.
    Bank: HDFC Bank, Acc: 5020001234567, IFSC: HDFC0001234
    """


@pytest.fixture
def contract_text():
    return """
    SERVICE AGREEMENT
    This Agreement is entered into as of 15 October 2024, between:
    Party A: Infosys Limited, Electronics City, Bengaluru - 560100 ("Client")
    Party B: Amazon Web Services India Pvt. Ltd., MG Road, Bengaluru ("Service Provider")
    1. SCOPE OF SERVICES
    Service Provider agrees to provide cloud migration consulting and AWS infrastructure setup.
    2. TERM
    This Agreement commences 15 October 2024 and continues until 31 March 2025.
    3. PAYMENT
    Total contract value: Rs. 14,50,000. Advance payment of Rs. 2,00,000 due upon signing.
    4. TERMINATION
    Either party may terminate with 30 days written notice.
    Signed: Rahul Mehta (Infosys Ltd.)     Date: 15 Oct 2024
    Signed: S. Krishnan (Amazon Web Services) Date: 15 Oct 2024
    """


@pytest.fixture
def medical_text():
    return """
    APOLLO DIAGNOSTICS — PATHOLOGY REPORT
    Patient: Ananya Sharma   Age: 28   Gender: Female
    Patient ID: APL-2024-09821   Date: 10 October 2024
    Referred by: Dr. Vikram Rao, MD (Internal Medicine)
    COMPLETE BLOOD COUNT (CBC)
    Test          Result   Normal Range          Unit         Status
    Haemoglobin   11.2     12.0 - 16.0           g/dL         LOW
    WBC Count     7800     4500 - 11000           /uL          NORMAL
    Platelet      240000   150000 - 450000        /uL          NORMAL
    CLINICAL NOTES
    Mild microcytic hypochromic anaemia noted. Iron deficiency suspected.
    Reported by: Dr. Priya Nair, MD (Pathology)   Date: 11 October 2024
    """


@pytest.fixture
def sample_invoice_pdf():
    """Path to the generated invoice PDF — requires Phase 1 setup to have run."""
    path = os.path.join(TEST_DOCS, "sample_invoice.pdf")
    if not os.path.exists(path):
        pytest.skip("sample_invoice.pdf not found — run scripts/setup_resources.py first")
    return path


@pytest.fixture
def sample_contract_pdf():
    path = os.path.join(TEST_DOCS, "sample_contract.pdf")
    if not os.path.exists(path):
        pytest.skip("sample_contract.pdf not found — run scripts/setup_resources.py first")
    return path


@pytest.fixture
def sample_medical_pdf():
    path = os.path.join(TEST_DOCS, "sample_medical_report.pdf")
    if not os.path.exists(path):
        pytest.skip("sample_medical_report.pdf not found — run scripts/setup_resources.py first")
    return path


@pytest.fixture
def phase2_results():
    """Load phase2 results JSON if it exists."""
    path = os.path.join(OUTPUTS_DIR, "all_results.json")
    if not os.path.exists(path):
        pytest.skip("all_results.json not found — run main.py first")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def mock_lambda_event():
    """Factory fixture for building mock API Gateway events."""
    def _make(method="GET", path_params=None, body=None):
        return {
            "httpMethod":      method,
            "pathParameters":  path_params or {},
            "body":            json.dumps(body) if body else None,
            "headers":         {"Content-Type": "application/json"},
        }
    return _make
