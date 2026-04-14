"""
main.py
--------
Lumi — AI Document Intelligence System
Entry point for running the full pipeline.

Usage:
    # Process all 3 test documents (Textract only):
    python main.py

    # Process all test documents + run Bedrock AI analysis:
    python main.py --ai

    # Process a single custom document:
    python main.py --file path/to/your.pdf --type invoices

    # Process a custom document with AI analysis:
    python main.py --file path/to/your.pdf --type contracts --ai
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.aws_config import OUTPUTS_DIR, TEST_DOCS, ok, info, warn, err, head
from pipeline.process_document import run_pipeline
from utils.helpers import load_json, save_json
from tabulate import tabulate


def process_test_documents(run_ai: bool = False, run_rag: bool = False) -> dict:
    """
    Run the full pipeline on all 3 test documents generated in Phase 1.
    Returns a dict of all results keyed by doc_type.
    """
    jobs_path = os.path.join(OUTPUTS_DIR, "phase1_jobs.json")

    if not os.path.exists(jobs_path):
        err("phase1_jobs.json not found.")
        err("Run setup first: python scripts/setup_resources.py")
        sys.exit(1)

    jobs = load_json(jobs_path)
    info(f"Loaded {len(jobs)} job(s) from phase1_jobs.json")

    all_results = {}

    for doc_type, job_info in jobs.items():
        filename   = job_info.get("filename") or _infer_filename(doc_type)
        local_path = os.path.join(TEST_DOCS, filename)

        if not os.path.exists(local_path):
            warn(f"Test doc not found: {local_path} — skipping")
            continue

        results = run_pipeline(local_path, doc_type=doc_type, run_ai=run_ai, run_rag=run_rag)
        if results:
            all_results[doc_type] = results

    # Save combined results
    combined_path = os.path.join(OUTPUTS_DIR, "all_results.json")
    save_json(all_results, combined_path)
    ok(f"Combined results saved to: {combined_path}")

    return all_results


def _infer_filename(doc_type: str) -> str:
    """Fallback filename inference from doc_type key."""
    mapping = {
        "invoices":  "sample_invoice.pdf",
        "contracts": "sample_contract.pdf",
        "medical":   "sample_medical_report.pdf",
    }
    return mapping.get(doc_type, f"{doc_type}.pdf")


def print_final_summary(all_results: dict):
    """Print a final summary table after all documents are processed."""
    print()
    print("=" * 60)
    print("  Lumi — Run Complete")
    print("=" * 60)

    rows = []
    for doc_type, r in all_results.items():
        rows.append([
            r.get("filename", doc_type),
            r.get("pages",        "—"),
            r.get("line_count",   "—"),
            r.get("table_count",  len(r.get("tables", []))) if "tables" in r else "—",
            r.get("category",     "—") or "—",
            r.get("status",       "—"),
        ])

    print(tabulate(
        rows,
        headers=["File", "Pages", "Lines", "Tables", "Category", "Status"],
        tablefmt="rounded_outline",
    ))
    print()
    print("  Results saved to: outputs/all_results.json")
    print()
    print("  Next steps:")
    print("    Phase 3 AI  → set run_ai=True or use --ai flag")
    print("    Phase 4 RAG → coming soon (FAISS + Titan Embeddings)")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def run_query_mode(job_id: str):
    """
    Interactive Q&A loop for a specific document.
    Loads its FAISS index and lets you ask questions until you type 'exit'.
    """
    from services.rag_service import query, list_indexed_documents

    indexed = list_indexed_documents()
    if job_id not in indexed:
        err(f"No RAG index found for job_id '{job_id}'.")
        info(f"Indexed documents: {indexed or 'none yet'}")
        info("Run with --rag flag first to index a document.")
        return

    print(f"\nAsking questions about job: {job_id}")
    print("Type 'exit' to quit.\n")

    while True:
        try:
            question = input("  Your question: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if question.lower() in ("exit", "quit", "q"):
            break
        if not question:
            continue

        result = query(job_id, question)
        print(f"\n  Answer: {result['answer']}")
        print(f"  (from chunks: {result['chunk_indices']})\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Lumi — AI Document Intelligence System"
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        default=None,
        help="Path to a specific document to process (optional)",
    )
    parser.add_argument(
        "--type", "-t",
        type=str,
        default="other",
        choices=["invoices", "contracts", "medical", "ids", "other"],
        help="Document type / S3 folder (default: other)",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Run Bedrock AI analysis (summarization, entities, classification)",
    )
    parser.add_argument(
        "--rag",
        action="store_true",
        help="Build RAG index after extraction so the document is queryable",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        metavar="JOB_ID",
        help="Enter interactive Q&A mode for a previously indexed document",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("\n" + "=" * 60)
    print("  Lumi — AI Document Intelligence System")
    print("  AWS Textract + Amazon Bedrock")
    print("=" * 60)

    if args.query:
        # ── Query mode ────────────────────────────────────────────────────────
        run_query_mode(args.query)
        return

    if args.file:
        # ── Single document mode ──────────────────────────────────────────────
        if not os.path.exists(args.file):
            err(f"File not found: {args.file}")
            sys.exit(1)

        info(f"Processing single file: {args.file}")
        result = run_pipeline(args.file, doc_type=args.type, run_ai=args.ai, run_rag=args.rag)

        if result:
            ok(f"Done! Job ID: {result['job_id']}")
            ok(f"Results saved to: outputs/{result['job_id']}_results.json")
        else:
            err("Pipeline failed.")
            sys.exit(1)

    else:
        # ── Batch mode: all test documents ───────────────────────────────────
        info("Processing all test documents from Phase 1...")
        if args.ai:
            info("AI analysis enabled (Bedrock summarization + entities + classification)")
        if args.rag:
            info("RAG indexing enabled (chunks + embeddings + FAISS index)")

        all_results = process_test_documents(run_ai=args.ai, run_rag=args.rag)
        print_final_summary(all_results)


if __name__ == "__main__":
    main()
